"""HTTP-level integration test: `GET /api/v1/shifts/{shift_id}/replay`
(Sprint 4 Step 5). Mirrors `test_work_log_and_task_detail_api.py`'s
TestClient + dependency_overrides style.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.deps import (
    get_audit_log_repository,
    get_current_store_id,
    get_shift_report_repository,
    get_shift_repository,
)
from app.application.ports import (
    InMemoryAuditLogRepository,
    InMemoryShiftReportRepository,
    InMemoryShiftRepository,
)
from app.domain.models import AuditLogEntry, Shift, ShiftReport
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_overrides_after_test():
    yield
    app.dependency_overrides.pop(get_current_store_id, None)
    app.dependency_overrides.pop(get_shift_repository, None)
    app.dependency_overrides.pop(get_shift_report_repository, None)
    app.dependency_overrides.pop(get_audit_log_repository, None)


def _shift(*, store_id: uuid.UUID, shift_number: int = 3) -> Shift:
    now = datetime.now(timezone.utc)
    return Shift(
        id=uuid.uuid4(),
        store_id=store_id,
        shift_number=shift_number,
        status="COMPLETED",
        started_at=now,
        completed_at=now,
        created_at=now,
    )


def test_shift_replay_returns_entries_chronologically_with_icons(client: TestClient):
    store_id = uuid.uuid4()
    shift = _shift(store_id=store_id)

    shifts_repo = InMemoryShiftRepository()
    shifts_repo.seed(shift)

    audit_logs = InMemoryAuditLogRepository()
    base = datetime(2026, 8, 1, 2, 0, 0, tzinfo=timezone.utc)
    # Seeded out of chronological order on purpose — the endpoint must sort.
    audit_logs.seed(
        AuditLogEntry(
            id=uuid.uuid4(),
            store_id=store_id,
            shift_id=shift.id,
            actor_type="AI_AGENT",
            actor_id="DEACTIVATE_DUPLICATE_DISCOUNT",
            action="EXECUTION_COMPLETED",
            rationale="Deactivated the duplicate discount code.",
            timestamp=base + timedelta(minutes=5),
        )
    )
    audit_logs.seed(
        AuditLogEntry(
            id=uuid.uuid4(),
            store_id=store_id,
            shift_id=shift.id,
            actor_type="AI_AGENT",
            actor_id="DEACTIVATE_DUPLICATE_DISCOUNT",
            action="TASK_PLANNED",
            rationale="Planned a fix for the duplicate discount issue.",
            timestamp=base,
        )
    )
    # A different shift's entry must never leak into this shift's replay.
    other_shift_id = uuid.uuid4()
    audit_logs.seed(
        AuditLogEntry(
            id=uuid.uuid4(),
            store_id=store_id,
            shift_id=other_shift_id,
            actor_type="AI_AGENT",
            actor_id="GENERATE_ALT_TEXT",
            action="EXECUTION_COMPLETED",
            rationale="Unrelated shift's entry.",
            timestamp=base + timedelta(minutes=1),
        )
    )

    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_shift_repository] = lambda: shifts_repo
    app.dependency_overrides[get_audit_log_repository] = lambda: audit_logs

    response = client.get(f"/api/v1/shifts/{shift.id}/replay")

    assert response.status_code == 200
    body = response.json()
    assert body["shift_id"] == str(shift.id)
    assert body["shift_number"] == 3
    assert len(body["entries"]) == 2
    # Chronological ascending despite seed order.
    assert body["entries"][0]["action"] == "TASK_PLANNED"
    assert body["entries"][0]["icon"] == "🧠"
    assert body["entries"][1]["action"] == "EXECUTION_COMPLETED"
    assert body["entries"][1]["icon"] == "⚡"


def test_shift_replay_404s_for_a_shift_belonging_to_another_store(client: TestClient):
    owner_store_id = uuid.uuid4()
    requesting_store_id = uuid.uuid4()
    shift = _shift(store_id=owner_store_id)

    shifts_repo = InMemoryShiftRepository()
    shifts_repo.seed(shift)

    app.dependency_overrides[get_current_store_id] = lambda: requesting_store_id
    app.dependency_overrides[get_shift_repository] = lambda: shifts_repo
    app.dependency_overrides[get_audit_log_repository] = lambda: InMemoryAuditLogRepository()

    response = client.get(f"/api/v1/shifts/{shift.id}/replay")

    assert response.status_code == 404
    assert response.json()["code"] == "SHIFT_NOT_FOUND"


def test_shift_replay_404s_for_an_unknown_shift_id(client: TestClient):
    store_id = uuid.uuid4()
    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_shift_repository] = lambda: InMemoryShiftRepository()
    app.dependency_overrides[get_audit_log_repository] = lambda: InMemoryAuditLogRepository()

    response = client.get(f"/api/v1/shifts/{uuid.uuid4()}/replay")

    assert response.status_code == 404


def _report(*, shift_id: uuid.UUID, store_id: uuid.UUID, published_at: datetime) -> ShiftReport:
    return ShiftReport(
        id=uuid.uuid4(),
        shift_id=shift_id,
        store_id=store_id,
        executive_summary="test",
        report_json={},
        published_at=published_at,
        created_at=published_at,
    )


# --- Sprint 5 Phase 1.2: GET /api/v1/shifts/replay/latest-active ------------


def test_latest_active_replay_falls_back_to_an_older_shift_with_real_activity(client: TestClient):
    store_id = uuid.uuid4()
    quiet_shift = _shift(store_id=store_id, shift_number=5)
    active_shift = _shift(store_id=store_id, shift_number=4)

    shifts_repo = InMemoryShiftRepository()
    shifts_repo.seed(quiet_shift)
    shifts_repo.seed(active_shift)

    reports_repo = InMemoryShiftReportRepository()
    reports_repo.seed(_report(shift_id=quiet_shift.id, store_id=store_id, published_at=datetime(2026, 8, 2, tzinfo=timezone.utc)))
    reports_repo.seed(_report(shift_id=active_shift.id, store_id=store_id, published_at=datetime(2026, 8, 1, tzinfo=timezone.utc)))

    audit_logs = InMemoryAuditLogRepository()
    audit_logs.seed(
        AuditLogEntry(
            id=uuid.uuid4(),
            store_id=store_id,
            shift_id=active_shift.id,
            actor_type="AI_AGENT",
            actor_id="DEACTIVATE_DUPLICATE_DISCOUNT",
            action="EXECUTION_COMPLETED",
            rationale="Deactivated a duplicate discount.",
            timestamp=datetime(2026, 8, 1, 2, 5, tzinfo=timezone.utc),
        )
    )
    # `quiet_shift` (the actual latest) has no audit_logs entries at all.

    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_shift_repository] = lambda: shifts_repo
    app.dependency_overrides[get_shift_report_repository] = lambda: reports_repo
    app.dependency_overrides[get_audit_log_repository] = lambda: audit_logs

    response = client.get("/api/v1/shifts/replay/latest-active")

    assert response.status_code == 200
    body = response.json()
    assert body["shift_id"] == str(active_shift.id)
    assert body["shift_number"] == 4
    assert len(body["entries"]) == 1


def test_latest_active_replay_finds_activity_beyond_any_fixed_shift_count_bound(client: TestClient):
    """Regression test for a real, live-reproduced gap: an earlier version
    of this endpoint walked backward through only the 10 most recent shifts
    before giving up, which broke again the first time a store went quiet
    for more than 10 shifts in a row (confirmed live on a real dev store —
    16 consecutive shifts with zero audit_log activity, with the nearest
    real activity 6 shifts past that bound). This test seeds 15 quiet
    shifts newer than the one real active shift to prove the current
    query-based implementation has no such bound at all."""
    store_id = uuid.uuid4()
    shifts_repo = InMemoryShiftRepository()
    reports_repo = InMemoryShiftReportRepository()
    audit_logs = InMemoryAuditLogRepository()

    active_shift = _shift(store_id=store_id, shift_number=1)
    shifts_repo.seed(active_shift)
    reports_repo.seed(
        _report(shift_id=active_shift.id, store_id=store_id, published_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    )
    audit_logs.seed(
        AuditLogEntry(
            id=uuid.uuid4(),
            store_id=store_id,
            shift_id=active_shift.id,
            actor_type="AI_AGENT",
            actor_id="DEACTIVATE_DUPLICATE_DISCOUNT",
            action="EXECUTION_COMPLETED",
            rationale="Deactivated a duplicate discount.",
            timestamp=datetime(2026, 8, 1, 2, 5, tzinfo=timezone.utc),
        )
    )

    for shift_number in range(2, 17):  # 15 quiet shifts, all newer, all with zero activity
        quiet_shift = _shift(store_id=store_id, shift_number=shift_number)
        shifts_repo.seed(quiet_shift)
        reports_repo.seed(
            _report(
                shift_id=quiet_shift.id,
                store_id=store_id,
                published_at=datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(days=shift_number - 1),
            )
        )

    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_shift_repository] = lambda: shifts_repo
    app.dependency_overrides[get_shift_report_repository] = lambda: reports_repo
    app.dependency_overrides[get_audit_log_repository] = lambda: audit_logs

    response = client.get("/api/v1/shifts/replay/latest-active")

    assert response.status_code == 200
    body = response.json()
    assert body["shift_id"] == str(active_shift.id)
    assert body["shift_number"] == 1
    assert len(body["entries"]) == 1


def test_latest_active_replay_returns_latest_shifts_empty_replay_if_nothing_has_activity(client: TestClient):
    store_id = uuid.uuid4()
    shift = _shift(store_id=store_id, shift_number=1)

    shifts_repo = InMemoryShiftRepository()
    shifts_repo.seed(shift)

    reports_repo = InMemoryShiftReportRepository()
    reports_repo.seed(_report(shift_id=shift.id, store_id=store_id, published_at=datetime(2026, 8, 1, tzinfo=timezone.utc)))

    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_shift_repository] = lambda: shifts_repo
    app.dependency_overrides[get_shift_report_repository] = lambda: reports_repo
    app.dependency_overrides[get_audit_log_repository] = lambda: InMemoryAuditLogRepository()

    response = client.get("/api/v1/shifts/replay/latest-active")

    assert response.status_code == 200
    body = response.json()
    assert body["shift_id"] == str(shift.id)
    assert body["entries"] == []


def test_latest_active_replay_404s_when_no_shifts_exist_at_all(client: TestClient):
    store_id = uuid.uuid4()
    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_shift_repository] = lambda: InMemoryShiftRepository()
    app.dependency_overrides[get_shift_report_repository] = lambda: InMemoryShiftReportRepository()
    app.dependency_overrides[get_audit_log_repository] = lambda: InMemoryAuditLogRepository()

    response = client.get("/api/v1/shifts/replay/latest-active")

    assert response.status_code == 404
