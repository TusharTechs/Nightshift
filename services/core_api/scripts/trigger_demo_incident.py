"""Manually trigger a Demo Incident Generator scenario for a store — dev/QA
and live-demo use, mirroring `scripts/trigger_shift.py`'s shape.

Unlike `trigger_shift.py` (which enqueues an async Celery task), this script
calls `TriggerDemoIncident` directly and synchronously, in-process — there's
no worker to wait on, and a demo operator wants to see the created discount
codes immediately, not poll for a shift report later.

This script does NOT check `Settings.demo_mode_enabled` — that flag only
gates the HTTP API (`POST /api/v1/demo/incidents/{scenario_id}`), which is
reachable by anyone who can authenticate as the store. Running this script
requires local machine/repo access in the first place, the same trust level
`trigger_shift.py` already assumes.

Usage (run from `services/core_api`, same virtualenv/.env as the API/worker):

    PYTHONPATH=. python scripts/trigger_demo_incident.py \\
        --shop-domain your-store.myshopify.com \\
        --scenario midnight_pricing_disaster

    PYTHONPATH=. python scripts/trigger_demo_incident.py \\
        --store-id 3b2f...-uuid --scenario midnight_pricing_disaster
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid as uuid_module

from app.application.use_cases.trigger_demo_incident import TriggerDemoIncident
from app.config import get_settings
from app.domain.demo_incidents import DEMO_SCENARIOS
from app.domain.security import EncryptedPayload, TokenCipher
from app.infrastructure.database.repositories import (
    SqlAuditLogRepository,
    SqlStoreRepository,
    SqlStoreTokenRepository,
    SqlTrackingSnapshotRepository,
)
from app.infrastructure.database.session import create_engine, create_session_factory
from app.infrastructure.shopify_client import ShopifyGraphQLClient


async def _run(*, shop_domain: str | None, store_id: str | None, scenario_id: str) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    token_cipher = TokenCipher.from_base64_key(settings.nightshift_local_data_key)

    async with session_factory() as session:
        store_repo = SqlStoreRepository(session)
        token_repo = SqlStoreTokenRepository(session)
        audit_log_repo = SqlAuditLogRepository(session)
        tracking_snapshot_repo = SqlTrackingSnapshotRepository(session)

        store = (
            await store_repo.get_by_id(uuid_module.UUID(store_id))
            if store_id
            else await store_repo.get_by_shopify_domain(shop_domain)
        )
        if store is None:
            print(f"No store found for {store_id or shop_domain!r}", file=sys.stderr)
            raise SystemExit(1)

        token_row = await token_repo.get_by_store_id(store.id)
        if token_row is None:
            print(f"No Shopify access token on file for store {store.id}", file=sys.stderr)
            raise SystemExit(1)
        access_token = token_cipher.decrypt(EncryptedPayload.deserialize(token_row.access_token_encrypted))

        shopify_client = ShopifyGraphQLClient(
            shop_domain=store.shopify_domain,
            access_token=access_token,
            api_version=settings.shopify_api_version,
        )
        try:
            use_case = TriggerDemoIncident(
                shopify_client=shopify_client,
                audit_logs=audit_log_repo,
                tracking_snapshots=tracking_snapshot_repo,
            )
            result = await use_case.execute(scenario_id=scenario_id, store_id=store.id)
        finally:
            await shopify_client.aclose()

        await session.commit()

    print(f"Triggered demo scenario {result.scenario_id!r} for store {store.id}.")
    if result.created_discount_codes:
        print(f"Created discount codes: {', '.join(result.created_discount_codes)}")
        print("Check your Shopify admin (Discounts) to see them live.")
    if result.notes:
        print(f"NOTE: {result.notes}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--shop-domain", help="e.g. your-store.myshopify.com")
    group.add_argument("--store-id", help="Internal NightShift store UUID")
    parser.add_argument(
        "--scenario",
        required=True,
        choices=sorted(DEMO_SCENARIOS.keys()),
        help=(
            "Demo scenario id to trigger ('midnight_pricing_disaster' and "
            "'rogue_developer_theme_break' are wired as of Step 3; "
            "'catalog_seo_collapse' is not)"
        ),
    )
    args = parser.parse_args()

    asyncio.run(_run(shop_domain=args.shop_domain, store_id=args.store_id, scenario_id=args.scenario))


if __name__ == "__main__":
    main()
