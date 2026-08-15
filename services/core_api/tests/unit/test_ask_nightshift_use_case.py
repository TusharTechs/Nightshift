"""Unit tests for the `AskNightShift` use case (Sprint 4 Step 4). Mirrors
`test_chief_ops_domain.py`'s fake-LLM-client style, using
`InMemoryShiftReportRepository` (no database) per this codebase's own
use-case-testing convention (`test_trigger_demo_incident_use_case.py`, etc.).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.application.ports import InMemoryShiftReportRepository
from app.application.use_cases.ask_nightshift import (
    AskNightShift,
    AskNightShiftAnswerSchema,
)
from app.domain.models import ShiftReport
from app.infrastructure.llm.budget_guard import LlmBudgetExceededError
from app.infrastructure.llm.errors import LlmSchemaValidationError

STORE_ID = uuid.uuid4()


class _FakeLlmClientSuccess:
    model_name = "fake-model-success"

    async def generate_structured(self, *, prompt: str, response_schema):
        assert response_schema is AskNightShiftAnswerSchema
        assert "why did revenue" in prompt.lower()
        return AskNightShiftAnswerSchema(
            answer="Revenue protection rose because Checkout Specialist deactivated a duplicate "
            "discount code that had been stacking with your storewide sale.",
            grounded=True,
        )


class _FakeLlmClientFailure:
    model_name = "fake-model-failure"

    async def generate_structured(self, *, prompt: str, response_schema):
        raise LlmSchemaValidationError("simulated schema validation failure")


class _ClientThatMustNotBeCalled:
    model_name = "must-not-be-called"

    async def generate_structured(self, *, prompt: str, response_schema):
        raise AssertionError("LLM must never be called when there is no shift history at all")


class _AlwaysExceededBudgetGuard:
    async def check_and_increment(self) -> int:
        raise LlmBudgetExceededError("simulated daily budget exhausted")


def _seed_report(repo: InMemoryShiftReportRepository, *, shift_number: int, revenue_protected: float) -> None:
    now = datetime.now(timezone.utc)
    repo.seed(
        ShiftReport(
            id=uuid.uuid4(),
            shift_id=uuid.uuid4(),
            store_id=STORE_ID,
            executive_summary=f"Shift #{shift_number}: found and fixed a duplicate discount.",
            report_json={
                "shift_number": shift_number,
                "health_score": 92,
                "executive_summary": f"Shift #{shift_number}: found and fixed a duplicate discount.",
                "metrics": {
                    "issues_detected": 1,
                    "issues_resolved": 1,
                    "estimated_revenue_protected_usd": revenue_protected,
                    "time_saved_hours": 0.25,
                },
                "issues": [{"title": "Duplicate discount code deactivated"}],
                "chief_ops_briefing": {"narrative": "Checkout Specialist handled it alone.", "correlated": False},
            },
            published_at=now,
            created_at=now,
        )
    )


async def test_ask_returns_deterministic_answer_and_no_llm_call_when_no_shift_history():
    repo = InMemoryShiftReportRepository()
    use_case = AskNightShift(shift_reports=repo, client=_ClientThatMustNotBeCalled())

    result = await use_case.ask(STORE_ID, "why did revenue increase yesterday?")

    assert result.used_llm is False
    assert "don't have any completed shifts" in result.answer.lower()
    assert result.grounded_in_shift_ids == []


async def test_ask_grounds_answer_in_real_shift_report_data_via_llm():
    repo = InMemoryShiftReportRepository()
    _seed_report(repo, shift_number=1, revenue_protected=150.0)
    _seed_report(repo, shift_number=2, revenue_protected=400.0)

    use_case = AskNightShift(shift_reports=repo, client=_FakeLlmClientSuccess())
    result = await use_case.ask(STORE_ID, "why did revenue increase yesterday?")

    assert result.used_llm is True
    assert "Checkout Specialist" in result.answer
    assert len(result.grounded_in_shift_ids) == 2


async def test_ask_falls_back_on_schema_validation_failure():
    repo = InMemoryShiftReportRepository()
    _seed_report(repo, shift_number=1, revenue_protected=150.0)

    use_case = AskNightShift(shift_reports=repo, client=_FakeLlmClientFailure())
    result = await use_case.ask(STORE_ID, "why did revenue increase yesterday?")

    assert result.used_llm is False
    assert "Shift #1" in result.answer


async def test_ask_falls_back_when_daily_budget_exceeded():
    repo = InMemoryShiftReportRepository()
    _seed_report(repo, shift_number=1, revenue_protected=150.0)

    use_case = AskNightShift(
        shift_reports=repo,
        client=_ClientThatMustNotBeCalled(),
        budget_guard=_AlwaysExceededBudgetGuard(),
    )
    result = await use_case.ask(STORE_ID, "why did revenue increase yesterday?")

    assert result.used_llm is False
    assert result.grounded_in_shift_ids  # still grounded, just no LLM narration


async def test_ask_only_gathers_recent_shifts_up_to_configured_limit():
    repo = InMemoryShiftReportRepository()
    for i in range(1, 8):
        _seed_report(repo, shift_number=i, revenue_protected=float(i))

    use_case = AskNightShift(shift_reports=repo, client=_FakeLlmClientSuccess(), shift_history_limit=3)
    result = await use_case.ask(STORE_ID, "why did revenue increase yesterday?")

    assert len(result.grounded_in_shift_ids) == 3
