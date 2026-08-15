"""Billing API — the real, Shopify Billing API-backed monetization path
behind the hackathon's "Business Viability" judging criterion.

Pricing (product owner decision, not renegotiated here — see
`app/domain/billing_plans.py`):
  - NightShift Free     — $0/mo  — 1 store, on-demand-only shifts, limited AI employees.
  - NightShift Pro      — $29/mo — continuous nightly monitoring, all AI employees,
    automatic fixes, approval center, shift reports, revenue protection.
  - NightShift Business — $79/mo — stretch tier, NOT yet behaviorally
    differentiated from Pro anywhere in this codebase.

Billing provider: Shopify's own Billing API (`appSubscriptionCreate`) — no
third-party payment processor, and no new Shopify OAuth scope (the Billing
API needs none). See `infrastructure/shopify_client.py`'s own module
comment for the exact, confirmed-via-shopify.dev mutation/query shapes.

Free tier requires ZERO Shopify interaction and ZERO merchant payment
approval: `GET /plans` and `GET /status` never call Shopify, and a store
never has to touch this router at all to keep using Free (every store
already has a FREE/ACTIVE subscription row from install time — see
`CompleteOAuthInstallation`). Only `POST /subscribe` (upgrading to
Pro/Business) ever calls Shopify, and even then the merchant must explicitly
approve the resulting charge on Shopify's own confirmation page
(`confirmation_url`) — this app never bypasses that.

`GET /confirm` is the `returnUrl` Shopify redirects the merchant's browser
to after they approve/decline on that page. It carries no App Bridge Bearer
session token (it's a plain top-level browser navigation Shopify itself
performs, not an authenticated API call from the embedded app) — `store_id`
is instead a query parameter THIS router embeds into the `returnUrl` at
`/subscribe` time, and `charge_id` is Shopify's own redirect parameter. Per
the same "always re-query, never trust the response/redirect alone" axiom
already used everywhere else in this codebase (see
`verify_execution.py`'s own module docstring), the actual ACTIVE/DECLINED/
etc. status this route persists always comes from a fresh Shopify
`node(id:)` query, never from `charge_id` alone.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse

from app.api.deps import (
    get_current_store_id,
    get_shopify_client_for_store,
    get_shopify_client_for_store_id,
    get_store_repository,
    get_subscription_repository,
)
from app.api.errors import (
    BillingDisabledProblem,
    InvalidPlanProblem,
    ShopifyApiProblem,
    SubscriptionNotFoundProblem,
)
from app.application.dtos import (
    BillingPlansResponse,
    BillingStatusResponse,
    BillingSubscribeRequest,
    BillingSubscribeResponse,
)
from app.application.ports import StoreRepository, SubscriptionRepository
from app.config import Settings, get_settings
from app.domain.billing_plans import PLAN_CATALOG
from app.domain.enums import PlanTier
from app.infrastructure.shopify_client import ShopifyGraphQLClient

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

# Plans a merchant can actually POST /subscribe to. FREE is deliberately
# excluded — it's the zero-payment default every store already has from
# install time, never something Shopify's Billing API is invoked for.
SUBSCRIBABLE_PLANS = {PlanTier.PRO.value, PlanTier.BUSINESS.value}

_PLAN_PRICES_USD = {plan["plan"]: plan["monthly_price_usd"] for plan in PLAN_CATALOG}
_PLAN_DISPLAY_NAMES = {plan["plan"]: plan["display_name"] for plan in PLAN_CATALOG}

# Shopify redirects here with `charge_id` as a bare numeric id (its
# long-standing convention, inherited from the legacy REST
# RecurringApplicationCharge API) rather than a full GID — this app's own
# GraphQL queries need the GID form.
_APP_SUBSCRIPTION_GID_PREFIX = "gid://shopify/AppSubscription/"

# Shopify AppSubscriptionStatus values that represent a terminal, non-active
# outcome for this app's own purposes — used to decide whether to stamp
# `cancelled_at` on confirm.
_TERMINAL_INACTIVE_STATUSES = {"CANCELLED", "DECLINED", "EXPIRED"}


def _to_subscription_gid(charge_id: str) -> str:
    return charge_id if charge_id.startswith("gid://") else f"{_APP_SUBSCRIPTION_GID_PREFIX}{charge_id}"


@router.get("/plans", response_model=BillingPlansResponse)
async def list_plans() -> BillingPlansResponse:
    """Static plan/feature data — no Shopify call, no DB read, no auth
    required (a merchant may want to see pricing before installing has even
    fully wired up their session)."""
    return BillingPlansResponse(plans=PLAN_CATALOG)


@router.post("/subscribe", response_model=BillingSubscribeResponse)
async def subscribe(
    body: BillingSubscribeRequest,
    store_id: uuid.UUID = Depends(get_current_store_id),
    settings: Settings = Depends(get_settings),
    shopify_client: ShopifyGraphQLClient = Depends(get_shopify_client_for_store),
    subscriptions: SubscriptionRepository = Depends(get_subscription_repository),
) -> BillingSubscribeResponse:
    if not settings.billing_enabled:
        raise BillingDisabledProblem(
            "Billing is disabled on this deployment. Set BILLING_ENABLED=true to enable "
            "POST /api/v1/billing/subscribe."
        )

    plan = body.plan.upper()
    if plan not in SUBSCRIBABLE_PLANS:
        raise InvalidPlanProblem(
            f"Cannot subscribe to plan {body.plan!r} — must be one of {sorted(SUBSCRIBABLE_PLANS)}. "
            "FREE is the default tier every store already has and needs no subscription call."
        )

    monthly_price_usd = _PLAN_PRICES_USD[plan]
    return_url = f"{settings.shopify_app_url.rstrip('/')}/api/v1/billing/confirm?store_id={store_id}"

    # Shopify call FIRST, DB write SECOND: a rejected/failed
    # appSubscriptionCreate call (ShopifyApiProblem propagates as a 502, per
    # `app/api/errors.py`) must never leave a PENDING row behind that isn't
    # actually backed by a real Shopify AppSubscription — no orphaned
    # PENDING rows on Shopify API failure.
    subscription_payload = await shopify_client.create_recurring_subscription(
        name=f"{_PLAN_DISPLAY_NAMES[plan]} (NightShift AI)",
        return_url=return_url,
        monthly_price_usd=monthly_price_usd,
        test=settings.shopify_billing_test_mode,
    )
    confirmation_url = subscription_payload.get("confirmation_url")
    if not confirmation_url:
        raise ShopifyApiProblem("appSubscriptionCreate did not return a confirmationUrl")

    subscription = await subscriptions.create(
        store_id=store_id,
        plan=plan,
        status="PENDING",
        shopify_charge_gid=subscription_payload.get("id"),
        monthly_price_usd=monthly_price_usd,
    )

    return BillingSubscribeResponse(
        subscription_id=subscription.id,
        plan=subscription.plan,
        status=subscription.status,
        confirmation_url=confirmation_url,
        monthly_price_usd=monthly_price_usd,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/confirm")
async def confirm(
    store_id: uuid.UUID = Query(...),
    charge_id: str = Query(...),
    stores: StoreRepository = Depends(get_store_repository),
    subscriptions: SubscriptionRepository = Depends(get_subscription_repository),
    shopify_client: ShopifyGraphQLClient = Depends(get_shopify_client_for_store_id),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    subscription_gid = _to_subscription_gid(charge_id)

    # Tenant isolation: looked up by BOTH store_id AND the charge's own GID
    # together. A charge_id that belongs to a DIFFERENT store's subscription
    # row (or doesn't exist at all) returns None here and gets the exact
    # same 404 either way — never leaking which case it is, never confirming
    # (or even reading) a subscription that isn't this store_id's own.
    subscription = await subscriptions.get_by_store_and_charge_gid(store_id, subscription_gid)
    if subscription is None:
        raise SubscriptionNotFoundProblem(
            f"No pending subscription found for store {store_id} and charge {charge_id}"
        )

    # Always re-query Shopify for the real, current status — never trust
    # `charge_id`/the redirect itself (same axiom as `verify_execution.py`).
    live_state = await shopify_client.fetch_app_subscription_state(subscription_gid=subscription_gid)
    live_status = live_state.get("status") or "EXPIRED"

    now = datetime.now(timezone.utc)
    await subscriptions.update_status(
        subscription.id,
        status=live_status,
        activated_at=now if live_status == "ACTIVE" else None,
        cancelled_at=now if live_status in _TERMINAL_INACTIVE_STATUSES else None,
    )

    store = await stores.get_by_id(store_id)
    redirect_target = (
        f"https://{store.shopify_domain}/admin/apps/{settings.shopify_app_client_id}"
        if store is not None
        else settings.shopify_app_url
    )
    return RedirectResponse(url=redirect_target, status_code=302)


@router.get("/status", response_model=BillingStatusResponse)
async def status(
    store_id: uuid.UUID = Depends(get_current_store_id),
    subscriptions: SubscriptionRepository = Depends(get_subscription_repository),
) -> BillingStatusResponse:
    subscription = await subscriptions.get_current_for_store(store_id)
    if subscription is None:
        raise SubscriptionNotFoundProblem(f"No subscription on file for store {store_id}")

    return BillingStatusResponse(
        plan=subscription.plan,
        status=subscription.status,
        monthly_price_usd=subscription.monthly_price_usd,
        activated_at=subscription.activated_at,
        cancelled_at=subscription.cancelled_at,
        shopify_charge_gid=subscription.shopify_charge_gid,
    )
