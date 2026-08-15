"""LLM call budget guard — Sprint 2 hardening, added 2026-07-31 at the
user's explicit request to bound API spend while testing against a real,
metered Gemini API key (see DECISIONS.md ADR-024).

Hard daily ceiling on the number of LLM API calls this deployment makes,
backed by Redis so it holds across Celery worker processes and restarts —
not just a single Python process, and not reset by a task retry. Exists
specifically so a retry storm, a bug, or an accidental repeated test trigger
can never run away and exhaust a developer's API credits: once the ceiling
is hit, `ProductQualityAgent` falls back to its existing deterministic
rule-based path instead of ever reaching the network again that day.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class LlmBudgetExceededError(Exception):
    """Raised — without making a network call — once the day's LLM call
    budget is already spent."""


class AsyncCounterBackend(Protocol):
    """Minimal surface this guard needs from an async Redis-like client.
    Kept as a narrow Protocol (rather than depending on `redis.asyncio`
    directly) so tests can inject a trivial in-memory fake instead of a
    real Redis connection."""

    async def incr(self, key: str) -> int: ...
    async def expire(self, key: str, seconds: int) -> object: ...


class LlmCallBudgetGuard:
    def __init__(self, *, backend: AsyncCounterBackend, max_calls_per_day: int) -> None:
        self._backend = backend
        self._max_calls_per_day = max_calls_per_day

    async def check_and_increment(self) -> int:
        """Increments today's call counter and returns the new count.

        Raises `LlmBudgetExceededError` once the configured daily maximum
        is exceeded (the counter is still incremented on the call that
        crosses the threshold, so the ceiling stays enforced under
        concurrent callers rather than racing back under the limit).

        A non-positive `max_calls_per_day` disables the guard entirely —
        this is intentionally NOT the default, since the whole point of
        this class is to be a safety net that's on unless someone
        deliberately turns it off.
        """
        if self._max_calls_per_day <= 0:
            return 0

        day_key = f"llm:call_budget:{datetime.now(timezone.utc):%Y-%m-%d}"
        count = await self._backend.incr(day_key)
        if count == 1:
            await self._backend.expire(day_key, 60 * 60 * 26)  # slightly over 24h

        if count > self._max_calls_per_day:
            raise LlmBudgetExceededError(
                f"LLM daily call budget exceeded ({count} > {self._max_calls_per_day}). "
                "Falling back to deterministic rule-based analysis for the rest of today."
            )
        return count
