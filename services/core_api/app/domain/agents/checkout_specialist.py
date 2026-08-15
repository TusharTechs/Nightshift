"""Checkout Specialist — Sprint 4 Step 2 (Duplicate Discount lifecycle).

Second entry in the agent registry (see `plan_cognitive_tasks.py`'s Sprint 4
Step 1 docstring) and the "Build first" item in
`SPRINT4_AI_WORKFORCE_VISION.md`'s Phase A. Unlike `ProductQualityAgent`,
this agent's detection step is deliberately NOT an LLM call: a duplicate,
mutually-stackable storewide discount is a deterministic, structural fact
about the store's configuration (see `domain/discount_inspection.py`'s own
docstring for the exact rule), not a judgment call that benefits from an
LLM's reasoning. Keeping this path fully deterministic also keeps Step 2
inside the roadmap's own "zero new scope" framing for this slice — no new
LLM budget consumer, no new prompt to validate.

`domain_category = "DISCOUNT"` (not `"CHECKOUT"`) — deliberate: `IssueCategory`
already has both values, and this agent only ever detects/fixes discount
pricing overlaps, not the broader checkout-flow health checks the roadmap
explicitly defers past the hackathon. Reserving `"CHECKOUT"` keeps that door
open for a genuinely broader future specialist without a naming collision.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domain.agents.base import Agent, AgentAnalysisResult, ProposedAction
from app.domain.discount_inspection import DiscountFinding
from app.domain.enums import ActionType
from app.domain.models import Issue

STRUCTURAL_DETECTION_CONFIDENCE = 0.95
"""Unlike Product Quality's LLM-judged findings, a duplicate/overlapping
stackable discount is a deterministic structural fact about the store's live
discount configuration, not a probabilistic judgment call — so a single
fixed, documented confidence constant is used here instead of a per-finding
LLM-estimated score, mirroring `product_quality.py`'s own
`FALLBACK_CONFIDENCE_SCORE` pattern for its own non-LLM path."""


class DetectedDiscountIssueSchema(BaseModel):
    """Checkout Specialist's own issue schema — structurally parallel to
    `product_quality.py`'s `DetectedIssueSchema` but intentionally a
    separate class, since this agent's issues are never LLM output and
    carry no severity ambiguity to validate against a schema."""

    title: str
    severity: str
    description: str
    revenue_impact_estimate: float
    confidence_score: float = Field(ge=0.0, le=1.0)
    affected_resources: list[str]
    fix_check: str | None = None


class CheckoutAnalysisResponse(AgentAnalysisResult):
    issues: list[DetectedDiscountIssueSchema] = Field(default_factory=list)


class CheckoutSpecialistAgent(Agent):
    identifier = "checkout-specialist-agent"
    domain_category = "DISCOUNT"

    async def analyze(self, inspection_data: dict[str, Any]) -> CheckoutAnalysisResponse:
        return await self.analyze_discount_diff(inspection_data)

    async def analyze_discount_diff(self, inspection_data: dict[str, Any]) -> CheckoutAnalysisResponse:
        """Deterministic — no LLM call, no `await` actually needed, but kept
        `async` for calling-convention parity with `ProductQualityAgent.
        analyze_catalog_diff` (the worker task that invokes this is already
        inside an async function either way) and so a future narrative-only
        LLM enhancement wouldn't need to change this method's signature."""
        findings: list[DiscountFinding] = inspection_data.get("findings", [])
        issues = [
            DetectedDiscountIssueSchema(
                title=f.title,
                severity=f.severity,
                description=f.description,
                # Real, Shopify-reported total sales already attributed to
                # these codes — never a fabricated projection (see this
                # module's own docstring and discount_inspection.py's).
                revenue_impact_estimate=float(f.evidence.get("total_sales_usd") or 0.0),
                confidence_score=STRUCTURAL_DETECTION_CONFIDENCE,
                affected_resources=f.affected_resources,
                fix_check=f.evidence.get("check"),
            )
            for f in findings
        ]
        summary = (
            f"Checkout Specialist: {len(issues)} overlapping discount incident(s) detected."
            if issues
            else "Checkout Specialist: no overlapping or duplicate discounts detected."
        )
        return CheckoutAnalysisResponse(executive_summary=summary, issues=issues)

    # --- Plan step (propose_action) -----------------------------------------

    def propose_action(self, issue: Issue) -> ProposedAction | None:
        """Mirrors `ProductQualityAgent.propose_action`'s own convention
        exactly: `evidence_data` carries only the `fix_check` identifier
        (plus lightweight metadata), never the structural data needed to
        build the execution plan — that lives in `affected_resources`,
        matching how the ALT-text fix reads `(product_gid, image_gid)`
        pairs from `affected_resources` rather than from evidence_data.

        `affected_resources` here is ordered `[keep_id, *duplicate_ids]` —
        the oldest (by createdAt) discount always comes first (see
        `domain/discount_inspection.py::inspect_discounts`'s own
        docstring) — so the keeper is always `affected_resources[0]` and
        every remaining entry is a duplicate to deactivate."""
        fix_check = issue.evidence_data.get("fix_check")
        if fix_check != "duplicate_stackable_discount":
            return None

        resources = issue.affected_resources
        if len(resources) < 2:
            return None
        duplicate_ids = resources[1:]

        items = [
            {
                "discount_id": discount_id,
                "before_state": {"status": "ACTIVE"},
                # Fully reversible, unlike Product Quality's two fix types:
                # `discountCodeActivate` genuinely restores the discount to
                # active, not an empty/None placeholder — no known-limitation
                # rollback gap here.
                "rollback": {"mutation": "discountCodeActivate", "discount_id": discount_id},
            }
            for discount_id in duplicate_ids
        ]
        execution_plan = {"mutation": "discountCodeDeactivate", "items": items}
        return ProposedAction(
            action_type=ActionType.DEACTIVATE_DUPLICATE_DISCOUNT.value, execution_plan=execution_plan
        )
