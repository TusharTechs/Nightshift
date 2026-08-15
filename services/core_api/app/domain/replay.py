"""Shift Replay / Work Log icon mapping — Sprint 4 Step 5.

Pure domain logic, no I/O. A second, explicitly separate icon convention
from `domain/chief_ops.py`'s per-*issue* 🟢/⚡/🧠 assignment (see
CONFLICTS.md item 46 for why the two can't share one mapping): this one is
keyed by the real `audit_logs.action` string every use case in this
codebase already writes (`TASK_PLANNED`, `EXECUTION_COMPLETED`,
`VERIFICATION_PASSED`, etc. — see `plan_cognitive_tasks.py`,
`execute_cognitive_task.py`, `verify_execution.py`,
`handle_approval_action.py`, `rollback_cognitive_task.py`,
`trigger_demo_incident.py`), never invented or LLM-derived. Reused by both
`GET /api/v1/work-log` (Sprint 3) and the new `GET
/api/v1/shifts/{shift_id}/replay` (Step 5) so the two surfaces never render
this event with two different icons.
"""

from __future__ import annotations

ACTION_ICONS: dict[str, str] = {
    "TASK_PLANNED": "🧠",
    "APPROVAL_REQUESTED": "🧠",
    "APPROVAL_GRANTED": "✅",
    # Same underlying merchant decision as a plain APPROVAL_GRANTED — the
    # "with modification" detail lives in the entry's own rationale/
    # execution_override_params, not in the icon.
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
DEFAULT_ICON = "🔹"
"""Never raises on an unrecognized action — a future action type added
without an entry here degrades to a neutral bullet, never a crash."""


def icon_for_action(action: str) -> str:
    return ACTION_ICONS.get(action, DEFAULT_ICON)
