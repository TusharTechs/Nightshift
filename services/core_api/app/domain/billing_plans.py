"""Static plan catalog for NightShift's pricing tiers.

Pure data, no Shopify call, no DB read — `GET /api/v1/billing/plans` returns
this verbatim. Prices/features are the product owner's own decision, not
renegotiated here.
"""

from __future__ import annotations

from app.domain.enums import PlanTier

PLAN_CATALOG: list[dict] = [
    {
        "plan": PlanTier.FREE.value,
        "display_name": "NightShift Free",
        "monthly_price_usd": 0.0,
        "features": [
            "1 store",
            "Limited monitoring — manual/on-demand shifts only (no continuous nightly automation)",
            "Limited AI employees",
        ],
    },
    {
        "plan": PlanTier.PRO.value,
        "display_name": "NightShift Pro",
        "monthly_price_usd": 29.0,
        "features": [
            "Continuous nightly monitoring",
            "All AI employees",
            "Automatic fixes",
            "Approval center",
            "Shift reports",
            "Revenue protection",
        ],
    },
    {
        # Stretch/optional tier. The enum value (`PlanTier.BUSINESS`) exists
        # and this row is billed at its own real price via a real Shopify
        # AppSubscription, but it has NO enforced behavioral difference from
        # PRO anywhere in this codebase yet — e.g.
        # `app/application/use_cases/select_nightly_dispatch_stores.py`
        # treats PRO and BUSINESS identically for the nightly-dispatch gate.
        # Do not build BUSINESS-specific differentiators without a
        # corresponding product decision — listed here only because pricing
        # for it has already been fixed.
        "plan": PlanTier.BUSINESS.value,
        "display_name": "NightShift Business",
        "monthly_price_usd": 79.0,
        "features": [
            "Multiple stores (not yet enforced — not yet differentiated from Pro)",
            "Advanced monitoring (not yet differentiated from Pro)",
            "Higher AI limits (not yet enforced — not yet differentiated from Pro)",
            "Priority support",
        ],
    },
]
