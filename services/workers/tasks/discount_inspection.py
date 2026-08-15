"""Discount Inspection Engine worker task — Sprint 4 Step 2, Checkout
Specialist's Observe step.

Queue: celery:observation — same queue as `tasks.inspect_catalog`, since
this is also a raw-data observation step, not a reasoning step.

Chained between `tasks.inspect_catalog` and `tasks.inspect_theme_files`
(Sprint 4 Step 3 extends the Observe chain further: `inspect_catalog` ->
`inspect_discounts` -> `inspect_theme_files` -> `inspect_tracking_scripts` ->
`plan_cognitive_tasks`) — `inspect_catalog` dispatches this task (with the
same shift_id) instead of going straight to planning, so every specialist's
issues land in the same shift before planning runs once. This task
dispatches `tasks.inspect_theme_files` itself once discount inspection
completes — including on every early-return error path, since by the time
this task runs the shift already exists (created by `inspect_catalog`), and
a shift that never reaches planning/compilation would silently stall
forever otherwise.

No LLM call here (see `CheckoutSpecialistAgent`'s own module docstring for
why), so unlike `tasks.inspect_catalog` there is no LLM budget guard or
findings-truncation cap to wire up — just a bounded discount-count cap
(`MAX_DISCOUNTS_SCANNED`), mirroring Sprint 2's own bounded-scan precedent.
"""

from __future__ import annotations

import asyncio
import uuid as uuid_module

import structlog

from app.config import get_settings
from app.domain.agents.checkout_specialist import CheckoutSpecialistAgent
from app.domain.discount_inspection import inspect_discounts
from app.domain.enums import IssueCategory, IssueSeverity
from app.domain.security import EncryptedPayload, TokenCipher
from app.infrastructure.database.repositories import (
    SqlAgentRepository,
    SqlIssueRepository,
    SqlStoreRepository,
    SqlStoreTokenRepository,
)
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.messaging.celery_app import celery_app
from app.infrastructure.shopify_client import ShopifyGraphQLClient

logger = structlog.get_logger(component="discount_inspection_worker")

RETRY_BACKOFF_SECONDS = (10, 30, 90, 270, 810)
MAX_DISCOUNTS_SCANNED = 200

_VALID_SEVERITIES = {s.value for s in IssueSeverity}


@celery_app.task(
    name="tasks.inspect_discounts",
    bind=True,
    max_retries=len(RETRY_BACKOFF_SECONDS),
    acks_late=True,
    reject_on_worker_lost=True,
)
def inspect_discounts_task(self, shift_id: str, store_id: str) -> None:
    try:
        asyncio.run(_run_discount_inspection(shift_id, store_id))
    except Exception as exc:  # noqa: BLE001 — deliberate: route all failures through Celery retry
        attempt = self.request.retries
        countdown = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
        logger.warning(
            "inspect_discounts_retry",
            shift_id=shift_id,
            store_id=store_id,
            attempt=attempt,
            countdown=countdown,
            status="retrying",
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=countdown)


