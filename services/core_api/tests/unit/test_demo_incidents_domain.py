"""Unit tests for the Demo Incident Generator's pure domain layer
(`app/domain/demo_incidents.py`) — Sprint 4 Step 1.

No I/O at all: just the scenario registry and the Scenario 1 discount specs.
"""

from __future__ import annotations

from app.domain.demo_incidents import (
    CATALOG_SEO_COLLAPSE,
    DEMO_SCENARIOS,
    MIDNIGHT_PRICING_DISASTER,
    MIDNIGHT_PRICING_DISASTER_DISCOUNT_SPECS,
    ROGUE_DEVELOPER_THEME_BREAK,
    WIRED_SCENARIO_IDS,
    get_scenario,
)


def test_all_three_named_judge_scenarios_are_registered():
    assert set(DEMO_SCENARIOS.keys()) == {
        "midnight_pricing_disaster",
        "rogue_developer_theme_break",
        "catalog_seo_collapse",
    }
    assert DEMO_SCENARIOS["midnight_pricing_disaster"] is MIDNIGHT_PRICING_DISASTER
    assert DEMO_SCENARIOS["rogue_developer_theme_break"] is ROGUE_DEVELOPER_THEME_BREAK
    assert DEMO_SCENARIOS["catalog_seo_collapse"] is CATALOG_SEO_COLLAPSE


def test_all_three_scenarios_are_wired_as_of_sprint_5_phase_4():
    # Scenario 2 is wired as of Step 3, though only its tracking half
    # actually executes — see ROGUE_DEVELOPER_THEME_BREAK's own description.
    # Scenario 3 is wired as of Sprint 5 Phase 4 (Chaos Panel).
    assert WIRED_SCENARIO_IDS == frozenset(
        {"midnight_pricing_disaster", "rogue_developer_theme_break", "catalog_seo_collapse"}
    )


def test_get_scenario_returns_none_for_unknown_id():
    assert get_scenario("not_a_real_scenario") is None
    assert get_scenario("midnight_pricing_disaster") is MIDNIGHT_PRICING_DISASTER


def test_midnight_pricing_disaster_creates_two_overlapping_stackable_discounts():
    specs = MIDNIGHT_PRICING_DISASTER_DISCOUNT_SPECS
    assert len(specs) == 2

    codes_prefixes = {spec.code_prefix for spec in specs}
    assert len(codes_prefixes) == 2  # distinct codes, so they can coexist as two real discounts

    for spec in specs:
        assert spec.percentage == 0.5
        # "Stackable" per the scenario's own name: must combine with every
        # discount class, not just be created twice and left non-combining.
        assert spec.combines_with == {
            "orderDiscounts": True,
            "productDiscounts": True,
            "shippingDiscounts": True,
        }
