"""Integration test: Sprint 3's `pending_approvals[]` / `completed_tasks[]`
sections of the Morning Shift Report.

Scope note: `workers/tasks/shift_report.py::_run_compilation` builds its own
`AsyncEngine`/session factory directly from `Settings` (via
`create_engine(settings.database_url)`) — it cannot be pointed at the
`InMemory*` fakes without a real Postgres connection, which is not available
in this test environment (same constraint `test_full_shift_compilation_workflow.py`
already documents for `tasks.compile_shift_report`). This test therefore
mirrors `_run_compilation`'s own `pending_approvals`/`completed_tasks`
assembly logic verbatim (same field names, same source repositories) against
seeded `InMemory*` fakes, then feeds the result into the real, production
`domain.shift_compiler.compile_shift_report` — the actual DB-session-wiring
half of the worker task (lines building `create_engine`/`create_session_factory`
and committing) is the one piece left untested here; that gap is called out
in the Sprint 3 completion report as a Known Limitation / manual QA item,
same as Sprint 2's own precedent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.application.ports import (
    InMemoryApprovalRepository,
    InMemoryCognitiveTaskRepository,
    InMemoryExecutionRepository,
    InMemoryIssueRepository,
    InMemoryVerificationRepository,
)
from app.domain.health import calculate_store_health
from app.domain.shift_compiler import CompiledIssue, compile_shift_report

EXPECTED_PENDING_APPROVAL_FIELDS = {
    "approval_id", "issue_id", "title", "risk_level", "recommended_action",
    "revenue_impact_usd", "confidence_score", "expires_at",
}
EXPECTED_COMPLETED_TASK_FIELDS = {
    "task_id", "category", "title", "risk_level", "verified", "verified_at",
}


async def _build_pending_approvals(approval_repo, cognitive_task_repo, issue_repo, shift_id) -> list[dict]:
    """Verbatim mirror of `workers/tasks/shift_report.py::_run_compilation`'s
    own pending_approvals[] assembly loop."""
    pending_approvals: list[dict] = []
    for approval in await approval_repo.list_for_shift(shift_id):
        if approval.status != "PENDING":
            continue
        approval_task = await cognitive_task_repo.get_by_id(approval.task_id)
        approval_issue = await issue_repo.get_by_id(approval.issue_id)
        if approval_task is None or approval_issue is None:
            continue
        pending_approvals.append(
            {
                "approval_id": str(approval.id),
                "issue_id": str(approval_issue.id),
                "title": approval_issue.title,
                "risk_level": approval_task.risk_level,
                "recommended_action": approval_task.action_type,
                "revenue_impact_usd": float(approval_issue.revenue_impact_estimate),
                "confidence_score": approval_task.confidence_assessment.get("overall_score", 0.0),
                "expires_at": approval.expires_at.isoformat(),
            }
        )
    return pending_approvals


async def _build_completed_tasks(cognitive_task_repo, issue_repo, execution_repo, verification_repo, shift_id) -> list[dict]:
    """Verbatim mirror of `workers/tasks/shift_report.py::_run_compilation`'s
    own completed_tasks[] assembly loop."""
    completed_tasks: list[dict] = []
    for task in await cognitive_task_repo.list_for_shift(shift_id):
        if task.status != "SUCCESS":
            continue
        task_issue = await issue_repo.get_by_id(task.issue_id)
        execution = await execution_repo.get_by_task_id(task.id)
        verified = False
        verified_at = None
        if execution is not None:
            verification = await verification_repo.get_by_execution_id(execution.id)
            if verification is not None:
                verified = verification.status == "PASSED"
                verified_at = verification.verified_at
        completed_tasks.append(
            {
                "task_id": str(task.id),
                "category": task_issue.category if task_issue else "",
                "title": task_issue.title if task_issue else task.action_type,
                "risk_level": task.risk_level,
                "verified": verified,
                "verified_at": verified_at.isoformat() if verified_at else None,
            }
        )
    return completed_tasks


async def test_shift_report_contains_pending_approvals_and_completed_tasks_with_correct_field_names():
    store_id, shift_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)

    issues = InMemoryIssueRepository()
    cognitive_tasks = InMemoryCognitiveTaskRepository()
    approvals = InMemoryApprovalRepository(cognitive_tasks)
    executions = InMemoryExecutionRepository()
    verifications = InMemoryVerificationRepository()

    # --- One PENDING_APPROVAL task (Level 2 Moderate, awaiting merchant) ---
    pending_issue = await issues.create(
        store_id=store_id, shift_id=shift_id, agent_id=uuid.uuid4(),
        category="PRODUCT_QUALITY", severity="MEDIUM",
        title="Thin product description on 'Gadget'",
        description="Only 10 words.",
        evidence_data={"fix_check": "thin_description"},
        affected_resources=["gid://shopify/Product/9"],
        revenue_impact_estimate=75.0, confidence_score=0.8,
    )
    pending_task = await cognitive_tasks.create(
        issue_id=pending_issue.id, store_id=store_id, shift_id=shift_id, agent_id=uuid.uuid4(),
        action_type="REWRITE_PRODUCT_DESCRIPTION", execution_plan={}, risk_level="LEVEL_2_MODERATE",
        risk_reasoning="moderate risk", idempotency_key=f"{pending_issue.id}:REWRITE_PRODUCT_DESCRIPTION",
        status="PENDING_APPROVAL", confidence_assessment={"overall_score": 0.72},
    )
    await approvals.create(
        task_id=pending_task.id, issue_id=pending_issue.id, store_id=store_id,
        expires_at=now + timedelta(hours=24),
    )

    # --- One SUCCESS/verified-PASSED task (Level 1 Safe, auto-executed) ----
    completed_issue = await issues.create(
        store_id=store_id, shift_id=shift_id, agent_id=uuid.uuid4(),
        category="PRODUCT_QUALITY", severity="LOW",
        title="Image missing ALT text on 'Widget'",
        description="No ALT text.",
        evidence_data={"fix_check": "missing_alt_text"},
        affected_resources=["gid://shopify/Product/1", "gid://shopify/ProductImage/2"],
        revenue_impact_estimate=50.0, confidence_score=0.95,
    )
    completed_task = await cognitive_tasks.create(
        issue_id=completed_issue.id, store_id=store_id, shift_id=shift_id, agent_id=uuid.uuid4(),
        action_type="GENERATE_ALT_TEXT", execution_plan={}, risk_level="LEVEL_1_SAFE",
        risk_reasoning="safe", idempotency_key=f"{completed_issue.id}:GENERATE_ALT_TEXT",
        status="SUCCESS",
    )
    execution = await executions.create(task_id=completed_task.id, store_id=store_id, request_payload={})
    await executions.mark_completed(execution.id, response_payload={}, execution_duration_ms=10)
    await verifications.create(
        execution_id=execution.id, task_id=completed_task.id, store_id=store_id,
        status="PASSED", method="READ_AFTER_WRITE", result_data={},
    )

    pending_approvals = await _build_pending_approvals(approvals, cognitive_tasks, issues, shift_id)
    completed_tasks = await _build_completed_tasks(cognitive_tasks, issues, executions, verifications, shift_id)

    assert len(pending_approvals) == 1
    assert len(completed_tasks) == 1
    assert set(pending_approvals[0].keys()) == EXPECTED_PENDING_APPROVAL_FIELDS
    assert set(completed_tasks[0].keys()) == EXPECTED_COMPLETED_TASK_FIELDS

    assert pending_approvals[0]["title"] == "Thin product description on 'Gadget'"
    assert pending_approvals[0]["risk_level"] == "LEVEL_2_MODERATE"
    assert pending_approvals[0]["recommended_action"] == "REWRITE_PRODUCT_DESCRIPTION"
    assert pending_approvals[0]["revenue_impact_usd"] == 75.0
    assert pending_approvals[0]["confidence_score"] == 0.72

    assert completed_tasks[0]["title"] == "Image missing ALT text on 'Widget'"
    assert completed_tasks[0]["category"] == "PRODUCT_QUALITY"
    assert completed_tasks[0]["risk_level"] == "LEVEL_1_SAFE"
    assert completed_tasks[0]["verified"] is True
    assert completed_tasks[0]["verified_at"] is not None

    # --- Feed into the real, production compiler --------------------------
    started_at = now - timedelta(hours=6)
    payload = compile_shift_report(
        shift_id=str(shift_id),
        shift_number=1,
        started_at=started_at,
        completed_at=now,
        issues=[
            CompiledIssue(
                id=str(pending_issue.id), category="PRODUCT_QUALITY", severity="MEDIUM",
                status="AWAITING_APPROVAL", title=pending_issue.title, description=pending_issue.description,
                revenue_impact_estimate=75.0, confidence_score=0.8, affected_resources=pending_issue.affected_resources,
            ),
            CompiledIssue(
                id=str(completed_issue.id), category="PRODUCT_QUALITY", severity="LOW",
                status="RESOLVED", title=completed_issue.title, description=completed_issue.description,
                revenue_impact_estimate=50.0, confidence_score=0.95, affected_resources=completed_issue.affected_resources,
            ),
        ],
        health_result=calculate_store_health([]),
        issues_resolved=1,
        pending_approvals=pending_approvals,
        completed_tasks=completed_tasks,
    )

    api_response = payload.to_api_response()
    assert api_response["pending_approvals"] == pending_approvals
    assert api_response["completed_tasks"] == completed_tasks
    assert len(api_response["pending_approvals"]) == 1
    assert len(api_response["completed_tasks"]) == 1


async def test_shift_report_pending_approvals_and_completed_tasks_default_to_empty_lists():
    """Sprint 2 callers that never pass these Sprint 3 fields still work
    unchanged — the dataclass defaults them to `[]`, not `None`."""
    payload = compile_shift_report(
        shift_id=str(uuid.uuid4()),
        shift_number=1,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        issues=[],
        health_result=calculate_store_health([]),
    )
    api_response = payload.to_api_response()
    assert api_response["pending_approvals"] == []
    assert api_response["completed_tasks"] == []
