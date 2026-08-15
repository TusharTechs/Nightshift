"""HTTP-level integration test: `POST /internal/dispatch-nightly-shifts`
(Cloud Run migration — Cloud Scheduler's replacement for Celery Beat).
Mirrors `test_demo_incident_api.py`'s `TestClient` + `app.dependency_overrides`
pattern.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_task_dispatcher
from app.application.ports import InMemoryTaskDispatcher
from app.config import Settings, get_settings
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_overrides_after_test():
    yield
    app.dependency_overrides.pop(get_settings, None)
    app.dependency_overrides.pop(get_task_dispatcher, None)


def _settings_with_secret(secret: str) -> Settings:
    settings = Settings()
    settings.internal_dispatch_secret = secret
    return settings


def test_dispatch_rejects_when_no_secret_configured(client: TestClient):
    app.dependency_overrides[get_settings] = lambda: _settings_with_secret("")
    app.dependency_overrides[get_task_dispatcher] = lambda: InMemoryTaskDispatcher()

    response = client.post(
        "/internal/dispatch-nightly-shifts", headers={"X-Internal-Secret": "anything"}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INTERNAL_DISPATCH_UNAUTHORIZED"


def test_dispatch_rejects_wrong_secret(client: TestClient):
    app.dependency_overrides[get_settings] = lambda: _settings_with_secret("correct-secret")
    dispatcher = InMemoryTaskDispatcher()
    app.dependency_overrides[get_task_dispatcher] = lambda: dispatcher

    response = client.post(
        "/internal/dispatch-nightly-shifts", headers={"X-Internal-Secret": "wrong-secret"}
    )

    assert response.status_code == 401
    assert dispatcher.dispatch_nightly_shifts_call_count == 0


def test_dispatch_rejects_missing_header(client: TestClient):
    app.dependency_overrides[get_settings] = lambda: _settings_with_secret("correct-secret")
    app.dependency_overrides[get_task_dispatcher] = lambda: InMemoryTaskDispatcher()

    response = client.post("/internal/dispatch-nightly-shifts")

    assert response.status_code == 401


def test_dispatch_succeeds_with_correct_secret(client: TestClient):
    app.dependency_overrides[get_settings] = lambda: _settings_with_secret("correct-secret")
    dispatcher = InMemoryTaskDispatcher()
    app.dependency_overrides[get_task_dispatcher] = lambda: dispatcher

    response = client.post(
        "/internal/dispatch-nightly-shifts", headers={"X-Internal-Secret": "correct-secret"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dispatched"] is True
    assert body["task_id"]
    assert dispatcher.dispatch_nightly_shifts_call_count == 1
