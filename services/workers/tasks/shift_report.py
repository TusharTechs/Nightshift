"""Morning Shift Report Compiler worker task — Sprint 2 Feature 4.

Queue: celery:reasoning — report compilation aggregates the AI-derived issue
data that `tasks.inspect_catalog` just persisted (Sprint 2's own spec does
not name an explicit queue for this task).
"""

from __future__ import annotations

import asyncio
import uuid as uuid_module

import redis.asyncio as aioredis
import structlog

from app.config import get_settings
from app.domain.chief_ops import (
    ChiefOpsSynthesizer,
    build_specialist_turns,
    deterministic_briefing,
    should_synthesize,
)
from app.domain.confidence import merchant_memory_note
from app.domain.enums import IssueCategory, IssueSeverity
from app.domain.health import ScoredIssue, calculate_store_health
from app.domain.shift_compiler import CompiledIssue, compile_shift_report
from app.infrastructure.llm.budget_guard import LlmCallBudgetGuard
from app.infrastructure.llm.factory import build_chief_ops_llm_client
from app.infrastructure.database.repositories import (
    SqlApprovalRepository,
    SqlCognitiveTaskRepository,
    SqlExecutionRepository,
    SqlIssueRepository,
    SqlShiftReportRepository,
    SqlShiftRepository,
    SqlVerificationRepository,
)
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.messaging.celery_app import celery_app

logger = structlog.get_logger(component="shift_report_worker")


@celery_app.task(name="tasks.compile_shift_report", bind=True, max_retries=3, acks_late=True)
def compile_shift_report_task(self, shift_id: str) -> None:
    try:
        asyncio.run(_run_compilation(shift_id))
    except Exception as exc:  # noqa: BLE001 — deliberate: route all failures through Celery retry
        logger.warning(
            "compile_shift_report_retry",
            shift_id=shift_id,
            attempt=self.request.retries,
            status="retrying",
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=30)


