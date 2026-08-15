"""HTTP-level integration test: `POST /api/v1/ask` (Sprint 4 Step 4). Mirrors
`test_work_log_and_task_detail_api.py`'s TestClient + dependency_overrides
style.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_ask_nightshift_use_case, get_current_store_id
from app.application.use_cases.ask_nightshift import AskNightShiftResult
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_overrides_after_test():
    yield
    app.dependency_overrides.pop(get_current_store_id, None)
    app.dependency_overrides.pop(get_ask_nightshift_use_case, None)


class _FakeAskNightShift:
    def __init__(self, result: AskNightShiftResult) -> None:
        self._result = result

    async def ask(self, store_id, question: str) -> AskNightShiftResult:
        assert question  # never called with an empty question by the route itself
        return self._result


def test_ask_nightshift_returns_grounded_answer(client: TestClient):
    store_id = uuid.uuid4()
    shift_id = str(uuid.uuid4())
    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_ask_nightshift_use_case] = lambda: _FakeAskNightShift(
        AskNightShiftResult(
            answer="Revenue rose because a duplicate discount was deactivated.",
            grounded_in_shift_ids=[shift_id],
            used_llm=True,
        )
    )

    response = client.post("/api/v1/ask", json={"question": "why did revenue increase yesterday?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Revenue rose because a duplicate discount was deactivated."
    assert body["grounded_in_shift_ids"] == [shift_id]
    assert body["used_llm"] is True


def test_ask_nightshift_surfaces_deterministic_fallback_answer(client: TestClient):
    store_id = uuid.uuid4()
    app.dependency_overrides[get_current_store_id] = lambda: store_id
    app.dependency_overrides[get_ask_nightshift_use_case] = lambda: _FakeAskNightShift(
        AskNightShiftResult(
            answer="I don't have any completed shifts to draw on yet — once your first overnight "
            "shift finishes, ask me again.",
            grounded_in_shift_ids=[],
            used_llm=False,
        )
    )

    response = client.post("/api/v1/ask", json={"question": "why did revenue increase yesterday?"})

    assert response.status_code == 200
    body = response.json()
    assert body["used_llm"] is False
    assert body["grounded_in_shift_ids"] == []
