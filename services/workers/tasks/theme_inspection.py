"""Theme Inspection Engine worker task — Sprint 4 Step 3, Theme Guardian's
Observe step.

Queue: celery:observation — same queue as `tasks.inspect_catalog`/
`tasks.inspect_discounts`, since this is also a raw-data observation step.

Chained after `tasks.inspect_discounts` (which now dispatches this task with
the same shift_id instead of going straight to `tasks.inspect_tracking_scripts`
or planning — see its own last line) and before
`tasks.inspect_tracking_scripts`. Dispatches
`tasks.inspect_tracking_scripts` itself once theme inspection completes,
including on every early-return error path, mirroring
`discount_inspection.py`'s own "a shift must never silently stall" rationale
exactly.

First-observation seeding (see `domain/theme_inspection.py`'s own docstring):
if this is the first time a watched file has ever been fetched for this
store, that fetch becomes the permanent baseline — no Issue is raised, and
no LLM call happens for it (nothing to explain yet).
"""

from __future__ import annotations

import asyncio
import uuid as uuid_module

import structlog

from app.config import get_settings
from app.domain.agents.theme_guardian import ThemeGuardianAgent
from app.domain.enums import IssueCategory, IssueSeverity
from app.domain.security import EncryptedPayload, TokenCipher
from app.domain.theme_inspection import (
    DEFAULT_WATCHED_FILENAMES,
    ThemeFileBaseline,
    compute_checksum,
    inspect_theme_files,
)
from app.infrastructure.database.repositories import (
    SqlAgentRepository,
    SqlIssueRepository,
    SqlStoreRepository,
    SqlStoreTokenRepository,
    SqlThemeSnapshotRepository,
)
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.llm.factory import build_llm_client
from app.infrastructure.messaging.celery_app import celery_app
from app.infrastructure.shopify_client import ShopifyGraphQLClient

logger = structlog.get_logger(component="theme_inspection_worker")

RETRY_BACKOFF_SECONDS = (10, 30, 90, 270, 810)

_VALID_SEVERITIES = {s.value for s in IssueSeverity}


@celery_app.task(
    name="tasks.inspect_theme_files",
    bind=True,
    max_retries=len(RETRY_BACKOFF_SECONDS),
    acks_late=True,
    reject_on_worker_lost=True,
)
def inspect_theme_files_task(self, shift_id: str, store_id: str) -> None:
    try:
        asyncio.run(_run_theme_inspection(shift_id, store_id))
    except Exception as exc:  # noqa: BLE001 — deliberate: route all failures through Celery retry
        attempt = self.request.retries
        countdown = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
        logger.warning(
            "inspect_theme_files_retry",
            shift_id=shift_id,
            store_id=store_id,
            attempt=attempt,
            countdown=countdown,
            status="retrying",
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=countdown)


