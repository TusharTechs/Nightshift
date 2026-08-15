"""Unit tests: Merchant Memory (Sprint 3 AI Trust & Execution).

`has_repeated_rejection_history` is a pure function over a plain int
(`rejection_count`) — the pragmatic scope approved 2026-08-01, deriving
merchant-preference signals directly from `approvals` history rather than
building the PRD's full 6-category memory architecture. Threshold is
`REJECTION_THRESHOLD = 2` (>=2, not >2) per `app/domain/merchant_memory.py`.
"""

from __future__ import annotations

from app.domain.merchant_memory import REJECTION_THRESHOLD, has_repeated_rejection_history


def test_rejection_threshold_constant_is_two():
    assert REJECTION_THRESHOLD == 2


def test_has_repeated_rejection_history_zero_rejections_is_false():
    assert has_repeated_rejection_history(0) is False


def test_has_repeated_rejection_history_one_rejection_is_false():
    assert has_repeated_rejection_history(1) is False


def test_has_repeated_rejection_history_two_rejections_is_true():
    assert has_repeated_rejection_history(2) is True


def test_has_repeated_rejection_history_five_rejections_is_true():
    assert has_repeated_rejection_history(5) is True
