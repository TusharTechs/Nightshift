"""Discount Inspection Engine — Sprint 4 Step 2, Checkout Specialist's
Observe step.

Pure domain logic — no framework or Shopify-client imports, mirroring
`domain/inspection.py`'s own contract exactly. Takes a normalized list of
currently-active discount dicts (as produced by
`ShopifyGraphQLClient.fetch_discount_codes_for_inspection`) and
deterministically identifies overlapping, mutually-stackable storewide
discounts — the "duplicate/stackable discount" incident this specialist
exists to catch (Demo Incident Generator Scenario 1: "Midnight Pricing
Disaster").

Detection rule (intentionally simple and fully deterministic — no LLM
judgment call involved, unlike the Product Quality Agent's analysis):
among all currently ACTIVE, storewide (`targets_all_items=True`) discounts
that can combine with other product discounts (`combines_with.product_discounts
=True`), if 2 or more exist simultaneously, that is always a real pricing
hazard — any two such discounts can legitimately stack at checkout,
compounding beyond what either was individually designed to give away. This
mirrors exactly what Scenario 1 constructs (two storewide 50%-off codes,
both `combinesWith` enabled on every axis), but is a genuine general-purpose
rule, not a check hardcoded to the demo's own discount codes/titles.

Revenue impact is never invented here: `total_sales_usd` (summed across the
flagged discounts) is Shopify's own reported `totalSales` figure — real,
already-realized sales attributed to these codes, not a projected or
fabricated number (AI Specification Safety Rule, carried forward from
Sprint 2's Product Inspection Engine docstring).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DiscountFinding:
    title: str
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    description: str
    affected_resources: list[str]  # discount_ids of every discount in the overlapping group
    evidence: dict


@dataclass(frozen=True)
class DiscountInspectionReport:
    discounts_scanned: int
    findings: list[DiscountFinding] = field(default_factory=list)


def inspect_discounts(discounts: list[dict]) -> DiscountInspectionReport:
    """`discounts` is the normalized shape `fetch_discount_codes_for_inspection`
    returns: `{id, title, code, status, created_at, targets_all_items,
    combines_with: {order_discounts, product_discounts, shipping_discounts},
    total_sales_usd}`.

    Groups all currently-active, storewide, product-combinable discounts
    into a single finding (not one finding per pair) — from a merchant's
    perspective, 3 overlapping storewide discounts is one incident to fix,
    not three separate ones.
    """
    stackable_storewide = [
        d
        for d in discounts
        if d.get("status") == "ACTIVE"
        and d.get("targets_all_items") is True
        and (d.get("combines_with") or {}).get("product_discounts") is True
    ]

    if len(stackable_storewide) < 2:
        return DiscountInspectionReport(discounts_scanned=len(discounts), findings=[])

    # Oldest (by created_at) is treated as the canonical/original discount;
    # everything else in the group is the "duplicate" — this only affects
    # which discount the Plan step recommends keeping, not detection itself.
    ordered = sorted(stackable_storewide, key=lambda d: d.get("created_at") or "")
    keep = ordered[0]
    duplicates = ordered[1:]

    discount_ids = [d["id"] for d in ordered]
    duplicate_codes = [d.get("code") or d.get("title") or d["id"] for d in duplicates]
    total_sales_usd = sum(float(d.get("total_sales_usd") or 0.0) for d in ordered)
    # Real exposure duration (e.g. "live for 6.5 hours before NightShift
    # deactivated it") needs the duplicate's own createdAt — carried through
    # to the Issue's evidence_data and from there to the Task Detail API, so
    # the frontend can compute elapsed time against the execution's own
    # completed_at/verified_at rather than this module inventing a duration
    # itself (which would go stale the instant it's computed).
    duplicate_created_at = {d["id"]: d.get("created_at") for d in duplicates}

    finding = DiscountFinding(
        title=f"{len(ordered)} overlapping, mutually-stackable storewide discounts active",
        severity="HIGH",
        description=(
            f"{len(ordered)} discount codes are simultaneously active, each applies to every "
            "item in the cart, and each can combine with other product discounts — meaning a "
            "customer can stack more than one at checkout, compounding the total discount well "
            f"beyond what any single code was designed to give. Codes involved: "
            f"{', '.join(str(d.get('code') or d.get('title') or d['id']) for d in ordered)}."
        ),
        affected_resources=discount_ids,
        evidence={
            "check": "duplicate_stackable_discount",
            "discount_ids": discount_ids,
            "keep_discount_id": keep["id"],
            "duplicate_discount_ids": [d["id"] for d in duplicates],
            "duplicate_created_at": duplicate_created_at,
            "total_sales_usd": round(total_sales_usd, 2),
            # Sprint 4: content-aware — includes the sorted set of discount
            # ids, not just a fixed singleton string. Re-observing the exact
            # same overlapping set on a later shift reuses the existing open
            # issue/approval (no duplicate), but if the set changes (a new
            # discount joins the overlap, or one drops out) while the old
            # issue is still open, that's a materially different situation
            # and correctly surfaces as a new one — same class of "silently
            # swallowed real change" bug found and fixed for Theme Guardian.
            "dedup_key": "discount:duplicate_stackable_discount:" + ",".join(sorted(discount_ids)),
        },
    )
    return DiscountInspectionReport(discounts_scanned=len(discounts), findings=[finding])
