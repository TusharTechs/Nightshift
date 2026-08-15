"""Unit tests for the Discount Inspection Engine (Sprint 4 Step 2 — Checkout
Specialist's Observe step). Pure domain logic, no I/O — mirrors
`test_product_inspection_engine.py`'s own scope and style.
"""

from __future__ import annotations

from app.domain.demo_incidents import MIDNIGHT_PRICING_DISASTER_DISCOUNT_SPECS
from app.domain.discount_inspection import inspect_discounts


def _discount(
    discount_id: str,
    *,
    status: str = "ACTIVE",
    targets_all_items: bool = True,
    product_discounts: bool = True,
    created_at: str = "2026-08-01T00:00:00Z",
    total_sales_usd: float = 0.0,
    code: str | None = None,
) -> dict:
    return {
        "id": discount_id,
        "title": f"Discount {discount_id}",
        "code": code or discount_id,
        "status": status,
        "created_at": created_at,
        "targets_all_items": targets_all_items,
        "combines_with": {
            "order_discounts": True,
            "product_discounts": product_discounts,
            "shipping_discounts": True,
        },
        "total_sales_usd": total_sales_usd,
    }


def test_no_findings_when_fewer_than_two_stackable_storewide_discounts():
    discounts = [_discount("A")]
    report = inspect_discounts(discounts)
    assert report.discounts_scanned == 1
    assert report.findings == []


def test_no_findings_when_only_one_discount_is_active():
    discounts = [_discount("A", status="ACTIVE"), _discount("B", status="EXPIRED")]
    report = inspect_discounts(discounts)
    assert report.findings == []


def test_no_findings_when_discounts_do_not_target_all_items():
    discounts = [
        _discount("A", targets_all_items=False),
        _discount("B", targets_all_items=False),
    ]
    report = inspect_discounts(discounts)
    assert report.findings == []


def test_no_findings_when_discounts_cannot_combine_with_product_discounts():
    discounts = [
        _discount("A", product_discounts=False),
        _discount("B", product_discounts=False),
    ]
    report = inspect_discounts(discounts)
    assert report.findings == []


def test_two_overlapping_stackable_discounts_produce_one_grouped_finding():
    discounts = [
        _discount("A", created_at="2026-08-01T00:00:00Z", total_sales_usd=10.0, code="MIDNIGHT50-A"),
        _discount("B", created_at="2026-08-01T00:05:00Z", total_sales_usd=5.0, code="MIDNIGHT50-B"),
    ]
    report = inspect_discounts(discounts)

    assert report.discounts_scanned == 2
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.severity == "HIGH"
    assert set(finding.affected_resources) == {"A", "B"}
    assert finding.evidence["check"] == "duplicate_stackable_discount"
    assert set(finding.evidence["discount_ids"]) == {"A", "B"}
    # A was created first -> canonical/keep; B is the duplicate to deactivate.
    assert finding.evidence["keep_discount_id"] == "A"
    assert finding.evidence["duplicate_discount_ids"] == ["B"]
    assert finding.evidence["total_sales_usd"] == 15.0
    # Real exposure duration needs the duplicate's own createdAt — the keeper
    # (A) is deliberately excluded, only duplicates (B) are ever deactivated
    # so only their exposure time is relevant.
    assert finding.evidence["duplicate_created_at"] == {"B": "2026-08-01T00:05:00Z"}
    assert finding.evidence["dedup_key"] == "discount:duplicate_stackable_discount:A,B"
    assert "MIDNIGHT50-A" in finding.description
    assert "MIDNIGHT50-B" in finding.description


def test_a_different_overlapping_set_produces_a_different_dedup_key():
    """Same class of bug fixed for Theme Guardian: if the overlapping
    discount set changes while an old grouped issue is still open, that must
    surface as a new issue, not be silently absorbed into the stale one."""
    first_set = [_discount("A"), _discount("B")]
    second_set = [_discount("A"), _discount("C")]

    first_key = inspect_discounts(first_set).findings[0].evidence["dedup_key"]
    second_key = inspect_discounts(second_set).findings[0].evidence["dedup_key"]
    assert first_key != second_key


def test_three_overlapping_discounts_are_grouped_into_a_single_finding_not_three():
    discounts = [
        _discount("A", created_at="2026-08-01T00:00:00Z"),
        _discount("B", created_at="2026-08-01T00:05:00Z"),
        _discount("C", created_at="2026-08-01T00:10:00Z"),
    ]
    report = inspect_discounts(discounts)

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.evidence["keep_discount_id"] == "A"
    assert finding.evidence["duplicate_discount_ids"] == ["B", "C"]
    assert finding.evidence["duplicate_created_at"] == {
        "B": "2026-08-01T00:05:00Z",
        "C": "2026-08-01T00:10:00Z",
    }


def test_demo_incident_generator_scenario_1_is_actually_caught_by_this_detector():
    """Cross-check tying Step 1 (Demo Incident Generator, Scenario 1:
    'Midnight Pricing Disaster') to Step 2 (this detector): simulates the
    two discounts Scenario 1 actually creates
    (`MIDNIGHT_PRICING_DISASTER_DISCOUNT_SPECS`) as live Shopify discount
    data and confirms they genuinely trip this general-purpose rule — not
    a check hardcoded to the demo's own titles/codes, but a real detector
    that happens to catch what the demo deliberately breaks."""
    discounts = [
        {
            "id": f"gid://shopify/DiscountCodeNode/{i}",
            "title": spec.title,
            "code": f"{spec.code_prefix}-ABC123",
            "status": "ACTIVE",
            "created_at": f"2026-08-01T00:0{i}:00Z",
            "targets_all_items": True,  # Scenario 1 creates customerGets.items.all=True
            # `spec.combines_with` uses Shopify's own camelCase field names
            # (it feeds directly into the create-discount GraphQL mutation
            # variables) — translated to the snake_case keys
            # `_normalize_discount_node` produces when reading discounts
            # back, since that's the shape this detector actually consumes.
            "combines_with": {
                "order_discounts": spec.combines_with.get("orderDiscounts", False),
                "product_discounts": spec.combines_with.get("productDiscounts", False),
                "shipping_discounts": spec.combines_with.get("shippingDiscounts", False),
            },
            "total_sales_usd": 0.0,
        }
        for i, spec in enumerate(MIDNIGHT_PRICING_DISASTER_DISCOUNT_SPECS)
    ]

    report = inspect_discounts(discounts)

    assert len(report.findings) == 1
    assert len(report.findings[0].affected_resources) == 2


def test_non_overlapping_active_discounts_are_unaffected():
    # Two ACTIVE discounts that DON'T both target all items with product
    # stacking enabled should never be flagged, even alongside a flagged pair.
    discounts = [
        _discount("A", created_at="2026-08-01T00:00:00Z"),
        _discount("B", created_at="2026-08-01T00:05:00Z"),
        _discount("C", targets_all_items=False),
    ]
    report = inspect_discounts(discounts)
    assert len(report.findings) == 1
    assert "C" not in report.findings[0].affected_resources
