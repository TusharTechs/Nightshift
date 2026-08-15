"""HTTP-level integration tests for Shopify's 4 mandatory compliance
webhooks (app/api/v1/webhooks.py): `app/uninstalled`, `customers/data_request`,
`customers/redact`, `shop/redact`.

Follows `test_demo_incident_api.py`'s `TestClient` + `dependency_overrides`
pattern, swapping in `InMemoryStoreRepository` / `InMemoryAuditLogRepository`
so no live Postgres connection is required.

Mounts `webhooks.router` on a throwaway `FastAPI()` instance (plus the same
RFC 7807 error handlers `app/main.py` registers) rather than importing
`app.main.app` — `app/main.py` is not yet wired to include this router (see
this feature's own report for the exact `app.include_router(...)` line to
add there); testing against a local app that mounts only this router
exercises the real HTTP/Depends/FastAPI stack end-to-end without depending
on that wiring being in place yet, and without touching `app/main.py`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_audit_log_repository, get_store_repository
from app.api.errors import register_error_handlers
from app.api.v1 import webhooks
from app.application.ports import InMemoryAuditLogRepository, InMemoryStoreRepository
from app.domain.models import Store

SECRET = os.environ["SHOPIFY_APP_SECRET"]


def _build_test_app() -> FastAPI:
    test_app = FastAPI()
    register_error_handlers(test_app)
    test_app.include_router(webhooks.router)
    return test_app


app = _build_test_app()


def _sign(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _seed_store(stores: InMemoryStoreRepository, *, shopify_domain: str) -> Store:
    now = datetime.now(timezone.utc)
    store = Store(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        shopify_domain=shopify_domain,
        myshopify_domain=shopify_domain,
        store_name="Acme Test Store",
        created_at=now,
        updated_at=now,
    )
    stores.seed(store)
    return store


@pytest.fixture
def wiring():
    stores = InMemoryStoreRepository()
    audit_logs = InMemoryAuditLogRepository()

    app.dependency_overrides[get_store_repository] = lambda: stores
    app.dependency_overrides[get_audit_log_repository] = lambda: audit_logs
    yield {"stores": stores, "audit_logs": audit_logs}
    app.dependency_overrides.pop(get_store_repository, None)
    app.dependency_overrides.pop(get_audit_log_repository, None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# --- app/uninstalled ---------------------------------------------------------


def test_app_uninstalled_valid_hmac_deactivates_known_store(client: TestClient, wiring):
    store = _seed_store(wiring["stores"], shopify_domain="acme.myshopify.com")
    body = json.dumps({"id": 954889, "myshopify_domain": "acme.myshopify.com"}).encode()

    response = client.post(
        "/api/v1/webhooks/app-uninstalled",
        content=body,
        headers={
            "X-Shopify-Hmac-Sha256": _sign(body),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200

    reloaded = wiring["stores"]._by_id[store.id]
    assert reloaded.is_active is False

    entries = wiring["audit_logs"]._entries
    assert len(entries) == 1
    assert entries[0].store_id == store.id
    assert entries[0].action == "STORE_DEACTIVATED"
    assert entries[0].actor_type == "SHOPIFY_WEBHOOK"


def test_app_uninstalled_invalid_hmac_rejected_and_store_untouched(client: TestClient, wiring):
    store = _seed_store(wiring["stores"], shopify_domain="acme.myshopify.com")
    body = json.dumps({"id": 954889, "myshopify_domain": "acme.myshopify.com"}).encode()

    response = client.post(
        "/api/v1/webhooks/app-uninstalled",
        content=body,
        headers={"X-Shopify-Hmac-Sha256": "not-a-real-signature"},
    )

    assert response.status_code == 401
    # Store must be untouched and no audit log written — an invalid HMAC
    # must never reach any DB write.
    assert wiring["stores"]._by_id[store.id].is_active is True
    assert wiring["audit_logs"]._entries == []


def test_app_uninstalled_missing_hmac_header_rejected(client: TestClient, wiring):
    body = json.dumps({"id": 1, "myshopify_domain": "acme.myshopify.com"}).encode()

    response = client.post("/api/v1/webhooks/app-uninstalled", content=body)

    assert response.status_code == 401
    assert wiring["audit_logs"]._entries == []


def test_app_uninstalled_unknown_shop_returns_200_without_crashing(client: TestClient, wiring):
    body = json.dumps({"id": 1, "myshopify_domain": "never-installed.myshopify.com"}).encode()

    response = client.post(
        "/api/v1/webhooks/app-uninstalled",
        content=body,
        headers={"X-Shopify-Hmac-Sha256": _sign(body)},
    )

    assert response.status_code == 200
    assert wiring["audit_logs"]._entries == []


# --- customers/data_request / customers/redact / shop/redact ----------------


@pytest.mark.parametrize(
    ("path", "expected_action"),
    [
        ("/api/v1/webhooks/customers-data-request", "CUSTOMER_DATA_REQUEST_ACKNOWLEDGED"),
        ("/api/v1/webhooks/customers-redact", "CUSTOMER_REDACT_ACKNOWLEDGED"),
        ("/api/v1/webhooks/shop-redact", "SHOP_REDACT_ACKNOWLEDGED"),
    ],
)
def test_gdpr_webhook_valid_hmac_known_shop_writes_audit_log(
    client: TestClient, wiring, path: str, expected_action: str
):
    store = _seed_store(wiring["stores"], shopify_domain="acme.myshopify.com")
    body = json.dumps({"shop_id": 954889, "shop_domain": "acme.myshopify.com"}).encode()

    response = client.post(path, content=body, headers={"X-Shopify-Hmac-Sha256": _sign(body)})

    assert response.status_code == 200
    entries = wiring["audit_logs"]._entries
    assert len(entries) == 1
    assert entries[0].store_id == store.id
    assert entries[0].action == expected_action
    assert entries[0].actor_type == "SHOPIFY_WEBHOOK"
    # No fabricated "customer data deleted" claim: the recorded rationale
    # must state plainly that there was nothing to delete.
    if "redact" in expected_action.lower():
        assert "no" in entries[0].rationale.lower()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/webhooks/customers-data-request",
        "/api/v1/webhooks/customers-redact",
        "/api/v1/webhooks/shop-redact",
    ],
)
def test_gdpr_webhook_invalid_hmac_rejected_no_audit_log(client: TestClient, wiring, path: str):
    body = json.dumps({"shop_id": 1, "shop_domain": "acme.myshopify.com"}).encode()

    response = client.post(path, content=body, headers={"X-Shopify-Hmac-Sha256": "garbage"})

    assert response.status_code == 401
    assert wiring["audit_logs"]._entries == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/webhooks/customers-data-request",
        "/api/v1/webhooks/customers-redact",
        "/api/v1/webhooks/shop-redact",
    ],
)
def test_gdpr_webhook_unknown_shop_still_returns_200(client: TestClient, wiring, path: str):
    body = json.dumps({"shop_id": 1, "shop_domain": "never-installed.myshopify.com"}).encode()

    response = client.post(path, content=body, headers={"X-Shopify-Hmac-Sha256": _sign(body)})

    assert response.status_code == 200
    # No store found -> no store_id to attach an audit_logs row to.
    assert wiring["audit_logs"]._entries == []
