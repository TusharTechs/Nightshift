"""Unit tests: Product Quality Agent structured JSON parsing + fallback
(Sprint 2 Feature 2 / Story 2).

Required test name per Sprint 2's own Testing section:
test_product_quality_agent_json_parsing (covered by the suite below).

The real LLM SDK call is never exercised here — a fake client (implementing
just the `model_name` + `generate_structured` structural contract) is
injected directly, so these tests run offline and deterministically without
ever touching the real `GeminiClient`.
"""

from __future__ import annotations

import pytest

from app.domain.agents.product_quality import (
    AgentAnalysisResponse,
    DetectedIssueSchema,
    FALLBACK_CONFIDENCE_SCORE,
    ProductQualityAgent,
)
from app.infrastructure.llm.budget_guard import LlmBudgetExceededError, LlmCallBudgetGuard
from app.infrastructure.llm.errors import LlmSchemaValidationError


class _FakeLlmClientSuccess:
    model_name = "fake-model-success"

    async def generate_structured(self, *, prompt: str, response_schema):
        return AgentAnalysisResponse(
            executive_summary="Found 1 issue.",
            issues=[
                DetectedIssueSchema(
                    title="Missing primary image on 'Widget'",
                    severity="HIGH",
                    description="Widget has no featured image.",
                    revenue_impact_estimate=120.0,
                    confidence_score=0.96,
                    affected_resources=["gid://shopify/Product/1"],
                )
            ],
        )


class _FakeLlmClientFailure:
    model_name = "fake-model-failure"

    async def generate_structured(self, *, prompt: str, response_schema):
        raise LlmSchemaValidationError("simulated schema validation failure")


class _CountingBudgetGuardBackend:
    """Minimal in-memory stand-in for the Redis-backed counter, so the
    budget-exceeded fallback path can be exercised without a real Redis
    connection."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True


@pytest.mark.asyncio
async def test_product_quality_agent_json_parsing_success_path():
    agent = ProductQualityAgent(client=_FakeLlmClientSuccess())
    result = await agent.analyze_catalog_diff({"findings": []})

    assert isinstance(result, AgentAnalysisResponse)
    assert len(result.issues) == 1
    assert result.issues[0].severity == "HIGH"
    assert 0.0 <= result.issues[0].confidence_score <= 1.0


@pytest.mark.asyncio
async def test_product_quality_agent_falls_back_on_schema_validation_failure():
    agent = ProductQualityAgent(client=_FakeLlmClientFailure())
    inspection_data = {
        "findings": [
            {
                "title": "Missing primary image on 'Widget'",
                "severity": "HIGH",
                "description": "Widget has no featured image.",
                "affected_resources": ["gid://shopify/Product/1"],
            }
        ]
    }

    result = await agent.analyze_catalog_diff(inspection_data)

    assert len(result.issues) == 1
    assert result.issues[0].severity == "HIGH"
    assert result.issues[0].confidence_score == FALLBACK_CONFIDENCE_SCORE
    # The fallback path never fabricates a revenue estimate the deterministic
    # inspection engine didn't itself provide.
    assert result.issues[0].revenue_impact_estimate == 0.0


@pytest.mark.asyncio
async def test_product_quality_agent_fallback_defaults_unknown_severity_to_low():
    agent = ProductQualityAgent(client=_FakeLlmClientFailure())
    inspection_data = {
        "findings": [
            {
                "title": "Some finding",
                "severity": "NOT_A_REAL_SEVERITY",
                "description": "desc",
                "affected_resources": [],
            }
        ]
    }

    result = await agent.analyze_catalog_diff(inspection_data)
    assert result.issues[0].severity == "LOW"


@pytest.mark.asyncio
async def test_product_quality_agent_fallback_on_empty_findings_reports_no_issues():
    agent = ProductQualityAgent(client=_FakeLlmClientFailure())
    result = await agent.analyze_catalog_diff({"findings": []})
    assert result.issues == []
    assert "no catalog issues" in result.executive_summary.lower()


@pytest.mark.asyncio
async def test_product_quality_agent_falls_back_without_calling_llm_once_budget_exceeded():
    # Cost-safety regression test (2026-07-31 hardening): once the daily
    # budget is spent, the agent must fall back WITHOUT ever reaching
    # generate_structured — verified here with a client that fails the test
    # outright if it's ever called.
    class _ClientThatMustNotBeCalled:
        model_name = "should-never-be-called"

        async def generate_structured(self, *, prompt: str, response_schema):
            raise AssertionError("generate_structured must not be called once budget is exceeded")

    backend = _CountingBudgetGuardBackend()
    guard = LlmCallBudgetGuard(backend=backend, max_calls_per_day=1)
    agent = ProductQualityAgent(client=_ClientThatMustNotBeCalled(), budget_guard=guard)

    # Exhaust the day's only allowed slot directly against the guard (not
    # through the agent), so the "already exhausted" state doesn't depend
    # on the client at all.
    await guard.check_and_increment()

    # The guard is now exhausted. analyze_catalog_diff must catch
    # LlmBudgetExceededError internally and fall back — never reaching
    # generate_structured, which would raise AssertionError and fail this
    # test if it were called.
    result = await agent.analyze_catalog_diff({"findings": []})

    assert result.issues == []
    assert "no catalog issues" in result.executive_summary.lower()


@pytest.mark.asyncio
async def test_budget_guard_raises_once_exhausted():
    backend = _CountingBudgetGuardBackend()
    guard = LlmCallBudgetGuard(backend=backend, max_calls_per_day=1)

    await guard.check_and_increment()  # 1/1 — within budget

    with pytest.raises(LlmBudgetExceededError):
        await guard.check_and_increment()  # 2/1 — exceeded
