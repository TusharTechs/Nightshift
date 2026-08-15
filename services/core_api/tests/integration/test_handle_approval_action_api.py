"""HTTP-level integration test: `POST /api/v1/approvals/{id}/action`
(Sprint 3 AI Trust & Execution), following `test_shifts_api_endpoint.py`'s
`TestClient` + `app.dependency_overrides` pattern.

`get_handle_approval_action_use_case` is overridden directly (rather than
overriding each of its constituent repository providers separately) so the
test can hold onto the exact same `InMemory*` instances the route will
mutate and assert against them afterward — mirroring how
`test_shifts_api_endpoint.py` overrides `get_shift_report_repository`
directly for the same reason.

Test functions are plain `def` (matching this repo's existing HTTP
integration test convention) rather than `async def` — the `InMemory*`
fakes' methods are coroutines, so seeding/assertions run them via a small
`_run()` `asyncio.run()` helper instead of collecting these as
pytest-asyncio async tests, keeping `TestClient`'s own (thread-portal-based)
sync HTTP call as the only thing actually exercising the ASGI app.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_handle_approval_action_use_case
from app.application.ports import (
    InMemoryApprovalRepository,
    InMemoryAuditLogRepository,
    InMemoryCognitiveTaskRepository,
    InMemoryIssueRepository,
    InMemoryShiftRepository,
    InMemoryTaskDispatcher,
)
from app.application.use_cases.handle_approval_action import HandleApprovalAction
from app.domain.enums import IssueCategory, IssueSeverity
from app.domain.models import Shift
from app.main import app

IDEMPOTENCY_HEADERS = {"Idempotency-Key": "test-idempotency-key-1"}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


async def _seed(*, expires_at: datetime | None = None, approval_status: str = "PENDING") -> SimpleNamespace:
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)

    issues = InMemoryIssueRepository()
    cognitive_tasks = InMemoryCognitiveTaskRepository()
    approvals = InMemoryApprovalRepository(cognitive_tasks)
    shifts = InMemoryShiftRepository()
    audit_logs = InMemoryAuditLogRepository()
    dispatcher = InMemoryTaskDispatcher()

    shifts.seed(Shift(id=shift_id, store_id=store_id, shift_number=1, started_at=now, created_at=now))

    issue = await issues.create(
        store_id=store_id,
        shift_id=shift_id,
        agent_id=uuid.uuid4(),
        category=IssueCategory.PRODUCT_QUALITY.value,
        severity=IssueSeverity.MEDIUM.value,
        title="Image missing ALT text on 'Widget'",
        description="Widget's primary image has no ALT text.",
        evidence_data={"fix_check": "missing_alt_text"},
        affected_resources=["gid://shopify/Product/1", "gid://shopify/ProductImage/2"],
        revenue_impact_estimate=50.0,
        confidence_score=0.9,
    )

    task = await cognitive_tasks.create(
        issue_id=issue.id,
        store_id=store_id,
        shift_id=shift_id,
        agent_id=uuid.uuid4(),
        action_type="GENERATE_ALT_TEXT",
        execution_plan={
            "mutation": "productImageUpdate",
            "items": [
                {
                    "product_gid": "gid://shopify/Product/1",
                    "image_gid": "gid://shopify/ProductImage/2",
                    "new_alt_text": "Widget product photo",
                    "before_state": {"alt_text": None},
                    "rollback": {"mutation": "productImageUpdate", "alt_text": None},
                }
            ],
        },
        risk_level="LEVEL_2_MODERATE",
        risk_reasoning="test",
        idempotency_key=f"{issue.id}:GENERATE_ALT_TEXT",
        status="PENDING_APPROVAL",
    )

    approval = await approvals.create(
        task_id=task.id, issue_id=issue.id, store_id=store_id, expires_at=expires_at
    )
    if approval_status != "PENDING":
        await approvals.decide(approval.id, status=approval_status, decided_at=now)
        approval = await approvals.get_by_id(approval.id)

    use_case = HandleApprovalAction(
        approvals=approvals,
        cognitive_tasks=cognitive_tasks,
        issues=issues,
        shifts=shifts,
        audit_logs=audit_logs,
        dispatch_execution=dispatcher.dispatch_execute_cognitive_task,
    )

    return SimpleNamespace(
        store_id=store_id,
        shift_id=shift_id,
        issue=issue,
        task=task,
        approval=approval,
        issues=issues,
        cognitive_tasks=cognitive_tasks,
        approvals=approvals,
        shifts=shifts,
        audit_logs=audit_logs,
        dispatcher=dispatcher,
        use_case=use_case,
    )


def _override(use_case: HandleApprovalAction):
    app.dependency_overrides[get_handle_approval_action_use_case] = lambda: use_case


def _clear_override():
    app.dependency_overrides.pop(get_handle_approval_action_use_case, None)


def test_approve_action_returns_executing_and_dispatches_execution(client: TestClient):
    env = _run(_seed())
    _override(env.use_case)
    try:
        response = client.post(
            f"/api/v1/approvals/{env.approval.id}/action",
            json={"action": "APPROVE"},
            headers=IDEMPOTENCY_HEADERS,
        )
    finally:
        _clear_override()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "EXECUTING"
    assert env.task.id in env.dispatcher.dispatched_execute_task_ids


def test_approve_with_execution_override_params_modifies_execution_plan(client: TestClient):
    env = _run(_seed())
    _override(env.use_case)
    try:
        response = client.post(
            f"/api/v1/approvals/{env.approval.id}/action",
            json={"action": "APPROVE", "execution_override_params": {"new_alt_text": "custom merchant text"}},
            headers=IDEMPOTENCY_HEADERS,
        )
    finally:
        _clear_override()

    assert response.status_code == 200
    assert response.json()["status"] == "EXECUTING"

    updated_task = _run(env.cognitive_tasks.get_by_id(env.task.id))
    assert updated_task.execution_plan["items"][0]["new_alt_text"] == "custom merchant text"


def test_reject_action_without_rejection_reason_returns_400(client: TestClient):
    env = _run(_seed())
    _override(env.use_case)
    try:
        response = client.post(
            f"/api/v1/approvals/{env.approval.id}/action",
            json={"action": "REJECT"},
            headers=IDEMPOTENCY_HEADERS,
        )
    finally:
        _clear_override()

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_APPROVAL_ACTION"


def test_reject_action_with_valid_reason_returns_200_and_reopens_issue(client: TestClient):
    env = _run(_seed())
    _override(env.use_case)
    try:
        response = client.post(
            f"/api/v1/approvals/{env.approval.id}/action",
            json={"action": "REJECT", "rejection_reason": "Not accurate for this product"},
            headers=IDEMPOTENCY_HEADERS,
        )
    finally:
        _clear_override()

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"

    refreshed_issue = _run(env.issues.get_by_id(env.issue.id))
    assert refreshed_issue.status == "OPEN"


def test_defer_action_returns_pending_and_extends_expiry(client: TestClient):
    env = _run(_seed())
    original_expires_at = env.approval.expires_at
    _override(env.use_case)
    try:
        response = client.post(
            f"/api/v1/approvals/{env.approval.id}/action",
            json={"action": "DEFER"},
            headers=IDEMPOTENCY_HEADERS,
        )
    finally:
        _clear_override()

    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"

    refreshed_approval = _run(env.approvals.get_by_id(env.approval.id))
    assert refreshed_approval.expires_at > original_expires_at


def test_action_on_already_decided_approval_returns_409(client: TestClient):
    env = _run(_seed(approval_status="APPROVED"))
    _override(env.use_case)
    try:
        response = client.post(
            f"/api/v1/approvals/{env.approval.id}/action",
            json={"action": "APPROVE"},
            headers=IDEMPOTENCY_HEADERS,
        )
    finally:
        _clear_override()

    assert response.status_code == 409
    assert response.json()["code"] == "APPROVAL_ALREADY_DECIDED"


def test_action_on_expired_approval_returns_409(client: TestClient):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    env = _run(_seed(expires_at=past))
    _override(env.use_case)
    try:
        response = client.post(
            f"/api/v1/approvals/{env.approval.id}/action",
            json={"action": "APPROVE"},
            headers=IDEMPOTENCY_HEADERS,
        )
    finally:
        _clear_override()

    assert response.status_code == 409
    assert response.json()["code"] == "TASK_APPROVAL_EXPIRED"


def test_missing_idempotency_key_header_returns_422_per_fastapi_default_validation(client: TestClient):
    # NOTE: the router docstring anticipates this as "already 422s if
    # missing" (FastAPI's own required-Header validation, not a custom
    # MissingIdempotencyKeyProblem 400) — confirmed here against the real
    # app rather than assumed.
    env = _run(_seed())
    _override(env.use_case)
    try:
        response = client.post(
            f"/api/v1/approvals/{env.approval.id}/action",
            json={"action": "APPROVE"},
        )
    finally:
        _clear_override()

    assert response.status_code == 422
