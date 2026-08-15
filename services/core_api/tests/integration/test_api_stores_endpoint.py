"""HTTP-level tests for GET /api/v1/stores/me (Sprint 1 Endpoint 3)."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_store_id, get_store_repository
from app.application.ports import InMemoryStoreRepository
from app.domain.models import Store
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def seeded_store_repo():
    repo = InMemoryStoreRepository()
    store_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    store = Store(
        id=store_id,
        organization_id=uuid.uuid4(),
        shopify_domain="acme.myshopify.com",
        myshopify_domain="acme.myshopify.com",
        store_name="Acme Store",
        currency_code="USD",
        iana_timezone="America/New_York",
        health_score=88,
        created_at=now,
        updated_at=now,
    )
    repo.seed(store)

    app.dependency_overrides[get_store_repository] = lambda: repo
    app.dependency_overrides[get_current_store_id] = lambda: store_id
    yield store_id
    app.dependency_overrides.pop(get_store_repository, None)
    app.dependency_overrides.pop(get_current_store_id, None)


def test_get_my_store_returns_snapshot(client: TestClient, seeded_store_repo):
    response = client.get(
        "/api/v1/stores/me",
        headers={
            "Authorization": "Bearer fake-session-token",
            "X-Shopify-Shop-Domain": "acme.myshopify.com",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["shopify_domain"] == "acme.myshopify.com"
    assert body["health_score"] == 88
    # Sprint 5 Phase 4: real reflection of Settings.demo_mode_enabled, not a
    # hardcoded value — this test doesn't override get_settings, so it just
    # asserts the field is present and boolean.
    assert isinstance(body["demo_mode_enabled"], bool)


def test_get_my_store_404_when_store_missing(client: TestClient):
    app.dependency_overrides[get_store_repository] = lambda: InMemoryStoreRepository()
    app.dependency_overrides[get_current_store_id] = lambda: uuid.uuid4()
    try:
        response = client.get(
            "/api/v1/stores/me",
            headers={"Authorization": "Bearer fake", "X-Shopify-Shop-Domain": "x.myshopify.com"},
        )
        assert response.status_code == 404
        assert response.json()["code"] == "STORE_NOT_FOUND"
    finally:
        app.dependency_overrides.pop(get_store_repository, None)
        app.dependency_overrides.pop(get_current_store_id, None)


def test_get_my_store_401_without_bearer_token(client: TestClient):
    response = client.get(
        "/api/v1/stores/me", headers={"X-Shopify-Shop-Domain": "acme.myshopify.com"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_get_current_store_id_resolves_via_dest_claim():
    """app.api.deps.get_current_store_id decodes the JWT structurally and
    resolves the store via the token's `dest` claim — real Shopify session
    tokens (App Bridge's getSessionToken()) only ever carry Shopify's own
    standard claims (iss/dest/aud/sub/exp/nbf/iat/jti/sid), never a custom
    store_id claim, so `dest` (the shop's myshopify.com domain) is the only
    reliable way to identify the store from the token itself. Full JWKS
    signature verification against Shopify's live keys is a production
    integration point noted in the function's docstring."""
    store_id = uuid.uuid4()
    token = jwt.encode(
        {"dest": "https://acme.myshopify.com", "exp": int(time.time()) + 900},
        "unused-secret",
        algorithm="HS256",
    )
    response = _call_get_current_store_id(f"Bearer {token}", "acme.myshopify.com", store_id=store_id)
    assert response == store_id


def test_get_current_store_id_falls_back_to_header_when_dest_missing():
    """If a token lacks `dest` (shouldn't happen for real App Bridge tokens,
    but keeps the endpoint resilient), fall back to the X-Shopify-Shop-Domain
    header the frontend already sends alongside the bearer token."""
    store_id = uuid.uuid4()
    token = jwt.encode({"exp": int(time.time()) + 900}, "unused-secret", algorithm="HS256")
    response = _call_get_current_store_id(f"Bearer {token}", "acme.myshopify.com", store_id=store_id)
    assert response == store_id


def test_get_current_store_id_rejects_unknown_shop_domain():
    from app.api.errors import UnauthorizedProblem

    token = jwt.encode(
        {"dest": "https://unknown.myshopify.com", "exp": int(time.time()) + 900},
        "unused-secret",
        algorithm="HS256",
    )
    with pytest.raises(UnauthorizedProblem):
        _call_get_current_store_id(f"Bearer {token}", "unknown.myshopify.com", store_id=uuid.uuid4())


def _call_get_current_store_id(authorization: str, shop_domain: str, *, store_id: uuid.UUID):
    import asyncio
    from datetime import datetime, timezone

    from app.api.deps import get_current_store_id
    from app.application.ports import InMemoryStoreRepository
    from app.config import get_settings
    from app.domain.models import Store

    repo = InMemoryStoreRepository()
    now = datetime.now(timezone.utc)
    repo.seed(
        Store(
            id=store_id,
            organization_id=uuid.uuid4(),
            shopify_domain="acme.myshopify.com",
            myshopify_domain="acme.myshopify.com",
            store_name="Acme Store",
            currency_code="USD",
            iana_timezone="America/New_York",
            created_at=now,
            updated_at=now,
        )
    )

    return asyncio.run(
        get_current_store_id(
            authorization=authorization,
            x_shopify_shop_domain=shop_domain,
            settings=get_settings(),
            stores=repo,
        )
    )
