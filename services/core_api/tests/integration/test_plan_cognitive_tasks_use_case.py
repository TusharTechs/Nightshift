"""Integration test: `PlanCognitiveTasks` — Sprint 3 AI Trust & Execution,
the full Plan / Assess Risk / Compute Confidence / Merchant Memory /
Request-Approval-or-Auto-Execute lifecycle, wired against every
`InMemory*` fake in `app.application.ports` (no database, no HTTP).

`ProductQualityAgent.propose_action` is exercised directly against real,
seeded `Issue` domain objects — it never touches the LLM client, so a
`_NeverCalledLlmClient` stand-in is injected into the real agent instance
rather than faking the whole agent.

Shopify itself is faked with a tiny local class (no `respx`/HTTP at all,
per this test's scope) whose `fetch_product_state` echoes back whatever
`update_product_image_alt_text`/`update_product_description` most recently
wrote — this lets the synchronous auto-execute path's inline
Execute -> Verify flow actually reach a PASSED verification, exactly like a
real, successful Shopify mutation followed by a read-after-write check
would.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
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
from app.domain.agents.base import ProposedAction
from app.domain.agents.product_quality import ProductQualityAgent
from app.domain.enums import ApprovalStatus, IssueCategory, IssueSeverity, RiskLevel, TaskStatus
from app.domain.models import Approval, CognitiveTask, Issue, Shift, Store


class _NeverCalledLlmClient:
    model_name = "should-never-be-called"

    async def generate_structured(self, *, prompt: str, response_schema):
        raise AssertionError("propose_action must never call the LLM client")


class _FakeShopifyClient:
    """No HTTP at all — a plain in-process stand-in that echoes back
    whatever was most recently written, so a successful mutation always
    verifies as PASSED on the next `fetch_product_state` read, matching a
    real, well-behaved Shopify Admin API round trip."""

    def __init__(self) -> None:
        self._image_alt_text: dict[str, str] = {}
        self._description_html: str | None = None

    async def update_product_image_alt_text(self, *, product_gid: str, image_gid: str, alt_text: str) -> dict[str, Any]:
        self._image_alt_text[image_gid] = alt_text
        return {"id": image_gid, "altText": alt_text}

    async def update_product_description(self, *, product_gid: str, description_html: str) -> dict[str, Any]:
        self._description_html = description_html
        return {"id": product_gid, "descriptionHtml": description_html}

    async def fetch_product_state(self, *, product_gid: str) -> dict[str, Any]:
        return {
            "id": product_gid,
            "descriptionHtml": self._description_html,
            "images": {
                "nodes": [{"id": gid, "altText": alt} for gid, alt in self._image_alt_text.items()]
            },
        }


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


def _issue(store_id: uuid.UUID, shift_id: uuid.UUID, **overrides) -> Issue:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid.uuid4(),
        store_id=store_id,
        shift_id=shift_id,
        agent_id=uuid.uuid4(),
        category=IssueCategory.PRODUCT_QUALITY,
        severity=IssueSeverity.MEDIUM,
        status="OPEN",
        title="Image missing ALT text on 'Widget'",
        description="Widget's primary image has no ALT text.",
        evidence_data={"fix_check": "missing_alt_text"},
        affected_resources=["gid://shopify/Product/1", "gid://shopify/ProductImage/2"],
        revenue_impact_estimate=50.0,
        confidence_score=0.9,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Issue(**defaults)


def _build_environment(store: Store, *, extra_agents: dict[str, Any] | None = None) -> SimpleNamespace:
    issues = InMemoryIssueRepository()
    cognitive_tasks = InMemoryCognitiveTaskRepository()
    approvals = InMemoryApprovalRepository(cognitive_tasks)
    shifts = InMemoryShiftRepository()
    audit_logs = InMemoryAuditLogRepository()
    executions = InMemoryExecutionRepository()
    verifications = InMemoryVerificationRepository()
    rollbacks = InMemoryRollbackRepository()
    fake_shopify = _FakeShopifyClient()
    agent = ProductQualityAgent(client=_NeverCalledLlmClient())

    execute_cognitive_task = ExecuteCognitiveTask(
        cognitive_tasks=cognitive_tasks,
        executions=executions,
        audit_logs=audit_logs,
        shopify_client=fake_shopify,
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

    # Sprint 4 Step 1: agent registry, keyed by domain_category. Tests that
    # want to exercise the multi-agent routing mechanism pass `extra_agents`
    # (see test_registry_only_invokes_the_agent_matching_the_issues_category
    # below) — everything else keeps the single real ProductQualityAgent,
    # matching this use case's real production wiring today.
    agents: dict[str, Any] = {agent.domain_category: agent}
    agent_record_ids: dict[str, uuid.UUID] = {agent.domain_category: uuid.uuid4()}
    if extra_agents:
        for category, extra_agent in extra_agents.items():
            agents[category] = extra_agent
            agent_record_ids[category] = uuid.uuid4()

    planner = PlanCognitiveTasks(
        issues=issues,
        cognitive_tasks=cognitive_tasks,
        approvals=approvals,
        shifts=shifts,
        audit_logs=audit_logs,
        agents=agents,
        store=store,
        execute_cognitive_task=execute_cognitive_task,
        verify_execution=verify_execution,
        agent_record_ids=agent_record_ids,
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
        fake_shopify=fake_shopify,
        planner=planner,
    )


async def test_level_1_safe_issue_auto_executes_synchronously_and_reaches_success():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id, is_autonomous_execution_enabled=True, autonomy_level=1)
    shift = _shift(shift_id, store_id)
    env = _build_environment(store)
    env.shifts.seed(shift)

    issue = _issue(store_id, shift_id, evidence_data={"fix_check": "missing_alt_text"})
    env.issues.seed(issue)

    result = await env.planner.execute(shift_id, store_id)
    assert result.tasks_planned == 1
    assert result.auto_executed == 1
    assert result.pending_approval == 0

    tasks = await env.cognitive_tasks.list_for_shift(shift_id)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.risk_level == RiskLevel.LEVEL_1_SAFE
    assert task.status == TaskStatus.SUCCESS

    refreshed_issue = await env.issues.get_by_id(issue.id)
    assert refreshed_issue.status == "RESOLVED"

    execution = await env.executions.get_by_task_id(task.id)
    assert execution is not None
    assert execution.status == "COMPLETED"

    verification = await env.verifications.get_by_execution_id(execution.id)
    assert verification is not None
    assert verification.status == "PASSED"

    audit_actions = {entry.action for entry in await env.audit_logs.get_for_task(task.id)}
    assert {"TASK_PLANNED", "EXECUTION_COMPLETED", "VERIFICATION_PASSED"} <= audit_actions


async def test_level_2_moderate_issue_with_autonomy_level_1_requires_approval():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id, is_autonomous_execution_enabled=True, autonomy_level=1)
    shift = _shift(shift_id, store_id)
    env = _build_environment(store)
    env.shifts.seed(shift)

    issue = _issue(
        store_id,
        shift_id,
        title="Thin product description on 'Widget'",
        description="Widget's description is only 10 words long.",
        evidence_data={"fix_check": "thin_description"},
        affected_resources=["gid://shopify/Product/1"],
    )
    env.issues.seed(issue)

    result = await env.planner.execute(shift_id, store_id)
    assert result.tasks_planned == 1
    assert result.auto_executed == 0
    assert result.pending_approval == 1

    tasks = await env.cognitive_tasks.list_for_shift(shift_id)
    task = tasks[0]
    assert task.risk_level == RiskLevel.LEVEL_2_MODERATE
    assert task.status == TaskStatus.PENDING_APPROVAL

    approval = await env.approvals.get_by_task_id(task.id)
    assert approval is not None
    assert approval.status == ApprovalStatus.PENDING

    refreshed_issue = await env.issues.get_by_id(issue.id)
    assert refreshed_issue.status == "AWAITING_APPROVAL"

    refreshed_shift = await env.shifts.get_by_id(shift_id)
    assert refreshed_shift.pending_approvals_count == 1


async def test_merchant_memory_override_forces_approval_despite_level_1_safe():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id, is_autonomous_execution_enabled=True, autonomy_level=1)
    shift = _shift(shift_id, store_id)
    env = _build_environment(store)
    env.shifts.seed(shift)

    now = datetime.now(timezone.utc)
    # Seed 2 prior REJECTED approvals for GENERATE_ALT_TEXT at this store —
    # the merchant-memory rejection threshold (>=2).
    for _ in range(2):
        seed_task = CognitiveTask(
            id=uuid.uuid4(),
            issue_id=uuid.uuid4(),
            store_id=store_id,
            shift_id=shift_id,
            agent_id=uuid.uuid4(),
            action_type="GENERATE_ALT_TEXT",
            execution_plan={},
            risk_level=RiskLevel.LEVEL_1_SAFE,
            risk_reasoning="seed",
            status=TaskStatus.FAILED,
            idempotency_key=f"seed:{uuid.uuid4()}",
            created_at=now,
            updated_at=now,
        )
        env.cognitive_tasks.seed(seed_task)
        env.approvals.seed(
            Approval(
                id=uuid.uuid4(),
                task_id=seed_task.id,
                issue_id=seed_task.issue_id,
                store_id=store_id,
                status="REJECTED",
                expires_at=now + timedelta(hours=24),
                decided_at=now,
                created_at=now,
            )
        )

    new_issue = _issue(store_id, shift_id, evidence_data={"fix_check": "missing_alt_text"})
    env.issues.seed(new_issue)

    result = await env.planner.execute(shift_id, store_id)
    assert result.auto_executed == 0
    assert result.pending_approval == 1

    tasks = await env.cognitive_tasks.list_for_shift(shift_id)
    new_task = next(t for t in tasks if t.issue_id == new_issue.id)
    assert new_task.risk_level == RiskLevel.LEVEL_1_SAFE
    assert new_task.status == TaskStatus.PENDING_APPROVAL

    approval = await env.approvals.get_by_task_id(new_task.id)
    assert approval is not None
    assert approval.status == ApprovalStatus.PENDING


async def test_planning_is_idempotent_and_never_creates_a_duplicate_task_for_the_same_issue():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id, is_autonomous_execution_enabled=True, autonomy_level=1)
    shift = _shift(shift_id, store_id)
    env = _build_environment(store)
    env.shifts.seed(shift)

    issue = _issue(store_id, shift_id, evidence_data={"fix_check": "missing_alt_text"})
    env.issues.seed(issue)

    await env.planner.execute(shift_id, store_id)
    tasks_after_first_run = await env.cognitive_tasks.list_for_shift(shift_id)
    assert len(tasks_after_first_run) == 1

    # Force the issue back to OPEN (simulating a resurfaced/re-run planning
    # pass) so the idempotency-key guard inside `_plan_one_issue` — rather
    # than the "no longer OPEN" issue filter — is what's actually exercised.
    await env.issues.update_status(issue.id, "OPEN")

    await env.planner.execute(shift_id, store_id)
    tasks_after_second_run = await env.cognitive_tasks.list_for_shift(shift_id)
    assert len(tasks_after_second_run) == 1
    assert tasks_after_second_run[0].id == tasks_after_first_run[0].id


# --- Sprint 4 Step 1: Agent registry -----------------------------------------


class _FakeCheckoutAgent:
    """Minimal stand-in for a future specialist (e.g. Step 2's Checkout
    Specialist) — proves `PlanCognitiveTasks` routes an issue to whichever
    registered agent's `domain_category` matches the issue's own category,
    not just to a single hardcoded agent."""

    identifier = "fake-checkout-agent"
    domain_category = "CHECKOUT"

    def __init__(self) -> None:
        self.propose_action_calls: list[uuid.UUID] = []

    async def analyze(self, inspection_data):  # pragma: no cover - not exercised by planning
        raise AssertionError("analyze() is not part of the Plan step and should never be called here")

    def propose_action(self, issue):
        self.propose_action_calls.append(issue.id)
        return ProposedAction(
            action_type="DEACTIVATE_DUPLICATE_DISCOUNT",
            execution_plan={"mutation": "discountCodeDeactivate", "items": []},
        )


async def test_registry_only_invokes_the_agent_matching_the_issues_category():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id, is_autonomous_execution_enabled=True, autonomy_level=1)
    shift = _shift(shift_id, store_id)
    fake_checkout_agent = _FakeCheckoutAgent()
    env = _build_environment(store, extra_agents={"CHECKOUT": fake_checkout_agent})
    env.shifts.seed(shift)

    product_quality_issue = _issue(store_id, shift_id, evidence_data={"fix_check": "missing_alt_text"})
    checkout_issue = _issue(
        store_id,
        shift_id,
        category=IssueCategory.CHECKOUT,
        title="Duplicate stackable discount code detected",
        description="Two overlapping 50% off codes are both active.",
        evidence_data={},
        affected_resources=["gid://shopify/DiscountCodeNode/1"],
    )
    env.issues.seed(product_quality_issue)
    env.issues.seed(checkout_issue)

    result = await env.planner.execute(shift_id, store_id)

    # The fake CHECKOUT agent was invoked exactly once, only for the
    # CHECKOUT-categorized issue — never for the PRODUCT_QUALITY one.
    assert fake_checkout_agent.propose_action_calls == [checkout_issue.id]

    tasks = await env.cognitive_tasks.list_for_shift(shift_id)
    assert result.tasks_planned == 2
    action_types_by_issue = {task.issue_id: task.action_type for task in tasks}
    assert action_types_by_issue[checkout_issue.id] == "DEACTIVATE_DUPLICATE_DISCOUNT"
    assert action_types_by_issue[product_quality_issue.id] == "GENERATE_ALT_TEXT"


async def test_issue_with_no_registered_agent_for_its_category_stays_untouched():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id, is_autonomous_execution_enabled=True, autonomy_level=1)
    shift = _shift(shift_id, store_id)
    env = _build_environment(store)  # no CHECKOUT agent registered
    env.shifts.seed(shift)

    checkout_issue = _issue(
        store_id,
        shift_id,
        category=IssueCategory.CHECKOUT,
        title="Duplicate stackable discount code detected",
        evidence_data={},
        affected_resources=["gid://shopify/DiscountCodeNode/1"],
    )
    env.issues.seed(checkout_issue)

    result = await env.planner.execute(shift_id, store_id)

    assert result.tasks_planned == 0
    tasks = await env.cognitive_tasks.list_for_shift(shift_id)
    assert tasks == []
    refreshed_issue = await env.issues.get_by_id(checkout_issue.id)
    assert refreshed_issue.status == "OPEN"
