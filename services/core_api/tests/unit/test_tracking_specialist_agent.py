"""Unit tests for `TrackingSpecialistAgent` (Sprint 4 Step 3) — both its
deterministic `analyze_script_tag_diff` (no LLM) and its `propose_action`
(Plan step). Mirrors `test_checkout_specialist_agent.py`'s scope/style.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.domain.agents.tracking_specialist import (
    STRUCTURAL_DETECTION_CONFIDENCE,
    TrackingSpecialistAgent,
)
from app.domain.enums import ActionType, IssueCategory, IssueSeverity
from app.domain.models import Issue
from app.domain.script_tag_inspection import ScriptTagFinding

META_PIXEL_SRC = "https://connect.facebook.net/en_US/fbevents.js"


def _issue(**overrides) -> Issue:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid.uuid4(),
        store_id=uuid.uuid4(),
        shift_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        category=IssueCategory.PIXEL_TRACKING,
        severity=IssueSeverity.HIGH,
        status="OPEN",
        title="Meta Pixel script tag removed from storefront",
        description="test",
        evidence_data={"fix_check": "tracking_script_removed", "pattern_name": "Meta Pixel"},
        affected_resources=[META_PIXEL_SRC, "ONLINE_STORE", "Meta Pixel"],
        revenue_impact_estimate=0.0,
        confidence_score=STRUCTURAL_DETECTION_CONFIDENCE,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Issue(**defaults)


async def test_analyze_script_tag_diff_never_calls_an_llm_and_maps_findings_directly():
    agent = TrackingSpecialistAgent()
    finding = ScriptTagFinding(
        title="Meta Pixel script tag removed from storefront",
        severity="HIGH",
        description="test description",
        affected_resources=[META_PIXEL_SRC, "ONLINE_STORE", "Meta Pixel"],
        evidence={"check": "tracking_script_removed", "src": META_PIXEL_SRC, "pattern_name": "Meta Pixel"},
    )

    result = await agent.analyze_script_tag_diff({"findings": [finding]})

    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.title == finding.title
    assert issue.severity == "HIGH"
    assert issue.confidence_score == STRUCTURAL_DETECTION_CONFIDENCE
    assert issue.revenue_impact_estimate == 0.0  # never fabricated
    assert issue.fix_check == "tracking_script_removed"
    assert issue.affected_resources == finding.affected_resources


async def test_analyze_script_tag_diff_with_no_findings_returns_empty_issues():
    agent = TrackingSpecialistAgent()
    result = await agent.analyze_script_tag_diff({"findings": []})
    assert result.issues == []
    assert "no tracking script removals" in result.executive_summary.lower()


def test_propose_action_builds_recreate_plan_from_snapshot_src_and_display_scope():
    agent = TrackingSpecialistAgent()
    issue = _issue()

    proposed = agent.propose_action(issue)

    assert proposed is not None
    assert proposed.action_type == ActionType.RECREATE_TRACKING_SCRIPT_TAG.value
    assert proposed.execution_plan["mutation"] == "scriptTagCreate"
    items = proposed.execution_plan["items"]
    assert len(items) == 1
    assert items[0]["src"] == META_PIXEL_SRC
    assert items[0]["display_scope"] == "ONLINE_STORE"
    assert items[0]["before_state"] == {"existed": False}
    # script_tag_id is deliberately absent — patched in post-execution.
    assert items[0]["rollback"] == {"mutation": "scriptTagDelete"}


def test_propose_action_returns_none_for_unrelated_fix_check():
    agent = TrackingSpecialistAgent()
    issue = _issue(evidence_data={"fix_check": "duplicate_stackable_discount"})
    assert agent.propose_action(issue) is None


def test_propose_action_returns_none_when_fewer_than_two_affected_resources():
    agent = TrackingSpecialistAgent()
    issue = _issue(affected_resources=[META_PIXEL_SRC])
    assert agent.propose_action(issue) is None
