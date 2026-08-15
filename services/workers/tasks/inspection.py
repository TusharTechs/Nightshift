"""Product Inspection Engine worker task — Sprint 2 Feature 1 / Story 1.

Queue: celery:observation (raw catalog scanning). The Product Quality
Agent's LLM call happens synchronously inside this same task rather than as
a separate queue hop — Sprint 2's own spec names exactly two Celery tasks,
`tasks.inspect_catalog` and `tasks.compile_shift_report`, with no third
"reasoning" task in between.

Retry strategy mirrors Sprint 1's `tasks.store_discovery`: max 5 retries,
exponential backoff (10s, 30s, 90s, 270s, 810s), `acks_late=True`,
`reject_on_worker_lost=True`. Combined with the LLM call budget guard below,
a persistently failing shift costs at most 6 LLM calls total (1 initial +
5 retries), never an unbounded loop.
"""

from __future__ import annotations

import asyncio
import time
import uuid as uuid_module

import redis.asyncio as aioredis
import structlog

from app.config import get_settings
from app.domain.agents.product_quality import ProductQualityAgent
from app.domain.enums import IssueCategory, IssueSeverity
from app.domain.health import ScoredIssue, calculate_store_health
from app.domain.inspection import InspectionFinding, inspect_catalog
from app.domain.security import EncryptedPayload, TokenCipher
from app.infrastructure.database.repositories import (
    SqlAgentRepository,
    SqlIssueRepository,
    SqlMetricsRepository,
    SqlShiftRepository,
    SqlStoreRepository,
    SqlStoreTokenRepository,
)
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.llm.budget_guard import LlmCallBudgetGuard
from app.infrastructure.llm.factory import build_llm_client
from app.infrastructure.messaging.celery_app import celery_app
from app.infrastructure.shopify_client import ShopifyGraphQLClient

logger = structlog.get_logger(component="inspection_worker")

RETRY_BACKOFF_SECONDS = (10, 30, 90, 270, 810)
MAX_INSPECTION_SKUS = 500  # Sprint 2 Risk 2 mitigation

_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _prioritize_and_cap_findings(
    findings: list[InspectionFinding], max_findings: int
) -> tuple[list[InspectionFinding], int]:
    """Bounds the number of findings sent to the paid LLM API per call,
    regardless of catalog size — a large or messy catalog must never turn
    into an unbounded-cost single request. Keeps the highest-severity
    findings first when truncating, since those matter most to a merchant.
    Returns (kept_findings, dropped_count).
    """
    if max_findings <= 0 or len(findings) <= max_findings:
        return findings, 0

    ordered = sorted(findings, key=lambda f: _SEVERITY_RANK.get(f.severity, 99))
    return ordered[:max_findings], len(findings) - max_findings


@celery_app.task(
    name="tasks.inspect_catalog",
    bind=True,
    max_retries=len(RETRY_BACKOFF_SECONDS),
    acks_late=True,
    reject_on_worker_lost=True,
)
def inspect_catalog_task(self, store_id: str) -> None:
    try:
        asyncio.run(_run_inspection(store_id))
    except Exception as exc:  # noqa: BLE001 — deliberate: route all failures through Celery retry
        attempt = self.request.retries
        countdown = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
        logger.warning(
            "inspect_catalog_retry",
            store_id=store_id,
            attempt=attempt,
            countdown=countdown,
            status="retrying",
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=countdown)


