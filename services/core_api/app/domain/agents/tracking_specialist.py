"""Tracking Specialist — Sprint 4 Step 3.

Deterministic-only detection, no LLM call — mirrors `CheckoutSpecialistAgent`
exactly: a previously-snapshotted script tag going missing from the live
`scriptTags` listing is a structural fact (see
`domain/script_tag_inspection.py`), not a judgment call.

`domain_category = "PIXEL_TRACKING"` — exact semantic fit with the existing
`IssueCategory` enum value (present since Sprint 2's own migration, unused
until this step), no reservation/disambiguation needed the way Checkout
Specialist's `"DISCOUNT"` vs `"CHECKOUT"` choice required.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domain.agents.base import Agent, AgentAnalysisResult, ProposedAction
from app.domain.enums import ActionType
from app.domain.models import Issue
from app.domain.script_tag_inspection import ScriptTagFinding

STRUCTURAL_DETECTION_CONFIDENCE = 0.95
"""A previously-snapshotted script tag no longer appearing in the live
listing is a deterministic structural fact, not a probabilistic judgment
call — same fixed-confidence rationale as
`checkout_specialist.py::STRUCTURAL_DETECTION_CONFIDENCE`."""


class DetectedTrackingIssueSchema(BaseModel):
    title: str
    severity: str
    description: str
    revenue_impact_estimate: float
    confidence_score: float = Field(ge=0.0, le=1.0)
    affected_resources: list[str]
    fix_check: str | None = None


class TrackingAnalysisResponse(AgentAnalysisResult):
    issues: list[DetectedTrackingIssueSchema] = Field(default_factory=list)


class TrackingSpecialistAgent(Agent):
    identifier = "tracking-specialist-agent"
    domain_category = "PIXEL_TRACKING"

    async def analyze(self, inspection_data: dict[str, Any]) -> TrackingAnalysisResponse:
        return await self.analyze_script_tag_diff(inspection_data)

    async def analyze_script_tag_diff(self, inspection_data: dict[str, Any]) -> TrackingAnalysisResponse:
        """Deterministic — no LLM call, `async` only for calling-convention
        parity with the LLM-backed agents (mirrors
        `CheckoutSpecialistAgent.analyze_discount_diff`'s own docstring)."""
        findings: list[ScriptTagFinding] = inspection_data.get("findings", [])
        issues = [
            DetectedTrackingIssueSchema(
                title=f.title,
                severity=f.severity,
                description=f.description,
                # No real revenue signal is available for a removed tracking
                # script (unlike Checkout Specialist's Shopify-reported
                # totalSales) — never fabricated, always 0.0.
                revenue_impact_estimate=0.0,
                confidence_score=STRUCTURAL_DETECTION_CONFIDENCE,
                affected_resources=f.affected_resources,
                fix_check=f.evidence.get("check"),
            )
            for f in findings
        ]
        summary = (
            f"Tracking Specialist: {len(issues)} removed tracking script(s) detected."
            if issues
            else "Tracking Specialist: no tracking script removals detected."
        )
        return TrackingAnalysisResponse(executive_summary=summary, issues=issues)

    # --- Plan step (propose_action) -----------------------------------------

    def propose_action(self, issue: Issue) -> ProposedAction | None:
        """`affected_resources` is the flat triple `[src, display_scope,
        pattern_name]` per `script_tag_inspection.py::inspect_script_tags`'s
        own docstring — `pattern_name` is unused here (display-only), kept
        in the tuple purely so it survives the round-trip to
        `Issue.affected_resources` for the frontend's own display use."""
        fix_check = issue.evidence_data.get("fix_check")
        if fix_check != "tracking_script_removed":
            return None

        resources = issue.affected_resources
        if len(resources) < 2:
            return None
        src, display_scope = resources[0], resources[1]

        items = [
            {
                "src": src,
                "display_scope": display_scope or "ONLINE_STORE",
                "before_state": {"existed": False},
                # `script_tag_id` is deliberately absent here — Shopify
                # assigns a new id only once `scriptTagCreate` actually
                # runs, so it can't be known at Plan time. Execute step
                # patches it into this same rollback dict post-creation and
                # persists the updated execution_plan (see
                # `execute_cognitive_task.py`'s own comment) so a later
                # rollback has a real id to delete.
                "rollback": {"mutation": "scriptTagDelete"},
            }
        ]
        execution_plan = {"mutation": "scriptTagCreate", "items": items}
        return ProposedAction(
            action_type=ActionType.RECREATE_TRACKING_SCRIPT_TAG.value, execution_plan=execution_plan
        )
