"""Integration test: a newly installed store always gets an auto-provisioned
FREE/ACTIVE subscription row (Sprint 6 Billing) — "every store always has
exactly one current subscription row, never 'no row = implicitly free'."

Follows `test_oauth_callback.py`'s own in-memory-fake `CompleteOAuthInstallation`
exercise pattern.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from app.application.ports import (
    InMemoryOrganizationRepository,
    InMemoryStoreRepository,
    InMemoryStoreTokenRepository,
    InMemorySubscriptionRepository,
    InMemoryTaskDispatcher,
)
from app.application.use_cases.complete_oauth_installation import (
    CompleteOAuthInstallation,
    ShopifyCallbackParams,
    ShopifyTokenExchangeResult,
)
from app.domain.security import TokenCipher

SECRET = "test-shopify-app-secret"


def _sign(params: dict[str, str], secret: str = SECRET) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def token_cipher() -> TokenCipher:
    import base64
    import os

    return TokenCipher.from_base64_key(base64.b64encode(os.urandom(32)).decode())


async def test_new_install_creates_free_active_subscription_row(token_cipher: TokenCipher):
    organizations = InMemoryOrganizationRepository()
    stores = InMemoryStoreRepository()
    store_tokens = InMemoryStoreTokenRepository()
    subscriptions = InMemorySubscriptionRepository()
    task_dispatcher = InMemoryTaskDispatcher()

    use_case = CompleteOAuthInstallation(
        organizations=organizations,
        stores=stores,
        store_tokens=store_tokens,
        task_dispatcher=task_dispatcher,
        token_cipher=token_cipher,
        shopify_app_secret=SECRET,
        subscriptions=subscriptions,
    )

    now = int(time.time())
    raw_params = {
        "shop": "new-merchant.myshopify.com",
        "code": "abc123",
        "timestamp": str(now),
    }
    signed_params = {**raw_params, "hmac": _sign(raw_params)}
    params = ShopifyCallbackParams(
        shop=raw_params["shop"],
        code=raw_params["code"],
        hmac=signed_params["hmac"],
        timestamp=now,
        host="",
        raw_query_params=signed_params,
    )
    token_result = ShopifyTokenExchangeResult(access_token="shpat_new", scope="read_products")

    result = await use_case.execute(params, token_result, current_timestamp=now)

    subscription = await subscriptions.get_current_for_store(result.store.id)
    assert subscription is not None
    assert subscription.plan == "FREE"
    assert subscription.status == "ACTIVE"
    assert subscription.monthly_price_usd == 0.0
    assert subscription.shopify_charge_gid is None


async def test_reinstall_does_not_reset_an_existing_paid_subscription(token_cipher: TokenCipher):
    """A store that already upgraded to Pro must not have its subscription
    silently reset back to FREE on a re-install (e.g. uninstall/reinstall
    cycle)."""
    organizations = InMemoryOrganizationRepository()
    stores = InMemoryStoreRepository()
    store_tokens = InMemoryStoreTokenRepository()
    subscriptions = InMemorySubscriptionRepository()
    task_dispatcher = InMemoryTaskDispatcher()

    use_case = CompleteOAuthInstallation(
        organizations=organizations,
        stores=stores,
        store_tokens=store_tokens,
        task_dispatcher=task_dispatcher,
        token_cipher=token_cipher,
        shopify_app_secret=SECRET,
        subscriptions=subscriptions,
    )

    async def run_install():
        now = int(time.time())
        raw_params = {"shop": "returning-merchant.myshopify.com", "code": "abc", "timestamp": str(now)}
        signed_params = {**raw_params, "hmac": _sign(raw_params)}
        params = ShopifyCallbackParams(
            shop=raw_params["shop"],
            code=raw_params["code"],
            hmac=signed_params["hmac"],
            timestamp=now,
            host="",
            raw_query_params=signed_params,
        )
        token_result = ShopifyTokenExchangeResult(access_token="shpat_x", scope="read_products")
        return await use_case.execute(params, token_result, current_timestamp=now)

    first = await run_install()
    # Simulate the merchant upgrading to Pro before uninstalling.
    await subscriptions.create(
        store_id=first.store.id, plan="PRO", status="ACTIVE", monthly_price_usd=29.0
    )

    await run_install()  # reinstall

    current = await subscriptions.get_current_for_store(first.store.id)
    assert current.plan == "PRO"
    assert current.status == "ACTIVE"
