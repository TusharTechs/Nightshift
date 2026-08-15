"""AI Work Log API — Sprint 3 AI Trust & Execution.

`GET /api/v1/work-log` lists this store's append-only `audit_logs` entries,
Stripe-style cursor pagination (`limit`/`starting_after`). The cursor is the
ISO-8601 timestamp string of the last item on the previous page — simple and
sufficient for this sprint's scope (no opaque cursor encoding); parsed back
with `datetime.fromisoformat` into `AuditLogRepository.list_for_store`'s
`before_timestamp` parameter.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_audit_log_repository, get_current_store_id
from app.application.dtos import WorkLogEntryDTO, WorkLogListResponse
from app.application.ports import AuditLogRepository
from app.domain.replay import icon_for_action

router = APIRouter(prefix="/api/v1/work-log", tags=["work-log"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@router.get("", response_model=WorkLogListResponse)
async def list_work_log(
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    starting_after: str | None = Query(default=None),
    store_id: uuid.UUID = Depends(get_current_store_id),
    audit_logs: AuditLogRepository = Depends(get_audit_log_repository),
) -> WorkLogListResponse:
    before_timestamp: datetime | None = None
    if starting_after:
        before_timestamp = datetime.fromisoformat(starting_after)

    # Fetch one extra row to determine `has_more` without a second query.
    entries = await audit_logs.list_for_store(store_id, limit=limit + 1, before_timestamp=before_timestamp)
    has_more = len(entries) > limit
    page = entries[:limit]

    data = [
        WorkLogEntryDTO(
            id=entry.id,
            timestamp=entry.timestamp,
            actor_type=entry.actor_type,
            actor_id=entry.actor_id,
            action=entry.action,
            rationale=entry.rationale,
            before_state=entry.before_state,
            after_state=entry.after_state,
            task_id=entry.task_id,
            execution_id=entry.execution_id,
            icon=icon_for_action(entry.action),
        )
        for entry in page
    ]
    next_cursor = page[-1].timestamp.isoformat() if has_more and page else None

    return WorkLogListResponse(data=data, has_more=has_more, next_cursor=next_cursor)
