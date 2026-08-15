"""Chief Ops AI — Sprint 4 Step 4, Phase B ("Intelligence") per
`SPRINT4_AI_WORKFORCE_VISION.md`.

Pure domain logic + one LLM-calling class, no DB/HTTP/Celery imports (mirrors
`shift_compiler.py`'s own contract). Once a shift has closed with 1+
specialist findings, one more structured-LLM call synthesizes them into a
clean Chief Ops Executive Memo — when 2+ specialists reported, that means
narrating the correlated story across them ("Theme Guardian noticed a
change, Tracking Specialist saw the pixel disappear at the same time — same
incident"), the Vision doc's own worked example; when only one specialist
reported, it's still worth an LLM pass to turn that single finding into a
polished executive-memo sentence rather than a template one (Sprint 5 Phase
1.3 — see CONFLICTS.md item 48; this reverses the original 2+-only gate).
Only a shift with zero findings at all skips the LLM entirely — genuinely
nothing to summarize — and falls back to a deterministic "all clear" line.
The LLM call itself remains guarded by the same daily budget ceiling
(`LlmCallBudgetGuard`, ADR-024) and always has a deterministic fallback if
the call fails or the budget is exhausted.

"Multi-Agent Handshake log format" (Vision doc, Three Locked Additions #2):
structured output is a list of per-specialist "turns" (agent name, finding,
timestamp) plus a final synthesis field, rendered with a 🟢/⚡/🧠 icon
treatment. No prior source document defines this icon convention — it is
this step's own, deterministic rendering decision (not LLM-assigned, so it
can never be wrong or invented): ⚡ = this issue was actually auto-executed
by NightShift this shift (a `CognitiveTask` reached SUCCESS for it), 🧠 = this
issue is awaiting a merchant's judgment call (`AWAITING_APPROVAL`), 🟢 =
detected and informational, no action pending.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog
from pydantic import BaseModel, Field

from app.domain.shift_compiler import CompiledIssue
from app.infrastructure.llm.budget_guard import LlmBudgetExceededError, LlmCallBudgetGuard
from app.infrastructure.llm.errors import LlmSchemaValidationError
from app.infrastructure.llm.factory import StructuredLlmClient

logger = structlog.get_logger(component="chief_ops")

MINIMUM_TURNS_FOR_SYNTHESIS = 1
"""Sprint 5 Phase 1.3: an LLM synthesis pass is attempted once a shift has
1+ specialist findings — down from the original 2+-distinct-categories gate
(Vision doc's initial framing). A shift with zero findings is the only case
that still skips the LLM outright."""

# Deliberately a static mirror of the `Agent.identifier` values already
# established in Steps 1-3 (`checkout_specialist.py`, `theme_guardian.py`,
# `tracking_specialist.py`, `product_quality.py`) — not re-derived from the
# live agent registry, since `compile_shift_report_task` (this module's
# caller) never constructs agent instances, only reads already-persisted
# `Issue.category` strings. Kept in sync by hand when a new specialist's
# domain_category is added; a category missing from this dict falls back to
# its raw enum value rather than raising, so a forgotten update degrades to
# an ugly-but-correct label, never a crash.
CATEGORY_DISPLAY_NAMES: dict[str, str] = {
    "PRODUCT_QUALITY": "Product Quality Employee",
    "DISCOUNT": "Checkout Specialist",
    # Reused, not a typo — ThemeGuardianAgent deliberately reuses the
    # CHECKOUT category (see its own docstring / migration 0005's comment).
    "CHECKOUT": "Theme Guardian",
    "PIXEL_TRACKING": "Tracking Specialist",
}


@dataclass(frozen=True)
class SpecialistTurn:
    issue_id: str
    category: str
    agent_name: str
    icon: str
    finding_title: str
    finding_summary: str
    severity: str
    status: str
    revenue_impact_estimate: float
    timestamp: str  # ISO-8601; Issue has no per-issue "detected_at" column
    # distinct from created_at, so created_at (already on CompiledIssue via
    # the issue itself) is reused as-is — never fabricated.
    # Sprint 5 Phase 5: grounded verbatim from the originating CognitiveTask's
    # confidence_assessment (see `domain/confidence.py::merchant_memory_note`)
    # — None whenever this issue's action type has no real prior approval
    # history at this store. Never LLM-derived, same discipline as `icon`.
    merchant_memory_note: str | None = None

    def to_dict(self) -> dict:
        return {
            "issue_id": self.issue_id,
            "category": self.category,
            "agent_name": self.agent_name,
            "icon": self.icon,
            "finding_title": self.finding_title,
            "finding_summary": self.finding_summary,
            "severity": self.severity,
            "status": self.status,
            "revenue_impact_estimate": round(self.revenue_impact_estimate, 2),
            "timestamp": self.timestamp,
            "merchant_memory_note": self.merchant_memory_note,
        }


@dataclass(frozen=True)
class ChiefOpsBriefing:
    turns: list[SpecialistTurn] = field(default_factory=list)
    narrative: str = ""
    correlated: bool = False
    used_llm: bool = False

    def to_dict(self) -> dict:
        return {
            "turns": [t.to_dict() for t in self.turns],
            "narrative": self.narrative,
            "correlated": self.correlated,
            "used_llm": self.used_llm,
        }


def _icon_for_issue(issue: CompiledIssue, resolved_issue_ids: set[str]) -> str:
    if issue.id in resolved_issue_ids:
        return "⚡"
    if issue.status == "AWAITING_APPROVAL":
        return "🧠"
    return "🟢"


def build_specialist_turns(
    issues: list[CompiledIssue],
    *,
    resolved_issue_ids: set[str] | None = None,
    timestamps: dict[str, str] | None = None,
    merchant_memory_notes: dict[str, str] | None = None,
) -> list[SpecialistTurn]:
    """Builds one turn per issue this shift. `resolved_issue_ids` is the set
    of `Issue.id` strings whose `CognitiveTask` reached SUCCESS this shift
    (see `shift_report.py`'s `completed_tasks[]`) — used only to pick the ⚡
    icon, never to alter the issue's own persisted status. `timestamps` maps
    issue id -> ISO timestamp (the issue's own `created_at`, passed in by the
    caller since `CompiledIssue` itself carries no timestamp field); issues
    missing an entry fall back to an empty string rather than fabricating a
    time. `merchant_memory_notes` (Sprint 5 Phase 5) maps issue id -> a
    grounded "🧠 Merchant Preference Applied" note (see
    `domain/confidence.py::merchant_memory_note`); issues missing an entry
    simply have no note, never a fabricated one.
    """
    resolved = resolved_issue_ids or set()
    ts_map = timestamps or {}
    notes_map = merchant_memory_notes or {}
    return [
        SpecialistTurn(
            issue_id=issue.id,
            category=issue.category,
            agent_name=CATEGORY_DISPLAY_NAMES.get(issue.category, issue.category),
            icon=_icon_for_issue(issue, resolved),
            finding_title=issue.title,
            finding_summary=issue.description,
            severity=issue.severity,
            status=issue.status,
            revenue_impact_estimate=issue.revenue_impact_estimate,
            timestamp=ts_map.get(issue.id, ""),
            merchant_memory_note=notes_map.get(issue.id),
        )
        for issue in issues
    ]


def should_synthesize(turns: list[SpecialistTurn]) -> bool:
    """Sprint 5 Phase 1.3: Chief Ops now attempts an LLM synthesis call
    whenever a shift has 1+ specialist findings — even a single specialist's
    finding gets a clean, LLM-written executive-memo sentence rather than a
    template one. Only a shift with zero findings at all skips the LLM
    entirely, since there is genuinely nothing to summarize."""
    return len(turns) >= MINIMUM_TURNS_FOR_SYNTHESIS


class ChiefOpsSynthesisSchema(BaseModel):
    narrative: str = Field(description="The correlated story across every specialist turn listed below.")
    correlated: bool = Field(
        description="true only if 2+ turns plausibly describe the same underlying incident, "
        "false if they are genuinely unrelated findings that merely happened in the same shift."
    )


_PROMPT_TEMPLATE = """You are Chief Ops AI at NightShift AI, an autonomous Shopify store-operations workforce. Below are this shift's findings from {category_count} different specialist AI employees.

Ground your narrative ONLY in the findings listed below — never invent a cause, a specialist, or a fact not present here.

Rules:
- Write 2-4 sentences a busy merchant can read in ten seconds.
- Call out whether any of these findings look like the same underlying incident (e.g. a theme change and a tracking pixel disappearing at the same time) versus genuinely unrelated issues.
- Set `correlated` to true only if you can point to a real, stated reason two or more turns are connected; otherwise false.
- If `correlated` is true, begin `narrative` with the exact prefix "Root Cause: " followed by one sentence naming the single connecting cause across those turns (e.g. "Root Cause: A theme deployment removed the Meta Pixel app embed, breaking checkout tracking."), then continue with any remaining supporting detail. If `correlated` is false, do not use the words "Root Cause" at all — just summarize the unrelated findings plainly.

Findings this shift:
{findings_block}

Respond with strict JSON matching the required schema only."""


def _format_findings_block(turns: list[SpecialistTurn]) -> str:
    lines = []
    for turn in turns:
        lines.append(
            f"- [{turn.agent_name} / {turn.category}] {turn.finding_title}: {turn.finding_summary} "
            f"(severity={turn.severity}, status={turn.status}, "
            f"revenue_impact_estimate=${turn.revenue_impact_estimate:,.2f}, "
            f"timestamp={turn.timestamp or 'unknown'})"
        )
    return "\n".join(lines)


def _deterministic_narrative(turns: list[SpecialistTurn]) -> str:
    """Fallback used both when synthesis is skipped entirely (zero findings
    this shift) and when an attempted LLM call fails for a non-empty shift —
    never leaves a shift's Executive Briefing blank just because the LLM
    step didn't run or didn't succeed."""
    if not turns:
        return "No specialists reported findings this shift — all clear."

    categories = {t.category for t in turns}
    if len(categories) == 1:
        agent_name = turns[0].agent_name
        return (
            f"{agent_name} was the only specialist with findings this shift — "
            "nothing to correlate across agents yet."
        )

    titles = "; ".join(f"{t.agent_name}: {t.finding_title}" for t in turns)
    return (
        f"{len(categories)} specialists reported findings this shift: {titles}. "
        "AI-generated cross-agent correlation was unavailable this shift; review the Work Log "
        "for full detail on each finding."
    )


def deterministic_briefing(turns: list[SpecialistTurn]) -> ChiefOpsBriefing:
    """Used directly (no LLM call at all) whenever `should_synthesize` is
    False — i.e. a shift with zero specialist findings at all."""
    return ChiefOpsBriefing(turns=turns, narrative=_deterministic_narrative(turns), correlated=False, used_llm=False)


class ChiefOpsSynthesizer:
    """Provider-agnostic via `StructuredLlmClient`, same pattern as
    `ThemeGuardianAgent`/`ProductQualityAgent` — constructed with whichever
    client `infrastructure/llm/factory.py` builds from
    `Settings.llm_provider`. Not an `Agent` subclass: Chief Ops doesn't
    detect issues or `propose_action` against Shopify, it only synthesizes
    already-persisted findings across categories, so it doesn't fit the
    per-issue-category `Agent` ABC/registry at all."""

    def __init__(self, *, client: StructuredLlmClient, budget_guard: LlmCallBudgetGuard | None = None) -> None:
        self._client = client
        self._budget_guard = budget_guard

    async def synthesize(self, turns: list[SpecialistTurn]) -> ChiefOpsBriefing:
        if not should_synthesize(turns):
            return deterministic_briefing(turns)

        if self._budget_guard is not None:
            try:
                await self._budget_guard.check_and_increment()
            except LlmBudgetExceededError as exc:
                logger.warning(
                    "chief_ops_synthesizer_budget_exceeded_fallback",
                    model=self._client.model_name,
                    status="budget_exceeded",
                    error=str(exc),
                )
                return ChiefOpsBriefing(
                    turns=turns, narrative=_deterministic_narrative(turns), correlated=False, used_llm=False
                )

        categories = {t.category for t in turns}
        prompt = _PROMPT_TEMPLATE.format(
            category_count=len(categories), findings_block=_format_findings_block(turns)
        )

        start = time.perf_counter()
        try:
            result = await self._client.generate_structured(
                prompt=prompt, response_schema=ChiefOpsSynthesisSchema
            )
        except LlmSchemaValidationError as exc:
            duration = time.perf_counter() - start
            logger.warning(
                "chief_ops_synthesizer_fallback_triggered",
                model=self._client.model_name,
                duration_seconds=round(duration, 3),
                status="fallback",
                error=str(exc),
            )
            return ChiefOpsBriefing(
                turns=turns, narrative=_deterministic_narrative(turns), correlated=False, used_llm=False
            )

        duration = time.perf_counter() - start
        logger.info(
            "chief_ops_synthesis_completed",
            model=self._client.model_name,
            duration_seconds=round(duration, 3),
            turn_count=len(turns),
            correlated=result.correlated,
            status="success",
        )
        return ChiefOpsBriefing(turns=turns, narrative=result.narrative, correlated=result.correlated, used_llm=True)
