"""Product Quality Employee — Sprint 2 Feature 2 / Story 2.

LLM-powered agent that evaluates raw catalog inspection diffs (produced by
`domain/inspection.py`), detects operational issues, estimates revenue
impact, and assigns confidence scores.

Provider-agnostic by design: this class depends only on the
`StructuredLlmClient` structural contract (a `model_name` property + a
`generate_structured` coroutine), never on a concrete SDK. Which provider is
actually active is decided once, at construction time, by
`infrastructure/llm/factory.py::build_llm_client` from `Settings.llm_provider`
— Gemini, the sole supported provider.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import structlog
from pydantic import BaseModel, Field

from app.domain.agents.base import Agent, AgentAnalysisResult, ProposedAction
from app.domain.enums import ActionType
from app.domain.models import Issue
from app.infrastructure.llm.budget_guard import LlmBudgetExceededError, LlmCallBudgetGuard
from app.infrastructure.llm.errors import LlmSchemaValidationError
from app.infrastructure.llm.factory import StructuredLlmClient

logger = structlog.get_logger(component="product_quality_agent")

FALLBACK_CONFIDENCE_SCORE = 0.70
"""Sprint 2 Feature 2 edge case, verbatim: "Gemini API timeouts or schema
parsing errors; falls back to deterministic rule-based issue formatting
with a default confidence score of 0.70." — the same fallback now also
triggers if the daily LLM call budget guard trips."""

_VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


class DetectedIssueSchema(BaseModel):
    """The only Pydantic issue schema Sprint 2's own spec actually defines
    in code. Story 2's acceptance criteria names it `AgentIssueSchema`, but
    no such model is ever given anywhere in that document — a naming slip
    in the source doc (see CONFLICTS.md item 14). `DetectedIssueSchema` is
    used consistently throughout this codebase since it's the one with a
    concrete definition."""

    title: str
    severity: str = Field(description="CRITICAL, HIGH, MEDIUM, or LOW")
    description: str
    revenue_impact_estimate: float
    confidence_score: float = Field(ge=0.0, le=1.0)
    affected_resources: list[str]
    fix_check: str | None = Field(
        default=None,
        description=(
            "Sprint 3: verbatim copy of the originating inspection finding's "
            "evidence.check identifier (e.g. 'missing_alt_text', "
            "'thin_description'), so the Plan step can match issues to "
            "automated fixes deterministically instead of parsing LLM prose. "
            "Null if the finding carries no such identifier."
        ),
    )


class AgentAnalysisResponse(AgentAnalysisResult):
    issues: list[DetectedIssueSchema] = Field(default_factory=list)


_PROMPT_TEMPLATE = """You are the Product Quality Employee at NightShift AI, acting strictly as a senior e-commerce operations manager for this merchant's Shopify store.

You are given a structured JSON diff of catalog inspection findings below. Ground every issue and every revenue impact estimate ONLY in the data provided in this context — never invent product or variant GIDs, prices, or facts not present here.

Rules:
- Prioritize issues that create conversion friction (missing images, missing pricing, broken variants) over cosmetic issues.
- Estimate realistic USD revenue impact using only the catalog data provided.
- Write clear, actionable, professional explanations a merchant can act on immediately.
- Classify each issue's severity as exactly one of: CRITICAL, HIGH, MEDIUM, LOW.
- Be concise — you have a limited output budget.
- If an inspection finding includes an 'evidence.check' identifier, copy that exact string verbatim into the issue's fix_check field; otherwise leave fix_check null. Never invent a fix_check value not present in the provided findings.

Inspection findings:
{inspection_data_json}

