"""HTTP-level integration tests for the OAuth ingress endpoints.

Exercises app/api/v1/auth.py, app/api/deps.py, and app/api/errors.py through
FastAPI's TestClient, with the Shopify token-exchange HTTP call mocked via
respx (no live Shopify credentials required) and the persistence layer
swapped for in-memory fakes via dependency_overrides.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.api.deps import get_complete_oauth_installation_use_case
from app.application.ports import (
    InMemoryOrganizationRepository,
    InMemoryStoreRepository,
    InMemoryStoreTokenRepository,
    InMemoryTaskDispatcher,
)
from app.application.use_cases.complete_oauth_installation import CompleteOAuthInstallation
from app.domain.security import TokenCipher
from app.main import app

SECRET = os.environ["SHOPIFY_APP_SECRET"]


def _sign(params: dict[str, str]) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def fake_use_case_wiring():
    """Overrides the OAuth use-case dependency with in-memory fakes so no
    real Postgres connection is required for these HTTP-level tests."""
    token_cipher = TokenCipher.from_base64_key(base64.b64encode(os.urandom(32)).decode())
    task_dispatcher = InMemoryTaskDispatcher()
    stores = InMemoryStoreRepository()

    use_case = CompleteOAuthInstallation(
        organizations=InMemoryOrganizationRepository(),
        stores=stores,
        store_tokens=InMemoryStoreTokenRepository(),
        task_dispatcher=task_dispatcher,
        token_cipher=token_cipher,
        shopify_app_secret=SECRET,
    )

    app.dependency_overrides[get_complete_oauth_installation_use_case] = lambda: use_case
    yield {"stores": stores, "task_dispatcher": task_dispatcher}
    app.dependency_overrides.pop(get_complete_oauth_installation_use_case, None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_initiate_oauth_redirects_with_exact_eight_scopes(client: TestClient):
    response = client.get(
        "/api/v1/auth/shopify", params={"shop": "acme.myshopify.com"}, follow_redirects=False
    )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://acme.myshopify.com/admin/oauth/authorize?")

    scope_param = next(p for p in location.split("?", 1)[1].split("&") if p.startswith("scope="))
    scopes = scope_param.removeprefix("scope=").split("%2C")
    # `write_script_tags` added Sprint 4 Step 3 (Tracking Specialist's
    # scriptTagCreate/scriptTagDelete) — live-verified against the real
    # Shopify Admin API to actually require this scope (an ACCESS_DENIED
    # error without it), not just a docs claim.
    assert set(scopes) == {
        "read_products",
        "write_products",
        "read_discounts",
        "write_discounts",
        "read_themes",
        "write_themes",
        "read_script_tags",
        "write_script_tags",
    }
    assert "write_payment_gateways" not in scopes


def test_initiate_oauth_rejects_invalid_shop_domain(client: TestClient):
    response = client.get("/api/v1/auth/shopify", params={"shop": "not-a-shop.evil.com"})

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "INVALID_SHOP_DOMAIN"
    assert body["status"] == 400


def test_oauth_callback_success_redirects_to_app_bridge(
    client: TestClient, fake_use_case_wiring, respx_mock: respx.MockRouter
):
    respx_mock.post("https://acme.myshopify.com/admin/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "shpat_x", "scope": "read_products"})
    )

    now = int(time.time())
    raw_params = {"shop": "acme.myshopify.com", "code": "abc123", "timestamp": str(now), "host": "aGVsbG8"}
    signed_params = {**raw_params, "hmac": _sign(raw_params)}

    response = client.get(
        "/api/v1/auth/shopify/callback", params=signed_params, follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["location"] == "https://acme.myshopify.com/admin/apps/nightshift-ai"
    assert len(fake_use_case_wiring["task_dispatcher"].dispatched_store_ids) == 1


def test_oauth_callback_rejects_tampered_hmac(
    client: TestClient, fake_use_case_wiring, respx_mock: respx.MockRouter
):
    now = int(time.time())
    raw_params = {"shop": "acme.myshopify.com", "code": "abc123", "timestamp": str(now)}
    signed_params = {**raw_params, "hmac": _sign(raw_params)}
    signed_params["code"] = "tampered-code"

    response = client.get("/api/v1/auth/shopify/callback", params=signed_params)

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_HMAC_SIGNATURE"


def test_oauth_callback_returns_502_on_shopify_api_failure(
    client: TestClient, fake_use_case_wiring, respx_mock: respx.MockRouter
):
    respx_mock.post("https://acme.myshopify.com/admin/oauth/access_token").mock(
        return_value=httpx.Response(500)
    )

    now = int(time.time())
    raw_params = {"shop": "acme.myshopify.com", "code": "abc123", "timestamp": str(now)}
    signed_params = {**raw_params, "hmac": _sign(raw_params)}

    response = client.get("/api/v1/auth/shopify/callback", params=signed_params)

    # Brief Section 7.3: Sprint 1's own spec calls for 502 here, not the API
    # Contract doc's stale 500.
    assert response.status_code == 502
    assert response.json()["code"] == "SHOPIFY_API_ERROR"


def test_health_check_endpoint(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
