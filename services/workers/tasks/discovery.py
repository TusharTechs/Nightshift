"""Baseline store discovery task (Sprint 1 Feature 3 / Section 1.13).

Queue: celery:observation
Retry strategy: max 5 retries, exponential backoff (10s, 30s, 90s, 270s, 810s).

Execution steps (verbatim from the Sprint 1 spec):
  1. Decrypt access_token from store_tokens.
  2. Initialize ShopifyGraphQLClient with rate-limit bucket awareness.
  3. Execute baseline queries: shop, products(first:250), discountNodes(first:50),
     themes(first:10), scriptTags(first:50).
  4. Aggregate findings and write initial baseline metric to metrics_hourly.
  5. Publish completion event to Redis PubSub topic store:{store_id}:discovery.

PII exclusion (Feature 3 Out-of-Scope / Security / AI Spec Safety Rule):
customer order data and PII are never fetched or persisted by this task.
"""

from __future__ import annotations

import asyncio

import redis.asyncio as aioredis
import structlog

from app.config import get_settings
from app.domain.health import calculate_store_health
from app.domain.security import EncryptedPayload, TokenCipher
from app.infrastructure.database.repositories import (
    SqlMetricsRepository,
    SqlStoreRepository,
    SqlStoreTokenRepository,
)
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.messaging.celery_app import celery_app
from app.infrastructure.shopify_client import ShopifyGraphQLClient

logger = structlog.get_logger(component="discovery_worker")

RETRY_BACKOFF_SECONDS = (10, 30, 90, 270, 810)


@celery_app.task(
    name="tasks.store_discovery",
    bind=True,
    max_retries=len(RETRY_BACKOFF_SECONDS),
    acks_late=True,
    reject_on_worker_lost=True,
)
def store_discovery_task(self, store_id: str) -> None:
    try:
        asyncio.run(_run_discovery(store_id))
    except Exception as exc:  # noqa: BLE001 — deliberate: route all failures through Celery retry
        attempt = self.request.retries
        countdown = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
        logger.warning(
            "discovery_task_retry",
            store_id=store_id,
            attempt=attempt,
            countdown=countdown,
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=countdown)


async def _run_discovery(store_id: str) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    token_cipher = TokenCipher.from_base64_key(settings.nightshift_local_data_key)
    redis_client = aioredis.from_url(settings.redis_url)

    async with session_factory() as session:
        store_repo = SqlStoreRepository(session)
        token_repo = SqlStoreTokenRepository(session)
        metrics_repo = SqlMetricsRepository(session)

        import uuid as uuid_module

        store_uuid = uuid_module.UUID(store_id)
        store = await store_repo.get_by_id(store_uuid)
        if store is None:
            logger.error("discovery_task_store_not_found", store_id=store_id)
            return

        # Step 1: decrypt access_token from store_tokens.
        token_row = await token_repo.get_by_store_id(store_uuid)
        if token_row is None:
            logger.error("discovery_task_no_token", store_id=store_id)
            return

        access_token = token_cipher.decrypt(
            EncryptedPayload.deserialize(token_row.access_token_encrypted)
        )

        # Step 2: initialize ShopifyGraphQLClient with rate-limit awareness.
        client = ShopifyGraphQLClient(
            shop_domain=store.shopify_domain,
            access_token=access_token,
            api_version=settings.shopify_api_version,
        )

        try:
            # Step 3: execute baseline queries.
            snapshot = await client.fetch_baseline_snapshot()
        finally:
            await client.aclose()

        products = snapshot.get("products", [])
        # Sprint 1's baseline scan runs before any issue-detection agent
        # exists — there are no issues to score yet, so the baseline health
        # score is the neutral ceiling (100) until Sprint 2's nightly
        # inspection shift runs and calculate_store_health receives real
        # issues. This replaces Sprint 1's own weighted-average category
        # scoring, which domain/health.py no longer implements (see
        # CONFLICTS.md item 10 / DECISIONS.md ADR-014 — Sprint 2's
        # deduction-from-100 model is the approved successor algorithm).
        health_result = calculate_store_health([])
        health_score = health_result.score

        # Step 4: aggregate findings, write baseline metric, update store.
        await metrics_repo.record_baseline_metric(
            store_id=store_uuid, health_score=health_score, open_issues_count=0
        )
        await store_repo.update_health_score(store_uuid, health_score)
        await session.commit()

        logger.info(
            "discovery_scan_completed",
            store_id=store_id,
            health_score=health_score,
            products_indexed=len(products),
        )

    # Step 5: publish completion event to Redis PubSub.
    await redis_client.publish(f"store:{store_id}:discovery", "completed")
    await redis_client.aclose()
