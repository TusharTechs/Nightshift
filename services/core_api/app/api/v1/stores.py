"""Store provisioning & health metadata (Sprint 1 Endpoint 3).

GET /api/v1/stores/me — retrieve current store configuration, health score,
and baseline scan progress. Authenticated via Bearer JWT (Shopify Session
Token). Note (brief Section 7.3): this endpoint is defined in the Sprint 1
Spec but absent from the API Contract Specification — flagged as a doc gap
for the API architecture owner, built here per Sprint 1's binding spec.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.api.deps import get_current_store_id, get_store_repository
from app.api.errors import StoreNotFoundProblem
from app.application.dtos import StoreSnapshotResponse
from app.application.ports import StoreRepository
from app.config import Settings, get_settings

router = APIRouter(prefix="/api/v1/stores", tags=["stores"])


@router.get("/me", response_model=StoreSnapshotResponse)
async def get_my_store(
    store_id: uuid.UUID = Depends(get_current_store_id),
    stores: StoreRepository = Depends(get_store_repository),
    settings: Settings = Depends(get_settings),
) -> StoreSnapshotResponse:
    store = await stores.get_by_id(store_id)
    if store is None:
        raise StoreNotFoundProblem(f"No store found for id {store_id}")

    return StoreSnapshotResponse(
        id=store.id,
        shopify_domain=store.shopify_domain,
        store_name=store.store_name,
        currency_code=store.currency_code,
        iana_timezone=store.iana_timezone,
        health_score=store.health_score,
        autonomy_level=store.autonomy_level,
        is_discovery_completed=store.health_score != 100 or store.updated_at != store.created_at,
        installed_at=store.created_at,
        # Sprint 5 Phase 4: real-time reflection of the same flag that gates
        # the Demo Incident Generator itself — never hardcoded True/False.
        demo_mode_enabled=settings.demo_mode_enabled,
    )
