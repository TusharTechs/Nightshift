"""Theme Guardian — Sprint 4 Step 3.

LLM-powered agent (unlike Checkout Specialist/Tracking Specialist's
deterministic-only detection): a theme file's content diverging from its
baseline is a deterministic structural fact (a checksum mismatch — see
`domain/theme_inspection.py`), but *what changed and why it matters to a
merchant* benefits from an LLM's plain-English explanation, exactly the
judgment-call/explanation split `SPRINT4_AI_WORKFORCE_VISION.md` calls for.

Provider-agnostic via `StructuredLlmClient`, same as `ProductQualityAgent` —
constructed with whichever client `infrastructure/llm/factory.py` builds
from `Settings.llm_provider` (Gemini).

Unlike `ProductQualityAgent`'s single batched LLM call across every finding,
this agent calls the LLM once per finding, not once per shift. Justified
because `theme_inspection.py`'s watch-list is small and bounded (see its own
docstring — typically 0 or 1 finding per run, never catalog-scale), so the
batching optimization Product Quality needs for cost control doesn't apply
here, and a 1:1 call keeps the LLM's job narrowly scoped to "explain this one
diff" rather than needing to return a title/affected_resources/fix_check it
might get wrong for multiple unrelated files at once — those fields are set
deterministically from the finding itself here, never from LLM output,
mirroring `fix_check`'s "never invent an identifier not present in the
provided findings" rule elsewhere in this codebase.

Restore is never an UNSUPERVISED Shopify write — see
`domain/enums.py::ActionType.GENERATE_THEME_RESTORE_GUIDE`'s own comment and
CONFLICTS.md's `themeFilesUpsert`-exemption entry. `propose_action` here
always produces the same guided-resolution bundle (the exact baseline content
as the patch, plan-time), gated behind mandatory LEVEL_3_HIGH approval; what
happens once a merchant approves is decided at Execute time
(`execute_cognitive_task.py`), which now attempts a real, automated
`themeFilesUpsert` restore first and only falls back to handing the merchant
a Theme Editor deep link if Shopify denies the write (the common case, absent
a manually-granted exemption — see `infrastructure/shopify_client.py`).
"""

from __future__ import annotations

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

logger = structlog.get_logger(component="theme_guardian_agent")

DETECTION_CONFIDENCE = 0.90
"""Confidence reflects certainty of *detection* (a checksum mismatch against
a known baseline is a deterministic fact), not confidence in the LLM's
explanation quality — the two are independent, mirroring
`STRUCTURAL_DETECTION_CONFIDENCE`'s reasoning in `checkout_specialist.py`,
just slightly lower to leave room below Checkout Specialist's 0.95 for the
LLM-authored (not purely rule-derived) explanation text."""

FALLBACK_CONFIDENCE_SCORE = 0.70
"""Same fallback confidence Product Quality uses when its LLM call fails
schema validation or the daily budget is exhausted — see
`product_quality.py::FALLBACK_CONFIDENCE_SCORE`."""

_VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}

_PROMPT_TEMPLATE = """You are the Theme Guardian at NightShift AI. Below is the baseline (last known-good) content and the current live content of one Shopify theme file, which differ in {changed_line_count} line(s).

Ground your explanation ONLY in the actual content shown below — never invent a cause not visible in these two versions.

Rules:
- Explain, in plain English a non-technical merchant can act on immediately, what changed and why it matters for their storefront.
- Classify severity as exactly one of: CRITICAL, HIGH, MEDIUM, LOW.
- Be concise.

Filename: {filename}

--- BASELINE (last known-good) ---
{baseline_content}

--- CURRENT (live) ---
{current_content}

Respond with strict JSON matching the required schema only."""


class ThemeDiffExplanationSchema(BaseModel):
    severity: str = Field(description="CRITICAL, HIGH, MEDIUM, or LOW")
    explanation: str


class DetectedThemeIssueSchema(BaseModel):
    title: str
    severity: str
    description: str
    revenue_impact_estimate: float
    confidence_score: float = Field(ge=0.0, le=1.0)
    affected_resources: list[str]
    fix_check: str | None = None


class ThemeAnalysisResponse(AgentAnalysisResult):
    issues: list[DetectedThemeIssueSchema] = Field(default_factory=list)


