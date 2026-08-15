"""Unit tests for `CheckoutSpecialistAgent` (Sprint 4 Step 2) — both its
deterministic `analyze_discount_diff` (Observe->issue-shaping step, no LLM)
and its `propose_action` (Plan step). Mirrors
`test_product_quality_agent_propose_action.py`'s scope/style.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.domain.agents.checkout_specialist import (
    STRUCTURAL_DETECTION_CONFIDENCE,
    CheckoutSpecialistAgent,
)
from app.domain.discount_inspection import DiscountFinding
from app.domain.enums import ActionType, IssueCategory, IssueSeverity
from app.domain.models import Issue


def _issue(**overrides) -> Issue:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid.uuid4(),
        store_id=uuid.uuid4(),
        shift_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        category=IssueCategory.DISCOUNT,
        severity=IssueSeverity.HIGH,
        status="OPEN",
        title="2 overlapping stackable discounts active",
        description="test",
        evidence_data={"fix_check": "duplicate_stackable_discount", "total_sales_usd": 0.0},
        # Ordered [keep_id, *duplicate_ids] — see propose_action's docstring.
        affected_resources=["gid://shopify/DiscountCodeNode/1", "gid://shopify/DiscountCodeNode/2"],
        revenue_impact_estimate=0.0,
        confidence_score=STRUCTURAL_DETECTION_CONFIDENCE,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Issue(**defaults)


async def test_analyze_discount_diff_never_calls_an_llm_and_maps_findings_directly():
    agent = CheckoutSpecialistAgent()
    finding = DiscountFinding(
        title="2 overlapping stackable discounts active",
        severity="HIGH",
        description="test description",
        affected_resources=["gid://shopify/DiscountCodeNode/1", "gid://shopify/DiscountCodeNode/2"],
        evidence={
            "check": "duplicate_stackable_discount",
            "discount_ids": ["gid://shopify/DiscountCodeNode/1", "gid://shopify/DiscountCodeNode/2"],
            "keep_discount_id": "gid://shopify/DiscountCodeNode/1",
            "duplicate_discount_ids": ["gid://shopify/DiscountCodeNode/2"],
            "total_sales_usd": 12.5,
        },
    )

    result = await agent.analyze_discount_diff({"findings": [finding]})

    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.title == finding.title
    assert issue.severity == "HIGH"
    assert issue.confidence_score == STRUCTURAL_DETECTION_CONFIDENCE
    # Real, Shopify-reported total sales — never a fabricated projection.
    assert issue.revenue_impact_estimate == 12.5
    assert issue.fix_check == "duplicate_stackable_discount"
    assert issue.affected_resources == finding.affected_resources


async def test_analyze_discount_diff_with_no_findings_returns_empty_issues():
    agent = CheckoutSpecialistAgent()
    result = await agent.analyze_discount_diff({"findings": []})
    assert result.issues == []
    assert "no overlapping" in result.executive_summary.lower()


def test_propose_action_builds_deactivate_plan_for_duplicates_only_not_the_keeper():
    agent = CheckoutSpecialistAgent()
    issue = _issue()

    proposed = agent.propose_action(issue)

    assert proposed is not None
    assert proposed.action_type == ActionType.DEACTIVATE_DUPLICATE_DISCOUNT.value
    assert proposed.execution_plan["mutation"] == "discountCodeDeactivate"
    items = proposed.execution_plan["items"]
    assert len(items) == 1
    assert items[0]["discount_id"] == "gid://shopify/DiscountCodeNode/2"  # the duplicate, not the keeper
    assert items[0]["before_state"] == {"status": "ACTIVE"}
    assert items[0]["rollback"] == {
        "mutation": "discountCodeActivate",
        "discount_id": "gid://shopify/DiscountCodeNode/2",
    }


def test_propose_action_handles_multiple_duplicates_with_one_item_each():
    issue = _issue(
        evidence_data={"fix_check": "duplicate_stackable_discount", "total_sales_usd": 0.0},
        affected_resources=["A", "B", "C"],  # A = keep, B/C = duplicates
    )
    agent = CheckoutSpecialistAgent()
    proposed = agent.propose_action(issue)
    assert proposed is not None
    discount_ids = [item["discount_id"] for item in proposed.execution_plan["items"]]
    assert discount_ids == ["B", "C"]


def test_propose_action_returns_none_for_unrelated_fix_check():
    issue = _issue(evidence_data={"fix_check": "missing_alt_text"})
    agent = CheckoutSpecialistAgent()
    assert agent.propose_action(issue) is None


def test_propose_action_returns_none_when_fewer_than_two_affected_resources():
    issue = _issue(
        evidence_data={"fix_check": "duplicate_stackable_discount"},
        affected_resources=["gid://shopify/DiscountCodeNode/1"],
    )
    agent = CheckoutSpecialistAgent()
    assert agent.propose_action(issue) is None
