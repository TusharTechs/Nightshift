"""Unit tests: `SelectNightlyDispatchStores` — the ONE Free-vs-Pro
enforcement point for automatic nightly shift dispatch (Sprint 6 Billing).

Uses `InMemoryStoreRepository` / `InMemorySubscriptionRepository` directly —
no database, no Celery, mirroring the existing InMemory-fake test
convention used throughout this suite.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.application.ports import InMemoryStoreRepository, InMemorySubscriptionRepository
from app.application.use_cases.select_nightly_dispatch_stores import SelectNightlyDispatchStores
from app.domain.models import Store


def _store(*, is_active: bool = True) -> Store:
    now = datetime.now(timezone.utc)
    org_id = uuid.uuid4()
    return Store(
        id=uuid.uuid4(),
        organization_id=org_id,
        shopify_domain=f"{uuid.uuid4().hex}.myshopify.com",
        myshopify_domain=f"{uuid.uuid4().hex}.myshopify.com",
        store_name="Test Store",
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def stores() -> InMemoryStoreRepository:
    return InMemoryStoreRepository()


@pytest.fixture
def subscriptions() -> InMemorySubscriptionRepository:
    return InMemorySubscriptionRepository()


async def test_free_tier_store_is_excluded(stores, subscriptions):
    free_store = _store()
    stores.seed(free_store)
    await subscriptions.create(store_id=free_store.id, plan="FREE", status="ACTIVE", monthly_price_usd=0.0)

    selector = SelectNightlyDispatchStores(stores=stores, subscriptions=subscriptions)
    result = await selector.execute()

    assert result == []


async def test_active_pro_tier_store_is_included(stores, subscriptions):
    pro_store = _store()
    stores.seed(pro_store)
    await subscriptions.create(
        store_id=pro_store.id, plan="PRO", status="ACTIVE", monthly_price_usd=29.0
    )

    selector = SelectNightlyDispatchStores(stores=stores, subscriptions=subscriptions)
    result = await selector.execute()

    assert [s.id for s in result] == [pro_store.id]


async def test_active_business_tier_store_is_included(stores, subscriptions):
    business_store = _store()
    stores.seed(business_store)
    await subscriptions.create(
        store_id=business_store.id, plan="BUSINESS", status="ACTIVE", monthly_price_usd=79.0
    )

    selector = SelectNightlyDispatchStores(stores=stores, subscriptions=subscriptions)
    result = await selector.execute()

    assert [s.id for s in result] == [business_store.id]


async def test_pending_pro_subscription_is_excluded(stores, subscriptions):
    """Merchant clicked "Upgrade" but hasn't approved the charge on
    Shopify's confirmation page yet — must not get automatic nightly
    dispatch until the subscription is genuinely ACTIVE."""
    pending_store = _store()
    stores.seed(pending_store)
    await subscriptions.create(
        store_id=pending_store.id, plan="PRO", status="PENDING", monthly_price_usd=29.0
    )

    selector = SelectNightlyDispatchStores(stores=stores, subscriptions=subscriptions)
    result = await selector.execute()

    assert result == []


async def test_declined_pro_subscription_is_excluded(stores, subscriptions):
    declined_store = _store()
    stores.seed(declined_store)
    await subscriptions.create(
        store_id=declined_store.id, plan="PRO", status="DECLINED", monthly_price_usd=29.0
    )

    selector = SelectNightlyDispatchStores(stores=stores, subscriptions=subscriptions)
    result = await selector.execute()

    assert result == []


async def test_inactive_store_is_excluded_even_with_active_pro_subscription(stores, subscriptions):
    inactive_store = _store(is_active=False)
    stores.seed(inactive_store)
    await subscriptions.create(
        store_id=inactive_store.id, plan="PRO", status="ACTIVE", monthly_price_usd=29.0
    )

    selector = SelectNightlyDispatchStores(stores=stores, subscriptions=subscriptions)
    result = await selector.execute()

    assert result == []


async def test_store_with_no_subscription_row_is_excluded_not_crashed(stores, subscriptions):
    """Defensive: a store somehow missing a subscription row (legacy data)
    is treated as ineligible, not a crash that would take down the entire
    nightly fan-out for every other store."""
    orphan_store = _store()
    stores.seed(orphan_store)

    selector = SelectNightlyDispatchStores(stores=stores, subscriptions=subscriptions)
    result = await selector.execute()

    assert result == []


async def test_mixed_fleet_only_dispatches_to_eligible_stores(stores, subscriptions):
    free_store = _store()
    pro_store = _store()
    stores.seed(free_store)
    stores.seed(pro_store)
    await subscriptions.create(store_id=free_store.id, plan="FREE", status="ACTIVE", monthly_price_usd=0.0)
    await subscriptions.create(store_id=pro_store.id, plan="PRO", status="ACTIVE", monthly_price_usd=29.0)

    selector = SelectNightlyDispatchStores(stores=stores, subscriptions=subscriptions)
    result = await selector.execute()

    assert [s.id for s in result] == [pro_store.id]
