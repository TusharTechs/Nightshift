"""Merchant Memory — Sprint 3, pragmatic scope (approved 2026-08-01).

Rather than building the PRD's full 6-category memory architecture, this
derives merchant preference signals directly from existing `approvals`
history: "has this merchant rejected this exact action type at least twice
before?" The concrete example from the Sprint 3 brief: "Merchant rejected
automatic image edits twice -> future confidence -> require approval."
"""

from __future__ import annotations

REJECTION_THRESHOLD = 2


def has_repeated_rejection_history(rejection_count: int) -> bool:
    """`rejection_count` is the number of past REJECTED approvals for this
    exact (store_id, action_type) pair, as counted by the repository layer
    (see ApprovalRepository.count_rejections_for_action_type in Part 2 —
    not built yet, this function just takes the count as a plain int so it
    stays pure/unit-testable)."""
    return rejection_count >= REJECTION_THRESHOLD
