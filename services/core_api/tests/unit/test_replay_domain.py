"""Unit tests for the Shift Replay / Work Log icon mapping (Sprint 4 Step 5,
`domain/replay.py`). Pure, deterministic, no I/O."""

from __future__ import annotations

from app.domain.replay import DEFAULT_ICON, icon_for_action


def test_icon_for_action_maps_every_real_action_string_used_in_this_codebase():
    # Every one of these is a real `action=` value written by an existing
    # use case (see `domain/replay.py`'s own module docstring for the exact
    # call sites) — never a hypothetical/invented action string.
    expectations = {
        "TASK_PLANNED": "🧠",
        "APPROVAL_REQUESTED": "🧠",
        "APPROVAL_GRANTED": "✅",
        "APPROVAL_GRANTED_WITH_MODIFICATION": "✅",
        "EXECUTION_COMPLETED": "⚡",
        "ROLLBACK_COMPLETED": "⚡",
        "VERIFICATION_PASSED": "🟢",
        "APPROVAL_REJECTED": "🙅",
        "APPROVAL_DEFERRED": "⏸️",
        "EXECUTION_FAILED": "⚠️",
        "VERIFICATION_FAILED": "⚠️",
        "ROLLBACK_FAILED": "⚠️",
        "DEMO_INCIDENT_TRIGGERED": "🎬",
    }
    for action, expected_icon in expectations.items():
        assert icon_for_action(action) == expected_icon


def test_icon_for_action_falls_back_to_default_for_unrecognized_action():
    assert icon_for_action("SOME_FUTURE_ACTION_TYPE") == DEFAULT_ICON
