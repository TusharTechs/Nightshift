"""Confidence Engine — Sprint 3.

Computes a weighted multi-signal confidence score for a proposed cognitive
task, plus a human-readable signal breakdown for the Approval Center UI and
Work Log. All signals are grounded in real, queryable data — no fabricated
cross-store statistics (this codebase's established "never invent data"
principle, see domain/inspection.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConfidenceSignal:
    name: str
    score: float  # 0.0-1.0
    weight: float
    reasoning: str


@dataclass(frozen=True)
class ConfidenceAssessment:
    overall_score: float
    signals: list[ConfidenceSignal] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "overall_score": round(self.overall_score, 3),
            "signals": [
                {
                    "name": s.name,
                    "score": round(s.score, 3),
                    "weight": s.weight,
                    "reasoning": s.reasoning,
                }
                for s in self.signals
            ],
        }


def compute_confidence(
    *,
    issue_confidence_score: float,
    merchant_approval_rate: float | None,
    historical_success_rate: float | None,
    verification_method_is_deterministic: bool = True,
    merchant_approval_count: int | None = None,
) -> ConfidenceAssessment:
    """
    - issue_confidence_score: the detecting agent's own confidence (0-1),
      already computed by the Product Quality Agent / deterministic fallback.
    - merchant_approval_rate: fraction of this merchant's past decisions on
      this action_type that were APPROVED (None if no history yet -> use a
      neutral 0.75 prior rather than fabricating false confidence).
    - historical_success_rate: fraction of past executions of this
      action_type at this store that passed verification (None if no
      history -> neutral 0.85 prior, since Level-1/2 actions are simple
      metadata writes with inherently low failure risk).
    - verification_method_is_deterministic: True for read-after-write checks
      (always true this sprint — no synthetic/fuzzy verification exists yet).
    - merchant_approval_count: Sprint 5 Phase 5 (Root Cause/Merchant Memory
      pass): the raw count of this merchant's past APPROVED decisions on this
      action_type, passed in only when `merchant_approval_rate` is also real
      (not the neutral prior). Purely enriches this signal's `reasoning`
      sentence with the real count the caller already computed (see
      `plan_cognitive_tasks.py`) — never changes the score itself. This is
      what lets the UI surface a grounded "🧠 Merchant Preference Applied:
      based on your N previous approvals..." note without fabricating a
      number nowhere else in this assessment.
    """
    if merchant_approval_rate is not None and merchant_approval_count is not None:
        acceptance_reasoning = (
            f"Based on {merchant_approval_count} previous approval"
            f"{'s' if merchant_approval_count != 1 else ''} for this action type, this merchant has "
            f"approved {merchant_approval_rate:.0%} of similar past actions."
        )
    elif merchant_approval_rate is not None:
        acceptance_reasoning = f"This merchant has approved {merchant_approval_rate:.0%} of similar past actions."
    else:
        acceptance_reasoning = "No prior approval history for this action type at this store; using a neutral prior."

    signals = [
        ConfidenceSignal(
            name="detection_confidence",
            score=issue_confidence_score,
            weight=0.35,
            reasoning=f"Detecting agent reported {issue_confidence_score:.0%} confidence in this issue.",
        ),
        ConfidenceSignal(
            name="merchant_acceptance_history",
            score=merchant_approval_rate if merchant_approval_rate is not None else 0.75,
            weight=0.30,
            reasoning=acceptance_reasoning,
        ),
        ConfidenceSignal(
            name="execution_success_history",
            score=historical_success_rate if historical_success_rate is not None else 0.85,
            weight=0.20,
            reasoning=(
                f"{historical_success_rate:.0%} of past executions of this action type passed verification."
                if historical_success_rate is not None
                else "No prior execution history for this action type at this store; using a neutral prior."
            ),
        ),
        ConfidenceSignal(
            name="verification_rigor",
            score=1.0 if verification_method_is_deterministic else 0.6,
            weight=0.15,
            reasoning=(
                "Verified via deterministic read-after-write comparison against Shopify."
                if verification_method_is_deterministic
                else "Verification method is not fully deterministic."
            ),
        ),
    ]
    total_weight = sum(s.weight for s in signals)
    overall = sum(s.score * s.weight for s in signals) / total_weight
    return ConfidenceAssessment(overall_score=overall, signals=signals)


def merchant_memory_note(assessment: dict) -> str | None:
    """Sprint 5 Phase 5: extracts a user-facing "🧠 Merchant Preference
    Applied" note from an already-computed `ConfidenceAssessment.to_dict()`
    blob, if and only if the `merchant_acceptance_history` signal reflects
    genuine prior history rather than the neutral no-history prior. Returns
    that signal's own `reasoning` sentence verbatim — this function never
    composes a new phrase, it only decides whether the existing, already-
    grounded sentence is worth surfacing as a standalone UI callout. Shared
    by the live Approval Center endpoint (`api/v1/approvals.py`) and the
    persisted Morning Shift Report's Chief Ops turns
    (`workers/tasks/shift_report.py`) so both surfaces make the same call
    from the same data, never two independently-drifting text rules."""
    for signal in assessment.get("signals", []):
        if signal.get("name") != "merchant_acceptance_history":
            continue
        reasoning = signal.get("reasoning", "")
        if reasoning and not reasoning.startswith("No prior approval history"):
            return reasoning
        return None
    return None
