"""Integration test: OAuth callback provisions tenant records end-to-end
through the CompleteOAuthInstallation use case.

Named test required by Sprint 1's Testing section: test_oauth_callback_provisions_store.

Uses in-memory fake repositories (app.application.ports) rather than a live
Postgres instance, so the orchestration logic — HMAC verification, timestamp
check, token exchange result handling, encryption, tenant provisioning, and
discovery-task dispatch — is fully exercised without external services. Full
persistence-layer verification (that SQLAlchemy correctly writes these same
rows to Postgres) runs in CI via the Postgres service container configured
per the Technical Blueprint's CI quality gate; that is a Known Limitation of
this test suite as currently written (see the Sprint 1 completion summary).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import pytest

from app.application.ports import (
    InMemoryOrganizationRepository,
    InMemoryStoreRepository,
    InMemoryStoreTokenRepository,
    InMemoryTaskDispatcher,
)
from app.application.use_cases.complete_oauth_installation import (
    CompleteOAuthInstallation,
    ShopifyCallbackParams,
    ShopifyTokenExchangeResult,
)
from app.domain.security import EncryptedPayload, TokenCipher

SECRET = "test-shopify-app-secret"


def _sign(params: dict[str, str], secret: str = SECRET) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def token_cipher() -> TokenCipher:
    return TokenCipher.from_base64_key(base64.b64encode(os.urandom(32)).decode())


@pytest.mark.asyncio
async def test_oauth_callback_provisions_store(token_cipher: TokenCipher):
    organizations = InMemoryOrganizationRepository()
    stores = InMemoryStoreRepository()
    store_tokens = InMemoryStoreTokenRepository()
    task_dispatcher = InMemoryTaskDispatcher()

    use_case = CompleteOAuthInstallation(
        organizations=organizations,
        stores=stores,
        store_tokens=store_tokens,
        task_dispatcher=task_dispatcher,
        token_cipher=token_cipher,
        shopify_app_secret=SECRET,
    )

    now = int(time.time())
    raw_params = {
        "shop": "acme-test.myshopify.com",
        "code": "0907a61c0c2770281d092524a87c10b0",
        "timestamp": str(now),
        "host": "YWNtZS10ZXN0Lm15c2hvcGlmeS5jb20vYWRtaW4",
    }
    signed_params = {**raw_params, "hmac": _sign(raw_params)}

    params = ShopifyCallbackParams(
        shop=raw_params["shop"],
        code=raw_params["code"],
        hmac=signed_params["hmac"],
        timestamp=now,
        host=raw_params["host"],
        raw_query_params=signed_params,
    )
    token_result = ShopifyTokenExchangeResult(
        access_token="shpat_fake_access_token_value",
        scope="read_products,write_products,read_discounts,write_discounts,"
        "read_themes,write_themes,read_script_tags",
    )

    result = await use_case.execute(params, token_result, current_timestamp=now)

    # Organization + Store provisioned.
    stored_store = await stores.get_by_shopify_domain("acme-test.myshopify.com")
    assert stored_store is not None
    assert stored_store.id == result.store.id
    assert stored_store.is_active is True

    # StoreToken persisted with the encrypted (not plaintext) token.
    stored_token = await store_tokens.get_by_store_id(stored_store.id)
    assert stored_token is not None
    assert "shpat_fake_access_token_value" not in stored_token.access_token_encrypted
    decrypted = token_cipher.decrypt(
        EncryptedPayload.deserialize(stored_token.access_token_encrypted)
    )
    assert decrypted == "shpat_fake_access_token_value"

    # Discovery task enqueued for this store (Story 2 precondition).
    assert stored_store.id in task_dispatcher.dispatched_store_ids


@pytest.mark.asyncio
async def test_oauth_callback_rejects_tampered_hmac(token_cipher: TokenCipher):
    from app.domain.security import InvalidHmacSignatureError

    organizations = InMemoryOrganizationRepository()
    stores = InMemoryStoreRepository()
    store_tokens = InMemoryStoreTokenRepository()
    task_dispatcher = InMemoryTaskDispatcher()

    use_case = CompleteOAuthInstallation(
        organizations=organizations,
        stores=stores,
        store_tokens=store_tokens,
        task_dispatcher=task_dispatcher,
        token_cipher=token_cipher,
        shopify_app_secret=SECRET,
    )

    now = int(time.time())
    raw_params = {"shop": "acme-test.myshopify.com", "code": "abc", "timestamp": str(now)}
    signed_params = {**raw_params, "hmac": _sign(raw_params)}
    signed_params["shop"] = "attacker.myshopify.com"  # tamper after signing

    params = ShopifyCallbackParams(
        shop="attacker.myshopify.com",
        code="abc",
        hmac=signed_params["hmac"],
        timestamp=now,
        host="",
        raw_query_params=signed_params,
    )
    token_result = ShopifyTokenExchangeResult(access_token="shpat_x", scope="read_products")

    with pytest.raises(InvalidHmacSignatureError):
        await use_case.execute(params, token_result, current_timestamp=now)

    # No store should have been provisioned and no task dispatched.
    assert await stores.get_by_shopify_domain("attacker.myshopify.com") is None
    assert task_dispatcher.dispatched_store_ids == []


@pytest.mark.asyncio
async def test_oauth_callback_reinstall_rotates_token(token_cipher: TokenCipher):
    """Re-install edge case (Feature 2): existing Store record is updated and
    the token is rotated rather than duplicated."""
    organizations = InMemoryOrganizationRepository()
    stores = InMemoryStoreRepository()
    store_tokens = InMemoryStoreTokenRepository()
    task_dispatcher = InMemoryTaskDispatcher()

    use_case = CompleteOAuthInstallation(
        organizations=organizations,
        stores=stores,
        store_tokens=store_tokens,
        task_dispatcher=task_dispatcher,
        token_cipher=token_cipher,
        shopify_app_secret=SECRET,
    )

    async def run_install(access_token: str):
        now = int(time.time())
        raw_params = {"shop": "acme-test.myshopify.com", "code": "abc", "timestamp": str(now)}
        signed_params = {**raw_params, "hmac": _sign(raw_params)}
        params = ShopifyCallbackParams(
            shop=raw_params["shop"],
            code=raw_params["code"],
            hmac=signed_params["hmac"],
            timestamp=now,
            host="",
            raw_query_params=signed_params,
        )
        token_result = ShopifyTokenExchangeResult(access_token=access_token, scope="read_products")
        return await use_case.execute(params, token_result, current_timestamp=now)

    first = await run_install("shpat_first_token")
    second = await run_install("shpat_rotated_token")

    assert first.store.id == second.store.id  # same tenant, not duplicated

    stored_token = await store_tokens.get_by_store_id(second.store.id)
    decrypted = token_cipher.decrypt(EncryptedPayload.deserialize(stored_token.access_token_encrypted))
    assert decrypted == "shpat_rotated_token"
