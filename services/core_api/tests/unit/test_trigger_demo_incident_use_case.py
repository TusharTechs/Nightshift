"""Unit tests for `TriggerDemoIncident` — Sprint 4 Step 1's Demo Incident
Generator use case. Shopify is faked with a tiny in-process recorder (no
respx/HTTP — that's `test_shopify_client.py`'s job for the raw GraphQL
shape); this test is about the use case's own orchestration: which scenario
ids are accepted, how many discount codes get created, and that a
DEMO_INCIDENT_TRIGGERED audit entry is written.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.api.errors import DemoScenarioNoEligibleProductProblem, UnknownDemoScenarioProblem
from app.application.ports import InMemoryAuditLogRepository
from app.application.use_cases.trigger_demo_incident import TriggerDemoIncident


class _RecordingShopifyClient:
    def __init__(self, *, products: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.script_tag_calls: list[dict[str, Any]] = []
        self.deleted_script_tag_ids: list[str] = []
        self._products = products or []
        self.description_updates: list[dict[str, Any]] = []
        self.alt_text_updates: list[dict[str, Any]] = []

    async def fetch_catalog_for_inspection(self, *, max_products: int = 500) -> list[dict[str, Any]]:
        return self._products[:max_products]

    async def update_product_description(self, *, product_gid: str, description_html: str) -> dict[str, Any]:
        self.description_updates.append({"product_gid": product_gid, "description_html": description_html})
        return {"id": product_gid, "descriptionHtml": description_html}

    async def update_product_image_alt_text(
        self, *, product_gid: str, image_gid: str, alt_text: str
    ) -> dict[str, Any]:
        self.alt_text_updates.append({"product_gid": product_gid, "image_gid": image_gid, "alt_text": alt_text})
        return {"id": image_gid, "altText": alt_text}

    async def create_basic_discount_code(
        self, *, title: str, code: str, percentage: float, starts_at: str, combines_with=None
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "title": title,
                "code": code,
                "percentage": percentage,
                "starts_at": starts_at,
                "combines_with": combines_with,
            }
        )
        return {"id": f"gid://shopify/DiscountCodeNode/{len(self.calls)}"}

    async def create_script_tag(self, *, src: str, display_scope: str = "ONLINE_STORE") -> dict[str, Any]:
        # Sprint 4 Step 3: Rogue Developer Theme Break's tracking half.
        self.script_tag_calls.append({"src": src, "display_scope": display_scope})
        return {
            "id": f"gid://shopify/ScriptTag/{len(self.script_tag_calls)}",
            "src": src,
            "display_scope": display_scope,
        }

    async def delete_script_tag(self, *, script_tag_id: str) -> dict[str, Any]:
        self.deleted_script_tag_ids.append(script_tag_id)
        return {"deleted_script_tag_id": script_tag_id}


async def test_midnight_pricing_disaster_creates_two_overlapping_stackable_codes():
    store_id = uuid.uuid4()
    shopify_client = _RecordingShopifyClient()
    audit_logs = InMemoryAuditLogRepository()
    use_case = TriggerDemoIncident(shopify_client=shopify_client, audit_logs=audit_logs)

    result = await use_case.execute(scenario_id="midnight_pricing_disaster", store_id=store_id)

    assert result.scenario_id == "midnight_pricing_disaster"
    assert len(result.created_discount_codes) == 2
    assert len(shopify_client.calls) == 2

    # Both codes are genuinely distinct (unique suffix) yet both 50% off and
    # both combinable with every discount class — the "overlapping,
    # stackable" incident Scenario 1 is named for.
    assert len(set(result.created_discount_codes)) == 2
    for call in shopify_client.calls:
        assert call["percentage"] == 0.5
        assert call["combines_with"] == {
            "orderDiscounts": True,
            "productDiscounts": True,
            "shippingDiscounts": True,
        }

    audit_entries = await audit_logs.list_for_store(store_id)
    assert len(audit_entries) == 1
    entry = audit_entries[0]
    assert entry.action == "DEMO_INCIDENT_TRIGGERED"
    assert entry.actor_type == "DEMO_GENERATOR"
    assert entry.after_state == {"created_discount_codes": result.created_discount_codes}


async def test_retriggering_the_same_scenario_produces_non_colliding_codes():
    store_id = uuid.uuid4()
    shopify_client = _RecordingShopifyClient()
    audit_logs = InMemoryAuditLogRepository()
    use_case = TriggerDemoIncident(shopify_client=shopify_client, audit_logs=audit_logs)

    first = await use_case.execute(scenario_id="midnight_pricing_disaster", store_id=store_id)
    second = await use_case.execute(scenario_id="midnight_pricing_disaster", store_id=store_id)

    assert set(first.created_discount_codes).isdisjoint(second.created_discount_codes)


async def test_unknown_scenario_id_raises_problem():
    use_case = TriggerDemoIncident(
        shopify_client=_RecordingShopifyClient(), audit_logs=InMemoryAuditLogRepository()
    )
    with pytest.raises(UnknownDemoScenarioProblem):
        await use_case.execute(scenario_id="not_a_real_scenario", store_id=uuid.uuid4())


def _flagship_product(*, with_image: bool) -> dict[str, Any]:
    product = {"id": "gid://shopify/Product/1", "title": "Wireless Earbuds Pro", "media": {"nodes": []}}
    if with_image:
        product["media"]["nodes"] = [
            {"id": "gid://shopify/MediaImage/1", "mediaContentType": "IMAGE", "alt": "Studio photo"}
        ]
    return product


async def test_catalog_seo_collapse_strips_description_and_image_alt_text():
    # Sprint 5 Phase 4: Scenario 3 is now wired.
    store_id = uuid.uuid4()
    shopify_client = _RecordingShopifyClient(products=[_flagship_product(with_image=True)])
    audit_logs = InMemoryAuditLogRepository()
    use_case = TriggerDemoIncident(shopify_client=shopify_client, audit_logs=audit_logs)

    result = await use_case.execute(scenario_id="catalog_seo_collapse", store_id=store_id)

    assert result.scenario_id == "catalog_seo_collapse"
    assert result.notes is not None and "Wireless Earbuds Pro" in result.notes
    assert len(shopify_client.description_updates) == 1
    assert shopify_client.description_updates[0]["description_html"] == ""
    assert len(shopify_client.alt_text_updates) == 1
    assert shopify_client.alt_text_updates[0]["alt_text"] == ""

    audit_entries = await audit_logs.list_for_store(store_id)
    assert len(audit_entries) == 1
    assert audit_entries[0].action == "DEMO_INCIDENT_TRIGGERED"
    assert audit_entries[0].actor_type == "DEMO_GENERATOR"


async def test_catalog_seo_collapse_skips_alt_text_when_product_has_no_image():
    store_id = uuid.uuid4()
    shopify_client = _RecordingShopifyClient(products=[_flagship_product(with_image=False)])
    use_case = TriggerDemoIncident(shopify_client=shopify_client, audit_logs=InMemoryAuditLogRepository())

    result = await use_case.execute(scenario_id="catalog_seo_collapse", store_id=store_id)

    assert len(shopify_client.description_updates) == 1
    assert len(shopify_client.alt_text_updates) == 0
    assert result.notes is not None and "only its description was stripped" in result.notes


async def test_catalog_seo_collapse_raises_when_store_has_no_active_products():
    use_case = TriggerDemoIncident(
        shopify_client=_RecordingShopifyClient(products=[]), audit_logs=InMemoryAuditLogRepository()
    )
    with pytest.raises(DemoScenarioNoEligibleProductProblem):
        await use_case.execute(scenario_id="catalog_seo_collapse", store_id=uuid.uuid4())


async def test_rogue_developer_theme_break_triggers_only_the_tracking_half():
    # Sprint 4 Step 3: this scenario is wired, but only its tracking half
    # (create then delete a Meta Pixel-pattern script tag) actually executes
    # — the theme half has no automated write path at all (see
    # ROGUE_DEVELOPER_THEME_BREAK's own description). No
    # `tracking_snapshots` repo is passed here, so the baseline-seeding step
    # is skipped (optional dependency) — this test only verifies the
    # Shopify-facing half.
    store_id = uuid.uuid4()
    shopify_client = _RecordingShopifyClient()
    audit_logs = InMemoryAuditLogRepository()
    use_case = TriggerDemoIncident(shopify_client=shopify_client, audit_logs=audit_logs)

    result = await use_case.execute(scenario_id="rogue_developer_theme_break", store_id=store_id)

    assert result.scenario_id == "rogue_developer_theme_break"
    assert result.notes is not None and "theme half" in result.notes
    assert len(shopify_client.script_tag_calls) == 1
    assert "connect.facebook.net" in shopify_client.script_tag_calls[0]["src"]
    assert len(shopify_client.deleted_script_tag_ids) == 1

    audit_entries = await audit_logs.list_for_store(store_id)
    assert len(audit_entries) == 1
    assert audit_entries[0].action == "DEMO_INCIDENT_TRIGGERED"
    assert audit_entries[0].actor_type == "DEMO_GENERATOR"
