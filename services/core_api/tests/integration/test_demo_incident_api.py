"""HTTP-level integration test: `POST /api/v1/demo/incidents/{scenario_id}`
(Sprint 4 Step 1), following `test_handle_approval_action_api.py`'s
`TestClient` + `app.dependency_overrides` pattern.

`get_settings` and `get_trigger_demo_incident_use_case` are both overridden
directly — the former to flip `demo_mode_enabled` without needing a real
`.env`, the latter to hold onto the exact fake Shopify client/in-memory
audit log the route will exercise, mirroring how
`test_handle_approval_action_api.py` overrides its own use-case dependency
directly rather than each constituent repository provider.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_store_id, get_task_dispatcher, get_trigger_demo_incident_use_case
from app.application.ports import InMemoryAuditLogRepository, InMemoryTaskDispatcher
from app.application.use_cases.trigger_demo_incident import TriggerDemoIncident
from app.config import Settings, get_settings
from app.main import app


class _RecordingShopifyClient:
    def __init__(self, *, products: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._products = products or []

    async def create_basic_discount_code(
        self, *, title: str, code: str, percentage: float, starts_at: str, combines_with=None
    ) -> dict[str, Any]:
        self.calls.append({"title": title, "code": code})
        return {"id": f"gid://shopify/DiscountCodeNode/{len(self.calls)}"}

    async def fetch_catalog_for_inspection(self, *, max_products: int = 500) -> list[dict[str, Any]]:
        return self._products[:max_products]

    async def update_product_description(self, *, product_gid: str, description_html: str) -> dict[str, Any]:
        self.calls.append({"mutation": "update_product_description", "product_gid": product_gid})
        return {"id": product_gid, "descriptionHtml": description_html}

    async def update_product_image_alt_text(
        self, *, product_gid: str, image_gid: str, alt_text: str
    ) -> dict[str, Any]:
        self.calls.append({"mutation": "update_product_image_alt_text", "image_gid": image_gid})
        return {"id": image_gid, "altText": alt_text}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _settings_with_demo_mode(enabled: bool) -> Settings:
    # `demo_mode_enabled`'s field has a `validation_alias` (DEMO_MODE_ENABLED)
    # and the model has no `populate_by_name=True`, so constructing via the
    # plain field name kwarg (`Settings(demo_mode_enabled=enabled)`) is
    # silently ignored by pydantic-settings — set the attribute directly on
    # an already-built instance instead, which always works regardless of
    # alias configuration.
    settings = Settings()
    settings.demo_mode_enabled = enabled
    return settings


def _apply_overrides(
    *,
    demo_mode_enabled: bool,
    store_id: uuid.UUID,
    products: list[dict[str, Any]] | None = None,
) -> tuple[TriggerDemoIncident, InMemoryTaskDispatcher]:
    shopify_client = _RecordingShopifyClient(products=products)
    audit_logs = InMemoryAuditLogRepository()
    use_case = TriggerDemoIncident(shopify_client=shopify_client, audit_logs=audit_logs)
    dispatcher = InMemoryTaskDispatcher()

    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_settings] = lambda: _settings_with_demo_mode(demo_mode_enabled)
    app.dependency_overrides[get_trigger_demo_incident_use_case] = lambda: use_case
    app.dependency_overrides[get_task_dispatcher] = lambda: dispatcher
    return use_case, dispatcher


def _clear_overrides():
    app.dependency_overrides.pop(get_current_store_id, None)
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_trigger_demo_incident_use_case, None)
    app.dependency_overrides.pop(get_task_dispatcher, None)


def test_returns_404_when_demo_mode_disabled(client: TestClient):
    store_id = uuid.uuid4()
    _apply_overrides(demo_mode_enabled=False, store_id=store_id)
    try:
        response = client.post("/api/v1/demo/incidents/midnight_pricing_disaster")
    finally:
        _clear_overrides()

    assert response.status_code == 404
    assert response.json()["code"] == "DEMO_MODE_DISABLED"


def test_triggers_midnight_pricing_disaster_when_demo_mode_enabled(client: TestClient):
    store_id = uuid.uuid4()
    _, dispatcher = _apply_overrides(demo_mode_enabled=True, store_id=store_id)
    try:
        response = client.post("/api/v1/demo/incidents/midnight_pricing_disaster")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["scenario_id"] == "midnight_pricing_disaster"
    assert len(body["created_discount_codes"]) == 2
    # Sprint 5 Phase 4: a background shift is dispatched in the same request.
    assert body["shift_dispatch_task_id"] is not None
    assert dispatcher.dispatched_inspect_catalog_store_ids == [store_id]


def test_returns_404_for_unknown_scenario_even_when_demo_mode_enabled(client: TestClient):
    store_id = uuid.uuid4()
    _apply_overrides(demo_mode_enabled=True, store_id=store_id)
    try:
        response = client.post("/api/v1/demo/incidents/not_a_real_scenario")
    finally:
        _clear_overrides()

    assert response.status_code == 404
    assert response.json()["code"] == "UNKNOWN_DEMO_SCENARIO"


# --- Sprint 5 Phase 4: Scenario 3 ("Catalog SEO Collapse") -------------------


def _flagship_product(*, with_image: bool) -> dict[str, Any]:
    product = {
        "id": "gid://shopify/Product/1",
        "title": "Wireless Earbuds Pro",
        "media": {"nodes": []},
    }
    if with_image:
        product["media"]["nodes"] = [
            {"id": "gid://shopify/MediaImage/1", "mediaContentType": "IMAGE", "alt": "Studio photo"}
        ]
    return product


def test_triggers_catalog_seo_collapse_strips_description_and_alt_text(client: TestClient):
    store_id = uuid.uuid4()
    _, dispatcher = _apply_overrides(
        demo_mode_enabled=True, store_id=store_id, products=[_flagship_product(with_image=True)]
    )
    try:
        response = client.post("/api/v1/demo/incidents/catalog_seo_collapse")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert body["scenario_id"] == "catalog_seo_collapse"
    assert "Wireless Earbuds Pro" in body["notes"]
    assert body["shift_dispatch_task_id"] is not None
    assert dispatcher.dispatched_inspect_catalog_store_ids == [store_id]


def test_catalog_seo_collapse_handles_a_product_with_no_image(client: TestClient):
    store_id = uuid.uuid4()
    _apply_overrides(
        demo_mode_enabled=True, store_id=store_id, products=[_flagship_product(with_image=False)]
    )
    try:
        response = client.post("/api/v1/demo/incidents/catalog_seo_collapse")
    finally:
        _clear_overrides()

    assert response.status_code == 200
    body = response.json()
    assert "only its description was stripped" in body["notes"]


def test_catalog_seo_collapse_422s_when_store_has_no_active_products(client: TestClient):
    store_id = uuid.uuid4()
    _apply_overrides(demo_mode_enabled=True, store_id=store_id, products=[])
    try:
        response = client.post("/api/v1/demo/incidents/catalog_seo_collapse")
    finally:
        _clear_overrides()

    assert response.status_code == 422
    assert response.json()["code"] == "DEMO_SCENARIO_NO_ELIGIBLE_PRODUCT"