Respond with strict JSON matching the required schema only."""


class ProductQualityAgent(Agent):
    identifier = "product-quality-agent"
    domain_category = "PRODUCT_QUALITY"

    def __init__(
        self,
        *,
        client: StructuredLlmClient,
        budget_guard: LlmCallBudgetGuard | None = None,
    ) -> None:
        self._client = client
        self._budget_guard = budget_guard

    async def analyze(self, inspection_data: dict[str, Any]) -> AgentAnalysisResponse:
        return await self.analyze_catalog_diff(inspection_data)

    async def analyze_catalog_diff(self, inspection_data: dict[str, Any]) -> AgentAnalysisResponse:
        if self._budget_guard is not None:
            try:
                await self._budget_guard.check_and_increment()
            except LlmBudgetExceededError as exc:
                logger.warning(
                    "product_quality_agent_budget_exceeded_fallback",
                    agent=self.identifier,
                    model=self._client.model_name,
                    status="budget_exceeded",
                    error=str(exc),
                )
                return self._deterministic_fallback(inspection_data)

        prompt = _PROMPT_TEMPLATE.format(
            inspection_data_json=json.dumps(inspection_data, default=str)
        )

        start = time.perf_counter()
        try:
            result = await self._client.generate_structured(
                prompt=prompt, response_schema=AgentAnalysisResponse
            )
        except LlmSchemaValidationError as exc:
            duration = time.perf_counter() - start
            logger.warning(
                "product_quality_agent_fallback_triggered",
                agent=self.identifier,
                model=self._client.model_name,
                duration_seconds=round(duration, 3),
                status="fallback",
                error=str(exc),
            )
            return self._deterministic_fallback(inspection_data)

        duration = time.perf_counter() - start
        avg_confidence = (
            round(sum(i.confidence_score for i in result.issues) / len(result.issues), 3)
            if result.issues
            else None
        )
        logger.info(
            "product_quality_agent_analysis_completed",
            agent=self.identifier,
            model=self._client.model_name,
            duration_seconds=round(duration, 3),
            issues_found=len(result.issues),
            avg_confidence=avg_confidence,
            status="success",
        )
        return result

    def _deterministic_fallback(self, inspection_data: dict[str, Any]) -> AgentAnalysisResponse:
        """Rule-based issue formatting when the LLM times out, is refused,
        is truncated, fails schema validation, or the daily call budget is
        exhausted (Sprint 2 Feature 2 edge case, verbatim): "falls back to
        deterministic rule-based issue formatting with a default confidence
        score of 0.70." Revenue impact is not fabricated here — the
        deterministic inspection engine never estimates revenue, so the
        fallback conservatively reports $0.00 impact rather than inventing
        a number no upstream signal supports.
        """
        findings = inspection_data.get("findings", [])
        issues = [
            DetectedIssueSchema(
                title=finding["title"],
                severity=(
                    finding.get("severity")
                    if finding.get("severity") in _VALID_SEVERITIES
                    else "LOW"
                ),
                description=finding.get("description", finding["title"]),
                revenue_impact_estimate=0.0,
                confidence_score=FALLBACK_CONFIDENCE_SCORE,
                affected_resources=finding.get("affected_resources", []),
                fix_check=finding.get("evidence", {}).get("check"),
            )
            for finding in findings
        ]
        summary = (
            f"Rule-based fallback: {len(issues)} catalog issue(s) detected. "
            "AI reasoning was unavailable this shift; findings reflect "
            "deterministic rule matching only, with no revenue estimate."
            if issues
            else "Rule-based fallback: no catalog issues detected."
        )
        return AgentAnalysisResponse(executive_summary=summary, issues=issues)

    # --- Sprint 3: Plan step (propose_action) -------------------------------

    def propose_action(self, issue: Issue) -> ProposedAction | None:
        """Maps a persisted Issue back to a concrete, executable fix via the
        `fix_check` identifier threaded through from `evidence_data` at
        issue-creation time (see `services/workers/tasks/inspection.py`) —
        never by string-matching the LLM-generated title/description, which
        would be brittle and non-deterministic."""
        fix_check = issue.evidence_data.get("fix_check")
        if fix_check == "missing_alt_text":
            return self._propose_alt_text_fix(issue)
        if fix_check == "thin_description":
            return self._propose_description_fix(issue)
        return None

    @staticmethod
    def _extract_product_title(title: str) -> str | None:
        """Best-effort extraction of the product title from an
        `inspect_catalog`-generated issue title, which always wraps it in
        single quotes (e.g. "Image missing ALT text on 'Widget'"). Returns
        None if no quoted substring is found — callers must supply their own
        conservative fallback rather than inventing a title."""
        match = re.search(r"'([^']*)'", title)
        return match.group(1) if match else None

    def _propose_alt_text_fix(self, issue: Issue) -> ProposedAction:
        """`issue.affected_resources` is a flat list of `(product_gid,
        image_gid)` pairs per `domain/inspection.py`'s `missing_alt_text`
        finding — usually one pair, but Sprint 2's LLM issue-consolidation
        can bundle several per-product findings under one issue (e.g.
        "missing ALT text across N products"), so this must not assume
        exactly one pair. Every pair gets its own mutation item within this
        single task/execution — see `execute_cognitive_task.py`/
        `verify_execution.py`, which iterate `execution_plan["items"]` and
        only consider the whole task successful once every item verifies.
        ALT text is generated deterministically from the product title — no
        LLM call, keeping this fix cheap and fully reproducible."""
        resources = issue.affected_resources
        product_title = self._extract_product_title(issue.title)
        new_alt_text = f"{product_title} product photo" if product_title else "Product photo"

        items = []
        for i in range(0, len(resources) - 1, 2):
            items.append(
                {
                    "product_gid": resources[i],
                    "image_gid": resources[i + 1],
                    "new_alt_text": new_alt_text,
                    "before_state": {"alt_text": None},
                    "rollback": {"mutation": "productImageUpdate", "alt_text": None},
                }
            )

        execution_plan = {"mutation": "productImageUpdate", "items": items}
        return ProposedAction(action_type=ActionType.GENERATE_ALT_TEXT.value, execution_plan=execution_plan)

    def _propose_description_fix(self, issue: Issue) -> ProposedAction:
        """`issue.affected_resources` is a list of product_gids per
        `domain/inspection.py`'s `thin_description` finding — usually one,
        but Sprint 2's LLM issue-consolidation can bundle several per-product
        findings under one issue (e.g. "thin descriptions across N
        products"). Every product gets its own rewrite item within this
        single task/execution, and the issue is only marked RESOLVED once
        every item verifies (see `execute_cognitive_task.py`/
        `verify_execution.py`).

        Each generated description is a deterministic, generic template
        grounded ONLY in a generic subject — never `issue.description`,
        which for a multi-product issue names every OTHER affected product
        and their word counts, and previously leaked verbatim into whichever
        product happened to be item 0's live, customer-facing copy (found
        during Sprint 3 live E2E testing). No per-product title is available
        without an extra Shopify lookup at Plan time, so "This product" is
        the deliberate, safe generic subject rather than a wrong name."""
        subject = "This product"
        new_description_html = (
            f"<p>{subject} is thoughtfully designed to meet everyday needs. "
            f"Crafted with quality materials and built to last, {subject} "
            "offers reliable performance and lasting value for our "
            "customers.</p>"
        )

        items = [
            {
                "product_gid": product_gid,
                "new_description_html": new_description_html,
                # LIMITATION: the actual prior descriptionHtml is not
                # captured anywhere upstream this sprint (the Issue model /
                # evidence_data only carries `word_count`, not the real
                # text). Rollback for this action type therefore restores to
                # an empty string rather than the true prior description — a
                # known, documented gap, not an oversight. Flagged again in
                # the Sprint 3 completion report.
                "before_state": {"description_html": None},
                "rollback": {"mutation": "productUpdate", "description_html": None},
            }
            for product_gid in issue.affected_resources
        ]

        execution_plan = {"mutation": "productUpdate", "items": items}
        return ProposedAction(
            action_type=ActionType.REWRITE_PRODUCT_DESCRIPTION.value, execution_plan=execution_plan
        )
