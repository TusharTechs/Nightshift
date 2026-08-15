"""Manually enqueue an inspection shift for a store — dev/QA only.

No document in this repo defines a scheduler or a `POST /api/v1/shifts/trigger`
endpoint (Sprint 2's own Known Limitations, carried into Sprint 3, note this
explicitly: "no scheduler triggers the pipeline automatically"). Until one of
those lands, this is the supported way to fire `tasks.inspect_catalog` by hand
for local testing — a thin wrapper around the exact same
`celery_app.send_task(...)` call every other worker task in this codebase
already uses to chain to the next stage (see `workers/tasks/inspection.py`'s
own final line).

Usage (run from `services/core_api`, with the same virtualenv/.env the API
and Celery worker use; `PYTHONPATH=.` is required so `app` resolves, same as
this repo's own documented Celery worker invocation in README.md):

    PYTHONPATH=. python scripts/trigger_shift.py --shop-domain your-store.myshopify.com
    PYTHONPATH=. python scripts/trigger_shift.py --store-id 3b2f...-uuid

This only enqueues the task onto `celery:observation` — a worker process
(`celery -A app.infrastructure.messaging.celery_app worker -Q
celery:observation,celery:reasoning,celery:execution,celery:verification,celery:cron
-l info`) must already be running to actually pick it up and execute the
Observe -> Reason -> Plan -> ... -> Persist pipeline end to end.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import get_settings
from app.infrastructure.database.repositories import SqlStoreRepository
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.messaging.celery_app import celery_app


async def _resolve_store_id(shop_domain: str) -> str | None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        store = await SqlStoreRepository(session).get_by_shopify_domain(shop_domain)
        return str(store.id) if store else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--shop-domain", help="e.g. your-store.myshopify.com")
    group.add_argument("--store-id", help="Internal NightShift store UUID")
    args = parser.parse_args()

    if args.store_id:
        store_id = args.store_id
    else:
        store_id = asyncio.run(_resolve_store_id(args.shop_domain))
        if store_id is None:
            print(f"No store found for shop domain {args.shop_domain!r}", file=sys.stderr)
            raise SystemExit(1)

    result = celery_app.send_task("tasks.inspect_catalog", args=[store_id])
    print(f"Dispatched tasks.inspect_catalog for store {store_id} — celery task id: {result.id}")
    print(
        "Watch your worker logs for inspect_catalog -> plan_cognitive_tasks -> "
        "compile_shift_report; check GET /api/v1/shifts/latest and the "
        "Approval Center once it completes."
    )


if __name__ == "__main__":
    main()
