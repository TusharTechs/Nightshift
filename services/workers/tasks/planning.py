"""Cognitive Task Planning worker task — Sprint 3 AI Trust & Execution.

Queue: celery:reasoning — planning is a reasoning step, following Sprint 2's
own precedent that `compile_shift_report` (which also aggregates AI-derived
data) runs on celery:reasoning too.

Runs immediately after `tasks.inspect_catalog` (which now dispatches this
task instead of `tasks.compile_shift_report` directly — see
`services/workers/tasks/inspection.py`'s last line). Fires
`tasks.compile_shift_report` itself once planning (and any synchronous
auto-execution) completes, so by the time the shift report compiles, every
Level-1/auto-approved-Level-2 task has already executed and verified (see
`PlanCognitiveTasks`'s module docstring for the full synchronous-execution
design rationale).

Sprint 4 Step 1 — Agent registry: `PlanCognitiveTasks` now takes a
`dict[str, Agent]`/`dict[str, uuid.UUID]` pair keyed by domain category
instead of a single hardcoded agent. Only `ProductQualityAgent` exists today,
so the registry built here has exactly one entry — but it's built the same
way a second specialist's entry will be added in Step 2+ (fetch its
`agents` table row via `SqlAgentRepository.get_by_identifier`, construct the
agent, add both to their respective dicts keyed by
`agent.domain_category`).
"""

from __future__ import annotations

import asyncio
import uuid as uuid_module

import structlog

from app.config import get_settings
from app.domain.agents.checkout_specialist import CheckoutSpecialistAgent
from app.domain.agents.product_quality import ProductQualityAgent
from app.domain.agents.theme_guardian import ThemeGuardianAgent
from app.domain.agents.tracking_specialist import TrackingSpecialistAgent
from app.domain.security import EncryptedPayload, TokenCipher
from app.infrastructure.database.repositories import (
    SqlAgentRepository,
    SqlApprovalRepository,
    SqlAuditLogRepository,
    SqlCognitiveTaskRepository,
    SqlExecutionRepository,
    SqlIssueRepository,
    SqlRollbackRepository,
    SqlShiftRepository,
    SqlStoreRepository,
    SqlStoreTokenRepository,
    SqlVerificationRepository,
)
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.llm.factory import build_llm_client
from app.infrastructure.messaging.celery_app import celery_app
from app.infrastructure.shopify_client import ShopifyGraphQLClient
from app.application.use_cases.execute_cognitive_task import ExecuteCognitiveTask
from app.application.use_cases.plan_cognitive_tasks import PlanCognitiveTasks
from app.application.use_cases.rollback_cognitive_task import RollbackCognitiveTask
from app.application.use_cases.verify_execution import VerifyExecution

logger = structlog.get_logger(component="planning_worker")

PLANNING_RETRY_COUNTDOWN_SECONDS = 30


@celery_app.task(name="tasks.plan_cognitive_tasks", bind=True, max_retries=3, acks_late=True)
def plan_cognitive_tasks_task(self, shift_id: str, store_id: str) -> None:
    try:
        asyncio.run(_run_planning(shift_id, store_id))
    except Exception as exc:  # noqa: BLE001 — deliberate: route all failures through Celery retry
        logger.warning(
            "plan_cognitive_tasks_retry",
            shift_id=shift_id,
            store_id=store_id,
            attempt=self.request.retries,
            status="retrying",
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=PLANNING_RETRY_COUNTDOWN_SECONDS)


