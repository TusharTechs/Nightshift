"""Unit tests: AI Explanation Engine (Sprint 3 AI Trust & Execution).

`build_explanation` is deterministic/template-based (no LLM call) — see
`app/domain/explanation.py`. `.to_dict()` must expose all 5 structured
fields plus the derived `narrative` string (6 keys total).
"""

from __future__ import annotations

from app.domain.explanation import build_explanation

EXPECTED_KEYS = {"what_happened", "why", "business_impact", "what_changed", "how_verified", "narrative"}


def test_build_explanation_passed_verification_includes_formatted_dollar_amount():
    bundle = build_explanation(
        issue_title="Missing ALT text on 'Widget'",
        issue_description="Widget's product image has no ALT text.",
        action_type="GENERATE_ALT_TEXT",
        revenue_impact_estimate=312.0,
        verification_passed=True,
        verification_method="READ_AFTER_WRITE",
        before_value=None,
        after_value="Widget product photo",
    )
    as_dict = bundle.to_dict()

    assert set(as_dict.keys()) == EXPECTED_KEYS
    assert as_dict["business_impact"] == "Estimated revenue protected: $312.00."
    assert "confirmed" in as_dict["how_verified"].lower()
    assert "Widget product photo" in as_dict["what_changed"]
    assert as_dict["narrative"] == bundle.narrative()
    # Narrative concatenates all the structured fields in order.
    assert as_dict["narrative"].startswith(as_dict["what_happened"])
    assert as_dict["narrative"].endswith(as_dict["business_impact"])


def test_build_explanation_failed_verification_reports_no_revenue_impact_and_rollback():
    bundle = build_explanation(
        issue_title="Missing ALT text on 'Widget'",
        issue_description="Widget's product image has no ALT text.",
        action_type="GENERATE_ALT_TEXT",
        revenue_impact_estimate=312.0,
        verification_passed=False,
        verification_method="READ_AFTER_WRITE",
        before_value=None,
        after_value="Widget product photo",
    )
    as_dict = bundle.to_dict()

    assert set(as_dict.keys()) == EXPECTED_KEYS
    assert "no revenue impact" in as_dict["business_impact"].lower()
    assert "reverted" in as_dict["business_impact"].lower()
    assert "rolled back" in as_dict["how_verified"].lower()
    assert "failed" in as_dict["how_verified"].lower()


def test_build_explanation_rewrite_product_description_what_changed_text():
    bundle = build_explanation(
        issue_title="Thin description on 'Widget'",
        issue_description="Widget's description is only 12 words.",
        action_type="REWRITE_PRODUCT_DESCRIPTION",
        revenue_impact_estimate=45.5,
        verification_passed=True,
        verification_method="READ_AFTER_WRITE",
        before_value=None,
        after_value="<p>...</p>",
    )
    assert "rewrote the product description" in bundle.what_changed.lower()


def test_build_explanation_unknown_action_type_generic_what_changed_text():
    bundle = build_explanation(
        issue_title="Some issue",
        issue_description="Some description",
        action_type="SOME_OTHER_ACTION",
        revenue_impact_estimate=0.0,
        verification_passed=True,
        verification_method="READ_AFTER_WRITE",
        before_value=None,
        after_value=None,
    )
    assert "SOME_OTHER_ACTION" in bundle.what_changed
