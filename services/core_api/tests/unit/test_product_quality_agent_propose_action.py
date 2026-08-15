"""Unit tests: `ProductQualityAgent.propose_action` — Sprint 3 AI Trust &
Execution Plan step.

`propose_action` is purely deterministic (never calls the LLM client), so a
`_NeverCalledLlmClient` stand-in is injected to prove that at the same time
as testing the mapping itself — matching this repo's own established
"prove the fallback/deterministic path never reaches the network" pattern
from `test_product_quality_agent_json_parsing.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.domain.agents.product_quality import ProductQualityAgent
from app.domain.enums import ActionType, IssueCategory, IssueSeverity
from app.domain.models import Issue


class _NeverCalledLlmClient:
    model_name = "should-never-be-called"

    async def generate_structured(self, *, prompt: str, response_schema):
        raise AssertionError("propose_action must never call the LLM client")


def _issue(**overrides) -> Issue:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid.uuid4(),
        store_id=uuid.uuid4(),
        shift_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        category=IssueCategory.PRODUCT_QUALITY,
        severity=IssueSeverity.MEDIUM,
        status="OPEN",
        title="Issue title",
        description="Issue description",
        evidence_data={},
        affected_resources=[],
        revenue_impact_estimate=0.0,
        confidence_score=0.9,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Issue(**defaults)


def _agent() -> ProductQualityAgent:
    return ProductQualityAgent(client=_NeverCalledLlmClient())


def test_propose_action_missing_alt_text_returns_generate_alt_text_action():
    issue = _issue(
        title="Image missing ALT text on 'Widget'",
        description="Widget's primary image has no ALT text set.",
        evidence_data={"fix_check": "missing_alt_text"},
        affected_resources=["gid://shopify/Product/1", "gid://shopify/ProductImage/2"],
    )

    proposed = _agent().propose_action(issue)

    assert proposed is not None
    assert proposed.action_type == ActionType.GENERATE_ALT_TEXT.value

    plan = proposed.execution_plan
    assert plan["mutation"] == "productImageUpdate"
    assert len(plan["items"]) == 1
    item = plan["items"][0]
    assert item["product_gid"] == "gid://shopify/Product/1"
    assert item["image_gid"] == "gid://shopify/ProductImage/2"
    assert isinstance(item["new_alt_text"], str) and item["new_alt_text"].strip() != ""
    assert "Widget" in item["new_alt_text"]
    assert item["before_state"] == {"alt_text": None}
    assert item["rollback"]["mutation"] == "productImageUpdate"
    assert "alt_text" in item["rollback"]


def test_propose_action_missing_alt_text_without_quoted_title_falls_back_generic_alt_text():
    issue = _issue(
        title="Image missing ALT text",  # no quoted product title
        evidence_data={"fix_check": "missing_alt_text"},
        affected_resources=["gid://shopify/Product/1", "gid://shopify/ProductImage/2"],
    )

    proposed = _agent().propose_action(issue)
    assert proposed is not None
    assert proposed.execution_plan["items"][0]["new_alt_text"] == "Product photo"


def test_propose_action_missing_alt_text_across_multiple_products_creates_one_item_per_pair():
    issue = _issue(
        title="Image missing ALT text across 2 products",
        evidence_data={"fix_check": "missing_alt_text"},
        affected_resources=[
            "gid://shopify/Product/1",
            "gid://shopify/ProductImage/2",
            "gid://shopify/Product/3",
            "gid://shopify/ProductImage/4",
        ],
    )

    proposed = _agent().propose_action(issue)

    assert proposed is not None
    items = proposed.execution_plan["items"]
    assert len(items) == 2
    assert items[0]["product_gid"] == "gid://shopify/Product/1"
    assert items[0]["image_gid"] == "gid://shopify/ProductImage/2"
    assert items[1]["product_gid"] == "gid://shopify/Product/3"
    assert items[1]["image_gid"] == "gid://shopify/ProductImage/4"


def test_propose_action_thin_description_returns_rewrite_product_description_action():
    issue = _issue(
        title="Thin product description on 'Widget'",
        description="Description is only 10 words long, below the SEO minimum.",
        evidence_data={"fix_check": "thin_description"},
        affected_resources=["gid://shopify/Product/1"],
    )

    proposed = _agent().propose_action(issue)

    assert proposed is not None
    assert proposed.action_type == ActionType.REWRITE_PRODUCT_DESCRIPTION.value

    plan = proposed.execution_plan
    assert plan["mutation"] == "productUpdate"
    assert len(plan["items"]) == 1
    item = plan["items"][0]
    assert item["product_gid"] == "gid://shopify/Product/1"
    assert isinstance(item["new_description_html"], str) and item["new_description_html"].strip() != ""
    # The generic template must never leak the raw issue.description text —
    # for a multi-product aggregate issue that names every OTHER affected
    # product, embedding it verbatim into one product's live description
    # was a real bug found during Sprint 3 live E2E testing.
    assert issue.description not in item["new_description_html"]
    assert item["before_state"] == {"description_html": None}
    assert item["rollback"]["mutation"] == "productUpdate"
    assert "description_html" in item["rollback"]


def test_propose_action_thin_description_across_multiple_products_creates_one_item_per_product():
    issue = _issue(
        title="Thin product descriptions across 3 products",
        description="'A' (0 words), 'B' (0 words), 'C' (2 words) are all below the SEO minimum.",
        evidence_data={"fix_check": "thin_description"},
        affected_resources=["gid://shopify/Product/1", "gid://shopify/Product/2", "gid://shopify/Product/3"],
    )

    proposed = _agent().propose_action(issue)

    assert proposed is not None
    items = proposed.execution_plan["items"]
    assert len(items) == 3
    assert [item["product_gid"] for item in items] == [
        "gid://shopify/Product/1",
        "gid://shopify/Product/2",
        "gid://shopify/Product/3",
    ]
    for item in items:
        assert issue.description not in item["new_description_html"]


def test_propose_action_returns_none_when_fix_check_is_null():
    issue = _issue(evidence_data={"fix_check": None})
    assert _agent().propose_action(issue) is None


def test_propose_action_returns_none_when_fix_check_key_is_absent():
    issue = _issue(evidence_data={})
    assert _agent().propose_action(issue) is None


def test_propose_action_returns_none_for_unrecognized_fix_check_value():
    issue = _issue(evidence_data={"fix_check": "some_other_unhandled_check"})
    assert _agent().propose_action(issue) is None
