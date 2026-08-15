"""Integration test: `VerifyExecution` failure path — Sprint 3 AI Trust &
Execution Verify/Rollback steps, wired against `InMemory*` fakes only (no
database, no HTTP).

Builds an `Execution` + `CognitiveTask` pair directly via the fakes
(planning is skipped entirely — this test is scoped to Verify/Rollback).
The fake Shopify client's `fetch_product_state` deliberately returns a
product whose ALT text does NOT match the execution plan's `new_alt_text`,
simulating a write that silently failed (or was overwritten) between the
mutation and the read-after-write check — the "hard axiom (never violated):
ALWAYS re-query Shopify after a mutation" this use case's own docstring
describes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.application.ports import (
    InMemoryAuditLogRepository,
    InMemoryCognitiveTaskRepository,
    InMemoryExecutionRepository,
    InMemoryIssueRepository,
    InMemoryRollbackRepository,
    InMemoryShiftRepository,
    InMemoryVerificationRepository,
)
from app.application.use_cases.rollback_cognitive_task import RollbackCognitiveTask
from app.application.use_cases.verify_execution import VerifyExecution
from app.domain.enums import IssueCategory, IssueSeverity

PRODUCT_GID = "gid://shopify/Product/1"
IMAGE_GID = "gid://shopify/ProductImage/2"
NEW_ALT_TEXT = "Widget product photo"


class _FakeShopifyClientWithStaleReadAfterWrite:
    """Simulates a mutation whose write never actually took effect: the
    read-after-write query always returns the OLD (empty) ALT text,
    regardless of what was written — so verification must fail. Rollback's
    own mutation call succeeds normally (returns without raising), so the
    rollback path itself can be exercised end-to-end."""

    async def fetch_product_state(self, *, product_gid: str) -> dict[str, Any]:
        return {
            "id": product_gid,
            "descriptionHtml": None,
            "images": {"nodes": [{"id": IMAGE_GID, "altText": ""}]},  # stale — doesn't match NEW_ALT_TEXT
        }

    async def update_product_image_alt_text(self, *, product_gid: str, image_gid: str, alt_text: str) -> dict[str, Any]:
        return {"id": image_gid, "altText": alt_text}

    async def update_product_description(self, *, product_gid: str, description_html: str) -> dict[str, Any]:
        return {"id": product_gid, "descriptionHtml": description_html}


async def test_verify_execution_failure_rolls_back_and_reopens_issue():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)

    issues = InMemoryIssueRepository()
    cognitive_tasks = InMemoryCognitiveTaskRepository()
    executions = InMemoryExecutionRepository()
    verifications = InMemoryVerificationRepository()
    rollbacks = InMemoryRollbackRepository()
    shifts = InMemoryShiftRepository()
    audit_logs = InMemoryAuditLogRepository()
    fake_shopify = _FakeShopifyClientWithStaleReadAfterWrite()

    issue = await issues.create(
        store_id=store_id,
        shift_id=shift_id,
        agent_id=uuid.uuid4(),
        category=IssueCategory.PRODUCT_QUALITY.value,
        severity=IssueSeverity.MEDIUM.value,
        title="Image missing ALT text on 'Widget'",
        description="Widget's primary image has no ALT text.",
        evidence_data={"fix_check": "missing_alt_text"},
        affected_resources=[PRODUCT_GID, IMAGE_GID],
        revenue_impact_estimate=50.0,
        confidence_score=0.9,
    )

    execution_plan = {
        "mutation": "productImageUpdate",
        "items": [
            {
                "product_gid": PRODUCT_GID,
                "image_gid": IMAGE_GID,
                "new_alt_text": NEW_ALT_TEXT,
                "before_state": {"alt_text": None},
                "rollback": {"mutation": "productImageUpdate", "alt_text": None},
            }
        ],
    }
    task = await cognitive_tasks.create(
        issue_id=issue.id,
        store_id=store_id,
        shift_id=shift_id,
        agent_id=uuid.uuid4(),
        action_type="GENERATE_ALT_TEXT",
        execution_plan=execution_plan,
        risk_level="LEVEL_1_SAFE",
        risk_reasoning="test",
        idempotency_key=f"{issue.id}:GENERATE_ALT_TEXT",
        status="VERIFYING",
    )

    execution = await executions.create(task_id=task.id, store_id=store_id, request_payload=execution_plan)
    await executions.mark_completed(execution.id, response_payload={"id": IMAGE_GID, "altText": NEW_ALT_TEXT}, execution_duration_ms=5)

    rollback_cognitive_task = RollbackCognitiveTask(
        executions=executions,
        rollbacks=rollbacks,
        cognitive_tasks=cognitive_tasks,
        audit_logs=audit_logs,
        shopify_client=fake_shopify,
    )
    verify_execution = VerifyExecution(
        cognitive_tasks=cognitive_tasks,
        executions=executions,
        verifications=verifications,
        issues=issues,
        shifts=shifts,
        audit_logs=audit_logs,
        rollback_cognitive_task=rollback_cognitive_task,
        shopify_client=fake_shopify,
    )

    verification = await verify_execution.execute(execution.id)

    assert verification.status == "FAILED"

    rollback = await rollbacks.get_by_execution_id(execution.id)
    assert rollback is not None
    assert rollback.status == "COMPLETED"

    refreshed_task = await cognitive_tasks.get_by_id(task.id)
    assert refreshed_task.status == "FAILED"

    refreshed_issue = await issues.get_by_id(issue.id)
    assert refreshed_issue.status == "OPEN"

    audit_actions = {entry.action for entry in await audit_logs.get_for_task(task.id)}
    assert "VERIFICATION_FAILED" in audit_actions
    assert "ROLLBACK_COMPLETED" in audit_actions
