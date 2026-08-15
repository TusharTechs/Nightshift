"""HTTP-level integration tests: `/api/v1/billing/*` (Sprint 6 Billing).

Follows `test_demo_incident_api.py`'s `TestClient` + `app.dependency_overrides`
pattern for dependency overrides, and `test_webhooks_api.py`'s "mount the
router on a throwaway FastAPI() instance" pattern for the app itself:
`app/main.py` is not yet wired to include `billing.router` (see this
feature's own report for the exact `app.include_router(...)` line to add
there — deliberately not touched per this task's own instructions); testing
against a local app that mounts only this router exercises the real HTTP/
Depends/FastAPI stack end-to-end without depending on that wiring being in
place yet, and without touching `app/main.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import (
    get_current_store_id,
    get_shopify_client_for_store,
    get_shopify_client_for_store_id,
    get_store_repository,
    get_subscription_repository,
)
from app.api.errors import ShopifyApiProblem, register_error_handlers
from app.api.v1 import billing
from app.application.ports import InMemoryStoreRepository, InMemorySubscriptionRepository
from app.config import Settings, get_settings
from app.domain.models import Store


def _build_test_app() -> FastAPI:
    test_app = FastAPI()
    register_error_handlers(test_app)
    test_app.include_router(billing.router)
    return test_app


app = _build_test_app()


class _RecordingBillingShopifyClient:
    def __init__(
        self,
        *,
        create_result: dict[str, Any] | None = None,
        create_error: Exception | None = None,
        state_result: dict[str, Any] | None = None,
    ) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self._create_result = create_result
        self._create_error = create_error
        self._state_result = state_result or {}

    async def create_recurring_subscription(
        self, *, name: str, return_url: str, monthly_price_usd: float, test: bool = True, **_: Any
    ) -> dict[str, Any]:
        self.create_calls.append(
            {"name": name, "return_url": return_url, "monthly_price_usd": monthly_price_usd, "test": test}
        )
        if self._create_error is not None:
            raise self._create_error
        return self._create_result or {}

    async def fetch_app_subscription_state(self, *, subscription_gid: str) -> dict[str, Any]:
        return self._state_result


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _seeded_store(stores: InMemoryStoreRepository) -> Store:
    now = datetime.now(timezone.utc)
    store = Store(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        shopify_domain="acme.myshopify.com",
        myshopify_domain="acme.myshopify.com",
        store_name="Acme",
        created_at=now,
        updated_at=now,
    )
    stores.seed(store)
    return store


def _clear_overrides():
    for dep in (
        get_current_store_id,
        get_settings,
        get_shopify_client_for_store,
        get_shopify_client_for_store_id,
        get_store_repository,
        get_subscription_repository,
    ):
        app.dependency_overrides.pop(dep, None)


def _settings(**overrides) -> Settings:
    settings = Settings()
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


# --- GET /plans ---------------------------------------------------------


def test_list_plans_returns_three_tiers_with_no_auth_and_no_shopify_call(client: TestClient):
    response = client.get("/api/v1/billing/plans")

    assert response.status_code == 200
    body = response.json()
    plans = {p["plan"]: p for p in body["plans"]}
    assert set(plans) == {"FREE", "PRO", "BUSINESS"}
    assert plans["FREE"]["monthly_price_usd"] == 0.0
    assert plans["PRO"]["monthly_price_usd"] == 29.0
    assert plans["BUSINESS"]["monthly_price_usd"] == 79.0


# --- POST /subscribe ------------------------------------------------------


def test_subscribe_happy_path_creates_pending_subscription_and_returns_confirmation_url(
    client: TestClient,
):
    store_id = uuid.uuid4()
    subscriptions = InMemorySubscriptionRepository()
    shopify_client = _RecordingBillingShopifyClient(
        create_result={
            "id": "gid://shopify/AppSubscription/1",
            "status": "PENDING",
            "confirmation_url": "https://acme.myshopify.com/admin/charges/1/confirm",
        }
    )

    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_settings] = lambda: _settings()
    app.dependency_overrides[get_subscription_repository] = lambda: subscriptions

    async def _fake_client():
        yield shopify_client

    app.dependency_overrides[get_shopify_client_for_store] = _fake_client
    try:
        response = client.post("/api/v1/billing/subscribe", json={"plan": "PRO"})
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["plan"] == "PRO"
    assert body["status"] == "PENDING"
    assert body["confirmation_url"] == "https://acme.myshopify.com/admin/charges/1/confirm"
    assert body["monthly_price_usd"] == 29.0

    stored = subscriptions._by_id[uuid.UUID(body["subscription_id"])]
    assert stored.store_id == store_id
    assert stored.shopify_charge_gid == "gid://shopify/AppSubscription/1"
    assert len(shopify_client.create_calls) == 1
    assert f"store_id={store_id}" in shopify_client.create_calls[0]["return_url"]


def test_subscribe_rejects_invalid_plan(client: TestClient):
    store_id = uuid.uuid4()
    subscriptions = InMemorySubscriptionRepository()

    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_settings] = lambda: _settings()
    app.dependency_overrides[get_subscription_repository] = lambda: subscriptions

    async def _fake_client():
        yield _RecordingBillingShopifyClient()

    app.dependency_overrides[get_shopify_client_for_store] = _fake_client
    try:
        response = client.post("/api/v1/billing/subscribe", json={"plan": "FREE"})
    finally:
        _clear_overrides()

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PLAN"
    assert subscriptions._by_id == {}


def test_subscribe_shopify_failure_returns_502_with_no_orphaned_pending_row(client: TestClient):
    store_id = uuid.uuid4()
    subscriptions = InMemorySubscriptionRepository()
    shopify_client = _RecordingBillingShopifyClient(
        create_error=ShopifyApiProblem("appSubscriptionCreate rejected: [{'message': 'boom'}]")
    )

    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_settings] = lambda: _settings()
    app.dependency_overrides[get_subscription_repository] = lambda: subscriptions

    async def _fake_client():
        yield shopify_client

    app.dependency_overrides[get_shopify_client_for_store] = _fake_client
    try:
        response = client.post("/api/v1/billing/subscribe", json={"plan": "PRO"})
    finally:
        _clear_overrides()

    assert response.status_code == 502
    assert response.json()["code"] == "SHOPIFY_API_ERROR"
    # No PENDING row was ever persisted for the failed attempt.
    assert subscriptions._by_id == {}


def test_subscribe_returns_404_when_billing_disabled(client: TestClient):
    store_id = uuid.uuid4()
    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_settings] = lambda: _settings(billing_enabled=False)
    app.dependency_overrides[get_subscription_repository] = lambda: InMemorySubscriptionRepository()

    async def _fake_client():
        yield _RecordingBillingShopifyClient()

    app.dependency_overrides[get_shopify_client_for_store] = _fake_client
    try:
        response = client.post("/api/v1/billing/subscribe", json={"plan": "PRO"})
    finally:
        _clear_overrides()

    assert response.status_code == 404
    assert response.json()["code"] == "BILLING_DISABLED"


# --- GET /confirm ----------------------------------------------------------


def _confirm_overrides(
    *, stores: InMemoryStoreRepository, subscriptions: InMemorySubscriptionRepository, live_status: str
):
    app.dependency_overrides[get_store_repository] = lambda: stores
    app.dependency_overrides[get_subscription_repository] = lambda: subscriptions
    app.dependency_overrides[get_settings] = lambda: _settings()

    async def _fake_client():
        yield _RecordingBillingShopifyClient(state_result={"status": live_status})

    app.dependency_overrides[get_shopify_client_for_store_id] = _fake_client


def test_confirm_activates_subscription_and_redirects(client: TestClient):
    stores = InMemoryStoreRepository()
    store = _seeded_store(stores)
    subscriptions = InMemorySubscriptionRepository()
    charge_gid = "gid://shopify/AppSubscription/42"
    pending = asyncio.run(
        subscriptions.create(
            store_id=store.id, plan="PRO", status="PENDING", shopify_charge_gid=charge_gid, monthly_price_usd=29.0
        )
    )

    _confirm_overrides(stores=stores, subscriptions=subscriptions, live_status="ACTIVE")
    try:
        response = client.get(
            "/api/v1/billing/confirm",
            params={"store_id": str(store.id), "charge_id": "42"},
            follow_redirects=False,
        )
    finally:
        _clear_overrides()

    assert response.status_code == 302
    assert response.headers["location"] == f"https://{store.shopify_domain}/admin/apps/test-client-id"

    updated = asyncio.run(subscriptions.get_by_id(pending.id))
    assert updated.status == "ACTIVE"
    assert updated.activated_at is not None


def test_confirm_declined_marks_subscription_declined(client: TestClient):
    stores = InMemoryStoreRepository()
    store = _seeded_store(stores)
    subscriptions = InMemorySubscriptionRepository()
    charge_gid = "gid://shopify/AppSubscription/43"
    pending = asyncio.run(
        subscriptions.create(
            store_id=store.id, plan="PRO", status="PENDING", shopify_charge_gid=charge_gid, monthly_price_usd=29.0
        )
    )

    _confirm_overrides(stores=stores, subscriptions=subscriptions, live_status="DECLINED")
    try:
        response = client.get(
            "/api/v1/billing/confirm",
            params={"store_id": str(store.id), "charge_id": "43"},
            follow_redirects=False,
        )
    finally:
        _clear_overrides()

    assert response.status_code == 302
    updated = asyncio.run(subscriptions.get_by_id(pending.id))
    assert updated.status == "DECLINED"
    assert updated.activated_at is None
    assert updated.cancelled_at is not None


def test_confirm_tenant_isolation_store_cannot_confirm_another_stores_subscription(client: TestClient):
    stores = InMemoryStoreRepository()
    store_a = _seeded_store(stores)
    now = datetime.now(timezone.utc)
    store_b = Store(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        shopify_domain="other.myshopify.com",
        myshopify_domain="other.myshopify.com",
        store_name="Other",
        created_at=now,
        updated_at=now,
    )
    stores.seed(store_b)

    subscriptions = InMemorySubscriptionRepository()
    charge_gid = "gid://shopify/AppSubscription/99"
    asyncio.run(
        subscriptions.create(
            store_id=store_a.id, plan="PRO", status="PENDING", shopify_charge_gid=charge_gid, monthly_price_usd=29.0
        )
    )

    _confirm_overrides(stores=stores, subscriptions=subscriptions, live_status="ACTIVE")
    try:
        # store_b tries to confirm store_a's charge_id.
        response = client.get(
            "/api/v1/billing/confirm",
            params={"store_id": str(store_b.id), "charge_id": "99"},
            follow_redirects=False,
        )
    finally:
        _clear_overrides()

    assert response.status_code == 404
    assert response.json()["code"] == "SUBSCRIPTION_NOT_FOUND"

    # store_a's own subscription was never touched (still PENDING).
    current_a = asyncio.run(subscriptions.get_current_for_store(store_a.id))
    assert current_a.status == "PENDING"


# --- GET /status ------------------------------------------------------------


def test_status_returns_current_plan(client: TestClient):
    store_id = uuid.uuid4()
    subscriptions = InMemorySubscriptionRepository()
    asyncio.run(
        subscriptions.create(store_id=store_id, plan="FREE", status="ACTIVE", monthly_price_usd=0.0)
    )

    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_subscription_repository] = lambda: subscriptions
    try:
        response = client.get("/api/v1/billing/status")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["plan"] == "FREE"
    assert body["status"] == "ACTIVE"


def test_status_404_when_no_subscription_on_file(client: TestClient):
    store_id = uuid.uuid4()
    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_subscription_repository] = lambda: InMemorySubscriptionRepository()
    try:
        response = client.get("/api/v1/billing/status")
    finally:
        _clear_overrides()

    assert response.status_code == 404
    assert response.json()["code"] == "SUBSCRIPTION_NOT_FOUND"
