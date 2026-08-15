"""Approval Center API — Sprint 3 AI Trust & Execution.

`POST /api/v1/approvals/{approval_id}/action` is the merchant decision
endpoint (APPROVE/REJECT/DEFER) that drives `HandleApprovalAction`.
`GET /api/v1/approvals` lists this store's pending approvals for the
Approval Center UI.

Known, documented limitation (Idempotency-Key): full Redis-backed
Idempotency-Key replay-caching (an exact request replay returning the
identical cached response without re-processing) is NOT implemented this
sprint — no such store exists anywhere in this codebase yet, and building
one wasn't part of the approved scope. The header is required and validated
as present (FastAPI's `Header(...)` already 422s if missing — matching the
spirit of `MissingIdempotencyKeyProblem`, defined for this purpose but not
separately raised here since FastAPI's own required-header validation
already covers "missing"); true request-level dedup instead relies on the
approval state-machine guard (`ApprovalAlreadyDecidedProblem` on a second
decision attempt against an already-decided approval) rather than a
cached-response replay. See CONFLICTS.md Sprint 3 Part 2.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header

from app.api.deps import (
    get_approval_repository,
    get_cognitive_task_repository,
    get_current_store_id,
    get_handle_approval_action_use_case,
    get_issue_repository,
)
from app.api.errors import InvalidApprovalActionProblem
from app.application.dtos import ApprovalActionRequest, ApprovalActionResponse, PendingApprovalDTO
from app.application.ports import ApprovalRepository, CognitiveTaskRepository, IssueRepository
from app.application.use_cases.handle_approval_action import HandleApprovalAction
from app.domain.confidence import merchant_memory_note
from app.domain.enums import ApprovalAction

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])

_VALID_ACTIONS = {a.value for a in ApprovalAction}


@router.post("/{approval_id}/action", response_model=ApprovalActionResponse)
async def submit_approval_action(
    approval_id: uuid.UUID,
    body: ApprovalActionRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    use_case: HandleApprovalAction = Depends(get_handle_approval_action_use_case),
) -> ApprovalActionResponse:
    if body.action not in _VALID_ACTIONS:
        raise InvalidApprovalActionProblem(
            f"action must be one of {sorted(_VALID_ACTIONS)}, got {body.action!r}"
        )

    result = await use_case.execute(
        approval_id,
        action=body.action,
        rejection_reason=body.rejection_reason,
        execution_override_params=body.execution_override_params,
    )

    return ApprovalActionResponse(
        success=True,
        approval_id=result.approval_id,
        task_id=result.task_id,
        status=result.status,
        message=result.message,
        audit_log_id=result.audit_log_id,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("")
async def list_pending_approvals(
    store_id: uuid.UUID = Depends(get_current_store_id),
    approvals: ApprovalRepository = Depends(get_approval_repository),
    cognitive_tasks: CognitiveTaskRepository = Depends(get_cognitive_task_repository),
    issues: IssueRepository = Depends(get_issue_repository),
) -> dict:
    """Stripe-style list envelope. Cursor pagination is NOT implemented this
    sprint (documented, not silently skipped) — `has_more` is always False
    and `next_cursor` always None; the merchant-facing Approval Center is
    expected to have a small enough pending queue that a single flat page is
    sufficient for this sprint's scope."""
    pending = await approvals.list_pending_for_store(store_id)

    items: list[PendingApprovalDTO] = []
    for approval in pending:
        task = await cognitive_tasks.get_by_id(approval.task_id)
        issue = await issues.get_by_id(approval.issue_id)
        if task is None or issue is None:
            continue
        items.append(
            PendingApprovalDTO(
                approval_id=approval.id,
                issue_id=issue.id,
                title=issue.title,
                risk_level=task.risk_level.value if hasattr(task.risk_level, "value") else task.risk_level,
                recommended_action=task.action_type,
                revenue_impact_usd=float(issue.revenue_impact_estimate),
                confidence_score=task.confidence_assessment.get("overall_score", 0.0),
                expires_at=approval.expires_at,
                # Sprint 5 Phase 3.2: pass through unmodified, same source
                # `issue` already fetched above — see PendingApprovalDTO's
                # own docstring.
                description=issue.description,
                evidence_data=issue.evidence_data,
                # Sprint 5 Phase 5: same source dict as `confidence_score`
                # above, just also checked for a genuine merchant-history
                # signal worth surfacing as its own UI callout.
                merchant_memory_note=merchant_memory_note(task.confidence_assessment),
            )
        )

    return {
        "object": "list",
        "data": [item.model_dump(mode="json") for item in items],
        "has_more": False,
        "next_cursor": None,
    }
