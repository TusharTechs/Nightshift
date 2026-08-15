"""Ask NightShift use case — Sprint 4 Step 4, Phase B's conversational
surface (`SPRINT4_AI_WORKFORCE_VISION.md`).

Vision doc, verbatim: "merchant asks 'why did revenue increase yesterday,'
gets the attributed answer. Cheapest, highest-ROI item on the entire
roadmap — pure LLM call over data that already exists (audit logs, revenue
estimates, explanation bundles)."

Grounds every answer in real, already-persisted `ShiftReport.report_json`
rows for this store — never in freshly recomputed or invented data, and
never in raw `audit_logs`/DB rows re-derived ad hoc: `report_json` is already
the durable, structured summary Sprint 2 built for exactly this purpose (see
`shift_compiler.py`'s own "immutable after publication" contract), so
reusing it here is the same "don't invent a second source of truth" instinct
already applied elsewhere in this codebase. No new schema — purely additive,
read-only use of tables Sprint 2/3/4 already persist.

Stateless by design for this step: no conversation history table, matching
the Vision doc's own scope ("pure LLM call over data that already exists").
Revisit if the demo calls for multi-turn conversation later.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import structlog
from pydantic import BaseModel, Field

from app.application.ports import ShiftReportRepository
from app.infrastructure.llm.budget_guard import LlmBudgetExceededError, LlmCallBudgetGuard
from app.infrastructure.llm.errors import LlmSchemaValidationError
from app.infrastructure.llm.factory import StructuredLlmClient

logger = structlog.get_logger(component="ask_nightshift")

DEFAULT_SHIFT_HISTORY_LIMIT = 5
"""Bounded lookback window — mirrors `MAX_INSPECTION_SKUS`-style precedent
elsewhere in this codebase: enough recent shifts to answer "why did X change
recently" questions without letting a long-lived store's full history
balloon a single LLM prompt."""


class AskNightShiftAnswerSchema(BaseModel):
    answer: str = Field(description="Plain-English answer to the merchant's question.")
    grounded: bool = Field(
        description="true if the shift data below genuinely contains enough information to answer "
        "the question; false if it does not — in that case `answer` must say so plainly rather than "
        "guessing at a cause not present in the data."
    )


@dataclass(frozen=True)
class AskNightShiftResult:
    answer: str
    grounded_in_shift_ids: list[str] = field(default_factory=list)
    used_llm: bool = False


_PROMPT_TEMPLATE = """You are Chief Ops AI at NightShift AI, answering a merchant's question about their store's recent AI-run shifts.

Ground your answer ONLY in the shift data listed below — never invent a cause, number, or event not present here. If the data below genuinely doesn't answer the question, set `grounded` to false and say so plainly rather than guessing.

Merchant's question: {question}

Recent shifts (newest first):
{shifts_block}

Respond with strict JSON matching the required schema only."""


def _format_shifts_block(reports: list) -> str:
    lines = []
    for report in reports:
        payload = report.report_json
        metrics = payload.get("metrics", {})
        briefing = payload.get("chief_ops_briefing") or {}
        issue_titles = "; ".join(i.get("title", "") for i in payload.get("issues", [])) or "none"
        lines.append(
            f"- Shift #{payload.get('shift_number')} (published {report.published_at.isoformat()}): "
            f"health_score={payload.get('health_score')}, "
            f"issues_detected={metrics.get('issues_detected')}, "
            f"issues_resolved={metrics.get('issues_resolved')}, "
            f"revenue_protected_usd={metrics.get('estimated_revenue_protected_usd')}. "
            f"Executive summary: {payload.get('executive_summary')} "
            f"Issues: {issue_titles}. "
            f"Chief Ops narrative: {briefing.get('narrative', 'none')}"
        )
    return "\n".join(lines)


def _deterministic_answer(reports: list) -> str:
    """Fallback used when there's no shift history at all, the daily LLM
    budget is spent, or the LLM call fails schema validation — never leaves
    a merchant's question completely unanswered."""
    if not reports:
        return (
            "I don't have any completed shifts to draw on yet — once your first overnight shift "
            "finishes, ask me again."
        )
    latest = reports[0].report_json
    return (
        "An AI-generated answer wasn't available this time. Here's your most recent shift's own "
        f"summary (Shift #{latest.get('shift_number')}): {latest.get('executive_summary')}"
    )


class AskNightShift:
    def __init__(
        self,
        *,
        shift_reports: ShiftReportRepository,
        client: StructuredLlmClient,
        budget_guard: LlmCallBudgetGuard | None = None,
        shift_history_limit: int = DEFAULT_SHIFT_HISTORY_LIMIT,
    ) -> None:
        self._shift_reports = shift_reports
        self._client = client
        self._budget_guard = budget_guard
        self._shift_history_limit = shift_history_limit

    async def ask(self, store_id: uuid.UUID, question: str) -> AskNightShiftResult:
        reports = await self._shift_reports.list_recent_for_store(
            store_id, limit=self._shift_history_limit
        )
        grounded_ids = [str(r.shift_id) for r in reports]

        if not reports:
            return AskNightShiftResult(answer=_deterministic_answer(reports), used_llm=False)

        if self._budget_guard is not None:
            try:
                await self._budget_guard.check_and_increment()
            except LlmBudgetExceededError as exc:
                logger.warning(
                    "ask_nightshift_budget_exceeded_fallback", status="budget_exceeded", error=str(exc)
                )
                return AskNightShiftResult(
                    answer=_deterministic_answer(reports), grounded_in_shift_ids=grounded_ids, used_llm=False
                )

        prompt = _PROMPT_TEMPLATE.format(question=question, shifts_block=_format_shifts_block(reports))

        start = time.perf_counter()
        try:
            result = await self._client.generate_structured(
                prompt=prompt, response_schema=AskNightShiftAnswerSchema
            )
        except LlmSchemaValidationError as exc:
            duration = time.perf_counter() - start
            logger.warning(
                "ask_nightshift_fallback_triggered",
                duration_seconds=round(duration, 3),
                status="fallback",
                error=str(exc),
            )
            return AskNightShiftResult(
                answer=_deterministic_answer(reports), grounded_in_shift_ids=grounded_ids, used_llm=False
            )

        duration = time.perf_counter() - start
        logger.info(
            "ask_nightshift_answered",
            duration_seconds=round(duration, 3),
            grounded=result.grounded,
            shift_count=len(reports),
            status="success",
        )
        return AskNightShiftResult(answer=result.answer, grounded_in_shift_ids=grounded_ids, used_llm=True)
