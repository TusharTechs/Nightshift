"""Unit tests: LlmCallBudgetGuard (Sprint 2 hardening, 2026-07-31).

Uses a trivial in-memory fake instead of a real Redis connection — the
guard only depends on the narrow `AsyncCounterBackend` Protocol (`incr` +
`expire`), so this is a pure, fast, offline test of the counting logic
itself.
"""

from __future__ import annotations

import pytest

from app.infrastructure.llm.budget_guard import LlmBudgetExceededError, LlmCallBudgetGuard


class _FakeCounterBackend:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expire_calls.append((key, seconds))
        return True


@pytest.mark.asyncio
async def test_budget_guard_allows_calls_under_limit():
    backend = _FakeCounterBackend()
    guard = LlmCallBudgetGuard(backend=backend, max_calls_per_day=5)

    for expected_count in range(1, 6):
        count = await guard.check_and_increment()
        assert count == expected_count


@pytest.mark.asyncio
async def test_budget_guard_raises_once_limit_exceeded():
    backend = _FakeCounterBackend()
    guard = LlmCallBudgetGuard(backend=backend, max_calls_per_day=2)

    await guard.check_and_increment()
    await guard.check_and_increment()

    with pytest.raises(LlmBudgetExceededError):
        await guard.check_and_increment()


@pytest.mark.asyncio
async def test_budget_guard_sets_expiry_only_on_first_call_of_the_day():
    backend = _FakeCounterBackend()
    guard = LlmCallBudgetGuard(backend=backend, max_calls_per_day=10)

    await guard.check_and_increment()
    await guard.check_and_increment()
    await guard.check_and_increment()

    assert len(backend.expire_calls) == 1
    _, seconds = backend.expire_calls[0]
    assert seconds > 24 * 60 * 60  # slightly over 24h, so a day never rolls over mid-key


@pytest.mark.asyncio
async def test_budget_guard_disabled_when_max_calls_non_positive():
    backend = _FakeCounterBackend()
    guard = LlmCallBudgetGuard(backend=backend, max_calls_per_day=0)

    # Should never touch the backend at all when disabled.
    for _ in range(100):
        count = await guard.check_and_increment()
        assert count == 0

    assert backend.counts == {}


@pytest.mark.asyncio
async def test_budget_guard_stays_exceeded_on_repeated_calls_past_the_limit():
    # Guards against a bug where the guard might "reset" or allow calls
    # through again after the first rejection — the whole point is a hard
    # ceiling, not a one-time warning.
    backend = _FakeCounterBackend()
    guard = LlmCallBudgetGuard(backend=backend, max_calls_per_day=1)

    await guard.check_and_increment()

    for _ in range(10):
        with pytest.raises(LlmBudgetExceededError):
            await guard.check_and_increment()
