"""Unit tests for `ThemeGuardianAgent` (Sprint 4 Step 3) — its LLM-backed
`analyze_theme_diff` (with fallback) and its `propose_action` (guided
restore, never a live Shopify mutation). Mirrors
`test_product_quality_agent_json_parsing.py`'s fake-LLM-client style.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.domain.agents.theme_guardian import (
    DETECTION_CONFIDENCE,
    FALLBACK_CONFIDENCE_SCORE,
    ThemeDiffExplanationSchema,
    ThemeGuardianAgent,
)
from app.domain.enums import ActionType, IssueCategory, IssueSeverity
from app.domain.models import Issue
from app.infrastructure.llm.errors import LlmSchemaValidationError

FILENAME = "sections/main-product.liquid"
BASELINE_CONTENT = "line1\n{% render 'buy-buttons' %}\nline3"
CURRENT_CONTENT = "line1\nline3"


class _FakeLlmClientSuccess:
    model_name = "fake-model-success"

    async def generate_structured(self, *, prompt: str, response_schema):
        assert FILENAME in prompt
        assert BASELINE_CONTENT in prompt
        return ThemeDiffExplanationSchema(
            severity="CRITICAL", explanation="The Buy Button include was removed."
        )


class _FakeLlmClientFailure:
    model_name = "fake-model-failure"

    async def generate_structured(self, *, prompt: str, response_schema):
        raise LlmSchemaValidationError("simulated schema validation failure")


def _finding(**overrides) -> dict:
    defaults = dict(
        filename=FILENAME,
        baseline_content=BASELINE_CONTENT,
        current_content=CURRENT_CONTENT,
        changed_line_count=2,
        affected_resources=["gid://shopify/OnlineStoreTheme/1", FILENAME],
        evidence={"check": "theme_file_diverged_from_baseline"},
    )
    defaults.update(overrides)
    return defaults


def _issue(**overrides) -> Issue:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid.uuid4(),
        store_id=uuid.uuid4(),
        shift_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        category=IssueCategory.CHECKOUT,
        severity=IssueSeverity.HIGH,
        status="OPEN",
        title=f"Theme file '{FILENAME}' changed unexpectedly",
        description="test",
        evidence_data={
            "fix_check": "theme_file_diverged_from_baseline",
            "theme_id": "gid://shopify/OnlineStoreTheme/1",
            "filename": FILENAME,
            "baseline_content": BASELINE_CONTENT,
            "current_content": CURRENT_CONTENT,
        },
        affected_resources=["gid://shopify/OnlineStoreTheme/1", FILENAME],
        revenue_impact_estimate=0.0,
        confidence_score=DETECTION_CONFIDENCE,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Issue(**defaults)


async def test_analyze_theme_diff_success_path_uses_llm_severity_and_explanation():
    agent = ThemeGuardianAgent(client=_FakeLlmClientSuccess())
    result = await agent.analyze_theme_diff({"findings": [_finding()]})

    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.severity == "CRITICAL"
    assert "Buy Button" in issue.description
    assert issue.confidence_score == DETECTION_CONFIDENCE
    assert issue.revenue_impact_estimate == 0.0  # never fabricated
    assert issue.fix_check == "theme_file_diverged_from_baseline"
    assert issue.affected_resources == ["gid://shopify/OnlineStoreTheme/1", FILENAME]


async def test_analyze_theme_diff_falls_back_on_schema_validation_failure():
    agent = ThemeGuardianAgent(client=_FakeLlmClientFailure())
    result = await agent.analyze_theme_diff({"findings": [_finding()]})

    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.severity == "HIGH"
    assert "Rule-based fallback" in issue.description
    assert issue.revenue_impact_estimate == 0.0


async def test_analyze_theme_diff_with_no_findings_returns_empty_issues():
    agent = ThemeGuardianAgent(client=_FakeLlmClientSuccess())
    result = await agent.analyze_theme_diff({"findings": []})
    assert result.issues == []
    assert "no theme file divergences" in result.executive_summary.lower()


def test_propose_action_builds_guided_restore_plan_never_a_live_mutation():
    agent = ThemeGuardianAgent(client=_FakeLlmClientSuccess())
    issue = _issue()

    proposed = agent.propose_action(issue)

    assert proposed is not None
    assert proposed.action_type == ActionType.GENERATE_THEME_RESTORE_GUIDE.value
    assert proposed.execution_plan["mutation"] == "theme_restore_guide"
    items = proposed.execution_plan["items"]
    assert len(items) == 1
    assert items[0]["filename"] == FILENAME
    assert items[0]["expected_content"] == BASELINE_CONTENT
    assert items[0]["before_state"] == {"content": CURRENT_CONTENT}
    # No live Shopify mutation is ever executed for this action type.
    assert items[0]["rollback"] == {"mutation": "none"}


def test_propose_action_returns_none_for_unrelated_fix_check():
    agent = ThemeGuardianAgent(client=_FakeLlmClientSuccess())
    issue = _issue(evidence_data={"fix_check": "missing_alt_text"})
    assert agent.propose_action(issue) is None


def test_propose_action_returns_none_when_content_is_missing():
    agent = ThemeGuardianAgent(client=_FakeLlmClientSuccess())
    issue = _issue(
        evidence_data={
            "fix_check": "theme_file_diverged_from_baseline",
            "theme_id": "gid://shopify/OnlineStoreTheme/1",
            "filename": FILENAME,
            # baseline_content missing entirely
        }
    )
    assert agent.propose_action(issue) is None
