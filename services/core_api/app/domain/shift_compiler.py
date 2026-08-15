"""Morning Shift Report Compiler — Sprint 2 Feature 4.

Pure domain logic — no DB/HTTP imports. Aggregates a completed shift's
issues and health-score result into the structured payload persisted to
`shift_reports.report_json` and returned verbatim by
`GET /api/v1/shifts/latest`. The Celery task
(`workers/tasks/shift_report.py`) and the API route
(`app/api/v1/shifts.py`) both build on this module's output so the two
surfaces can never drift from each other.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

from app.domain.health import HealthScoreResult

TIME_SAVED_HOURS_PER_ISSUE = 0.25
"""Deterministic per-issue time-saved heuristic (15 minutes of manual audit
time assumed saved per issue the AI Employee found and explained instead of
a human finding it manually). No document in the source set defines a
formula for `estimated_time_saved_hours` — the PRD names it as a metric
without specifying how to compute it. This is a documented placeholder
assumption, not fabricated store data, and is called out as a Known
Limitation pending a product-defined formula."""


@dataclass(frozen=True)
class CompiledIssue:
    id: str
    category: str
    severity: str
    status: str
    title: str
    description: str
    revenue_impact_estimate: float
    confidence_score: float
    affected_resources: list[str]


@dataclass(frozen=True)
class ShiftReportPayload:
    shift_id: str
    shift_number: int
    status: str
    started_at: datetime
    completed_at: datetime | None
    health_score: int
    executive_summary: str
    issues_detected: int
    issues_resolved: int
    estimated_revenue_protected: float
    estimated_time_saved_hours: float
    issues: list[CompiledIssue] = field(default_factory=list)
    health_category_deductions: dict[str, int] = field(default_factory=dict)
    # Sprint 3 AI Trust & Execution: the richer task/approval-aware fields
    # deferred by Sprint 2 (CONFLICTS.md item 17) — now buildable, since the
    # CognitiveTask/Approval domain exists. Default to empty lists so every
    # Sprint 2 caller/test that doesn't pass them still works unchanged.
    pending_approvals: list[dict] = field(default_factory=list)
    completed_tasks: list[dict] = field(default_factory=list)
    # Sprint 4 Step 4: Chief Ops AI's "Multi-Agent Handshake" synthesis
    # (see `domain/chief_ops.py`) — {turns: [...], narrative, correlated,
    # used_llm}. None for shifts compiled before Step 4 shipped, or in any
    # test that doesn't pass one; the Executive Briefing surface treats
    # missing/None the same as an empty-turns briefing.
    chief_ops_briefing: dict | None = None

    def to_api_response(self) -> dict:
        """Shape matches Sprint 2's own `GET /api/v1/shifts/latest` contract,
        extended with Sprint 3's `pending_approvals[]`/`completed_tasks[]`
        and Sprint 4 Step 4's `chief_ops_briefing`
        (API Contract Specification field names, verbatim where named)."""
        return {
            "shift_id": self.shift_id,
            "shift_number": self.shift_number,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "health_score": self.health_score,
            "executive_summary": self.executive_summary,
            "metrics": {
                "issues_detected": self.issues_detected,
                "issues_resolved": self.issues_resolved,
                "estimated_revenue_protected_usd": round(self.estimated_revenue_protected, 2),
                "time_saved_hours": self.estimated_time_saved_hours,
            },
            "issues": [
                asdict(issue) for issue in sorted(
                    self.issues, key=lambda i: i.revenue_impact_estimate, reverse=True
                )
            ],
            "health_category_deductions": self.health_category_deductions,
            "pending_approvals": self.pending_approvals,
            "completed_tasks": self.completed_tasks,
            "chief_ops_briefing": self.chief_ops_briefing,
        }


def compile_shift_report(
    *,
    shift_id: str,
    shift_number: int,
    started_at: datetime,
    completed_at: datetime | None,
    issues: list[CompiledIssue],
    health_result: HealthScoreResult,
    executive_summary: str | None = None,
    issues_resolved: int = 0,
    pending_approvals: list[dict] | None = None,
    completed_tasks: list[dict] | None = None,
    chief_ops_briefing: dict | None = None,
) -> ShiftReportPayload:
    revenue_protected = sum(issue.revenue_impact_estimate for issue in issues)
    time_saved = round(len(issues) * TIME_SAVED_HOURS_PER_ISSUE, 2)
    summary = executive_summary or _default_executive_summary(issues, revenue_protected)

    return ShiftReportPayload(
        shift_id=shift_id,
        shift_number=shift_number,
        status="COMPLETED",
        started_at=started_at,
        completed_at=completed_at,
        health_score=health_result.score,
        executive_summary=summary,
        issues_detected=len(issues),
        # Sprint 3: cognitive tasks that reached SUCCESS during this shift's
        # synchronous auto-execute path (see `workers/tasks/planning.py`).
        # Callers that don't pass this (Sprint 2 tests/paths with no
        # execution engine involved) keep the old default of 0.
        issues_resolved=issues_resolved,
        estimated_revenue_protected=revenue_protected,
        estimated_time_saved_hours=time_saved,
        issues=issues,
        health_category_deductions=health_result.category_deductions,
        pending_approvals=pending_approvals or [],
        completed_tasks=completed_tasks or [],
        chief_ops_briefing=chief_ops_briefing,
    )


def _default_executive_summary(issues: list[CompiledIssue], revenue_protected: float) -> str:
    if not issues:
        # Sprint 2 Feature 4 edge case, verbatim: "Zero issues detected
        # during a shift; report renders a clean 'All Systems Operational'
        # reassurance state."
        return (
            "All Systems Operational. Your Product Quality Employee scanned "
            "the catalog overnight and found no issues."
        )

    return (
        f"Tonight, your Product Quality Employee found {len(issues)} catalog "
        f"issue(s). Estimated ${revenue_protected:,.2f} in revenue at risk "
        "until resolved."
    )
