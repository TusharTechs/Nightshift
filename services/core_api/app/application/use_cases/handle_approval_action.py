"""Use case: Handle Approval Action — Sprint 3 AI Trust & Execution.

Processes a merchant's decision (APPROVE / REJECT / DEFER) on a pending
`Approval`, per `POST /api/v1/approvals/{id}/action`.

Raises `NightShiftProblem` subclasses directly on domain-level failures
(approval not found, already decided, expired, invalid action) — this
codebase has no separate `domain/exceptions.py`; `app/api/errors.py` is
already the shared, framework-agnostic exception vocabulary (plain Python
exceptions, only rendered to an HTTP response at the FastAPI exception
handler registered in `register_error_handlers`), so use cases raising these
directly is consistent with existing precedent rather than a new pattern.

APPROVE dispatches `tasks.execute_cognitive_task` via Celery — this is the
one case in the whole Sprint 3 lifecycle where execution is truly
asynchronous (see `application/use_cases/plan_cognitive_tasks.py`'s
docstring for why the auto-execute path is synchronous instead).

REJECT/decision rows ARE the Merchant Memory substrate `domain/merchant_memory.py`
reads from on the next planning run — no separate memory write needed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import structlog

from app.api.errors import (
    ApprovalAlreadyDecidedProblem,
    ApprovalExpiredProblem,
    ApprovalNotFoundProblem,
    InvalidApprovalActionProblem,
)
from app.application.ports import (
    ApprovalRepository,
    AuditLogRepository,
    CognitiveTaskRepository,
    IssueRepository,
    ShiftRepository,
)
from app.domain.enums import ApprovalAction

logger = structlog.get_logger(component="handle_approval_action")

MIN_REJECTION_REASON_LENGTH = 5
DEFER_EXTENSION_HOURS = 24


@dataclass
class ApprovalActionResult:
    approval_id: uuid.UUID
    task_id: uuid.UUID
    status: str
    message: str
    audit_log_id: uuid.UUID | None


class HandleApprovalAction:
    def __init__(
        self,
        *,
        approvals: ApprovalRepository,
        cognitive_tasks: CognitiveTaskRepository,
        issues: IssueRepository,
        shifts: ShiftRepository,
        audit_logs: AuditLogRepository,
        dispatch_execution: Callable[[uuid.UUID], str],
    ) -> None:
        self._approvals = approvals
        self._cognitive_tasks = cognitive_tasks
        self._issues = issues
        self._shifts = shifts
        self._audit_logs = audit_logs
        self._dispatch_execution = dispatch_execution

    async def execute(
        self,
        approval_id: uuid.UUID,
        *,
        action: str,
        rejection_reason: str | None,
        execution_override_params: dict | None,
    ) -> ApprovalActionResult:
        approval = await self._approvals.get_by_id(approval_id)
        if approval is None:
            raise ApprovalNotFoundProblem(f"No approval found for id {approval_id}")

        if approval.status != "PENDING":
            raise ApprovalAlreadyDecidedProblem(
                f"Approval {approval_id} has already been decided (status={approval.status})"
            )

        now = datetime.now(timezone.utc)
        if now > approval.expires_at:
            await self._approvals.decide(approval_id, status="EXPIRED", decided_at=now)
            raise ApprovalExpiredProblem(f"Approval {approval_id} expired at {approval.expires_at.isoformat()}")

        if action == ApprovalAction.APPROVE.value:
            return await self._handle_approve(
                approval, execution_override_params=execution_override_params, now=now
            )
        if action == ApprovalAction.REJECT.value:
            return await self._handle_reject(approval, rejection_reason=rejection_reason, now=now)
        if action == ApprovalAction.DEFER.value:
            return await self._handle_defer(approval, now=now)

        raise InvalidApprovalActionProblem(f"Unsupported approval action: {action!r}")

    async def _handle_approve(
        self, approval, *, execution_override_params: dict | None, now: datetime
    ) -> ApprovalActionResult:
        await self._approvals.decide(
            approval.id,
            status="APPROVED",
            decided_at=now,
            execution_override_params=execution_override_params,
        )

        if execution_override_params:
            task = await self._cognitive_tasks.get_by_id(approval.task_id)
            if task is not None:
                # `execution_plan["items"]` holds one entry per affected
                # product (see `product_quality.py`'s Plan-step docstrings).
                # A merchant "modify" edit applies to items[0] — the common
                # case is a single-product task; overriding one field across
                # every item of a multi-product task isn't a well-defined
                # merchant intent, so this deliberately only touches the
                # first item rather than guessing.
                items = list(task.execution_plan.get("items", []))
                if items:
                    items[0] = {**items[0], **execution_override_params}
                    merged_plan = {**task.execution_plan, "items": items}
                else:
                    merged_plan = {**task.execution_plan, **execution_override_params}
                await self._cognitive_tasks.update_execution_plan(task.id, merged_plan)

        await self._cognitive_tasks.update_status(approval.task_id, "EXECUTING")
        await self._issues.update_status(approval.issue_id, "IN_PROGRESS")

        self._dispatch_execution(approval.task_id)

        task = await self._cognitive_tasks.get_by_id(approval.task_id)
        shift_id = task.shift_id if task is not None else None
        if shift_id is not None:
            await self._shifts.increment_pending_approvals(shift_id, -1)

        action_label = "APPROVAL_GRANTED_WITH_MODIFICATION" if execution_override_params else "APPROVAL_GRANTED"
        entry = await self._audit_logs.append(
            store_id=approval.store_id,
            shift_id=shift_id,
            task_id=approval.task_id,
            actor_type="MERCHANT",
            actor_id="merchant",
            action=action_label,
            rationale="Approved via Approval Center.",
        )

        logger.info(
            "handle_approval_action_approved",
            approval_id=str(approval.id),
            task_id=str(approval.task_id),
            modified=bool(execution_override_params),
            status="success",
        )
        return ApprovalActionResult(
            approval_id=approval.id,
            task_id=approval.task_id,
            status="EXECUTING",
            message="Task approval granted. Mutation dispatched to execution queue.",
            audit_log_id=entry.id,
        )

    async def _handle_reject(
        self, approval, *, rejection_reason: str | None, now: datetime
    ) -> ApprovalActionResult:
        if not rejection_reason or len(rejection_reason) < MIN_REJECTION_REASON_LENGTH:
            raise InvalidApprovalActionProblem(
                f"rejection_reason is required and must be at least {MIN_REJECTION_REASON_LENGTH} characters"
            )

        await self._approvals.decide(
            approval.id, status="REJECTED", decided_at=now, merchant_rationale=rejection_reason
        )
        # Never silently drop a real problem — resurfaces next shift, same
        # reasoning as a verification failure.
        await self._issues.update_status(approval.issue_id, "OPEN")
        await self._cognitive_tasks.update_status(approval.task_id, "FAILED")

        task = await self._cognitive_tasks.get_by_id(approval.task_id)
        shift_id = task.shift_id if task is not None else None
        if shift_id is not None:
            await self._shifts.increment_pending_approvals(shift_id, -1)

        # This approvals/audit_logs row IS the Merchant Memory substrate
        # `domain/merchant_memory.py::has_repeated_rejection_history` reads
        # from on the next planning run — no separate memory write needed.
        entry = await self._audit_logs.append(
            store_id=approval.store_id,
            shift_id=shift_id,
            task_id=approval.task_id,
            actor_type="MERCHANT",
            actor_id="merchant",
            action="APPROVAL_REJECTED",
            rationale=rejection_reason,
        )

        logger.info(
            "handle_approval_action_rejected",
            approval_id=str(approval.id),
            task_id=str(approval.task_id),
            status="success",
        )
        return ApprovalActionResult(
            approval_id=approval.id,
            task_id=approval.task_id,
            status="REJECTED",
            message="Task rejected by merchant.",
            audit_log_id=entry.id,
        )

    async def _handle_defer(self, approval, *, now: datetime) -> ApprovalActionResult:
        new_expires_at = approval.expires_at + timedelta(hours=DEFER_EXTENSION_HOURS)
        await self._approvals.extend_expiry(approval.id, new_expires_at)

        task = await self._cognitive_tasks.get_by_id(approval.task_id)
        shift_id = task.shift_id if task is not None else None

        entry = await self._audit_logs.append(
            store_id=approval.store_id,
            shift_id=shift_id,
            task_id=approval.task_id,
            actor_type="MERCHANT",
            actor_id="merchant",
            action="APPROVAL_DEFERRED",
            rationale="Merchant deferred the decision; approval window extended by 24 hours.",
        )

        logger.info(
            "handle_approval_action_deferred",
            approval_id=str(approval.id),
            task_id=str(approval.task_id),
            new_expires_at=new_expires_at.isoformat(),
            status="success",
        )
        return ApprovalActionResult(
            approval_id=approval.id,
            task_id=approval.task_id,
            status="PENDING",
            message="Decision deferred; approval window extended.",
            audit_log_id=entry.id,
        )