async def _run_inspection(store_id: str) -> None:
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
        metrics_repo = SqlMetricsRepository(session)

        store_uuid = uuid_module.UUID(store_id)
        store = await store_repo.get_by_id(store_uuid)
        if store is None:
            logger.error("inspect_catalog_store_not_found", store_id=store_id, status="error")
            return

        token_row = await token_repo.get_by_store_id(store_uuid)
        if token_row is None:
            logger.error("inspect_catalog_no_token", store_id=store_id, status="error")
            return
        access_token = token_cipher.decrypt(
            EncryptedPayload.deserialize(token_row.access_token_encrypted)
        )

        shift = await shift_repo.create_shift(store_uuid, status="IN_PROGRESS")
        await session.commit()

        client = ShopifyGraphQLClient(
            shop_domain=store.shopify_domain,
            access_token=access_token,
            api_version=settings.shopify_api_version,
        )
        scan_start = time.perf_counter()
        try:
            products = await client.fetch_catalog_for_inspection(max_products=MAX_INSPECTION_SKUS)
        finally:
            await client.aclose()

        inspection_report = inspect_catalog(products)
        scan_duration = time.perf_counter() - scan_start

        logger.info(
            "product_inspection_completed",
            store_id=store_id,
            shift_id=str(shift.id),
            skus_scanned=inspection_report.skus_scanned,
            products_scanned=inspection_report.products_scanned,
            issues_found=len(inspection_report.findings),
            duration_seconds=round(scan_duration, 3),
            status="success",
        )

        agent_record = await agent_repo.get_by_identifier("product-quality-agent")

        # --- LLM call, with cost guardrails (Sprint 2 hardening, 2026-07-31,
        # user request) --------------------------------------------------
        kept_findings, dropped_count = _prioritize_and_cap_findings(
            inspection_report.findings, settings.llm_max_findings_per_call
        )
        if dropped_count:
            logger.warning(
                "product_quality_agent_findings_truncated",
                store_id=store_id,
                shift_id=str(shift.id),
                kept=len(kept_findings),
                dropped=dropped_count,
                cap=settings.llm_max_findings_per_call,
            )

        llm_client = build_llm_client(settings)
        redis_client = aioredis.from_url(settings.redis_url)
        try:
            budget_guard = LlmCallBudgetGuard(
                backend=redis_client, max_calls_per_day=settings.llm_max_calls_per_day
            )
            agent = ProductQualityAgent(client=llm_client, budget_guard=budget_guard)
            analysis = await agent.analyze_catalog_diff(
                {
                    "findings": [
                        {
                            "title": f.title,
                            "severity": f.severity,
                            "description": f.description,
                            "affected_resources": f.affected_resources,
                            "evidence": f.evidence,
                        }
                        for f in kept_findings
                    ],
                    "skus_scanned": inspection_report.skus_scanned,
                    "products_scanned": inspection_report.products_scanned,
                }
            )
        finally:
            await redis_client.aclose()

        persisted_issues = []
        for detected in analysis.issues:
            severity = detected.severity if detected.severity in {s.value for s in IssueSeverity} else "LOW"
            issue = await issue_repo.create(
                store_id=store_uuid,
                shift_id=shift.id,
                agent_id=agent_record.id if agent_record else None,
                category=IssueCategory.PRODUCT_QUALITY.value,
                severity=severity,
                title=detected.title,
                description=detected.description,
                evidence_data={
                    # Sprint 2 Story 2 requires storing "AI model identifier
                    # and prompt version in issues metadata" — `issues` has
                    # no dedicated columns for these, only evidence_data
                    # JSONB, so they live here (CONFLICTS.md item 15).
                    "model_identifier": llm_client.model_name,
                    "prompt_version": "sprint2-v1",
                    # Sprint 3: verbatim inspection-finding check identifier,
                    # so the Plan step (Agent.propose_action) can match
                    # issues to automated fixes deterministically instead of
                    # parsing title/description text.
                    "fix_check": detected.fix_check,
                },
                affected_resources=detected.affected_resources,
                revenue_impact_estimate=detected.revenue_impact_estimate,
                confidence_score=detected.confidence_score,
            )
            persisted_issues.append(issue)

        scored_issues = [
            ScoredIssue(category=IssueCategory(i.category), severity=IssueSeverity(i.severity))
            for i in persisted_issues
        ]
        health_result = calculate_store_health(scored_issues)
        if health_result.unscored_categories:
            logger.warning(
                "health_score_categories_not_scored",
                store_id=store_id,
                shift_id=str(shift.id),
                categories=health_result.unscored_categories,
            )

        await store_repo.update_health_score(store_uuid, health_result.score)
        await metrics_repo.record_metric(
            store_id=store_uuid,
            health_score=health_result.score,
            open_issues_count=len(persisted_issues),
        )
        await session.commit()

    # Sprint 4 Step 2: discount inspection (Checkout Specialist's Observe
    # step) now sits between catalog inspection and planning, so both
    # specialists' issues land in the same shift before planning runs once.
    # See workers/tasks/discount_inspection.py, which itself dispatches
    # tasks.plan_cognitive_tasks once it completes (Sprint 3: planning —
    # Plan/Assess Risk/Confidence/Approval-or-Auto-Execute — in turn fires
    # tasks.compile_shift_report; see workers/tasks/planning.py).
    celery_app.send_task("tasks.inspect_discounts", args=[str(shift.id), str(store_uuid)])
