"""Nightly Shift Scheduler — closes the gap flagged since Sprint 2's own
completion report: "'Morning Shift Report' implies nightly automation for
real merchant use... Open product decision, not yet made." Carried forward
unchanged through Sprint 3. Before this task existed, every shift (across
all four specialists) only ever ran when a human manually enqueued
`tasks.inspect_catalog` — nothing in NightShift ran on its own, despite the
product's own name.

Queue: celery:cron — reserved for exactly this since Sprint 2's queue
topology comment, unused until now.

Fired by Celery Beat (a separate process — see docker-compose.yml's `beat`
service) on `Settings.shift_schedule_interval_minutes` (default 1440 = truly
nightly; lowered for local testing). This task itself does no inspection —
it only fans out `tasks.inspect_catalog` (the existing, unchanged entry
point to the full Observe -> ... -> Persist chain) to every currently
`is_active` store, exactly as if each store's merchant had run
`trigger_shift.py` themselves.

Known scope limitation, not an oversight: this fires at one fixed UTC time
for every store, not per-store local midnight (`Store.iana_timezone` exists
but isn't consulted here) — genuine per-timezone scheduling is a bigger
feature than this gap-closing step calls for.

--- Billing: Free vs Pro enforcement point ----------------------------------

Since NightShift's Free/Pro/Business billing tiers were added, this is the
ONE place Free-vs-Pro is enforced (see
`app/application/use_cases/select_nightly_dispatch_stores.py`'s own module
docstring for the full reasoning) — a Free-tier store's `is_active` Store
row is deliberately excluded from this nightly fan-out ("limited monitoring
— manual/on-demand shifts only"); only stores with a current ACTIVE
Pro/Business subscription are dispatched to automatically. Free-tier
merchants can still run a shift on demand at any time via the existing
manual-trigger endpoints (`dispatch_inspect_catalog`), which this task never
touches.
"""

from __future__ import annotations

import asyncio

import structlog

from app.application.use_cases.select_nightly_dispatch_stores import SelectNightlyDispatchStores
from app.config import get_settings
from app.infrastructure.database.repositories import SqlStoreRepository, SqlSubscriptionRepository
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.messaging.celery_app import celery_app

logger = structlog.get_logger(component="scheduler_worker")

RETRY_BACKOFF_SECONDS = (10, 30, 90, 270, 810)


@celery_app.task(
    name="tasks.dispatch_nightly_shifts",
    bind=True,
    max_retries=len(RETRY_BACKOFF_SECONDS),
    acks_late=True,
    reject_on_worker_lost=True,
)
def dispatch_nightly_shifts_task(self) -> None:
    try:
        asyncio.run(_dispatch())
    except Exception as exc:  # noqa: BLE001 — deliberate: route all failures through Celery retry
        attempt = self.request.retries
        countdown = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
        logger.warning(
            "dispatch_nightly_shifts_retry",
            attempt=attempt,
            countdown=countdown,
            status="retrying",
            error=str(exc),
        )
        raise self.retry(exc=exc, countdown=countdown)


async def _dispatch() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        selector = SelectNightlyDispatchStores(
            stores=SqlStoreRepository(session),
            subscriptions=SqlSubscriptionRepository(session),
        )
        stores = await selector.execute()

    dispatched_store_ids = []
    for store in stores:
        celery_app.send_task("tasks.inspect_catalog", args=[str(store.id)])
        dispatched_store_ids.append(str(store.id))

    logger.info(
        "dispatch_nightly_shifts_completed",
        store_count=len(dispatched_store_ids),
        store_ids=dispatched_store_ids,
        status="success",
    )
