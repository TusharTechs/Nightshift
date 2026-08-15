"""Integration test: Checkout Specialist — Duplicate Discount lifecycle,
end to end (Sprint 4 Step 2). Mirrors
`test_plan_cognitive_tasks_use_case.py`'s and
`test_verify_execution_rollback.py`'s own structure/conventions exactly,
but wired with the real `CheckoutSpecialistAgent` (not a fake) through the
real agent registry (Sprint 4 Step 1), proving Plan -> Assess Risk ->
Approval-or-Auto-Execute -> Execute -> Verify -> Rollback all work
end-to-end for the new DISCOUNT-category action type, with no database, no
HTTP (a tiny in-process fake Shopify client stands in for both).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from app.application.ports import (
    InMemoryApprovalRepository,
    InMemoryAuditLogRepository,
    InMemoryCognitiveTaskRepository,
    InMemoryExecutionRepository,
    InMemoryIssueRepository,
    InMemoryRollbackRepository,
    InMemoryShiftRepository,
    InMemoryVerificationRepository,
)
from app.application.use_cases.execute_cognitive_task import ExecuteCognitiveTask
from app.application.use_cases.plan_cognitive_tasks import PlanCognitiveTasks
from app.application.use_cases.rollback_cognitive_task import RollbackCognitiveTask
from app.application.use_cases.verify_execution import VerifyExecution
from app.domain.agents.checkout_specialist import CheckoutSpecialistAgent
from app.domain.enums import ApprovalStatus, IssueCategory, IssueSeverity, RiskLevel, TaskStatus
from app.domain.models import Issue, Shift, Store

KEEP_DISCOUNT_ID = "gid://shopify/DiscountCodeNode/1"
DUPLICATE_DISCOUNT_ID = "gid://shopify/DiscountCodeNode/2"


class _FakeDiscountShopifyClient:
    """In-process stand-in — tracks each discount's status so a successful
    deactivate+read-after-write reaches PASSED, exactly like a real,
    well-behaved Shopify round trip (mirrors
    `test_plan_cognitive_tasks_use_case.py`'s own `_FakeShopifyClient`)."""

    def __init__(self) -> None:
        self._status: dict[str, str] = {KEEP_DISCOUNT_ID: "ACTIVE", DUPLICATE_DISCOUNT_ID: "ACTIVE"}
        self.deactivate_calls: list[str] = []
        self.activate_calls: list[str] = []

    async def deactivate_discount_code(self, *, discount_id: str) -> dict[str, Any]:
        self.deactivate_calls.append(discount_id)
        self._status[discount_id] = "EXPIRED"
        return {"id": discount_id, "codeDiscount": {"status": "EXPIRED"}}

    async def activate_discount_code(self, *, discount_id: str) -> dict[str, Any]:
        self.activate_calls.append(discount_id)
        self._status[discount_id] = "ACTIVE"
        return {"id": discount_id, "codeDiscount": {"status": "ACTIVE"}}

    async def fetch_discount_state(self, *, discount_id: str) -> dict[str, Any]:
        return {"id": discount_id, "status": self._status.get(discount_id, "ACTIVE")}


class _FakeDiscountShopifyClientWithStaleReadAfterWrite:
    """Simulates a mutation whose write never actually took effect: the
    read-after-write query always reports ACTIVE regardless of what was
    deactivated, so verification must fail and roll back."""

    def __init__(self) -> None:
        self.activate_calls: list[str] = []

    async def deactivate_discount_code(self, *, discount_id: str) -> dict[str, Any]:
        return {"id": discount_id, "codeDiscount": {"status": "EXPIRED"}}

    async def activate_discount_code(self, *, discount_id: str) -> dict[str, Any]:
        self.activate_calls.append(discount_id)
        return {"id": discount_id, "codeDiscount": {"status": "ACTIVE"}}

    async def fetch_discount_state(self, *, discount_id: str) -> dict[str, Any]:
        return {"id": discount_id, "status": "ACTIVE"}  # stale — never reflects the deactivation


def _store(store_id: uuid.UUID, **overrides) -> Store:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=store_id,
        organization_id=uuid.uuid4(),
        shopify_domain="acme.myshopify.com",
        myshopify_domain="acme.myshopify.com",
        store_name="Acme",
        is_autonomous_execution_enabled=True,
        autonomy_level=1,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Store(**defaults)


def _shift(shift_id: uuid.UUID, store_id: uuid.UUID) -> Shift:
    now = datetime.now(timezone.utc)
    return Shift(id=shift_id, store_id=store_id, shift_number=1, started_at=now, created_at=now)


def _duplicate_discount_issue(store_id: uuid.UUID, shift_id: uuid.UUID) -> Issue:
    now = datetime.now(timezone.utc)
    return Issue(
        id=uuid.uuid4(),
        store_id=store_id,
        shift_id=shift_id,
        agent_id=uuid.uuid4(),
        category=IssueCategory.DISCOUNT,
        severity=IssueSeverity.HIGH,
        status="OPEN",
        title="2 overlapping stackable discounts active",
        description="test",
        evidence_data={"fix_check": "duplicate_stackable_discount", "total_sales_usd": 0.0},
        affected_resources=[KEEP_DISCOUNT_ID, DUPLICATE_DISCOUNT_ID],  # [keep, *duplicates]
        revenue_impact_estimate=0.0,
        confidence_score=0.95,
        created_at=now,
        updated_at=now,
    )


def _build_environment(store: Store, fake_shopify) -> SimpleNamespace:
    issues = InMemoryIssueRepository()
    cognitive_tasks = InMemoryCognitiveTaskRepository()
    approvals = InMemoryApprovalRepository(cognitive_tasks)
    shifts = InMemoryShiftRepository()
    audit_logs = InMemoryAuditLogRepository()
    executions = InMemoryExecutionRepository()
    verifications = InMemoryVerificationRepository()
    rollbacks = InMemoryRollbackRepository()

    execute_cognitive_task = ExecuteCognitiveTask(
        cognitive_tasks=cognitive_tasks, executions=executions, audit_logs=audit_logs, shopify_client=fake_shopify
    )
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

    checkout_agent = CheckoutSpecialistAgent()
    planner = PlanCognitiveTasks(
        issues=issues,
        cognitive_tasks=cognitive_tasks,
        approvals=approvals,
        shifts=shifts,
        audit_logs=audit_logs,
        agents={checkout_agent.domain_category: checkout_agent},  # real registry, real agent
        store=store,
        execute_cognitive_task=execute_cognitive_task,
        verify_execution=verify_execution,
        agent_record_ids={checkout_agent.domain_category: uuid.uuid4()},
    )
    return SimpleNamespace(
        issues=issues,
        cognitive_tasks=cognitive_tasks,
        approvals=approvals,
        shifts=shifts,
        audit_logs=audit_logs,
        executions=executions,
        verifications=verifications,
        rollbacks=rollbacks,
        planner=planner,
    )


async def test_duplicate_discount_issue_requires_approval_at_default_autonomy():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id, is_autonomous_execution_enabled=True, autonomy_level=1)
    env = _build_environment(store, _FakeDiscountShopifyClient())
    env.shifts.seed(_shift(shift_id, store_id))
    issue = _duplicate_discount_issue(store_id, shift_id)
    env.issues.seed(issue)

    result = await env.planner.execute(shift_id, store_id)

    assert result.tasks_planned == 1
    assert result.pending_approval == 1
    assert result.auto_executed == 0

    tasks = await env.cognitive_tasks.list_for_shift(shift_id)
    task = tasks[0]
    assert task.action_type == "DEACTIVATE_DUPLICATE_DISCOUNT"
    assert task.risk_level == RiskLevel.LEVEL_2_MODERATE
    assert task.status == TaskStatus.PENDING_APPROVAL
    # Only the duplicate is queued for deactivation — never the keeper.
    assert task.execution_plan["items"] == [
        {
            "discount_id": DUPLICATE_DISCOUNT_ID,
            "before_state": {"status": "ACTIVE"},
            "rollback": {"mutation": "discountCodeActivate", "discount_id": DUPLICATE_DISCOUNT_ID},
        }
    ]

    approval = await env.approvals.get_by_task_id(task.id)
    assert approval is not None
    assert approval.status == ApprovalStatus.PENDING


