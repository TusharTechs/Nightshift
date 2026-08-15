"""Use case: Plan Cognitive Tasks — Sprint 3 AI Trust & Execution, the Plan /
Assess Risk / Compute Confidence / Merchant Memory / Request-Approval-or-
Auto-Execute steps of the lifecycle, for a single already-completed shift.

Framework-agnostic: depends only on Protocol ports (app.application.ports)
and domain objects/functions (risk.py, confidence.py, merchant_memory.py) —
no FastAPI/Celery/SQLAlchemy imports. `workers/tasks/planning.py` constructs
this with real SQL repositories and calls `.execute(...)`.

Synchronous auto-execute design decision (see CONFLICTS.md Sprint 3 Part 2 /
DECISIONS.md): to avoid a race between the immutable Morning Shift Report
snapshot and async execution completion, tasks that do NOT require approval
are executed and verified synchronously, inline, within this same call —
by invoking the injected `execute_cognitive_task`/`verify_execution` use
cases directly (they are plain async classes; calling them in-process is
safe and avoids an unnecessary queue hop for the common case). Only tasks
that DO require approval stay PENDING_APPROVAL; their execution happens
later, truly asynchronously, when the merchant approves
(`HandleApprovalAction` dispatches `tasks.execute_cognitive_task` via
Celery).

Sprint 4 Step 1 — Agent registry refactor (see SPRINT4_AI_WORKFORCE_VISION.md
"Two pieces of shared infrastructure"): this use case previously took a
single hardcoded `product_quality_agent: Agent` constructor argument, which
meant it could only ever plan fixes for one specialist. It now takes
`agents: dict[str, Agent]`, keyed by `Agent.domain_category` (e.g.
"PRODUCT_QUALITY", "CHECKOUT" — the same string values as the
`IssueCategory` enum, which is also the Postgres enum type backing the
`agents.domain_category` column, so no new enum values are needed for future
specialists), plus a matching `agent_record_ids: dict[str, uuid.UUID]` so
each planned task is attributed to the correct row in the `agents` registry
table rather than a single hardcoded id. For a given issue, the agent whose
`domain_category` matches `issue.category` is asked to `propose_action` — if
no agent is registered for that category (or the matching agent has no fix
for this specific issue), the issue is left untouched, exactly as before.
This is intentionally mechanical: it unblocks Step 2+'s new specialists
without changing any planning/risk/approval behavior for Product Quality.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog

from app.application.ports import (
    ApprovalRepository,
    AuditLogRepository,
    CognitiveTaskRepository,
    IssueRepository,
    ShiftRepository,
)
from app.application.use_cases.execute_cognitive_task import ExecuteCognitiveTask
from app.application.use_cases.verify_execution import VerifyExecution
from app.domain.agents.base import Agent
from app.domain.confidence import compute_confidence
from app.domain.merchant_memory import has_repeated_rejection_history
from app.domain.models import Store
from app.domain.risk import assess_risk_level, requires_approval

logger = structlog.get_logger(component="plan_cognitive_tasks")


@dataclass
class PlanningResult:
    tasks_planned: int = 0
    auto_executed: int = 0
    pending_approval: int = 0


class PlanCognitiveTasks:
    def __init__(
        self,
        *,
        issues: IssueRepository,
        cognitive_tasks: CognitiveTaskRepository,
        approvals: ApprovalRepository,
        shifts: ShiftRepository,
        audit_logs: AuditLogRepository,
        agents: dict[str, Agent],
        store: Store,
        execute_cognitive_task: ExecuteCognitiveTask,
        verify_execution: VerifyExecution,
        agent_record_ids: dict[str, uuid.UUID],
    ) -> None:
        self._issues = issues
        self._cognitive_tasks = cognitive_tasks
        self._approvals = approvals
        self._shifts = shifts
        self._audit_logs = audit_logs
        self._agents = agents
        self._store = store
        self._execute_cognitive_task = execute_cognitive_task
        self._verify_execution = verify_execution
        self._agent_record_ids = agent_record_ids

    async def execute(self, shift_id: uuid.UUID, store_id: uuid.UUID) -> PlanningResult:
        result = PlanningResult()
        issues = await self._issues.list_for_shift(shift_id)
        open_issues = [issue for issue in issues if issue.status == "OPEN"]

        for issue in open_issues:
            try:
                await self._plan_one_issue(issue, shift_id=shift_id, store_id=store_id, result=result)
            except Exception as exc:  # noqa: BLE001 — never let one bad proposal kill shift planning
                logger.warning(
                    "plan_cognitive_tasks_issue_failed",
                    issue_id=str(issue.id),
                    shift_id=str(shift_id),
                    status="error",
                    error=str(exc),
                )
                continue

        logger.info(
            "plan_cognitive_tasks_completed",
            shift_id=str(shift_id),
            store_id=str(store_id),
            tasks_planned=result.tasks_planned,
            auto_executed=result.auto_executed,
            pending_approval=result.pending_approval,
            status="success",
        )
        return result

    async def _plan_one_issue(self, issue, *, shift_id: uuid.UUID, store_id: uuid.UUID, result: PlanningResult) -> None:
        category_key = issue.category.value if hasattr(issue.category, "value") else issue.category
        agent = self._agents.get(category_key)
        if agent is None:
            return  # No specialist registered for this issue's category — stays OPEN.

        proposed = agent.propose_action(issue)
        if proposed is None:
            return  # No automated fix path this sprint — issue stays OPEN.

        agent_record_id = self._agent_record_ids.get(category_key)
        if agent_record_id is None:
            # Defensive: an agent is registered but its `agents` table row id
            # wasn't supplied — never plan a task we can't attribute to a
            # real registry row.
            logger.warning(
                "plan_cognitive_tasks_missing_agent_record_id",
                category=category_key,
                issue_id=str(issue.id),
                status="skipped",
            )
            return

        idempotency_key = f"{issue.id}:{proposed.action_type}"
        existing = await self._cognitive_tasks.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return  # Already planned — idempotent against re-running planning for this shift.

        risk_level, risk_reasoning = assess_risk_level(proposed.action_type)

        rejection_count = await self._approvals.count_rejections_for_action_type(
            store_id, proposed.action_type
        )
        merchant_override = has_repeated_rejection_history(rejection_count)

        approval_count = await self._approvals.count_approvals_for_action_type(
            store_id, proposed.action_type
        )
        total_decisions = approval_count + rejection_count
        approval_rate = (approval_count / total_decisions) if total_decisions > 0 else None

        confidence = compute_confidence(
            issue_confidence_score=issue.confidence_score,
            merchant_approval_rate=approval_rate,
            historical_success_rate=None,
            # Sprint 5 Phase 5: only passed when the rate itself is real (not
            # the neutral no-history prior) — enriches the signal's own
            # reasoning sentence with the real approval count so the UI can
            # surface a grounded "based on your N previous approvals" note.
            merchant_approval_count=approval_count if total_decisions > 0 else None,
        )

        needs_approval = requires_approval(risk_level, self._store) or merchant_override

        task = await self._cognitive_tasks.create(
            issue_id=issue.id,
            store_id=store_id,
            shift_id=shift_id,
            agent_id=agent_record_id,
            action_type=proposed.action_type,
            execution_plan=proposed.execution_plan,
            risk_level=risk_level.value,
            risk_reasoning=risk_reasoning,
            idempotency_key=idempotency_key,
            status="PENDING_APPROVAL" if needs_approval else "PLANNED",
            confidence_assessment=confidence.to_dict(),
        )
        result.tasks_planned += 1

        await self._audit_logs.append(
            store_id=store_id,
            shift_id=shift_id,
            task_id=task.id,
            actor_type="AI_AGENT",
            actor_id=proposed.action_type,
            action="TASK_PLANNED",
            rationale=risk_reasoning,
        )

        if needs_approval:
            await self._issues.update_status(issue.id, "AWAITING_APPROVAL")
            await self._approvals.create(task_id=task.id, issue_id=issue.id, store_id=store_id)
            await self._shifts.increment_pending_approvals(shift_id, 1)
            await self._audit_logs.append(
                store_id=store_id,
                shift_id=shift_id,
                task_id=task.id,
                actor_type="AI_AGENT",
                actor_id=proposed.action_type,
                action="APPROVAL_REQUESTED",
                rationale=(
                    "Merchant approval required: "
                    + ("repeated past rejection history for this action type." if merchant_override else risk_reasoning)
                ),
            )
            result.pending_approval += 1
        else:
            execution = await self._execute_cognitive_task.execute(task.id)
            await self._verify_execution.execute(execution.id)
            result.auto_executed += 1
