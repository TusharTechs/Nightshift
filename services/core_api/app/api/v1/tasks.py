"""Cognitive Task detail & manual rollback API — Sprint 3 AI Trust &
Execution.

`GET /api/v1/tasks/{task_id}` assembles the full lifecycle picture for a
single task (execution/verification/rollback/approval, all optional —
a PLANNED task that auto-executed successfully has no `approval`; a task
still PENDING_APPROVAL has no `execution` yet).

`POST /api/v1/tasks/{task_id}/rollback` is the merchant-triggered manual
rollback path — distinct from the automatic rollback `VerifyExecution`
performs on a failed verification. Only valid against a task whose current
execution is COMPLETED (there is nothing meaningful to roll back otherwise).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header

from app.api.deps import (
    get_approval_repository,
    get_cognitive_task_repository,
    get_current_store_id,
    get_execution_repository,
    get_issue_repository,
    get_rollback_cognitive_task_use_case,
    get_rollback_repository,
    get_verification_repository,
)
from app.api.errors import TaskNotFoundProblem, TaskNotRollbackableProblem
from app.application.dtos import RollbackRequest, RollbackResponse, TaskDetailResponse
from app.application.ports import (
    ApprovalRepository,
    CognitiveTaskRepository,
    ExecutionRepository,
    IssueRepository,
    RollbackRepository,
    VerificationRepository,
)
from app.application.use_cases.rollback_cognitive_task import RollbackCognitiveTask

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else value


def _dt(value) -> str | None:
    return value.isoformat() if value is not None else None


def _execution_dict(execution) -> dict:
    """Repositories may return either the SQLAlchemy ORM `Execution` (the
    real, SQL-backed DI path) or the Pydantic domain `Execution` (in-memory
    fakes used by tests) — neither is guaranteed to have `.model_dump()`
    (ORM instances don't), so fields are read by plain attribute access and
    assembled into a JSON-safe dict explicitly here, working identically
    against either object type."""
    return {
        "id": str(execution.id),
        "task_id": str(execution.task_id),
        "store_id": str(execution.store_id),
        "status": _enum_value(execution.status),
        "request_payload": execution.request_payload,
        "response_payload": execution.response_payload,
        "error_log": execution.error_log,
        "execution_duration_ms": execution.execution_duration_ms,
        "retry_count": execution.retry_count,
        "started_at": _dt(execution.started_at),
        "completed_at": _dt(execution.completed_at),
    }


def _verification_dict(verification) -> dict:
    return {
        "id": str(verification.id),
        "execution_id": str(verification.execution_id),
        "task_id": str(verification.task_id),
        "store_id": str(verification.store_id),
        "status": _enum_value(verification.status),
        "method": verification.method,
        "result_data": verification.result_data,
        "verified_at": _dt(verification.verified_at),
    }


def _rollback_dict(rollback) -> dict:
    return {
        "id": str(rollback.id),
        "execution_id": str(rollback.execution_id),
        "task_id": str(rollback.task_id),
        "store_id": str(rollback.store_id),
        "status": _enum_value(rollback.status),
        "reverted_state": rollback.reverted_state,
        "rollback_reason": rollback.rollback_reason,
        "error_log": rollback.error_log,
        "started_at": _dt(rollback.started_at),
        "completed_at": _dt(rollback.completed_at),
    }


def _approval_dict(approval) -> dict:
    return {
        "id": str(approval.id),
        "task_id": str(approval.task_id),
        "issue_id": str(approval.issue_id),
        "store_id": str(approval.store_id),
        "status": _enum_value(approval.status),
        "approver_user_id": str(approval.approver_user_id) if approval.approver_user_id else None,
        "merchant_rationale": approval.merchant_rationale,
        "execution_override_params": approval.execution_override_params,
        "expires_at": _dt(approval.expires_at),
        "decided_at": _dt(approval.decided_at),
        "created_at": _dt(approval.created_at),
    }


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task_detail(
    task_id: uuid.UUID,
    store_id: uuid.UUID = Depends(get_current_store_id),
    cognitive_tasks: CognitiveTaskRepository = Depends(get_cognitive_task_repository),
    issues: IssueRepository = Depends(get_issue_repository),
    executions: ExecutionRepository = Depends(get_execution_repository),
    verifications: VerificationRepository = Depends(get_verification_repository),
    rollbacks: RollbackRepository = Depends(get_rollback_repository),
    approvals: ApprovalRepository = Depends(get_approval_repository),
) -> TaskDetailResponse:
    task = await cognitive_tasks.get_by_id(task_id)
    if task is None or task.store_id != store_id:
        raise TaskNotFoundProblem(f"No task found for id {task_id}")

    issue = await issues.get_by_id(task.issue_id)

    execution = await executions.get_by_task_id(task_id)
    verification = None
    rollback = None
    if execution is not None:
        verification = await verifications.get_by_execution_id(execution.id)
        rollback = await rollbacks.get_by_execution_id(execution.id)

    approval = await approvals.get_by_task_id(task_id)

    return TaskDetailResponse(
        task_id=task.id,
        issue_id=task.issue_id,
        action_type=task.action_type,
        status=_enum_value(task.status),
        risk_level=_enum_value(task.risk_level),
        risk_reasoning=task.risk_reasoning,
        confidence_assessment=task.confidence_assessment,
        explanation=task.explanation,
        issue_evidence=issue.evidence_data if issue else None,
        execution=_execution_dict(execution) if execution else None,
        verification=_verification_dict(verification) if verification else None,
        rollback=_rollback_dict(rollback) if rollback else None,
        approval=_approval_dict(approval) if approval else None,
    )


@router.post("/{task_id}/rollback", response_model=RollbackResponse)
async def rollback_task(
    task_id: uuid.UUID,
    body: RollbackRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    store_id: uuid.UUID = Depends(get_current_store_id),
    cognitive_tasks: CognitiveTaskRepository = Depends(get_cognitive_task_repository),
    executions: ExecutionRepository = Depends(get_execution_repository),
    use_case: RollbackCognitiveTask = Depends(get_rollback_cognitive_task_use_case),
) -> RollbackResponse:
    task = await cognitive_tasks.get_by_id(task_id)
    if task is None or task.store_id != store_id:
        raise TaskNotFoundProblem(f"No task found for id {task_id}")

    execution = await executions.get_by_task_id(task_id)
    if execution is None or execution.status != "COMPLETED":
        raise TaskNotRollbackableProblem(
            f"Task {task_id} has no COMPLETED execution to roll back "
            f"(current execution status: {execution.status if execution else 'none'})"
        )

    rollback = await use_case.execute(execution.id, reason=body.rollback_reason)
    await cognitive_tasks.update_status(task_id, "ROLLED_BACK" if rollback.status == "COMPLETED" else "FAILED")

    return RollbackResponse(
        success=rollback.status == "COMPLETED",
        task_id=task_id,
        status=_enum_value(rollback.status),
        reverted_state=rollback.reverted_state,
        audit_log_id=None,
        timestamp=datetime.now(timezone.utc),
    )
