"""Execution Verification worker task — Sprint 3 AI Trust & Execution.

Queue: celery:verification. Thin Celery wrapper around
`VerifyExecution.execute(execution_id)` — the async path's Verify step,
dispatched by `tasks.execute_cognitive_task` after a successful mutation.
The synchronous auto-execute path calls `VerifyExecution.execute()` directly,
in-process, from `tasks.plan_cognitive_tasks` instead of going through this
task at all.
"""

from __future__ import annotations

import asyncio
import uuid as uuid_module

import structlog

from app.config import get_settings
from app.domain.security import EncryptedPayload, TokenCipher
from app.infrastructure.database.repositories import (
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
from app.infrastructure.messaging.celery_app import celery_app
from app.infrastructure.shopify_client import ShopifyGraphQLClient
from app.application.use_cases.rollback_cognitive_task import RollbackCognitiveTask
from app.application.use_cases.verify_execution import VerifyExecution

logger = structlog.get_logger(component="verification_worker")

VERIFICATION_RETRY_COUNTDOWN_SECONDS = 30


@celery_app.task(name="tasks.verify_execution", bind=True, max_retries=3, acks_late=True)
def verify_execution_task(self, execution_id: str) -> None:
    try:
        asyncio.run(_run_verification(execution_id))
    except Exception as exc:  # noqa: BLE001 — deliberate: route all failures through Celery retry
        logger.warning(
            "verify_execution_task_retry",
            execution_id=execution_id,
            attempt=self.request.retries,
            status="retrying",
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=VERIFICATION_RETRY_COUNTDOWN_SECONDS)


async def _run_verification(execution_id: str) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    token_cipher = TokenCipher.from_base64_key(settings.nightshift_local_data_key)

    async with session_factory() as session:
        cognitive_task_repo = SqlCognitiveTaskRepository(session)
        execution_repo = SqlExecutionRepository(session)
        verification_repo = SqlVerificationRepository(session)
        issue_repo = SqlIssueRepository(session)
        shift_repo = SqlShiftRepository(session)
        rollback_repo = SqlRollbackRepository(session)
        audit_log_repo = SqlAuditLogRepository(session)
        store_repo = SqlStoreRepository(session)
        token_repo = SqlStoreTokenRepository(session)

        execution_uuid = uuid_module.UUID(execution_id)
        execution = await execution_repo.get_by_id(execution_uuid)
        if execution is None:
            logger.error("verify_execution_not_found", execution_id=execution_id, status="error")
            return

        store = await store_repo.get_by_id(execution.store_id)
        if store is None:
            logger.error("verify_execution_store_not_found", execution_id=execution_id, status="error")
            return

        token_row = await token_repo.get_by_store_id(execution.store_id)
        if token_row is None:
            logger.error("verify_execution_no_token", execution_id=execution_id, status="error")
            return
        access_token = token_cipher.decrypt(
            EncryptedPayload.deserialize(token_row.access_token_encrypted)
        )

        shopify_client = ShopifyGraphQLClient(
            shop_domain=store.shopify_domain,
            access_token=access_token,
            api_version=settings.shopify_api_version,
        )
        try:
            rollback_cognitive_task = RollbackCognitiveTask(
                executions=execution_repo,
                rollbacks=rollback_repo,
                cognitive_tasks=cognitive_task_repo,
                audit_logs=audit_log_repo,
                shopify_client=shopify_client,
            )
            use_case = VerifyExecution(
                cognitive_tasks=cognitive_task_repo,
                executions=execution_repo,
                verifications=verification_repo,
                issues=issue_repo,
                shifts=shift_repo,
                audit_logs=audit_log_repo,
                rollback_cognitive_task=rollback_cognitive_task,
                shopify_client=shopify_client,
            )
            verification = await use_case.execute(execution_uuid)
        finally:
            await shopify_client.aclose()

        await session.commit()

        logger.info(
            "verify_execution_task_completed",
            execution_id=execution_id,
            verification_status=verification.status,
            status="success",
        )
