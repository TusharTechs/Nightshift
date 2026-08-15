"""Unit tests: Confidence Engine (Sprint 3 AI Trust & Execution).

`compute_confidence` builds a weighted, multi-signal `ConfidenceAssessment`
from `app/domain/confidence.py`. Signal weights (verbatim from the module):
detection_confidence=0.35, merchant_acceptance_history=0.30,
execution_success_history=0.20, verification_rigor=0.15 — summing to 1.0
(the module divides by `total_weight` rather than assuming this, but the
four weights as written already sum to exactly 1.0).

Neutral-prior fallbacks when history is absent (verbatim from the module):
merchant_approval_rate=None -> 0.75; historical_success_rate=None -> 0.85.
"""

from __future__ import annotations

import pytest

from app.domain.confidence import compute_confidence, merchant_memory_note

EXPECTED_SIGNAL_NAMES = {
    "detection_confidence",
    "merchant_acceptance_history",
    "execution_success_history",
    "verification_rigor",
}

EXPECTED_WEIGHTS = {
    "detection_confidence": 0.35,
    "merchant_acceptance_history": 0.30,
    "execution_success_history": 0.20,
    "verification_rigor": 0.15,
}


def test_compute_confidence_with_all_signals_present_returns_score_in_unit_interval():
    assessment = compute_confidence(
        issue_confidence_score=0.9,
        merchant_approval_rate=0.8,
        historical_success_rate=0.95,
    )
    assert 0.0 <= assessment.overall_score <= 1.0

    as_dict = assessment.to_dict()
    signal_names = {s["name"] for s in as_dict["signals"]}
    assert signal_names == EXPECTED_SIGNAL_NAMES

    for signal in as_dict["signals"]:
        assert signal["weight"] == EXPECTED_WEIGHTS[signal["name"]]
        assert signal["reasoning"]  # populated, non-empty
        assert 0.0 <= signal["score"] <= 1.0


def test_compute_confidence_signal_weights_sum_to_one():
    assessment = compute_confidence(
        issue_confidence_score=0.9,
        merchant_approval_rate=0.8,
        historical_success_rate=0.95,
    )
    total_weight = sum(s.weight for s in assessment.signals)
    assert total_weight == pytest.approx(1.0)


def test_compute_confidence_reflects_reported_signal_scores():
    assessment = compute_confidence(
        issue_confidence_score=0.9,
        merchant_approval_rate=0.8,
        historical_success_rate=0.95,
    )
    by_name = {s.name: s for s in assessment.signals}
    assert by_name["detection_confidence"].score == 0.9
    assert by_name["merchant_acceptance_history"].score == 0.8
    assert by_name["execution_success_history"].score == 0.95
    assert by_name["merchant_acceptance_history"].reasoning == "This merchant has approved 80% of similar past actions."


def test_compute_confidence_uses_neutral_priors_when_no_prior_history():
    assessment = compute_confidence(
        issue_confidence_score=0.9,
        merchant_approval_rate=None,
        historical_success_rate=None,
    )
    by_name = {s.name: s for s in assessment.signals}

    merchant_signal = by_name["merchant_acceptance_history"]
    assert merchant_signal.score == 0.75
    assert "no prior" in merchant_signal.reasoning.lower()
    assert "approval history" in merchant_signal.reasoning.lower()

    execution_signal = by_name["execution_success_history"]
    assert execution_signal.score == 0.85
    assert "no prior" in execution_signal.reasoning.lower()
    assert "execution history" in execution_signal.reasoning.lower()


def test_compute_confidence_verification_rigor_signal_is_deterministic_by_default():
    assessment = compute_confidence(
        issue_confidence_score=0.5,
        merchant_approval_rate=None,
        historical_success_rate=None,
    )
    by_name = {s.name: s for s in assessment.signals}
    assert by_name["verification_rigor"].score == 1.0
    assert "deterministic" in by_name["verification_rigor"].reasoning.lower()


def test_compute_confidence_verification_rigor_signal_when_not_deterministic():
    assessment = compute_confidence(
        issue_confidence_score=0.5,
        merchant_approval_rate=None,
        historical_success_rate=None,
        verification_method_is_deterministic=False,
    )
    by_name = {s.name: s for s in assessment.signals}
    assert by_name["verification_rigor"].score == 0.6


# --- Sprint 5 Phase 5: Merchant Memory surfaced in the UI -------------------


def test_compute_confidence_enriches_reasoning_with_real_approval_count():
    assessment = compute_confidence(
        issue_confidence_score=0.9,
        merchant_approval_rate=0.8,
        historical_success_rate=0.95,
        merchant_approval_count=2,
    )
    by_name = {s.name: s for s in assessment.signals}
    reasoning = by_name["merchant_acceptance_history"].reasoning
    assert reasoning == (
        "Based on 2 previous approvals for this action type, this merchant has "
        "approved 80% of similar past actions."
    )
    # The score itself is unaffected by the enrichment — only the sentence.
    assert by_name["merchant_acceptance_history"].score == 0.8


def test_compute_confidence_singular_approval_count_wording():
    assessment = compute_confidence(
        issue_confidence_score=0.9,
        merchant_approval_rate=1.0,
        historical_success_rate=None,
        merchant_approval_count=1,
    )
    by_name = {s.name: s for s in assessment.signals}
    assert "1 previous approval for this action type" in by_name["merchant_acceptance_history"].reasoning
    assert "1 previous approvals" not in by_name["merchant_acceptance_history"].reasoning


def test_compute_confidence_ignores_approval_count_when_rate_is_neutral_prior():
    # merchant_approval_count without a real rate never happens from the one
    # real call site (plan_cognitive_tasks.py only passes both or neither),
    # but the function itself must still degrade safely rather than lying
    # about a count with no rate behind it.
    assessment = compute_confidence(
        issue_confidence_score=0.9,
        merchant_approval_rate=None,
        historical_success_rate=None,
        merchant_approval_count=5,
    )
    by_name = {s.name: s for s in assessment.signals}
    assert "no prior" in by_name["merchant_acceptance_history"].reasoning.lower()


def test_merchant_memory_note_returns_none_when_no_real_history():
    assessment = compute_confidence(
        issue_confidence_score=0.9,
        merchant_approval_rate=None,
        historical_success_rate=None,
    ).to_dict()
    assert merchant_memory_note(assessment) is None


def test_merchant_memory_note_returns_the_signal_reasoning_verbatim_when_history_is_real():
    assessment = compute_confidence(
        issue_confidence_score=0.9,
        merchant_approval_rate=0.8,
        historical_success_rate=None,
        merchant_approval_count=2,
    ).to_dict()
    expected = next(
        s["reasoning"] for s in assessment["signals"] if s["name"] == "merchant_acceptance_history"
    )
    assert merchant_memory_note(assessment) == expected
    assert "2 previous approvals" in merchant_memory_note(assessment)


def test_merchant_memory_note_handles_assessment_with_no_signals_key():
    assert merchant_memory_note({}) is None
