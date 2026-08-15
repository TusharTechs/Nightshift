"""Integration tests: Theme Guardian's guided restore + Tracking
Specialist's script-tag recreate lifecycles, end to end (Sprint 4 Step 3).
Mirrors `test_checkout_specialist_discount_lifecycle.py`'s structure: real
agents through the real agent registry, in-memory repositories, a tiny
in-process fake Shopify client — no database, no HTTP.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
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
from app.domain.agents.theme_guardian import ThemeGuardianAgent
from app.domain.agents.tracking_specialist import TrackingSpecialistAgent
from app.domain.enums import ApprovalStatus, IssueCategory, IssueSeverity, RiskLevel, TaskStatus
from app.domain.models import Issue, Shift, Store
from app.infrastructure.shopify_client import ThemeWriteAccessDeniedError

THEME_ID = "gid://shopify/OnlineStoreTheme/1"
FILENAME = "sections/main-product.liquid"
BASELINE_CONTENT = "{% render 'buy-buttons' %}"
CORRUPTED_CONTENT = ""
META_PIXEL_SRC = "https://connect.facebook.net/en_US/fbevents.js"


class _FakeThemeShopifyClient:
    """Tracks the live theme file's current content — a merchant applying
    the guided fix via the Theme Editor is simulated by mutating
    `live_content` directly between the "not yet applied" and "applied"
    verify calls, mirroring a human, out-of-band edit.

    `grant_write_exemption` defaults to False (the realistic default for a
    Shopify app installation — see `shopify_client.py`'s own
    `themeFilesUpsert` comment): `restore_theme_file` raises
    `ThemeWriteAccessDeniedError`, exactly like a real store without the
    manually-granted exemption. Set it True to simulate a store that DOES
    have the exemption, where the automated write genuinely applies.
    """

    def __init__(self, *, live_content: str, grant_write_exemption: bool = False) -> None:
        self.shop_domain = "acme.myshopify.com"
        self.live_content = live_content
        self.grant_write_exemption = grant_write_exemption
        self.restore_calls: list[dict[str, Any]] = []

    async def fetch_theme_files(self, *, theme_id: str, filenames: list[str]) -> dict[str, str]:
        return {f: self.live_content for f in filenames}

    async def restore_theme_file(self, *, theme_id: str, filename: str, content: str) -> dict[str, Any]:
        self.restore_calls.append({"theme_id": theme_id, "filename": filename, "content": content})
        if not self.grant_write_exemption:
            raise ThemeWriteAccessDeniedError(
                "themeFilesUpsert rejected: this store's app installation has no write exemption",
                user_errors=[{"code": "ACCESS_DENIED", "message": "no exemption"}],
            )
        self.live_content = content
        return {"filename": filename, "upserted": True, "status": "restored"}


class _FakeTrackingShopifyClient:
    def __init__(self) -> None:
        self.created_ids: list[str] = []
        self.deleted_ids: list[str] = []
        self._live_src: str | None = None

    async def create_script_tag(self, *, src: str, display_scope: str = "ONLINE_STORE") -> dict[str, Any]:
        new_id = f"gid://shopify/ScriptTag/{len(self.created_ids) + 1}"
        self.created_ids.append(new_id)
        self._live_src = src
        return {"id": new_id, "src": src, "display_scope": display_scope}

    async def delete_script_tag(self, *, script_tag_id: str) -> dict[str, Any]:
        self.deleted_ids.append(script_tag_id)
        self._live_src = None
        return {"deleted_script_tag_id": script_tag_id}

    async def fetch_script_tag_state(self, *, src: str) -> dict[str, Any]:
        if self._live_src == src:
            return {"id": self.created_ids[-1], "src": src, "display_scope": "ONLINE_STORE"}
        return {}


def _store(store_id: uuid.UUID, **overrides) -> Store:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=store_id,
        organization_id=uuid.uuid4(),
        shopify_domain="acme.myshopify.com",
        myshopify_domain="acme.myshopify.com",
        store_name="Acme",
        is_autonomous_execution_enabled=True,
        autonomy_level=2,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Store(**defaults)


def _shift(shift_id: uuid.UUID, store_id: uuid.UUID) -> Shift:
    now = datetime.now(timezone.utc)
    return Shift(id=shift_id, store_id=store_id, shift_number=1, started_at=now, created_at=now)


def _theme_diff_issue(store_id: uuid.UUID, shift_id: uuid.UUID) -> Issue:
    now = datetime.now(timezone.utc)
    return Issue(
        id=uuid.uuid4(),
        store_id=store_id,
        shift_id=shift_id,
        agent_id=uuid.uuid4(),
        category=IssueCategory.CHECKOUT,
        severity=IssueSeverity.HIGH,
        status="OPEN",
        title=f"Theme file '{FILENAME}' changed unexpectedly",
        description="test",
        evidence_data={
            "fix_check": "theme_file_diverged_from_baseline",
            "theme_id": THEME_ID,
            "filename": FILENAME,
            "baseline_content": BASELINE_CONTENT,
            "current_content": CORRUPTED_CONTENT,
        },
        affected_resources=[THEME_ID, FILENAME],
        revenue_impact_estimate=0.0,
        confidence_score=0.90,
        created_at=now,
        updated_at=now,
    )


def _tracking_removed_issue(store_id: uuid.UUID, shift_id: uuid.UUID) -> Issue:
    now = datetime.now(timezone.utc)
    return Issue(
        id=uuid.uuid4(),
        store_id=store_id,
        shift_id=shift_id,
        agent_id=uuid.uuid4(),
        category=IssueCategory.PIXEL_TRACKING,
        severity=IssueSeverity.HIGH,
        status="OPEN",
        title="Meta Pixel script tag removed from storefront",
        description="test",
        evidence_data={"fix_check": "tracking_script_removed", "pattern_name": "Meta Pixel"},
        affected_resources=[META_PIXEL_SRC, "ONLINE_STORE", "Meta Pixel"],
        revenue_impact_estimate=0.0,
        confidence_score=0.95,
        created_at=now,
        updated_at=now,
    )


def _build_environment(store: Store, fake_shopify, agent) -> dict:
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
        executions=executions, rollbacks=rollbacks, cognitive_tasks=cognitive_tasks,
        audit_logs=audit_logs, shopify_client=fake_shopify,
    )
    verify_execution = VerifyExecution(
        cognitive_tasks=cognitive_tasks, executions=executions, verifications=verifications,
        issues=issues, shifts=shifts, audit_logs=audit_logs,
        rollback_cognitive_task=rollback_cognitive_task, shopify_client=fake_shopify,
    )
    planner = PlanCognitiveTasks(
        issues=issues, cognitive_tasks=cognitive_tasks, approvals=approvals, shifts=shifts,
        audit_logs=audit_logs, agents={agent.domain_category: agent}, store=store,
        execute_cognitive_task=execute_cognitive_task, verify_execution=verify_execution,
        agent_record_ids={agent.domain_category: uuid.uuid4()},
    )
    return dict(
        issues=issues, cognitive_tasks=cognitive_tasks, approvals=approvals, shifts=shifts,
        audit_logs=audit_logs, executions=executions, verifications=verifications,
        rollbacks=rollbacks, planner=planner, verify_execution=verify_execution,
    )


# --- Theme Guardian: guided restore --------------------------------------


class _FakeLlmClient:
    model_name = "fake-model"

    async def generate_structured(self, *, prompt, response_schema):
        from app.domain.agents.theme_guardian import ThemeDiffExplanationSchema

        return ThemeDiffExplanationSchema(severity="CRITICAL", explanation="Buy Button removed.")


async def test_theme_restore_guide_always_requires_approval_regardless_of_autonomy():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id, is_autonomous_execution_enabled=True, autonomy_level=3)  # max autonomy
    fake_shopify = _FakeThemeShopifyClient(live_content=CORRUPTED_CONTENT)
    agent = ThemeGuardianAgent(client=_FakeLlmClient())
    env = _build_environment(store, fake_shopify, agent)
    env["shifts"].seed(_shift(shift_id, store_id))
    issue = _theme_diff_issue(store_id, shift_id)
    env["issues"].seed(issue)

    result = await env["planner"].execute(shift_id, store_id)

    assert result.pending_approval == 1
    assert result.auto_executed == 0
    tasks = await env["cognitive_tasks"].list_for_shift(shift_id)
    task = tasks[0]
    assert task.action_type == "GENERATE_THEME_RESTORE_GUIDE"
    assert task.risk_level == RiskLevel.LEVEL_3_HIGH
    assert task.status == TaskStatus.PENDING_APPROVAL

    approval = await env["approvals"].get_by_task_id(task.id)
    assert approval.status == ApprovalStatus.PENDING


async def test_theme_restore_guide_execute_falls_back_to_guide_when_shopify_denies_the_write():
    """Default/realistic case: this store's app installation has no
    Shopify-granted `themeFilesUpsert` exemption, so the real restore attempt
    is denied and Execute falls back to the original guided-bundle
    behavior."""
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id)
    fake_shopify = _FakeThemeShopifyClient(live_content=CORRUPTED_CONTENT, grant_write_exemption=False)
    agent = ThemeGuardianAgent(client=_FakeLlmClient())

    from app.application.use_cases.execute_cognitive_task import ExecuteCognitiveTask
    from app.application.ports import InMemoryAuditLogRepository, InMemoryCognitiveTaskRepository, InMemoryExecutionRepository

    cognitive_tasks = InMemoryCognitiveTaskRepository()
    executions = InMemoryExecutionRepository()
    audit_logs = InMemoryAuditLogRepository()

    issue = _theme_diff_issue(store_id, shift_id)
    proposed = agent.propose_action(issue)
    task = await cognitive_tasks.create(
        issue_id=issue.id, store_id=store_id, shift_id=shift_id, agent_id=uuid.uuid4(),
        action_type=proposed.action_type, execution_plan=proposed.execution_plan,
        risk_level="LEVEL_3_HIGH", risk_reasoning="test",
        idempotency_key=f"{issue.id}:{proposed.action_type}", status="PENDING_APPROVAL",
    )

    execute = ExecuteCognitiveTask(
        cognitive_tasks=cognitive_tasks, executions=executions, audit_logs=audit_logs, shopify_client=fake_shopify
    )
    execution = await execute.execute(task.id)

    assert execution.status == "COMPLETED"
    # The real restore attempt genuinely happened (and was denied) — not
    # skipped.
    assert len(fake_shopify.restore_calls) == 1
    bundle = execution.response_payload["items"][0]
    assert bundle["filename"] == FILENAME
    assert bundle["patch_content"] == BASELINE_CONTENT
    assert bundle["status"] == "guide_generated"
    assert "automated_restore_denied_reason" in bundle
    # Unified-admin theme-code page, not the `/editor` Theme Customizer path
    # — confirmed against the live Shopify admin, not assumed from docs.
    assert bundle["theme_editor_url"] == "https://admin.shopify.com/store/acme/themes/1"

    refreshed_task = await cognitive_tasks.get_by_id(task.id)
    assert refreshed_task.status == "VERIFYING"


async def test_theme_restore_guide_execute_performs_real_restore_when_shopify_grants_the_write():
    """A store whose app installation HAS the Shopify-granted exemption gets
    a genuine, automated restore — no guide, no merchant action needed."""
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id)
    fake_shopify = _FakeThemeShopifyClient(live_content=CORRUPTED_CONTENT, grant_write_exemption=True)
    agent = ThemeGuardianAgent(client=_FakeLlmClient())

    from app.application.use_cases.execute_cognitive_task import ExecuteCognitiveTask
    from app.application.ports import InMemoryAuditLogRepository, InMemoryCognitiveTaskRepository, InMemoryExecutionRepository

    cognitive_tasks = InMemoryCognitiveTaskRepository()
    executions = InMemoryExecutionRepository()
    audit_logs = InMemoryAuditLogRepository()

    issue = _theme_diff_issue(store_id, shift_id)
    proposed = agent.propose_action(issue)
    task = await cognitive_tasks.create(
        issue_id=issue.id, store_id=store_id, shift_id=shift_id, agent_id=uuid.uuid4(),
        action_type=proposed.action_type, execution_plan=proposed.execution_plan,
        risk_level="LEVEL_3_HIGH", risk_reasoning="test",
        idempotency_key=f"{issue.id}:{proposed.action_type}", status="PENDING_APPROVAL",
    )

    execute = ExecuteCognitiveTask(
        cognitive_tasks=cognitive_tasks, executions=executions, audit_logs=audit_logs, shopify_client=fake_shopify
    )
    execution = await execute.execute(task.id)

    assert execution.status == "COMPLETED"
    bundle = execution.response_payload["items"][0]
    assert bundle["status"] == "restored"
    assert bundle["upserted"] is True
    assert "automated_restore_denied_reason" not in bundle
    assert fake_shopify.live_content == BASELINE_CONTENT

    refreshed_task = await cognitive_tasks.get_by_id(task.id)
    assert refreshed_task.status == "VERIFYING"

    # Verification confirms the live theme now matches baseline immediately
    # — no merchant action required.
    verify_execution = VerifyExecution(
        cognitive_tasks=cognitive_tasks,
        executions=executions,
        verifications=InMemoryVerificationRepository(),
        issues=InMemoryIssueRepository(),
        shifts=InMemoryShiftRepository(),
        audit_logs=audit_logs,
        rollback_cognitive_task=RollbackCognitiveTask(
            executions=executions, rollbacks=InMemoryRollbackRepository(), cognitive_tasks=cognitive_tasks,
            audit_logs=audit_logs, shopify_client=fake_shopify,
        ),
        shopify_client=fake_shopify,
    )
    verification = await verify_execution.execute(execution.id)
    assert verification.status == "PASSED"


async def test_theme_restore_guide_verify_fails_when_merchant_has_not_yet_applied_the_fix():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id)
    fake_shopify = _FakeThemeShopifyClient(live_content=CORRUPTED_CONTENT)  # not yet fixed
    agent = ThemeGuardianAgent(client=_FakeLlmClient())
    env = _build_environment(store, fake_shopify, agent)
    env["shifts"].seed(_shift(shift_id, store_id))
    issue = _theme_diff_issue(store_id, shift_id)
    env["issues"].seed(issue)

    proposed = agent.propose_action(issue)
    task = await env["cognitive_tasks"].create(
        issue_id=issue.id, store_id=store_id, shift_id=shift_id, agent_id=uuid.uuid4(),
        action_type=proposed.action_type, execution_plan=proposed.execution_plan,
        risk_level="LEVEL_3_HIGH", risk_reasoning="test",
        idempotency_key=f"{issue.id}:{proposed.action_type}", status="PLANNED",
    )
    execution = await env["executions"].create(
        task_id=task.id, store_id=store_id, request_payload=proposed.execution_plan
    )
    await env["executions"].mark_completed(execution.id, response_payload={}, execution_duration_ms=1)

    verification = await env["verify_execution"].execute(execution.id)

    assert verification.status == "FAILED"
    # Rollback is a documented no-op — no live Shopify mutation to revert.
    rollback = await env["rollbacks"].get_by_execution_id(execution.id)
    assert rollback.status == "COMPLETED"
    refreshed_task = await env["cognitive_tasks"].get_by_id(task.id)
    assert refreshed_task.status == "FAILED"
    refreshed_issue = await env["issues"].get_by_id(issue.id)
    assert refreshed_issue.status == "OPEN"  # resurfaces so the next scan can re-offer the guide


async def test_theme_restore_guide_verify_passes_once_merchant_applies_the_fix():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id)
    fake_shopify = _FakeThemeShopifyClient(live_content=CORRUPTED_CONTENT)
    agent = ThemeGuardianAgent(client=_FakeLlmClient())
    env = _build_environment(store, fake_shopify, agent)
    env["shifts"].seed(_shift(shift_id, store_id))
    issue = _theme_diff_issue(store_id, shift_id)
    env["issues"].seed(issue)

    proposed = agent.propose_action(issue)
    task = await env["cognitive_tasks"].create(
        issue_id=issue.id, store_id=store_id, shift_id=shift_id, agent_id=uuid.uuid4(),
        action_type=proposed.action_type, execution_plan=proposed.execution_plan,
        risk_level="LEVEL_3_HIGH", risk_reasoning="test",
        idempotency_key=f"{issue.id}:{proposed.action_type}", status="PLANNED",
    )
    execution = await env["executions"].create(
        task_id=task.id, store_id=store_id, request_payload=proposed.execution_plan
    )
    await env["executions"].mark_completed(execution.id, response_payload={}, execution_duration_ms=1)

    # Merchant applies the guide via the Theme Editor, out of band.
    fake_shopify.live_content = BASELINE_CONTENT

    verification = await env["verify_execution"].execute(execution.id)

    assert verification.status == "PASSED"
    refreshed_task = await env["cognitive_tasks"].get_by_id(task.id)
    assert refreshed_task.status == "SUCCESS"
    refreshed_issue = await env["issues"].get_by_id(issue.id)
    assert refreshed_issue.status == "RESOLVED"


async def test_theme_restore_guide_successful_automated_write_rolls_back_via_real_restore_on_verify_failure():
    """Regression test for a real, confirmed gap: before this fix, a
    genuinely successful automated `themeFilesUpsert` restore still left the
    execution_plan's rollback at the Plan-time placeholder
    `{"mutation": "none"}}` (theme_guardian.py can't know at Plan time
    whether Execute will perform a real write or fall back to a guide).
    If verification then failed for any reason (e.g. Shopify's own
    read-after-write lag on theme files), rollback would falsely report "no
    live Shopify mutation was ever executed" — even though one genuinely
    was, leaving the merchant's live theme with no automated way back to its
    pre-fix state. This test proves `execute_cognitive_task.py` now patches
    a real, executable `theme_restore` rollback mutation whenever the
    automated write actually succeeds, and that a subsequent verify failure
    triggers a genuine, live-reverting rollback — not a false no-op."""
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id)
    fake_shopify = _FakeThemeShopifyClient(live_content=CORRUPTED_CONTENT, grant_write_exemption=True)
    agent = ThemeGuardianAgent(client=_FakeLlmClient())
    env = _build_environment(store, fake_shopify, agent)
    env["shifts"].seed(_shift(shift_id, store_id))
    issue = _theme_diff_issue(store_id, shift_id)
    env["issues"].seed(issue)

    proposed = agent.propose_action(issue)
    task = await env["cognitive_tasks"].create(
        issue_id=issue.id, store_id=store_id, shift_id=shift_id, agent_id=uuid.uuid4(),
        action_type=proposed.action_type, execution_plan=proposed.execution_plan,
        risk_level="LEVEL_3_HIGH", risk_reasoning="test",
        idempotency_key=f"{issue.id}:{proposed.action_type}", status="PENDING_APPROVAL",
    )

    execution = await env["executions"].get_by_task_id(task.id) or await env["executions"].create(
        task_id=task.id, store_id=store_id, request_payload=proposed.execution_plan
    )
    execute = ExecuteCognitiveTask(
        cognitive_tasks=env["cognitive_tasks"], executions=env["executions"],
        audit_logs=env["audit_logs"], shopify_client=fake_shopify,
    )
    execution = await execute.execute(task.id)
    assert execution.status == "COMPLETED"
    assert fake_shopify.live_content == BASELINE_CONTENT  # the automated restore genuinely happened

    # The real assertion this test exists for: the execution_plan's rollback
    # was patched to a real, executable mutation — not left at "none".
    refreshed_task = await env["cognitive_tasks"].get_by_id(task.id)
    patched_rollback = refreshed_task.execution_plan["items"][0]["rollback"]
    assert patched_rollback["mutation"] == "theme_restore"
    assert patched_rollback["content"] == CORRUPTED_CONTENT

    # Something else diverges the live file again right after (e.g. Shopify
    # read-after-write lag, or a genuinely new edit) — verification must
    # fail and trigger a real rollback, not a false "nothing to revert".
    fake_shopify.live_content = "{% comment %} something else changed it {% endcomment %}"

    verification = await env["verify_execution"].execute(execution.id)

    assert verification.status == "FAILED"
    # Two real restore_theme_file calls now: the original fix, then the
    # rollback undoing it.
    assert len(fake_shopify.restore_calls) == 2
    assert fake_shopify.restore_calls[1]["content"] == CORRUPTED_CONTENT
    assert fake_shopify.live_content == CORRUPTED_CONTENT  # genuinely reverted, not a false no-op

    rollback = await env["rollbacks"].get_by_execution_id(execution.id)
    assert rollback.status == "COMPLETED"
    assert rollback.reverted_state["items"][0]["mutation"] == "theme_restore"


# --- Tracking Specialist: script tag recreate ------------------------------


async def test_tracking_script_recreate_auto_executes_captures_live_id_for_rollback():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id, is_autonomous_execution_enabled=True, autonomy_level=2)
    fake_shopify = _FakeTrackingShopifyClient()
    agent = TrackingSpecialistAgent()
    env = _build_environment(store, fake_shopify, agent)
    env["shifts"].seed(_shift(shift_id, store_id))
    issue = _tracking_removed_issue(store_id, shift_id)
    env["issues"].seed(issue)

    result = await env["planner"].execute(shift_id, store_id)

    assert result.auto_executed == 1
    tasks = await env["cognitive_tasks"].list_for_shift(shift_id)
    task = tasks[0]
    assert task.status == TaskStatus.SUCCESS
    assert fake_shopify.created_ids == ["gid://shopify/ScriptTag/1"]

    # The live-assigned id was patched into the persisted execution_plan's
    # rollback dict, not left as the Plan-time placeholder-free version.
    refreshed_task = await env["cognitive_tasks"].get_by_id(task.id)
    assert refreshed_task.execution_plan["items"][0]["rollback"]["script_tag_id"] == "gid://shopify/ScriptTag/1"

    refreshed_issue = await env["issues"].get_by_id(issue.id)
    assert refreshed_issue.status == "RESOLVED"


async def test_tracking_script_recreate_verify_failure_rolls_back_via_delete():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    store = _store(store_id)
    fake_shopify = _FakeTrackingShopifyClient()
    agent = TrackingSpecialistAgent()
    env = _build_environment(store, fake_shopify, agent)
    env["shifts"].seed(_shift(shift_id, store_id))
    issue = _tracking_removed_issue(store_id, shift_id)
    env["issues"].seed(issue)

    proposed = agent.propose_action(issue)
    task = await env["cognitive_tasks"].create(
        issue_id=issue.id, store_id=store_id, shift_id=shift_id, agent_id=uuid.uuid4(),
        action_type=proposed.action_type, execution_plan=proposed.execution_plan,
        risk_level="LEVEL_2_MODERATE", risk_reasoning="test",
        idempotency_key=f"{issue.id}:{proposed.action_type}", status="PLANNED",
    )
    execution = await env["executions"].create(
        task_id=task.id, store_id=store_id, request_payload=proposed.execution_plan
    )
    # Simulate a mutation that "succeeded" per Shopify's response but never
    # actually took effect live (fake client's internal state was never
    # updated) — verify must fail and roll back via the captured id.
    patched_plan = {
        **proposed.execution_plan,
        "items": [
            {**proposed.execution_plan["items"][0], "rollback": {"mutation": "scriptTagDelete", "script_tag_id": "gid://shopify/ScriptTag/99"}}
        ],
    }
    await env["cognitive_tasks"].update_execution_plan(task.id, patched_plan)
    await env["executions"].mark_completed(execution.id, response_payload={}, execution_duration_ms=1)

    verification = await env["verify_execution"].execute(execution.id)

    assert verification.status == "FAILED"
    assert fake_shopify.deleted_ids == ["gid://shopify/ScriptTag/99"]
    rollback = await env["rollbacks"].get_by_execution_id(execution.id)
    assert rollback.status == "COMPLETED"
    refreshed_issue = await env["issues"].get_by_id(issue.id)
    assert refreshed_issue.status == "OPEN"