async def _run_compilation(shift_id: str) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        shift_repo = SqlShiftRepository(session)
        issue_repo = SqlIssueRepository(session)
        report_repo = SqlShiftReportRepository(session)
        # Sprint 3: needed to build the persisted `pending_approvals[]` /
        # `completed_tasks[]` sections of the Morning Shift Report.
        cognitive_task_repo = SqlCognitiveTaskRepository(session)
        approval_repo = SqlApprovalRepository(session)
        execution_repo = SqlExecutionRepository(session)
        verification_repo = SqlVerificationRepository(session)

        shift_uuid = uuid_module.UUID(shift_id)
        shift = await shift_repo.get_by_id(shift_uuid)
        if shift is None:
            logger.error("compile_shift_report_shift_not_found", shift_id=shift_id, status="error")
            return

        issues = await issue_repo.list_for_shift(shift_uuid)

        compiled_issues = [
            CompiledIssue(
                id=str(issue.id),
                category=issue.category,
                severity=issue.severity,
                status=issue.status,
                title=issue.title,
                description=issue.description,
                revenue_impact_estimate=float(issue.revenue_impact_estimate),
                confidence_score=issue.confidence_score,
                affected_resources=issue.affected_resources,
            )
            for issue in issues
        ]
        scored_issues = [
            ScoredIssue(category=IssueCategory(issue.category), severity=IssueSeverity(issue.severity))
            for issue in issues
        ]
        health_result = calculate_store_health(scored_issues)

        await shift_repo.mark_completed(shift_uuid)

        revenue_protected = sum(issue.revenue_impact_estimate for issue in compiled_issues)
        time_saved = round(len(compiled_issues) * 0.25, 2)
        await shift_repo.update_metrics(
            shift_uuid,
            issues_detected_count=len(compiled_issues),
            estimated_revenue_protected=revenue_protected,
            estimated_time_saved_hours=time_saved,
        )

        refreshed_shift = await shift_repo.get_by_id(shift_uuid)
        completed_at = refreshed_shift.completed_at if refreshed_shift else None
        issues_resolved = refreshed_shift.issues_resolved_count if refreshed_shift else 0

        # --- Sprint 3: pending_approvals[] / completed_tasks[] --------------
        # Sprint 5 Phase 5: also collected here (issue_id -> note) so Chief
        # Ops AI's turns can carry a grounded "🧠 Merchant Preference
        # Applied" note — see `domain/confidence.py::merchant_memory_note`.
        merchant_memory_notes: dict[str, str] = {}

        completed_tasks: list[dict] = []
        for task in await cognitive_task_repo.list_for_shift(shift_uuid):
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
            note = merchant_memory_note(task.confidence_assessment)
            if note:
                merchant_memory_notes[str(task.issue_id)] = note
            completed_tasks.append(
                {
                    "task_id": str(task.id),
                    # Sprint 4 Step 4: needed so Chief Ops AI's turn-building
                    # can tell which issues were actually auto-executed this
                    # shift (the ⚡ icon) — see `domain/chief_ops.py`.
                    "issue_id": str(task.issue_id),
                    "category": task_issue.category if task_issue else "",
                    "title": task_issue.title if task_issue else task.action_type,
                    "risk_level": task.risk_level,
                    "verified": verified,
                    "verified_at": verified_at.isoformat() if verified_at else None,
                }
            )

        pending_approvals: list[dict] = []
        for approval in await approval_repo.list_for_shift(shift_uuid):
            if approval.status != "PENDING":
                continue
            approval_task = await cognitive_task_repo.get_by_id(approval.task_id)
            approval_issue = await issue_repo.get_by_id(approval.issue_id)
            if approval_task is None or approval_issue is None:
                continue
            note = merchant_memory_note(approval_task.confidence_assessment)
            if note:
                merchant_memory_notes[str(approval_issue.id)] = note
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

        # --- Sprint 4 Step 4: Chief Ops AI synthesis (Multi-Agent Handshake) -
        resolved_issue_ids = {t["issue_id"] for t in completed_tasks}
        issue_timestamps = {str(issue.id): issue.created_at.isoformat() for issue in issues}
        turns = build_specialist_turns(
            compiled_issues,
            resolved_issue_ids=resolved_issue_ids,
            timestamps=issue_timestamps,
            merchant_memory_notes=merchant_memory_notes,
        )
        if should_synthesize(turns):
            # Gemini integration point (productionization phase): Chief Ops
            # AI's Executive Briefing is hard-wired to
            # `build_chief_ops_llm_client` (Gemini by default, independent of
            # `llm_provider`) — NOT `build_llm_client`, which the specialist
            # inspection tasks (inspection.py/theme_inspection.py/planning.py)
            # use for per-specialist detection. This is the one call site
            # in the whole "Shopify observations -> specialist findings ->
            # evidence/risk/revenue impact -> Gemini -> executive briefing ->
            # merchant" flow — see `infrastructure/llm/factory.py`.
            llm_client = build_chief_ops_llm_client(settings)
            redis_client = aioredis.from_url(settings.redis_url)
            try:
                budget_guard = LlmCallBudgetGuard(
                    backend=redis_client, max_calls_per_day=settings.llm_max_calls_per_day
                )
                synthesizer = ChiefOpsSynthesizer(client=llm_client, budget_guard=budget_guard)
                chief_ops_briefing = (await synthesizer.synthesize(turns)).to_dict()
                logger.info(
                    "chief_ops_briefing_llm_provider",
                    shift_id=shift_id,
                    provider=settings.chief_ops_llm_provider,
                    model=llm_client.model_name,
                    used_llm=chief_ops_briefing.get("used_llm"),
                )
            finally:
                await redis_client.aclose()
        else:
            chief_ops_briefing = deterministic_briefing(turns).to_dict()

        payload = compile_shift_report(
            shift_id=str(shift.id),
            shift_number=shift.shift_number,
            started_at=shift.started_at,
            completed_at=completed_at,
            issues=compiled_issues,
            health_result=health_result,
            issues_resolved=issues_resolved,
            pending_approvals=pending_approvals,
            completed_tasks=completed_tasks,
            chief_ops_briefing=chief_ops_briefing,
        )

        await report_repo.create(
            shift_id=shift.id,
            store_id=shift.store_id,
            executive_summary=payload.executive_summary,
            report_json=payload.to_api_response(),
        )
        await session.commit()

        logger.info(
            "shift_report_compiled",
            store_id=str(shift.store_id),
            shift_id=shift_id,
            health_score=health_result.score,
            issues_detected=len(compiled_issues),
            estimated_revenue_protected_usd=payload.estimated_revenue_protected,
            status="success",
        )
