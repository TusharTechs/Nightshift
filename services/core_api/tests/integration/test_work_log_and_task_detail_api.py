"""HTTP-level integration test: `GET /api/v1/work-log` and
`GET /api/v1/tasks/{task_id}` (Sprint 3 AI Trust & Execution).

`GET /api/v1/tasks/{task_id}` previously had an ORM-vs-domain-object
`model_dump()` mismatch bug (fixed during Part 2's own smoke-testing): the
route's `_execution_dict`/`_verification_dict`/`_rollback_dict`/`_approval_dict`
helpers now read fields via plain attribute access rather than calling
`.model_dump()` — a method the real SQLAlchemy ORM rows don't have but the
Pydantic `InMemory*` domain objects used here do. To actually exercise that
code path (not just the trivial "no execution yet" case), this test seeds a
FULL execution + verification via the fakes and asserts on every field the
`_execution_dict`/`_verification_dict` helpers assemble, not just a
top-level "verified: true/false".
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.deps import (
    get_approval_repository,
    get_audit_log_repository,
    get_cognitive_task_repository,
    get_current_store_id,
    get_execution_repository,
    get_issue_repository,
    get_rollback_repository,
    get_verification_repository,
)
from app.application.ports import (
    InMemoryApprovalRepository,
    InMemoryAuditLogRepository,
    InMemoryCognitiveTaskRepository,
    InMemoryExecutionRepository,
    InMemoryIssueRepository,
    InMemoryRollbackRepository,
    InMemoryVerificationRepository,
)
from app.domain.models import AuditLogEntry
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
    app.dependency_overrides.pop(get_audit_log_repository, None)
    app.dependency_overrides.pop(get_cognitive_task_repository, None)
    app.dependency_overrides.pop(get_issue_repository, None)
    app.dependency_overrides.pop(get_execution_repository, None)
    app.dependency_overrides.pop(get_verification_repository, None)
    app.dependency_overrides.pop(get_rollback_repository, None)
    app.dependency_overrides.pop(get_approval_repository, None)


def test_work_log_lists_entries_newest_first_in_stripe_style_envelope(client: TestClient):
    store_id = uuid.uuid4()
    audit_logs = InMemoryAuditLogRepository()

    base = datetime(2026, 8, 1, 2, 0, 0, tzinfo=timezone.utc)
    for i, action in enumerate(["TASK_PLANNED", "EXECUTION_COMPLETED", "VERIFICATION_PASSED"]):
        audit_logs.seed(
            AuditLogEntry(
                id=uuid.uuid4(),
                store_id=store_id,
                actor_type="AI_AGENT",
                actor_id="GENERATE_ALT_TEXT",
                action=action,
                rationale=f"entry {i}",
                timestamp=base + timedelta(minutes=i),
            )
        )

    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_audit_log_repository] = lambda: audit_logs

    response = client.get("/api/v1/work-log")

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 3
    # Newest-first ordering.
    assert [entry["action"] for entry in body["data"]] == [
        "VERIFICATION_PASSED",
        "EXECUTION_COMPLETED",
        "TASK_PLANNED",
    ]
    assert body["has_more"] is False


def test_work_log_only_returns_entries_for_the_authenticated_store(client: TestClient):
    store_id = uuid.uuid4()
    other_store_id = uuid.uuid4()
    audit_logs = InMemoryAuditLogRepository()
    now = datetime.now(timezone.utc)

    audit_logs.seed(
        AuditLogEntry(
            id=uuid.uuid4(), store_id=store_id, actor_type="AI_AGENT", actor_id="x",
            action="TASK_PLANNED", rationale="mine", timestamp=now,
        )
    )
    audit_logs.seed(
        AuditLogEntry(
            id=uuid.uuid4(), store_id=other_store_id, actor_type="AI_AGENT", actor_id="x",
            action="TASK_PLANNED", rationale="not mine", timestamp=now,
        )
    )

    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_audit_log_repository] = lambda: audit_logs

    response = client.get("/api/v1/work-log")
    assert response.status_code == 200
    assert len(response.json()["data"]) == 1
    assert response.json()["data"][0]["rationale"] == "mine"


def test_get_task_detail_assembles_full_execution_and_verification_sub_objects(client: TestClient):
    store_id = uuid.uuid4()
    task_id = uuid.uuid4()

    cognitive_tasks = InMemoryCognitiveTaskRepository()
    issues = InMemoryIssueRepository()
    executions = InMemoryExecutionRepository()
    verifications = InMemoryVerificationRepository()
    rollbacks = InMemoryRollbackRepository()
    approvals = InMemoryApprovalRepository(cognitive_tasks)

    issue = _run(
        issues.create(
            store_id=store_id,
            shift_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            category="PRODUCT_QUALITY",
            severity="MEDIUM",
            title="Image missing ALT text on 'Widget'",
            description="Widget's primary image has no ALT text.",
            evidence_data={"fix_check": "missing_alt_text"},
            affected_resources=["gid://shopify/Product/1", "gid://shopify/ProductImage/2"],
            revenue_impact_estimate=50.0,
            confidence_score=0.9,
        )
    )

    task = _run(
        cognitive_tasks.create(
            issue_id=issue.id,
            store_id=store_id,
            shift_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            action_type="GENERATE_ALT_TEXT",
            execution_plan={
                "mutation": "productImageUpdate",
                "product_gid": "gid://shopify/Product/1",
                "image_gid": "gid://shopify/ProductImage/2",
                "new_alt_text": "Widget product photo",
            },
            risk_level="LEVEL_1_SAFE",
            risk_reasoning="deterministic and reversible",
            idempotency_key=f"seed:{task_id}",
            status="SUCCESS",
        )
    )
    # `InMemoryCognitiveTaskRepository.create` mints its own id — capture it.
    task_id = task.id

    execution = _run(
        executions.create(task_id=task_id, store_id=store_id, request_payload={"new_alt_text": "Widget product photo"})
    )
    _run(
        executions.mark_completed(
            execution.id,
            response_payload={"id": "gid://shopify/ProductImage/2", "altText": "Widget product photo"},
            execution_duration_ms=123,
        )
    )
    execution = _run(executions.get_by_id(execution.id))

    verification = _run(
        verifications.create(
            execution_id=execution.id,
            task_id=task_id,
            store_id=store_id,
            status="PASSED",
            method="READ_AFTER_WRITE",
            result_data={"images": {"nodes": [{"id": "gid://shopify/ProductImage/2", "altText": "Widget product photo"}]}},
        )
    )

    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_cognitive_task_repository] = lambda: cognitive_tasks
    app.dependency_overrides[get_issue_repository] = lambda: issues
    app.dependency_overrides[get_execution_repository] = lambda: executions
    app.dependency_overrides[get_verification_repository] = lambda: verifications
    app.dependency_overrides[get_rollback_repository] = lambda: rollbacks
    app.dependency_overrides[get_approval_repository] = lambda: approvals

    response = client.get(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 200
    body = response.json()

    assert body["task_id"] == str(task_id)
    assert body["status"] == "SUCCESS"
    assert body["risk_level"] == "LEVEL_1_SAFE"
    assert body["issue_evidence"] == {"fix_check": "missing_alt_text"}

    # --- Execution sub-object: every field _execution_dict assembles -------
    assert body["execution"]["id"] == str(execution.id)
    assert body["execution"]["task_id"] == str(task_id)
    assert body["execution"]["store_id"] == str(store_id)
    assert body["execution"]["status"] == "COMPLETED"
    assert body["execution"]["request_payload"] == {"new_alt_text": "Widget product photo"}
    assert body["execution"]["response_payload"] == {
        "id": "gid://shopify/ProductImage/2", "altText": "Widget product photo"
    }
    assert body["execution"]["execution_duration_ms"] == 123
    assert body["execution"]["retry_count"] == 0
    assert body["execution"]["started_at"] is not None
    assert body["execution"]["completed_at"] is not None

    # --- Verification sub-object --------------------------------------------
    assert body["verification"]["id"] == str(verification.id)
    assert body["verification"]["execution_id"] == str(execution.id)
    assert body["verification"]["status"] == "PASSED"
    assert body["verification"]["method"] == "READ_AFTER_WRITE"
    assert body["verification"]["result_data"]["images"]["nodes"][0]["altText"] == "Widget product photo"
    assert body["verification"]["verified_at"] is not None

    # No rollback/approval this scenario.
    assert body["rollback"] is None
    assert body["approval"] is None


def test_get_task_detail_returns_404_for_task_belonging_to_a_different_store(client: TestClient):
    store_id = uuid.uuid4()
    other_store_id = uuid.uuid4()

    cognitive_tasks = InMemoryCognitiveTaskRepository()
    issues = InMemoryIssueRepository()
    executions = InMemoryExecutionRepository()
    verifications = InMemoryVerificationRepository()
    rollbacks = InMemoryRollbackRepository()
    approvals = InMemoryApprovalRepository(cognitive_tasks)

    task = _run(
        cognitive_tasks.create(
            issue_id=uuid.uuid4(),
            store_id=other_store_id,
            shift_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            action_type="GENERATE_ALT_TEXT",
            execution_plan={},
            risk_level="LEVEL_1_SAFE",
            risk_reasoning="x",
            idempotency_key="seed:other",
            status="SUCCESS",
        )
    )

    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_issue_repository] = lambda: issues
    app.dependency_overrides[get_cognitive_task_repository] = lambda: cognitive_tasks
    app.dependency_overrides[get_execution_repository] = lambda: executions
    app.dependency_overrides[get_verification_repository] = lambda: verifications
    app.dependency_overrides[get_rollback_repository] = lambda: rollbacks
    app.dependency_overrides[get_approval_repository] = lambda: approvals

    response = client.get(f"/api/v1/tasks/{task.id}")
    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"
