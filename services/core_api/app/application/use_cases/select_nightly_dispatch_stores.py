"""Use case: which active stores are eligible for AUTOMATIC nightly shift
dispatch (Billing: Free vs Pro/Business enforcement point).

This is the ONE place Free-vs-Pro is enforced in this codebase (per the
product brief: "do not scatter Free/Pro checks through every specialist
agent, that's out of scope"). NightShift Free is "limited monitoring —
manual/on-demand shifts only": a Free-tier store never receives the nightly
`tasks.inspect_catalog` fan-out this use case feeds
(`workers/tasks/scheduler.py::dispatch_nightly_shifts_task`), but can still
trigger a shift manually at any time via the existing
`POST /api/v1/demo/incidents/{scenario_id}` / `dispatch_inspect_catalog`
paths, which this use case never touches.

A per-store `subscriptions.get_current_for_store` lookup (N+1 queries for N
active stores) is used rather than a single SQL join — this hackathon's
store counts make that cost negligible, and it keeps the exact same
selection logic trivially shared between the real `SqlSubscriptionRepository`
and `InMemorySubscriptionRepository` (both just implement
`get_current_for_store`), rather than duplicating a Postgres-specific
DISTINCT ON / window-function query that the in-memory fake would have to
separately re-implement and that this sandbox has no live Postgres to
validate against.
"""

from __future__ import annotations

from app.application.ports import StoreRepository, SubscriptionRepository
from app.domain.models import Store

# Plans whose subscription entitles a store to continuous, automatic nightly
# shift dispatch. BUSINESS is included deliberately: it is not yet
# behaviorally differentiated from PRO anywhere in this codebase (see
# `domain/billing_plans.py`), so it gets everything PRO gets, including this.
NIGHTLY_DISPATCH_ELIGIBLE_PLANS = frozenset({"PRO", "BUSINESS"})


class SelectNightlyDispatchStores:
    def __init__(self, *, stores: StoreRepository, subscriptions: SubscriptionRepository) -> None:
        self._stores = stores
        self._subscriptions = subscriptions

    async def execute(self) -> list[Store]:
        eligible: list[Store] = []
        for store in await self._stores.list_active():
            subscription = await self._subscriptions.get_current_for_store(store.id)
            if subscription is None:
                # Should not happen for any store provisioned after Billing
                # shipped (`CompleteOAuthInstallation` always creates a FREE
                # row) — treated the same as Free (no automatic dispatch)
                # rather than crashing the whole nightly fan-out over one
                # store's missing/legacy data.
                continue
            if subscription.status == "ACTIVE" and subscription.plan in NIGHTLY_DISPATCH_ELIGIBLE_PLANS:
                eligible.append(store)
        return eligible