async def test_duplicate_discount_issue_auto_executes_and_verifies_when_autonomy_level_2():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id, is_autonomous_execution_enabled=True, autonomy_level=2)
    fake_shopify = _FakeDiscountShopifyClient()
    env = _build_environment(store, fake_shopify)
    env.shifts.seed(_shift(shift_id, store_id))
    issue = _duplicate_discount_issue(store_id, shift_id)
    env.issues.seed(issue)

    result = await env.planner.execute(shift_id, store_id)

    assert result.auto_executed == 1
    assert result.pending_approval == 0
    assert fake_shopify.deactivate_calls == [DUPLICATE_DISCOUNT_ID]

    tasks = await env.cognitive_tasks.list_for_shift(shift_id)
    task = tasks[0]
    assert task.status == TaskStatus.SUCCESS

    refreshed_issue = await env.issues.get_by_id(issue.id)
    assert refreshed_issue.status == "RESOLVED"

    execution = await env.executions.get_by_task_id(task.id)
    verification = await env.verifications.get_by_execution_id(execution.id)
    assert verification.status == "PASSED"

    audit_actions = {entry.action for entry in await env.audit_logs.get_for_task(task.id)}
    assert {"TASK_PLANNED", "EXECUTION_COMPLETED", "VERIFICATION_PASSED"} <= audit_actions


