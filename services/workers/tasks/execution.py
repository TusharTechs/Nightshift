"""Cognitive Task Execution worker task — Sprint 3 AI Trust & Execution.

Queue: celery:execution. This is the ONE truly asynchronous execution path
in the whole lifecycle — dispatched only by `HandleApprovalAction` when a
merchant grants an APPROVE decision on a task that required approval. The
synchronous auto-execute path (Level-1/auto-approved-Level-2 tasks) never
goes through this Celery task at all; it calls `ExecuteCognitiveTask.execute()`
directly, in-process, from `tasks.plan_cognitive_tasks`
(`application/use_cases/plan_cognitive_tasks.py`). No double-execution risk
either way, since `ExecuteCognitiveTask.execute()`'s own idempotency check
(an existing terminal `executions` row short-circuits a re-run) protects
against ever mutating Shopify twice for the same task.

After a successful execution, this task enqueues `tasks.verify_execution`
itself (celery:verification) — the async path's own Verify step, mirroring
what the synchronous path does in-process immediately after execution.
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
    SqlStoreRepository,
    SqlStoreTokenRepository,
)
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.messaging.celery_app import celery_app
from app.infrastructure.shopify_client import ShopifyGraphQLClient
from app.application.use_cases.execute_cognitive_task import ExecuteCognitiveTask

logger = structlog.get_logger(component="execution_worker")

EXECUTION_RETRY_COUNTDOWN_SECONDS = 30


@celery_app.task(name="tasks.execute_cognitive_task", bind=True, max_retries=3, acks_late=True)
def execute_cognitive_task_task(self, task_id: str) -> None:
    try:
        asyncio.run(_run_execution(task_id))
    except Exception as exc:  # noqa: BLE001 — deliberate: route all failures through Celery retry
        logger.warning(
            "execute_cognitive_task_task_retry",
            task_id=task_id,
            attempt=self.request.retries,
            status="retrying",
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=EXECUTION_RETRY_COUNTDOWN_SECONDS)


async def _run_execution(task_id: str) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    token_cipher = TokenCipher.from_base64_key(settings.nightshift_local_data_key)

    async with session_factory() as session:
        cognitive_task_repo = SqlCognitiveTaskRepository(session)
        execution_repo = SqlExecutionRepository(session)
        audit_log_repo = SqlAuditLogRepository(session)
        store_repo = SqlStoreRepository(session)
        token_repo = SqlStoreTokenRepository(session)

        task_uuid = uuid_module.UUID(task_id)
        task = await cognitive_task_repo.get_by_id(task_uuid)
        if task is None:
            logger.error("execute_cognitive_task_not_found", task_id=task_id, status="error")
            return

        store = await store_repo.get_by_id(task.store_id)
        if store is None:
            logger.error("execute_cognitive_task_store_not_found", task_id=task_id, status="error")
            return

        token_row = await token_repo.get_by_store_id(task.store_id)
        if token_row is None:
            logger.error("execute_cognitive_task_no_token", task_id=task_id, status="error")
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
            use_case = ExecuteCognitiveTask(
                cognitive_tasks=cognitive_task_repo,
                executions=execution_repo,
                audit_logs=audit_log_repo,
                shopify_client=shopify_client,
            )
            execution = await use_case.execute(task_uuid)
        finally:
            await shopify_client.aclose()

        await session.commit()

        logger.info(
            "execute_cognitive_task_task_completed",
            task_id=task_id,
            execution_id=str(execution.id),
            execution_status=execution.status,
            status="success",
        )

    if execution.status == "COMPLETED":
        celery_app.send_task("tasks.verify_execution", args=[str(execution.id)])