async def _run_theme_inspection(shift_id: str, store_id: str) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    token_cipher = TokenCipher.from_base64_key(settings.nightshift_local_data_key)

    async with session_factory() as session:
        store_repo = SqlStoreRepository(session)
        token_repo = SqlStoreTokenRepository(session)
        issue_repo = SqlIssueRepository(session)
        agent_repo = SqlAgentRepository(session)
        theme_snapshot_repo = SqlThemeSnapshotRepository(session)

        store_uuid = uuid_module.UUID(store_id)
        shift_uuid = uuid_module.UUID(shift_id)

        store = await store_repo.get_by_id(store_uuid)
        if store is None:
            logger.error("inspect_theme_files_store_not_found", store_id=store_id, status="error")
            celery_app.send_task("tasks.inspect_tracking_scripts", args=[shift_id, store_id])
            return

        token_row = await token_repo.get_by_store_id(store_uuid)
        if token_row is None:
            logger.error("inspect_theme_files_no_token", store_id=store_id, status="error")
            celery_app.send_task("tasks.inspect_tracking_scripts", args=[shift_id, store_id])
            return
        access_token = token_cipher.decrypt(
            EncryptedPayload.deserialize(token_row.access_token_encrypted)
        )

        agent_record = await agent_repo.get_by_identifier("theme-guardian-agent")
        if agent_record is None:
            # Migration 0005 hasn't run yet on this environment — never
            # fatal to the whole shift, mirrors discount_inspection.py's own
            # tolerance of a missing agent record.
            logger.error(
                "inspect_theme_files_agent_not_registered", store_id=store_id, status="error"
            )
            celery_app.send_task("tasks.inspect_tracking_scripts", args=[shift_id, store_id])
            return

        client = ShopifyGraphQLClient(
            shop_domain=store.shopify_domain,
            access_token=access_token,
            api_version=settings.shopify_api_version,
        )
        try:
            theme_id = await client.fetch_active_theme_id()
            if theme_id is None:
                logger.warning("inspect_theme_files_no_active_theme", store_id=store_id, status="skipped")
                return
            current_files = await client.fetch_theme_files(
                theme_id=theme_id, filenames=list(DEFAULT_WATCHED_FILENAMES)
            )
        finally:
            await client.aclose()

        baselines: dict[str, ThemeFileBaseline] = {}
        for filename in current_files:
            existing = await theme_snapshot_repo.get_by_filename(store_uuid, theme_id, filename)
            if existing is not None:
                baselines[filename] = ThemeFileBaseline(
                    filename=filename, content=existing.content, checksum_md5=existing.checksum_md5
                )

        report = inspect_theme_files(theme_id=theme_id, current_files=current_files, baselines=baselines)

        for filename in report.newly_baselined_filenames:
            content = current_files[filename]
            await theme_snapshot_repo.create_baseline(
                store_id=store_uuid,
                theme_id=theme_id,
                filename=filename,
                content=content,
                checksum_md5=compute_checksum(content),
            )

        logger.info(
            "theme_inspection_completed",
            store_id=store_id,
            shift_id=shift_id,
            files_scanned=report.files_scanned,
            issues_found=len(report.findings),
            newly_baselined=len(report.newly_baselined_filenames),
            status="success",
        )

        # Sprint 4: drop findings that already have an open issue/approval
        # for this exact (theme_id, filename) before spending an LLM call on
        # them — a persistent, unfixed divergence gets re-observed every
        # single shift now that a real recurring scheduler exists, and
        # without this it would both burn an LLM call and create a
        # duplicate pending approval every time.
        new_findings = []
        for finding in report.findings:
            dedup_key = finding.evidence.get("dedup_key")
            existing_issue = (
                await issue_repo.get_open_by_dedup_key(store_uuid, dedup_key) if dedup_key else None
            )
            if existing_issue is not None:
                logger.info(
                    "inspect_theme_files_duplicate_skipped",
                    store_id=store_id,
                    shift_id=shift_id,
                    filename=finding.filename,
                    existing_issue_id=str(existing_issue.id),
                    status="skipped",
                )
                continue
            new_findings.append(finding)

        if new_findings:
            llm_client = build_llm_client(settings)
            agent = ThemeGuardianAgent(client=llm_client)
            analysis = await agent.analyze_theme_diff(
                {
                    "findings": [
                        {
                            "filename": f.filename,
                            "baseline_content": f.baseline_content,
                            "current_content": f.current_content,
                            "changed_line_count": f.changed_line_count,
                            "affected_resources": f.affected_resources,
                            "evidence": f.evidence,
                        }
                        for f in new_findings
                    ]
                }
            )

            for detected, finding in zip(analysis.issues, new_findings, strict=True):
                severity = detected.severity if detected.severity in _VALID_SEVERITIES else "LOW"
                await issue_repo.create(
                    store_id=store_uuid,
                    shift_id=shift_uuid,
                    agent_id=agent_record.id,
                    category=IssueCategory.CHECKOUT.value,
                    severity=severity,
                    title=detected.title,
                    description=detected.description,
                    # Intentional exception to the lightweight-evidence_data
                    # convention — see `ThemeGuardianAgent.propose_action`'s
                    # own docstring for why the full file content must
                    # travel with the Issue.
                    evidence_data={
                        "fix_check": detected.fix_check,
                        "theme_id": finding.theme_id,
                        "filename": finding.filename,
                        "baseline_content": finding.baseline_content,
                        "current_content": finding.current_content,
                        "dedup_key": finding.evidence.get("dedup_key"),
                    },
                    affected_resources=detected.affected_resources,
                    revenue_impact_estimate=detected.revenue_impact_estimate,
                    confidence_score=detected.confidence_score,
                )

        await session.commit()

    celery_app.send_task("tasks.inspect_tracking_scripts", args=[shift_id, store_id])