async def test_verify_failure_for_discount_rolls_back_and_reactivates():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)

    issues = InMemoryIssueRepository()
    cognitive_tasks = InMemoryCognitiveTaskRepository()
    executions = InMemoryExecutionRepository()
    verifications = InMemoryVerificationRepository()
    rollbacks = InMemoryRollbackRepository()
    shifts = InMemoryShiftRepository()
    audit_logs = InMemoryAuditLogRepository()
    fake_shopify = _FakeDiscountShopifyClientWithStaleReadAfterWrite()

    issue = await issues.create(
        store_id=store_id,
        shift_id=shift_id,
        agent_id=uuid.uuid4(),
        category=IssueCategory.DISCOUNT.value,
        severity=IssueSeverity.HIGH.value,
        title="2 overlapping stackable discounts active",
        description="test",
        evidence_data={"fix_check": "duplicate_stackable_discount"},
        affected_resources=[KEEP_DISCOUNT_ID, DUPLICATE_DISCOUNT_ID],
        revenue_impact_estimate=0.0,
        confidence_score=0.95,
    )

    execution_plan = {
        "mutation": "discountCodeDeactivate",
        "items": [
            {
                "discount_id": DUPLICATE_DISCOUNT_ID,
                "before_state": {"status": "ACTIVE"},
                "rollback": {"mutation": "discountCodeActivate", "discount_id": DUPLICATE_DISCOUNT_ID},
            }
        ],
    }
    task = await cognitive_tasks.create(
        issue_id=issue.id,
        store_id=store_id,
        shift_id=shift_id,
        agent_id=uuid.uuid4(),
        action_type="DEACTIVATE_DUPLICATE_DISCOUNT",
        execution_plan=execution_plan,
        risk_level="LEVEL_2_MODERATE",
        risk_reasoning="test",
        idempotency_key=f"{issue.id}:DEACTIVATE_DUPLICATE_DISCOUNT",
        status="VERIFYING",
    )
    execution = await executions.create(task_id=task.id, store_id=store_id, request_payload=execution_plan)
    await executions.mark_completed(
        execution.id,
        response_payload={"id": DUPLICATE_DISCOUNT_ID, "codeDiscount": {"status": "EXPIRED"}},
        execution_duration_ms=5,
    )

    rollback_cognitive_task = RollbackCognitiveTask(
        executions=executions, rollbacks=rollbacks, cognitive_tasks=cognitive_tasks,
        audit_logs=audit_logs, shopify_client=fake_shopify,
    )
    verify_execution = VerifyExecution(
        cognitive_tasks=cognitive_tasks, executions=executions, verifications=verifications,
        issues=issues, shifts=shifts, audit_logs=audit_logs,
        rollback_cognitive_task=rollback_cognitive_task, shopify_client=fake_shopify,
    )

    verification = await verify_execution.execute(execution.id)

    assert verification.status == "FAILED"
    assert fake_shopify.activate_calls == [DUPLICATE_DISCOUNT_ID]  # rollback re-activated it

    rollback = await rollbacks.get_by_execution_id(execution.id)
    assert rollback is not None
    assert rollback.status == "COMPLETED"

    refreshed_task = await cognitive_tasks.get_by_id(task.id)
    assert refreshed_task.status == "FAILED"

    refreshed_issue = await issues.get_by_id(issue.id)
    assert refreshed_issue.status == "OPEN"  # resurfaces next shift, never silently dropped
