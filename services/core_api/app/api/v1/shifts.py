"""Morning Shift Report API — Sprint 2 Feature 4 / Story 3.

`GET /api/v1/shifts/latest` was deliberately deferred by Sprint 1 (see this
file's original docstring, preserved in git history) to "the sprint that
delivers the Morning Shift Report" — this is that sprint.

Response shape matches Sprint 2's own API Specification exactly, not the
richer, task/approval-aware shape in the API Contract Specification document
or SATDD's camelCase illustration. Those two depend on domain objects
(`cognitive_tasks`, `approvals`) that don't exist until a later sprint —
Sprint 2's own narrower shape is the only one implementable this sprint. See
CONFLICTS.md item 17 for the full reasoning; the richer contract should be
revisited once the Task/Approval domain lands.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.api.deps import (
    get_audit_log_repository,
    get_current_store_id,
    get_shift_report_repository,
    get_shift_repository,
)
from app.api.errors import NoCompletedShiftProblem, ShiftNotFoundProblem
from app.application.dtos import ShiftReplayResponse, WorkLogEntryDTO
from app.application.ports import AuditLogRepository, ShiftReportRepository, ShiftRepository
from app.domain.models import AuditLogEntry
from app.domain.replay import icon_for_action

router = APIRouter(prefix="/api/v1/shifts", tags=["shifts"])


def _build_replay_response(
    *, shift_id: uuid.UUID, shift_number: int, entries: list[AuditLogEntry]
) -> ShiftReplayResponse:
    return ShiftReplayResponse(
        shift_id=shift_id,
        shift_number=shift_number,
        entries=[
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
            for entry in entries
        ],
    )


@router.get("/latest")
async def get_latest_shift(
    store_id: uuid.UUID = Depends(get_current_store_id),
    shift_reports: ShiftReportRepository = Depends(get_shift_report_repository),
) -> dict:
    """Returns the most recently published Morning Shift Report for the
    authenticated store.

    The response body is the exact `report_json` persisted by
    `tasks.compile_shift_report` (built from
    `domain.shift_compiler.ShiftReportPayload.to_api_response()`) — this
    endpoint never recomputes the report at request time, so what the
    merchant reads is guaranteed to match what was durably persisted (PRD
    Part 2: "The report is immutable after publication").

    Sprint 5 Phase 5: one additive field, `previous_shift_health_score`, is
    joined in on top of that persisted body — the "Tonight's Impact" widget's
    Store Health Delta needs a second, prior data point to show real movement
    (e.g. "78 -> 92"), and no prior sprint exposed one. This does NOT violate
    the immutability guarantee above: the current shift's own `report_json`
    is still returned completely unmodified; the extra field only names one
    additional fact about a *different*, already-published, equally-immutable
    ShiftReport row (see CONFLICTS.md item 55). None when this is the
    store's first shift ever.
    """
    recent = await shift_reports.list_recent_for_store(store_id, limit=2)
    if not recent:
        raise NoCompletedShiftProblem(f"No shifts completed yet for store {store_id}")

    report = recent[0]
    previous_health_score = recent[1].report_json.get("health_score") if len(recent) > 1 else None

    return {**report.report_json, "previous_shift_health_score": previous_health_score}


@router.get("/replay/latest-active", response_model=ShiftReplayResponse)
async def get_latest_active_shift_replay(
    store_id: uuid.UUID = Depends(get_current_store_id),
    shifts: ShiftRepository = Depends(get_shift_repository),
    shift_reports: ShiftReportRepository = Depends(get_shift_report_repository),
    audit_logs: AuditLogRepository = Depends(get_audit_log_repository),
) -> ShiftReplayResponse:
    """Sprint 5 Phase 1.2: the Shift Replay scrubber's real data source —
    resolves to this store's single most recent shift with actual
    audit_log activity, found via one direct query
    (`AuditLogRepository.get_latest_shift_id_with_activity`) rather than
    walking a capped list of recent shifts. A clean, all-clear shift
    genuinely has nothing to replay; showing that shift's empty replay by
    default made the scrubber look broken rather than showing what the AI
    actually did most recently. An earlier version of this fix bounded the
    walk-back to the 10 most recent shifts, which broke again in practice
    the first time a store went quiet for more than 10 shifts in a row —
    this version has no such bound, since the query goes straight to the
    activity itself instead of scanning shift-by-shift for it. Falls back
    to the single latest shift's (still empty) replay only if this store
    has never once had a single shift-scoped audit log entry — never
    fabricates activity that didn't happen.
    """
    active_shift_id = await audit_logs.get_latest_shift_id_with_activity(store_id)
    if active_shift_id is not None:
        shift = await shifts.get_by_id(active_shift_id)
        if shift is not None and shift.store_id == store_id:
            entries = await audit_logs.list_for_shift(active_shift_id)
            return _build_replay_response(shift_id=shift.id, shift_number=shift.shift_number, entries=entries)

    latest_report = await shift_reports.get_latest_for_store(store_id)
    if latest_report is None:
        raise NoCompletedShiftProblem(f"No shifts completed yet for store {store_id}")

    fallback_shift = await shifts.get_by_id(latest_report.shift_id)
    if fallback_shift is None:
        raise NoCompletedShiftProblem(f"No shifts completed yet for store {store_id}")
    return _build_replay_response(shift_id=fallback_shift.id, shift_number=fallback_shift.shift_number, entries=[])


@router.get("/{shift_id}/replay", response_model=ShiftReplayResponse)
async def get_shift_replay(
    shift_id: uuid.UUID,
    store_id: uuid.UUID = Depends(get_current_store_id),
    shifts: ShiftRepository = Depends(get_shift_repository),
    audit_logs: AuditLogRepository = Depends(get_audit_log_repository),
) -> ShiftReplayResponse:
    """Sprint 4 Step 5: Shift Replay scrubber's data source for one specific,
    known shift — every `audit_logs` entry for it, chronological ascending,
    each carrying a deterministic icon (`domain/replay.py`). See
    CONFLICTS.md item 45 for why this small, shift-scoped endpoint was
    added alongside the existing global `GET /api/v1/work-log`, not instead
    of it. See `GET /replay/latest-active` above for the "just show me
    something real" variant the frontend actually defaults to.
    """
    shift = await shifts.get_by_id(shift_id)
    if shift is None or shift.store_id != store_id:
        raise ShiftNotFoundProblem(f"No shift found for id {shift_id}")

    entries = await audit_logs.list_for_shift(shift_id)
    return _build_replay_response(shift_id=shift.id, shift_number=shift.shift_number, entries=entries)