async def _run_planning(shift_id: str, store_id: str) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    token_cipher = TokenCipher.from_base64_key(settings.nightshift_local_data_key)

    async with session_factory() as session:
        store_repo = SqlStoreRepository(session)
        token_repo = SqlStoreTokenRepository(session)
        shift_repo = SqlShiftRepository(session)
        issue_repo = SqlIssueRepository(session)
        agent_repo = SqlAgentRepository(session)
        cognitive_task_repo = SqlCognitiveTaskRepository(session)
        approval_repo = SqlApprovalRepository(session)
        execution_repo = SqlExecutionRepository(session)
        verification_repo = SqlVerificationRepository(session)
        rollback_repo = SqlRollbackRepository(session)
        audit_log_repo = SqlAuditLogRepository(session)

        store_uuid = uuid_module.UUID(store_id)
        shift_uuid = uuid_module.UUID(shift_id)

        store = await store_repo.get_by_id(store_uuid)
        if store is None:
            logger.error("plan_cognitive_tasks_store_not_found", store_id=store_id, status="error")
            return

        token_row = await token_repo.get_by_store_id(store_uuid)
        if token_row is None:
            logger.error("plan_cognitive_tasks_no_token", store_id=store_id, status="error")
            return
        access_token = token_cipher.decrypt(
            EncryptedPayload.deserialize(token_row.access_token_encrypted)
        )

        agent_record = await agent_repo.get_by_identifier("product-quality-agent")
        if agent_record is None:
            logger.error("plan_cognitive_tasks_agent_not_registered", store_id=store_id, status="error")
            return

        # Sprint 4 Step 2: Checkout Specialist. Missing this record is not
        # fatal to planning as a whole (mirrors discount_inspection.py's own
        # tolerance) — Product Quality planning still proceeds; only
        # DISCOUNT-category issues stay unplanned this cycle.
        checkout_agent_record = await agent_repo.get_by_identifier("checkout-specialist-agent")
        if checkout_agent_record is None:
            logger.warning(
                "plan_cognitive_tasks_checkout_agent_not_registered", store_id=store_id, status="warning"
            )

        # Sprint 4 Step 3: Theme Guardian + Tracking Specialist. Same
        # tolerance as Checkout Specialist above — a missing registry row
        # (migration 0005 not yet applied) only skips that specialist's
        # issues this cycle, never the whole shift.
        theme_agent_record = await agent_repo.get_by_identifier("theme-guardian-agent")
        if theme_agent_record is None:
            logger.warning(
                "plan_cognitive_tasks_theme_agent_not_registered", store_id=store_id, status="warning"
            )
        tracking_agent_record = await agent_repo.get_by_identifier("tracking-specialist-agent")
        if tracking_agent_record is None:
            logger.warning(
                "plan_cognitive_tasks_tracking_agent_not_registered", store_id=store_id, status="warning"
            )

        shopify_client = ShopifyGraphQLClient(
            shop_domain=store.shopify_domain,
            access_token=access_token,
            api_version=settings.shopify_api_version,
        )
        # `propose_action` (the Plan step) is deterministic and never calls
        # the LLM, but `ProductQualityAgent.__init__` requires a client —
        # constructing one is a cheap, no-network-call object build, so this
        # stays consistent with how the agent is constructed elsewhere
        # rather than special-casing a "no-op" client type.
        llm_client = build_llm_client(settings)

        try:
            product_quality_agent = ProductQualityAgent(client=llm_client)
            execute_cognitive_task = ExecuteCognitiveTask(
                cognitive_tasks=cognitive_task_repo,
                executions=execution_repo,
                audit_logs=audit_log_repo,
                shopify_client=shopify_client,
            )
            rollback_cognitive_task = RollbackCognitiveTask(
                executions=execution_repo,
                rollbacks=rollback_repo,
                cognitive_tasks=cognitive_task_repo,
                audit_logs=audit_log_repo,
                shopify_client=shopify_client,
            )
            verify_execution = VerifyExecution(
                cognitive_tasks=cognitive_task_repo,
                executions=execution_repo,
                verifications=verification_repo,
                issues=issue_repo,
                shifts=shift_repo,
                audit_logs=audit_log_repo,
                rollback_cognitive_task=rollback_cognitive_task,
                shopify_client=shopify_client,
            )
            # Agent registry (Sprint 4 Step 1, populated with its second
            # real entry in Step 2): keyed by domain_category so
            # `PlanCognitiveTasks` can route each issue to the right
            # specialist.
            agents = {product_quality_agent.domain_category: product_quality_agent}
            agent_record_ids = {product_quality_agent.domain_category: agent_record.id}
            if checkout_agent_record is not None:
                checkout_agent = CheckoutSpecialistAgent()
                agents[checkout_agent.domain_category] = checkout_agent
                agent_record_ids[checkout_agent.domain_category] = checkout_agent_record.id

            if theme_agent_record is not None:
                # Theme Guardian's `propose_action` is deterministic (it
                # only ever reads baseline/current content already captured
                # in evidence_data — see its own docstring), but its
                # constructor still requires an LLM client for interface
                # parity, exactly like ProductQualityAgent above.
                theme_agent = ThemeGuardianAgent(client=llm_client)
                agents[theme_agent.domain_category] = theme_agent
                agent_record_ids[theme_agent.domain_category] = theme_agent_record.id

            if tracking_agent_record is not None:
                tracking_agent = TrackingSpecialistAgent()
                agents[tracking_agent.domain_category] = tracking_agent
                agent_record_ids[tracking_agent.domain_category] = tracking_agent_record.id

            plan_cognitive_tasks = PlanCognitiveTasks(
                issues=issue_repo,
                cognitive_tasks=cognitive_task_repo,
                approvals=approval_repo,
                shifts=shift_repo,
                audit_logs=audit_log_repo,
                agents=agents,
                store=store,
                execute_cognitive_task=execute_cognitive_task,
                verify_execution=verify_execution,
                agent_record_ids=agent_record_ids,
            )

            result = await plan_cognitive_tasks.execute(shift_uuid, store_uuid)
        finally:
            await shopify_client.aclose()

        await session.commit()

        logger.info(
            "plan_cognitive_tasks_completed",
            store_id=store_id,
            shift_id=shift_id,
            tasks_planned=result.tasks_planned,
            auto_executed=result.auto_executed,
            pending_approval=result.pending_approval,
            status="success",
        )

    celery_app.send_task("tasks.compile_shift_report", args=[str(shift_uuid)])