async def _run_discount_inspection(shift_id: str, store_id: str) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    token_cipher = TokenCipher.from_base64_key(settings.nightshift_local_data_key)

    async with session_factory() as session:
        store_repo = SqlStoreRepository(session)
        token_repo = SqlStoreTokenRepository(session)
        issue_repo = SqlIssueRepository(session)
        agent_repo = SqlAgentRepository(session)

        store_uuid = uuid_module.UUID(store_id)
        shift_uuid = uuid_module.UUID(shift_id)

        store = await store_repo.get_by_id(store_uuid)
        if store is None:
            logger.error("inspect_discounts_store_not_found", store_id=store_id, status="error")
            celery_app.send_task("tasks.inspect_theme_files", args=[shift_id, store_id])
            return

        token_row = await token_repo.get_by_store_id(store_uuid)
        if token_row is None:
            logger.error("inspect_discounts_no_token", store_id=store_id, status="error")
            celery_app.send_task("tasks.inspect_theme_files", args=[shift_id, store_id])
            return
        access_token = token_cipher.decrypt(
            EncryptedPayload.deserialize(token_row.access_token_encrypted)
        )

        agent_record = await agent_repo.get_by_identifier("checkout-specialist-agent")
        if agent_record is None:
            # Migration 0004 hasn't run yet on this environment — never
            # fatal to the whole shift, just skip this specialist's findings
            # for this cycle (mirrors inspect_catalog's own tolerance of a
            # missing agent record).
            logger.error(
                "inspect_discounts_agent_not_registered", store_id=store_id, status="error"
            )
            celery_app.send_task("tasks.inspect_theme_files", args=[shift_id, store_id])
            return

        client = ShopifyGraphQLClient(
            shop_domain=store.shopify_domain,
            access_token=access_token,
            api_version=settings.shopify_api_version,
        )
        try:
            discounts = await client.fetch_discount_codes_for_inspection(
                max_discounts=MAX_DISCOUNTS_SCANNED
            )
        finally:
            await client.aclose()

        inspection_report = inspect_discounts(discounts)

        logger.info(
            "discount_inspection_completed",
            store_id=store_id,
            shift_id=shift_id,
            discounts_scanned=inspection_report.discounts_scanned,
            issues_found=len(inspection_report.findings),
            status="success",
        )

        agent = CheckoutSpecialistAgent()
        analysis = await agent.analyze_discount_diff({"findings": inspection_report.findings})

        # Paired by index rather than assuming a single finding — this
        # detector currently only ever emits 0 or 1 findings per shift (see
        # inspect_discounts's own docstring), but pairing correctly here
        # costs nothing and doesn't bake in that assumption.
        for detected, finding in zip(analysis.issues, inspection_report.findings, strict=True):
            # Sprint 4: skip if this exact still-unresolved condition
            # already has an open issue/approval — without this, every
            # shift the scheduler now runs would spawn a duplicate for as
            # long as the merchant hasn't acted on the existing one.
            dedup_key = finding.evidence.get("dedup_key")
            existing_issue = (
                await issue_repo.get_open_by_dedup_key(store_uuid, dedup_key) if dedup_key else None
            )
            if existing_issue is not None:
                logger.info(
                    "inspect_discounts_duplicate_skipped",
                    store_id=store_id,
                    shift_id=shift_id,
                    existing_issue_id=str(existing_issue.id),
                    status="skipped",
                )
                continue

            severity = detected.severity if detected.severity in _VALID_SEVERITIES else "LOW"
            await issue_repo.create(
                store_id=store_uuid,
                shift_id=shift_uuid,
                agent_id=agent_record.id,
                category=IssueCategory.DISCOUNT.value,
                severity=severity,
                title=detected.title,
                description=detected.description,
                # Mirrors inspect_catalog's own persist convention exactly:
                # evidence_data carries only the fix_check identifier (plus
                # lightweight metadata) — never the raw structural data the
                # Plan step needs, which lives in affected_resources instead
                # (ordered [keep_id, *duplicate_ids] — see
                # CheckoutSpecialistAgent.propose_action's own docstring).
                evidence_data={
                    "fix_check": detected.fix_check,
                    "total_sales_usd": finding.evidence.get("total_sales_usd"),
                    # Real exposure duration (e.g. "live for 6.5 hours before
                    # NightShift deactivated it") — the frontend computes
                    # elapsed time from this against the task's own
                    # execution.completed_at/verification.verified_at.
                    "duplicate_created_at": finding.evidence.get("duplicate_created_at"),
                    "dedup_key": dedup_key,
                },
                affected_resources=detected.affected_resources,
                revenue_impact_estimate=detected.revenue_impact_estimate,
                confidence_score=detected.confidence_score,
            )

        await session.commit()

    celery_app.send_task("tasks.inspect_theme_files", args=[shift_id, store_id])
