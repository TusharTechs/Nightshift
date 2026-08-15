"""HTTP-level integration test: `GET /api/v1/approvals` (Sprint 3 AI Trust &
Execution list endpoint; Sprint 5 Phase 3.2 added `description`/
`evidence_data` passthrough). Mirrors `test_handle_approval_action_api.py`'s
`TestClient` + `app.dependency_overrides` pattern — plain `def` tests with a
small `_run()` `asyncio.run()` helper, since the `InMemory*` fakes' methods
are coroutines.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import (
    get_approval_repository,
    get_cognitive_task_repository,
    get_current_store_id,
    get_issue_repository,
)
from app.application.ports import (
    InMemoryApprovalRepository,
    InMemoryCognitiveTaskRepository,
    InMemoryIssueRepository,
)
from app.domain.enums import IssueCategory, IssueSeverity
from app.main import app


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_overrides_after_test():
    yield
    app.dependency_overrides.pop(get_current_store_id, None)
    app.dependency_overrides.pop(get_approval_repository, None)
    app.dependency_overrides.pop(get_cognitive_task_repository, None)
    app.dependency_overrides.pop(get_issue_repository, None)


async def _seed_pending_approval(
    *, category: str, description: str, evidence_data: dict, action_type: str
) -> SimpleNamespace:
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()

    issues = InMemoryIssueRepository()
    cognitive_tasks = InMemoryCognitiveTaskRepository()
    approvals = InMemoryApprovalRepository(cognitive_tasks)

    issue = await issues.create(
        store_id=store_id,
        shift_id=shift_id,
        agent_id=uuid.uuid4(),
        category=category,
        severity=IssueSeverity.HIGH.value,
        title="Theme file 'sections/main-product.liquid' changed unexpectedly",
        description=description,
        evidence_data=evidence_data,
        affected_resources=["gid://shopify/OnlineStoreTheme/1", "sections/main-product.liquid"],
        revenue_impact_estimate=0.0,
        confidence_score=0.85,
    )

    task = await cognitive_tasks.create(
        issue_id=issue.id,
        store_id=store_id,
        shift_id=shift_id,
        agent_id=uuid.uuid4(),
        action_type=action_type,
        execution_plan={"mutation": "theme_restore_guide", "items": []},
        risk_level="LEVEL_3_HIGH",
        risk_reasoning="test",
        idempotency_key=f"{issue.id}:{action_type}",
        status="PENDING_APPROVAL",
    )

    approval = await approvals.create(task_id=task.id, issue_id=issue.id, store_id=store_id)

    return SimpleNamespace(
        store_id=store_id,
        issue=issue,
        task=task,
        approval=approval,
        issues=issues,
        cognitive_tasks=cognitive_tasks,
        approvals=approvals,
    )


def _override(env: SimpleNamespace):
    app.dependency_overrides[get_current_store_id] = lambda: env.store_id
    app.dependency_overrides[get_approval_repository] = lambda: env.approvals
    app.dependency_overrides[get_cognitive_task_repository] = lambda: env.cognitive_tasks
    app.dependency_overrides[get_issue_repository] = lambda: env.issues


def test_list_pending_approvals_passes_through_theme_guardian_description_and_full_evidence(
    client: TestClient,
):
    """Sprint 5 Phase 3.2's own reason for existing: the Approval Center's
    Theme Guardian diff card needs the real plain-English explanation and
    the real baseline/current file content, both already on the Issue, in
    this list response — not a new endpoint or an extra round-trip."""
    env = _run(
        _seed_pending_approval(
            category=IssueCategory.CHECKOUT.value,
            description="This change removes the Buy Button block, which will prevent customers from purchasing this product.",
            evidence_data={
                "fix_check": "theme_file_diverged_from_baseline",
                "filename": "sections/main-product.liquid",
                "theme_id": "gid://shopify/OnlineStoreTheme/1",
                "baseline_content": "{% render 'buy-buttons' %}",
                "current_content": "{% comment %}{% render 'buy-buttons' %}{% endcomment %}",
                "dedup_key": "theme:gid://shopify/OnlineStoreTheme/1:sections/main-product.liquid:abc123",
            },
            action_type="GENERATE_THEME_RESTORE_GUIDE",
        )
    )
    _override(env)
    try:
        response = client.get("/api/v1/approvals")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 1
    item = body["data"][0]

    assert item["description"] == env.issue.description
    assert item["evidence_data"] == env.issue.evidence_data
    assert item["evidence_data"]["baseline_content"] == "{% render 'buy-buttons' %}"
    assert item["evidence_data"]["current_content"] == "{% comment %}{% render 'buy-buttons' %}{% endcomment %}"
    assert item["recommended_action"] == "GENERATE_THEME_RESTORE_GUIDE"


def test_list_pending_approvals_passes_through_a_lightweight_evidence_shape_unchanged(client: TestClient):
    """The Sprint 5 Phase 3.2 docstring's own documented default case: most
    action types' evidence_data is a lightweight fix_check identifier, not
    Theme Guardian's full file content — confirms passthrough is identical
    regardless of shape, never reshaping or filtering evidence_data."""
    env = _run(
        _seed_pending_approval(
            category=IssueCategory.DISCOUNT.value,
            description="5 discount codes are simultaneously active and stackable.",
            evidence_data={"fix_check": "duplicate_stackable_discount", "discount_ids": ["A", "B"]},
            action_type="DEACTIVATE_DUPLICATE_DISCOUNT",
        )
    )
    _override(env)
    try:
        response = client.get("/api/v1/approvals")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    item = response.json()["data"][0]
    assert item["description"] == env.issue.description
    assert item["evidence_data"] == {"fix_check": "duplicate_stackable_discount", "discount_ids": ["A", "B"]}


def test_list_pending_approvals_returns_empty_list_when_nothing_pending(client: TestClient):
    store_id = uuid.uuid4()
    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_approval_repository] = lambda: InMemoryApprovalRepository(
        InMemoryCognitiveTaskRepository()
    )
    app.dependency_overrides[get_cognitive_task_repository] = lambda: InMemoryCognitiveTaskRepository()
    app.dependency_overrides[get_issue_repository] = lambda: InMemoryIssueRepository()
    try:
        response = client.get("/api/v1/approvals")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body == {"object": "list", "data": [], "has_more": False, "next_cursor": None}
