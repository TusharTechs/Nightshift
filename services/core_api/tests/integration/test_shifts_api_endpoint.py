"""HTTP-level integration test: GET /api/v1/shifts/latest (Sprint 2 Feature 4
/ Story 3).

Persistence swapped for `InMemoryShiftReportRepository` via
`dependency_overrides`, matching this repo's established pattern from
Sprint 1 (`test_api_auth_endpoints.py`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_store_id, get_shift_report_repository
from app.application.ports import InMemoryShiftReportRepository
from app.domain.models import ShiftReport
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def override_auth():
    """Bypasses real Shopify session-token verification for this endpoint
    test — HTTP-level HMAC/JWT auth is already covered by
    test_api_auth_endpoints.py; this test is about the shift report
    contract itself."""

    def _apply(store_id: uuid.UUID):
        app.dependency_overrides[get_current_store_id] = lambda: store_id

    yield _apply
    app.dependency_overrides.pop(get_current_store_id, None)


def test_get_latest_shift_returns_persisted_report_json(client: TestClient, override_auth):
    store_id = uuid.uuid4()
    shift_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    report_json = {
        "shift_id": str(shift_id),
        "shift_number": 1,
        "status": "COMPLETED",
        "started_at": now.isoformat(),
        "completed_at": now.isoformat(),
        "health_score": 92,
        "executive_summary": "All Systems Operational.",
        "metrics": {
            "issues_detected": 0,
            "issues_resolved": 0,
            "estimated_revenue_protected_usd": 0.0,
            "time_saved_hours": 0.0,
        },
        "issues": [],
        "health_category_deductions": {},
    }
    reports = InMemoryShiftReportRepository()
    reports.seed(
        ShiftReport(
            id=uuid.uuid4(),
            shift_id=shift_id,
            store_id=store_id,
            executive_summary="All Systems Operational.",
            report_json=report_json,
            published_at=now,
            created_at=now,
        )
    )

    override_auth(store_id)
    app.dependency_overrides[get_shift_report_repository] = lambda: reports
    try:
        response = client.get("/api/v1/shifts/latest")
    finally:
        app.dependency_overrides.pop(get_shift_report_repository, None)

    assert response.status_code == 200
    # Sprint 5 Phase 5: the response is the persisted report_json plus one
    # additive `previous_shift_health_score` field (None here — this store
    # has only ever had this one shift) — see CONFLICTS.md item 55.
    assert response.json() == {**report_json, "previous_shift_health_score": None}


def test_get_latest_shift_returns_404_when_no_shift_completed(client: TestClient, override_auth):
    store_id = uuid.uuid4()
    reports = InMemoryShiftReportRepository()

    override_auth(store_id)
    app.dependency_overrides[get_shift_report_repository] = lambda: reports
    try:
        response = client.get("/api/v1/shifts/latest")
    finally:
        app.dependency_overrides.pop(get_shift_report_repository, None)

    # Sprint 2 API Specification, verbatim status codes: "200 OK, 401
    # Unauthorized, 404 Not Found (No shifts run yet)".
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "NO_COMPLETED_SHIFT"


def test_get_latest_shift_returns_only_the_most_recently_published_report(
    client: TestClient, override_auth
):
    store_id = uuid.uuid4()
    older = datetime(2026, 7, 30, tzinfo=timezone.utc)
    newer = datetime(2026, 7, 31, tzinfo=timezone.utc)
    reports = InMemoryShiftReportRepository()
    reports.seed(
        ShiftReport(
            id=uuid.uuid4(),
            shift_id=uuid.uuid4(),
            store_id=store_id,
            executive_summary="Old report",
            report_json={"executive_summary": "Old report"},
            published_at=older,
            created_at=older,
        )
    )
    reports.seed(
        ShiftReport(
            id=uuid.uuid4(),
            shift_id=uuid.uuid4(),
            store_id=store_id,
            executive_summary="New report",
            report_json={"executive_summary": "New report"},
            published_at=newer,
            created_at=newer,
        )
    )

    override_auth(store_id)
    app.dependency_overrides[get_shift_report_repository] = lambda: reports
    try:
        response = client.get("/api/v1/shifts/latest")
    finally:
        app.dependency_overrides.pop(get_shift_report_repository, None)

    assert response.status_code == 200
    assert response.json()["executive_summary"] == "New report"


def test_get_latest_shift_includes_previous_shift_health_score(client: TestClient, override_auth):
    """Sprint 5 Phase 5: `previous_shift_health_score` is joined in from the
    second-most-recent published ShiftReport's own `health_score` — grounds
    the "Tonight's Impact" widget's Store Health Delta in a real prior data
    point rather than a fabricated one. See CONFLICTS.md item 55."""
    store_id = uuid.uuid4()
    older = datetime(2026, 7, 30, tzinfo=timezone.utc)
    newer = datetime(2026, 7, 31, tzinfo=timezone.utc)
    reports = InMemoryShiftReportRepository()
    reports.seed(
        ShiftReport(
            id=uuid.uuid4(),
            shift_id=uuid.uuid4(),
            store_id=store_id,
            executive_summary="Old report",
            report_json={"executive_summary": "Old report", "health_score": 78},
            published_at=older,
            created_at=older,
        )
    )
    reports.seed(
        ShiftReport(
            id=uuid.uuid4(),
            shift_id=uuid.uuid4(),
            store_id=store_id,
            executive_summary="New report",
            report_json={"executive_summary": "New report", "health_score": 92},
            published_at=newer,
            created_at=newer,
        )
    )

    override_auth(store_id)
    app.dependency_overrides[get_shift_report_repository] = lambda: reports
    try:
        response = client.get("/api/v1/shifts/latest")
    finally:
        app.dependency_overrides.pop(get_shift_report_repository, None)

    assert response.status_code == 200
    body = response.json()
    assert body["health_score"] == 92
    assert body["previous_shift_health_score"] == 78
