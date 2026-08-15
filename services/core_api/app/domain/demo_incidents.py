"""Demo Incident Generator — domain-level scenario registry.

Sprint 4 Step 1 (see `SPRINT4_AI_WORKFORCE_VISION.md`, "Two pieces of shared
infrastructure" / "Three locked additions #3, Named Judge Scenarios"): a
"break this on command" trigger that deliberately re-corrupts store data so
a live demo never has to wait for something to organically go wrong. This
module holds only the pure, framework-free scenario definitions — no
Shopify/HTTP/DB imports — mirroring how `risk.py`/`confidence.py` stay
framework-agnostic. `app/application/use_cases/trigger_demo_incident.py` is
the layer that actually calls out to Shopify.

Only Scenario 1 ("Midnight Pricing Disaster") is wired this step, per the
user-approved locked order of execution. Scenarios 2 ("Rogue Developer
Theme Break") and 3 ("Catalog SEO Collapse") are named here as placeholders
so the registry's shape doesn't change again in Step 3, but
`TriggerDemoIncident` raises `UnknownDemoScenarioProblem` for `demo_config
is None` (not yet wired) rather than silently no-op'ing — a judge-facing
demo trigger failing loud beats it failing invisible.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DemoScenario:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class DemoDiscountSpec:
    """One discount code to create as part of a demo scenario. `code_prefix`
    is combined with a short random suffix at trigger time (see
    `TriggerDemoIncident`) so re-triggering the same demo never collides
    with a still-active code from a previous run — Shopify discount codes
    must be unique per store."""

    title: str
    code_prefix: str
    percentage: float
    combines_with: dict[str, bool] = field(default_factory=dict)


MIDNIGHT_PRICING_DISASTER = DemoScenario(
    id="midnight_pricing_disaster",
    name="Midnight Pricing Disaster",
    description=(
        "Creates two overlapping, mutually-stackable storewide 50%-off "
        "discount codes — the exact 'duplicate/stackable discount' incident "
        "the Checkout Specialist (Step 2) is built to detect and deactivate."
    ),
)

ROGUE_DEVELOPER_THEME_BREAK = DemoScenario(
    id="rogue_developer_theme_break",
    name="Rogue Developer Theme Break",
    description=(
        "Deletes a live Meta Pixel-pattern script tag on cue — the "
        "'tracking half' of the incident, fully wired: Tracking Specialist "
        "genuinely detects the removal and proposes recreating it "
        "(approval-gated). The 'theme half' (removing the Buy Button block "
        "from the live theme file) is NOT auto-triggered by this scenario: "
        "corrupting a theme file requires the exact same Shopify-granted "
        "write exemption Theme Guardian's own restore step lacks (see "
        "CONFLICTS.md) — NightShift has no write path to theme files at "
        "all, for injection OR repair. Demoing Theme Guardian's "
        "detect/explain/guided-restore flow requires a human to edit "
        "`sections/main-product.liquid` once in the Shopify Theme Editor "
        "before the demo; the next scan then detects and explains it for "
        "real."
    ),
)

CATALOG_SEO_COLLAPSE = DemoScenario(
    id="catalog_seo_collapse",
    name="Catalog SEO Collapse",
    description=(
        "Strips the description and image ALT text from a flagship product "
        "(the catalog's first active product), reusing the exact same "
        "Product Quality checks (`missing_alt_text`, `thin_description`) "
        "the next inspection shift already runs — no new detection logic. "
        "Wired as of Sprint 5 Phase 4 (the Demo Incident Control Panel's "
        "third scenario)."
    ),
)

DEMO_SCENARIOS: dict[str, DemoScenario] = {
    scenario.id: scenario
    for scenario in (MIDNIGHT_PRICING_DISASTER, ROGUE_DEVELOPER_THEME_BREAK, CATALOG_SEO_COLLAPSE)
}

# Scenarios with an actual trigger implementation wired up in
# `TriggerDemoIncident` — a subset of `DEMO_SCENARIOS`, checked separately so
# a scenario can be *named* (for roadmap/UI purposes) before it is *wired*.
# `ROGUE_DEVELOPER_THEME_BREAK` is wired as of Step 3, but only its tracking
# half actually executes — see its own description above.
# `CATALOG_SEO_COLLAPSE` is wired as of Sprint 5 Phase 4.
WIRED_SCENARIO_IDS: frozenset[str] = frozenset(
    {MIDNIGHT_PRICING_DISASTER.id, ROGUE_DEVELOPER_THEME_BREAK.id, CATALOG_SEO_COLLAPSE.id}
)

DEMO_META_PIXEL_SCRIPT_TAG_SRC = "https://connect.facebook.net/en_US/fbevents.js?nsdemo=1"
"""Src used by the Rogue Developer Theme Break trigger's tracking half — the
`connect.facebook.net` substring deliberately matches
`script_tag_inspection.py::KNOWN_TRACKING_PATTERNS`'s Meta Pixel pattern, so
Tracking Specialist's finding reads "Meta Pixel script tag removed," not a
generic/unlabeled one. The `?nsdemo=1` query param makes it obvious in a
merchant's Shopify admin that this was NightShift's own demo script tag, not
a real integration, if a demo is ever run against a store with genuine
tracking already installed."""

MIDNIGHT_PRICING_DISASTER_DISCOUNT_SPECS: tuple[DemoDiscountSpec, ...] = (
    DemoDiscountSpec(
        title="NightShift Demo — Midnight 50 (A)",
        code_prefix="NSDEMO-MIDNIGHT50-A",
        percentage=0.5,
        combines_with={"orderDiscounts": True, "productDiscounts": True, "shippingDiscounts": True},
    ),
    DemoDiscountSpec(
        title="NightShift Demo — Midnight 50 (B)",
        code_prefix="NSDEMO-MIDNIGHT50-B",
        percentage=0.5,
        combines_with={"orderDiscounts": True, "productDiscounts": True, "shippingDiscounts": True},
    ),
)


def get_scenario(scenario_id: str) -> DemoScenario | None:
    return DEMO_SCENARIOS.get(scenario_id)
