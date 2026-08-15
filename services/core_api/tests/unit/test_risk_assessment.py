"""Unit tests: Risk Assessment (Sprint 3 AI Trust & Execution — Assess Risk
step).

Covers `assess_risk_level` (action_type -> RiskLevel mapping) and
`requires_approval` (the store-autonomy-based policy from
`app/domain/risk.py`, verbatim): LEVEL_1_SAFE auto-executes iff
`store.is_autonomous_execution_enabled`; LEVEL_2_MODERATE auto-executes iff
`is_autonomous_execution_enabled AND autonomy_level >= 2`; LEVEL_3_HIGH /
LEVEL_4_CRITICAL always require approval regardless of store settings.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.domain.enums import RiskLevel
from app.domain.models import Store
from app.domain.risk import assess_risk_level, requires_approval


def _store(*, is_autonomous_execution_enabled: bool, autonomy_level: int) -> Store:
    now = datetime.now(timezone.utc)
    return Store(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        shopify_domain="acme.myshopify.com",
        myshopify_domain="acme.myshopify.com",
        store_name="Acme",
        is_autonomous_execution_enabled=is_autonomous_execution_enabled,
        autonomy_level=autonomy_level,
        created_at=now,
        updated_at=now,
    )


def test_assess_risk_level_generate_alt_text_is_level_1_safe():
    level, reasoning = assess_risk_level("GENERATE_ALT_TEXT")
    assert level == RiskLevel.LEVEL_1_SAFE
    assert reasoning  # non-empty, human-readable


def test_assess_risk_level_rewrite_product_description_is_level_2_moderate():
    level, reasoning = assess_risk_level("REWRITE_PRODUCT_DESCRIPTION")
    assert level == RiskLevel.LEVEL_2_MODERATE
    assert reasoning


def test_assess_risk_level_deactivate_duplicate_discount_is_level_2_moderate():
    # Sprint 4 Step 2: Checkout Specialist's Duplicate Discount lifecycle.
    level, reasoning = assess_risk_level("DEACTIVATE_DUPLICATE_DISCOUNT")
    assert level == RiskLevel.LEVEL_2_MODERATE
    assert reasoning


def test_assess_risk_level_generate_theme_restore_guide_is_level_3_high():
    # Sprint 4 Step 3: Theme Guardian — always human-in-the-loop, and has no
    # automated execution path at all (no Shopify write exemption).
    level, reasoning = assess_risk_level("GENERATE_THEME_RESTORE_GUIDE")
    assert level == RiskLevel.LEVEL_3_HIGH
    assert reasoning


def test_assess_risk_level_recreate_tracking_script_tag_is_level_2_moderate():
    # Sprint 4 Step 3: Tracking Specialist — reversible but customer-facing.
    level, reasoning = assess_risk_level("RECREATE_TRACKING_SCRIPT_TAG")
    assert level == RiskLevel.LEVEL_2_MODERATE
    assert reasoning


def test_assess_risk_level_unknown_action_type_defaults_to_level_3_high():
    level, reasoning = assess_risk_level("SOME_UNKNOWN_ACTION_TYPE")
    assert level == RiskLevel.LEVEL_3_HIGH
    assert reasoning


# --- Sprint 5 Phase 5 bug fix: no cross-action-type text contamination ------
#
# Prior to this fix, every LEVEL_2_MODERATE action type returned the exact
# same paragraph naming all three LEVEL_2 action types at once, so a
# Checkout Specialist's Work Log entry could read "Product description
# rewrites are customer-facing..." (REWRITE_PRODUCT_DESCRIPTION's own
# sentence) and a Product Quality entry could read "Deactivating a
# duplicate discount code is reversible..." (DEACTIVATE_DUPLICATE_DISCOUNT's
# own sentence). Each assertion below checks the OTHER action types'
# distinguishing terms are absent from a given action type's own reasoning.


def test_reasoning_for_discount_deactivation_never_mentions_product_description_or_script_tags():
    _, reasoning = assess_risk_level("DEACTIVATE_DUPLICATE_DISCOUNT")
    assert "duplicate discount" in reasoning.lower()
    assert "description rewrite" not in reasoning.lower()
    assert "script tag" not in reasoning.lower()


def test_reasoning_for_product_description_rewrite_never_mentions_discounts_or_script_tags():
    _, reasoning = assess_risk_level("REWRITE_PRODUCT_DESCRIPTION")
    assert "description" in reasoning.lower()
    assert "discount code" not in reasoning.lower()
    assert "script tag" not in reasoning.lower()


def test_reasoning_for_tracking_script_tag_never_mentions_discounts_or_product_descriptions():
    _, reasoning = assess_risk_level("RECREATE_TRACKING_SCRIPT_TAG")
    assert "script tag" in reasoning.lower()
    assert "discount code" not in reasoning.lower()
    assert "description rewrite" not in reasoning.lower()


@pytest.mark.parametrize(
    "enabled, autonomy_level, expected_requires_approval",
    [
        (True, 1, False),
        (True, 2, False),
        (True, 3, False),
        (False, 1, True),
        (False, 2, True),
        (False, 3, True),
    ],
)
def test_requires_approval_level_1_safe_auto_iff_autonomous_execution_enabled(
    enabled, autonomy_level, expected_requires_approval
):
    store = _store(is_autonomous_execution_enabled=enabled, autonomy_level=autonomy_level)
    assert requires_approval(RiskLevel.LEVEL_1_SAFE, store) is expected_requires_approval


@pytest.mark.parametrize(
    "enabled, autonomy_level, expected_requires_approval",
    [
        (True, 1, True),  # enabled but autonomy_level < 2 -> still requires approval
        (True, 2, False),  # enabled AND autonomy_level >= 2 -> auto-executes
        (True, 3, False),
        (False, 1, True),
        (False, 2, True),  # autonomy_level >= 2 alone is not sufficient
        (False, 3, True),
    ],
)
def test_requires_approval_level_2_moderate_auto_iff_enabled_and_autonomy_at_least_2(
    enabled, autonomy_level, expected_requires_approval
):
    store = _store(is_autonomous_execution_enabled=enabled, autonomy_level=autonomy_level)
    assert requires_approval(RiskLevel.LEVEL_2_MODERATE, store) is expected_requires_approval


@pytest.mark.parametrize("risk_level", [RiskLevel.LEVEL_3_HIGH, RiskLevel.LEVEL_4_CRITICAL])
@pytest.mark.parametrize(
    "enabled, autonomy_level",
    [
        (True, 1),
        (True, 2),
        (True, 3),
        (False, 1),
        (False, 2),
        (False, 3),
    ],
)
def test_requires_approval_level_3_and_4_always_require_approval_regardless_of_store_settings(
    risk_level, enabled, autonomy_level
):
    store = _store(is_autonomous_execution_enabled=enabled, autonomy_level=autonomy_level)
    assert requires_approval(risk_level, store) is True
