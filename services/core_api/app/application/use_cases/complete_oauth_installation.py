"""Use case: complete a Shopify OAuth installation (Sprint 1 Story 1 + 1.12).

Orchestrates HMAC verification, timestamp freshness, token exchange,
encryption, tenant provisioning, and discovery-task enqueueing. Depends only
on the Protocol ports in app.application.ports — no FastAPI or SQLAlchemy
imports — so it is unit-testable with in-memory fakes and independently
reusable if the transport layer changes.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass

from app.application.ports import (
    OrganizationRepository,
    StoreRepository,
    StoreTokenRepository,
    SubscriptionRepository,
    TaskDispatcher,
)
from app.domain.models import Store
from app.domain.security import (
    TokenCipher,
    verify_shopify_hmac,
    verify_timestamp_freshness,
)


@dataclass
class ShopifyCallbackParams:
    shop: str
    code: str
    hmac: str
    timestamp: int
    host: str
    raw_query_params: dict[str, str]


@dataclass
class ShopifyTokenExchangeResult:
    access_token: str
    scope: str


@dataclass
class OAuthInstallationResult:
    store: Store
    discovery_task_id: str


class CompleteOAuthInstallation:
    def __init__(
        self,
        *,
        organizations: OrganizationRepository,
        stores: StoreRepository,
        store_tokens: StoreTokenRepository,
        task_dispatcher: TaskDispatcher,
        token_cipher: TokenCipher,
        shopify_app_secret: str,
        timestamp_drift_seconds: int = 300,
        subscriptions: SubscriptionRepository | None = None,
    ) -> None:
        self._organizations = organizations
        self._stores = stores
        self._store_tokens = store_tokens
        self._task_dispatcher = task_dispatcher
        self._token_cipher = token_cipher
        self._shopify_app_secret = shopify_app_secret
        self._timestamp_drift_seconds = timestamp_drift_seconds
        # Billing: kept Optional (rather than a required kwarg) purely so
        # every pre-Billing test/call site that constructs this use case
        # without a `subscriptions` repository keeps working unchanged — a
        # None here just means "this install doesn't get an auto-provisioned
        # FREE subscription row," never a crash. Every real call site
        # (`app/api/deps.py::get_complete_oauth_installation_use_case`)
        # always passes one.
        self._subscriptions = subscriptions

    def verify_callback(
        self, params: ShopifyCallbackParams, *, current_timestamp: int | None = None
    ) -> None:
        """Steps 3-4 of the OAuth Controller pseudocode: timestamp freshness,
        then HMAC. Callers MUST run this before exchanging the authorization
        code with Shopify (step 5) — a request that fails HMAC verification
        should never reach Shopify's token endpoint at all. `execute()` also
        calls this internally so the invariant holds even if a caller invokes
        it directly without going through the route handler.
        """
        current_timestamp = current_timestamp or int(time.time())
        verify_timestamp_freshness(
            params.timestamp, current_timestamp, self._timestamp_drift_seconds
        )
        verify_shopify_hmac(params.raw_query_params, self._shopify_app_secret)

    async def execute(
        self,
        params: ShopifyCallbackParams,
        token_result: ShopifyTokenExchangeResult,
        *,
        current_timestamp: int | None = None,
    ) -> OAuthInstallationResult:
        self.verify_callback(params, current_timestamp=current_timestamp)

        # Step 6: encrypt the access token before it ever reaches storage.
        encrypted = self._token_cipher.encrypt(token_result.access_token)

        # Step 7: provision Organization + Store + StoreToken atomically.
        # A store's organization is created 1:1 on first install for Sprint 1
        # (multi-store-per-org agency onboarding is deferred — Future
        # Considerations, Section 1.22).
        org_slug = params.shop.replace(".myshopify.com", "")
        organization = await self._organizations.get_by_slug(org_slug)
        if organization is None:
            organization = await self._organizations.create(
                name=org_slug,
                slug=org_slug,
                billing_email=f"billing+{org_slug}@nightshift.ai",
            )

        store = await self._stores.upsert_from_installation(
            organization_id=organization.id,
            shopify_domain=params.shop,
            myshopify_domain=params.shop,
            store_name=org_slug,
            currency_code="USD",
            iana_timezone="America/New_York",
        )

        await self._store_tokens.upsert(
            store_id=store.id,
            access_token_encrypted=encrypted.serialize(),
            scopes=token_result.scope.split(","),
        )

        # Billing: every store always has exactly one CURRENT subscription
        # row — never "no row = implicitly free" (see
        # `alembic/versions/0006_billing_subscriptions.py`'s own docstring).
        # Only provisioned if this store genuinely has none yet — a
        # reinstall of an already-billed store must never reset it back to
        # FREE.
        if self._subscriptions is not None:
            existing_subscription = await self._subscriptions.get_current_for_store(store.id)
            if existing_subscription is None:
                await self._subscriptions.create(
                    store_id=store.id,
                    plan="FREE",
                    status="ACTIVE",
                    monthly_price_usd=0.0,
                )

        # Step 8: enqueue async baseline discovery task.
        task_id = self._task_dispatcher.dispatch_store_discovery(store.id)

        return OAuthInstallationResult(store=store, discovery_task_id=task_id)


SHOP_DOMAIN_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*\.myshopify\.com$")


def validate_shop_domain(shop: str) -> bool:
    """`shop` regex per Sprint 1 Endpoint 1 validation rule."""
    return bool(SHOP_DOMAIN_PATTERN.match(shop))
