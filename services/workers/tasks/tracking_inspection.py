"""Script Tag Inspection Engine worker task — Sprint 4 Step 3, Tracking
Specialist's Observe step.

Queue: celery:observation. Last step in the Observe chain before planning:
`tasks.inspect_catalog` -> `tasks.inspect_discounts` -> `tasks.inspect_theme_files`
-> `tasks.inspect_tracking_scripts` -> `tasks.plan_cognitive_tasks`. Dispatches
`tasks.plan_cognitive_tasks` itself once this completes, including on every
early-return error path — same "a shift must never silently stall"
rationale as every other Observe-step task this sprint.

No LLM call (see `TrackingSpecialistAgent`'s own module docstring) — no
budget guard/findings cap to wire up, just the bounded-scan cap
(`MAX_SCRIPT_TAGS_SCANNED`) mirroring every other inspection task's own
bounded-scan precedent.
"""

from __future__ import annotations

import asyncio
import uuid as uuid_module

import structlog

from app.config import get_settings
from app.domain.agents.tracking_specialist import TrackingSpecialistAgent
from app.domain.enums import IssueCategory, IssueSeverity
from app.domain.script_tag_inspection import TrackingSnapshotEntry, inspect_script_tags
from app.domain.security import EncryptedPayload, TokenCipher
from app.infrastructure.database.repositories import (
    SqlAgentRepository,
    SqlIssueRepository,
    SqlStoreRepository,
    SqlStoreTokenRepository,
    SqlTrackingSnapshotRepository,
)
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.messaging.celery_app import celery_app
from app.infrastructure.shopify_client import ShopifyGraphQLClient

logger = structlog.get_logger(component="tracking_inspection_worker")

RETRY_BACKOFF_SECONDS = (10, 30, 90, 270, 810)
MAX_SCRIPT_TAGS_SCANNED = 200

_VALID_SEVERITIES = {s.value for s in IssueSeverity}


@celery_app.task(
    name="tasks.inspect_tracking_scripts",
    bind=True,
    max_retries=len(RETRY_BACKOFF_SECONDS),
    acks_late=True,
    reject_on_worker_lost=True,
)
def inspect_tracking_scripts_task(self, shift_id: str, store_id: str) -> None:
    try:
        asyncio.run(_run_tracking_inspection(shift_id, store_id))
    except Exception as exc:  # noqa: BLE001 — deliberate: route all failures through Celery retry
        attempt = self.request.retries
        countdown = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
        logger.warning(
            "inspect_tracking_scripts_retry",
            shift_id=shift_id,
            store_id=store_id,
            attempt=attempt,
            countdown=countdown,
            status="retrying",
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=countdown)


async def _run_tracking_inspection(shift_id: str, store_id: str) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    token_cipher = TokenCipher.from_base64_key(settings.nightshift_local_data_key)

    async with session_factory() as session:
        store_repo = SqlStoreRepository(session)
        token_repo = SqlStoreTokenRepository(session)
        issue_repo = SqlIssueRepository(session)
        agent_repo = SqlAgentRepository(session)
        tracking_snapshot_repo = SqlTrackingSnapshotRepository(session)

        store_uuid = uuid_module.UUID(store_id)
        shift_uuid = uuid_module.UUID(shift_id)

        store = await store_repo.get_by_id(store_uuid)
        if store is None:
            logger.error("inspect_tracking_scripts_store_not_found", store_id=store_id, status="error")
            celery_app.send_task("tasks.plan_cognitive_tasks", args=[shift_id, store_id])
            return

        token_row = await token_repo.get_by_store_id(store_uuid)
        if token_row is None:
            logger.error("inspect_tracking_scripts_no_token", store_id=store_id, status="error")
            celery_app.send_task("tasks.plan_cognitive_tasks", args=[shift_id, store_id])
            return
        access_token = token_cipher.decrypt(
            EncryptedPayload.deserialize(token_row.access_token_encrypted)
        )

        agent_record = await agent_repo.get_by_identifier("tracking-specialist-agent")
        if agent_record is None:
            logger.error(
                "inspect_tracking_scripts_agent_not_registered", store_id=store_id, status="error"
            )
            celery_app.send_task("tasks.plan_cognitive_tasks", args=[shift_id, store_id])
            return

        client = ShopifyGraphQLClient(
            shop_domain=store.shopify_domain,
            access_token=access_token,
            api_version=settings.shopify_api_version,
        )
        try:
            live_script_tags = await client.fetch_script_tags(max_tags=MAX_SCRIPT_TAGS_SCANNED)
        finally:
            await client.aclose()

        existing_snapshots = await tracking_snapshot_repo.list_for_store(store_uuid)
        known_snapshots = {
            s.src: TrackingSnapshotEntry(src=s.src, display_scope=s.display_scope, pattern_name=s.pattern_name)
            for s in existing_snapshots
        }

        report = inspect_script_tags(live_script_tags=live_script_tags, known_snapshots=known_snapshots)

        for entry in report.newly_snapshotted:
            await tracking_snapshot_repo.create(
                store_id=store_uuid,
                src=entry.src,
                display_scope=entry.display_scope,
                pattern_name=entry.pattern_name,
            )

        logger.info(
            "tracking_inspection_completed",
            store_id=store_id,
            shift_id=shift_id,
            live_script_tags_scanned=report.live_script_tags_scanned,
            issues_found=len(report.findings),
            newly_snapshotted=len(report.newly_snapshotted),
            status="success",
        )

        agent = TrackingSpecialistAgent()
        analysis = await agent.analyze_script_tag_diff({"findings": report.findings})

        for detected, finding in zip(analysis.issues, report.findings, strict=True):
            # Sprint 4: skip if this exact still-unresolved removal already
            # has an open issue/approval — without this, every shift the
            # scheduler now runs would spawn a duplicate for as long as the
            # merchant hasn't acted on the existing one.
            dedup_key = finding.evidence.get("dedup_key")
            existing_issue = (
                await issue_repo.get_open_by_dedup_key(store_uuid, dedup_key) if dedup_key else None
            )
            if existing_issue is not None:
                logger.info(
                    "inspect_tracking_scripts_duplicate_skipped",
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
                category=IssueCategory.PIXEL_TRACKING.value,
                severity=severity,
                title=detected.title,
                description=detected.description,
                evidence_data={
                    "fix_check": detected.fix_check,
                    "pattern_name": finding.evidence.get("pattern_name"),
                    "dedup_key": dedup_key,
                },
                affected_resources=detected.affected_resources,
                revenue_impact_estimate=detected.revenue_impact_estimate,
                confidence_score=detected.confidence_score,
            )

        await session.commit()

    celery_app.send_task("tasks.plan_cognitive_tasks", args=[shift_id, store_id])
