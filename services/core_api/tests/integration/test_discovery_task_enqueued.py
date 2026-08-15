"""Integration test: post-OAuth event enqueues tasks.store_discovery.

Named test required by Sprint 1's Testing section: test_discovery_task_enqueued.
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
from app.domain.security import TokenCipher

SECRET = "test-shopify-app-secret"


def _sign(params: dict[str, str]) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_discovery_task_enqueued_onto_observation_queue():
    token_cipher = TokenCipher.from_base64_key(base64.b64encode(os.urandom(32)).decode())
    task_dispatcher = InMemoryTaskDispatcher()

    use_case = CompleteOAuthInstallation(
        organizations=InMemoryOrganizationRepository(),
        stores=InMemoryStoreRepository(),
        store_tokens=InMemoryStoreTokenRepository(),
        task_dispatcher=task_dispatcher,
        token_cipher=token_cipher,
        shopify_app_secret=SECRET,
    )

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
    token_result = ShopifyTokenExchangeResult(access_token="shpat_x", scope="read_products")

    result = await use_case.execute(params, token_result, current_timestamp=now)

    assert len(task_dispatcher.dispatched_store_ids) == 1
    assert task_dispatcher.dispatched_store_ids[0] == result.store.id
    assert result.discovery_task_id.startswith("fake-task-")


def test_celery_task_registered_on_observation_queue():
    """Verifies the real Celery task (not the fake dispatcher) is routed to
    celery:observation, matching Feature 5's queue topology."""
    from app.infrastructure.messaging.celery_app import celery_app

    routes = celery_app.conf.task_routes
    assert routes["tasks.store_discovery"]["queue"] == "celery:observation"
