"""Unit tests for `ShopifyGraphQLClient`'s Billing API methods
(`create_recurring_subscription` / `fetch_app_subscription_state`) —
Sprint 6 Billing.

Uses the `respx_mock` fixture, same convention as `test_shopify_client.py`.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.api.errors import ShopifyApiProblem
from app.infrastructure.shopify_client import ShopifyGraphQLClient

SHOP = "acme.myshopify.com"
GRAPHQL_URL = f"https://{SHOP}/admin/api/2024-07/graphql.json"


async def test_create_recurring_subscription_success_returns_confirmation_url(
    respx_mock: respx.MockRouter,
):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "appSubscriptionCreate": {
                        "appSubscription": {
                            "id": "gid://shopify/AppSubscription/1",
                            "name": "NightShift Pro (NightShift AI)",
                            "status": "PENDING",
                            "test": True,
                        },
                        "confirmationUrl": "https://acme.myshopify.com/admin/charges/1/confirm",
                        "userErrors": [],
                    }
                }
            },
        )
    )

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        result = await client.create_recurring_subscription(
            name="NightShift Pro (NightShift AI)",
            return_url="https://api.nightshift.ai/api/v1/billing/confirm?store_id=abc",
            monthly_price_usd=29.0,
            test=True,
        )
    finally:
        await client.aclose()

    assert result["id"] == "gid://shopify/AppSubscription/1"
    assert result["status"] == "PENDING"
    assert result["confirmation_url"] == "https://acme.myshopify.com/admin/charges/1/confirm"

    # Verify the exact confirmed mutation shape was sent — nested
    # plan.appRecurringPricingDetails.price.{amount,currencyCode} + interval.
    sent_body = respx_mock.calls.last.request.content
    import json

    variables = json.loads(sent_body)["variables"]
    assert variables["test"] is True
    line_item = variables["lineItems"][0]["plan"]["appRecurringPricingDetails"]
    assert line_item["price"] == {"amount": 29.0, "currencyCode": "USD"}
    assert line_item["interval"] == "EVERY_30_DAYS"


async def test_create_recurring_subscription_raises_on_user_errors(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "appSubscriptionCreate": {
                        "appSubscription": None,
                        "confirmationUrl": None,
                        "userErrors": [{"field": ["returnUrl"], "message": "is invalid"}],
                    }
                }
            },
        )
    )

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        with pytest.raises(ShopifyApiProblem):
            await client.create_recurring_subscription(
                name="NightShift Pro", return_url="not-a-url", monthly_price_usd=29.0
            )
    finally:
        await client.aclose()


async def test_fetch_app_subscription_state_returns_normalized_status(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "node": {
                        "id": "gid://shopify/AppSubscription/1",
                        "name": "NightShift Pro (NightShift AI)",
                        "status": "ACTIVE",
                        "test": True,
                        "currentPeriodEnd": "2026-09-09T00:00:00Z",
                    }
                }
            },
        )
    )

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        state = await client.fetch_app_subscription_state(
            subscription_gid="gid://shopify/AppSubscription/1"
        )
    finally:
        await client.aclose()

    assert state["status"] == "ACTIVE"
    assert state["id"] == "gid://shopify/AppSubscription/1"


async def test_fetch_app_subscription_state_returns_empty_dict_when_node_missing(
    respx_mock: respx.MockRouter,
):
    respx_mock.post(GRAPHQL_URL).mock(return_value=httpx.Response(200, json={"data": {"node": None}}))

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        state = await client.fetch_app_subscription_state(subscription_gid="gid://shopify/AppSubscription/999")
    finally:
        await client.aclose()

    assert state == {}