class ThemeGuardianAgent(Agent):
    identifier = "theme-guardian-agent"
    domain_category = "CHECKOUT"
    """Deliberately reused, not invented — see the Step 3 migration's own
    comment: `checkout_specialist.py` explicitly reserved 'CHECKOUT' for "a
    genuinely broader future specialist," and a removed Buy Button block is
    exactly that: a storefront issue that breaks the customer's ability to
    check out, with no discount/pricing object involved at all."""

    def __init__(
        self,
        *,
        client: StructuredLlmClient,
        budget_guard: LlmCallBudgetGuard | None = None,
    ) -> None:
        self._client = client
        self._budget_guard = budget_guard

    async def analyze(self, inspection_data: dict[str, Any]) -> ThemeAnalysisResponse:
        return await self.analyze_theme_diff(inspection_data)

    async def analyze_theme_diff(self, inspection_data: dict[str, Any]) -> ThemeAnalysisResponse:
        findings: list[dict] = inspection_data.get("findings", [])
        issues: list[DetectedThemeIssueSchema] = []

        for finding in findings:
            severity, explanation = await self._explain_one_finding(finding)
            issues.append(
                DetectedThemeIssueSchema(
                    title=f"Theme file '{finding['filename']}' changed unexpectedly",
                    severity=severity,
                    description=explanation,
                    # No real revenue signal exists for a theme file diff
                    # (unlike Checkout Specialist's Shopify-reported
                    # totalSales) — never fabricated, always 0.0.
                    revenue_impact_estimate=0.0,
                    confidence_score=DETECTION_CONFIDENCE,
                    affected_resources=finding["affected_resources"],
                    fix_check=finding.get("evidence", {}).get("check"),
                )
            )

        summary = (
            f"Theme Guardian: {len(issues)} theme file divergence(s) detected."
            if issues
            else "Theme Guardian: no theme file divergences detected."
        )
        return ThemeAnalysisResponse(executive_summary=summary, issues=issues)

    async def _explain_one_finding(self, finding: dict) -> tuple[str, str]:
        """Returns (severity, explanation). Falls back to a deterministic,
        rule-based explanation if the budget guard trips or the LLM call
        fails schema validation — never leaves a genuinely detected finding
        unexplained just because the LLM step failed."""
        if self._budget_guard is not None:
            try:
                await self._budget_guard.check_and_increment()
            except LlmBudgetExceededError as exc:
                logger.warning(
                    "theme_guardian_agent_budget_exceeded_fallback",
                    agent=self.identifier,
                    model=self._client.model_name,
                    status="budget_exceeded",
                    error=str(exc),
                )
                return self._deterministic_fallback_explanation(finding)

        prompt = _PROMPT_TEMPLATE.format(
            changed_line_count=finding.get("changed_line_count", "an unknown number of"),
            filename=finding["filename"],
            baseline_content=finding["baseline_content"],
            current_content=finding["current_content"],
        )

        start = time.perf_counter()
        try:
            result = await self._client.generate_structured(
                prompt=prompt, response_schema=ThemeDiffExplanationSchema
            )
        except LlmSchemaValidationError as exc:
            duration = time.perf_counter() - start
            logger.warning(
                "theme_guardian_agent_fallback_triggered",
                agent=self.identifier,
                model=self._client.model_name,
                duration_seconds=round(duration, 3),
                status="fallback",
                error=str(exc),
            )
            return self._deterministic_fallback_explanation(finding)

        severity = result.severity if result.severity in _VALID_SEVERITIES else "HIGH"
        return severity, result.explanation

    @staticmethod
    def _deterministic_fallback_explanation(finding: dict) -> tuple[str, str]:
        changed = finding.get("changed_line_count", 0)
        explanation = (
            f"Rule-based fallback: {changed} line(s) of '{finding['filename']}' changed from the "
            "last known-good version. AI-generated explanation was unavailable this shift; a "
            "human review of the diff is recommended before restoring."
        )
        return "HIGH", explanation

    # --- Plan step (propose_action) -----------------------------------------

    def propose_action(self, issue: Issue) -> ProposedAction | None:
        """Never proposes a direct Shopify write (see this module's own
        docstring) — only a guided-resolution bundle. `evidence_data` here
        deliberately carries the full baseline/current file content, not
        just a `fix_check` identifier — an intentional, documented exception
        to the "evidence_data is lightweight metadata only" convention
        established in `checkout_specialist.py`: theme file content has no
        stable GID a later lookup could resolve (unlike a discount or
        product), so the content itself must travel with the Issue or the
        restore patch could never be reconstructed at Plan time."""
        fix_check = issue.evidence_data.get("fix_check")
        if fix_check != "theme_file_diverged_from_baseline":
            return None

        theme_id = issue.evidence_data.get("theme_id")
        filename = issue.evidence_data.get("filename")
        baseline_content = issue.evidence_data.get("baseline_content")
        current_content = issue.evidence_data.get("current_content")
        if not theme_id or not filename or baseline_content is None:
            return None

        items = [
            {
                "theme_id": theme_id,
                "filename": filename,
                "expected_content": baseline_content,
                "before_state": {"content": current_content},
                # No live Shopify mutation was ever executed by NightShift
                # for this action type (see execute_cognitive_task.py's
                # "theme_restore_guide" branch) — "none" is a real,
                # documented rollback mutation type, not a placeholder gap.
                "rollback": {"mutation": "none"},
            }
        ]
        execution_plan = {"mutation": "theme_restore_guide", "items": items}
        return ProposedAction(
            action_type=ActionType.GENERATE_THEME_RESTORE_GUIDE.value, execution_plan=execution_plan
        )
