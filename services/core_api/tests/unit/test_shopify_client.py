"""Unit tests for the Shopify Admin GraphQL client wrapper: pagination,
rate-limit backoff, and token-exchange error mapping (Sprint 1 Feature 3
edge cases / Risk 2 mitigation).

Uses the `respx_mock` fixture (provided by respx's pytest plugin) rather
than the `@respx.mock` decorator — more reliable when stacked under
pytest-asyncio's `asyncio_mode = auto`.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.api.errors import ShopifyApiProblem
from app.infrastructure.shopify_client import (
    ShopifyGraphQLClient,
    ThemeWriteAccessDeniedError,
    ThrottleStatus,
    exchange_authorization_code,
)

SHOP = "acme.myshopify.com"
GRAPHQL_URL = f"https://{SHOP}/admin/api/2024-07/graphql.json"


def _page(has_next: bool, cursor: str | None, products: list[dict]) -> dict:
    return {
        "data": {
            "shop": {"name": "Acme", "currencyCode": "USD", "ianaTimezone": "America/New_York"},
            "products": {
                "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                "nodes": products,
            },
            "discountNodes": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []},
            "themes": {"nodes": [{"id": "gid://1", "name": "Dawn", "role": "MAIN"}]},
            "scriptTags": {"nodes": []},
        },
        "extensions": {
            "cost": {
                "throttleStatus": {
                    "currentlyAvailable": 900,
                    "maximumAvailable": 1000,
                    "restoreRate": 50,
                }
            }
        },
    }


def test_throttle_status_seconds_until_available():
    throttle = ThrottleStatus(currently_available=10, maximum_available=1000, restore_rate=50)
    assert throttle.seconds_until_available(10) == 0.0
    assert throttle.seconds_until_available(60) == 1.0


async def test_execute_returns_data_on_first_success(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(return_value=httpx.Response(200, json=_page(False, None, [])))

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        result = await client.execute("query { shop { name } }")
        assert result.data["shop"]["name"] == "Acme"
        assert result.throttle_status.currently_available == 900
    finally:
        await client.aclose()


async def test_execute_raises_after_max_retries_on_persistent_errors(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "throttled"}]})
    )

    client = ShopifyGraphQLClient(
        shop_domain=SHOP, access_token="shpat_x", api_version="2024-07", max_retries=1
    )
    try:
        with pytest.raises(ShopifyApiProblem):
            await client.execute("query { shop { name } }")
    finally:
        await client.aclose()


async def test_execute_raises_on_5xx_without_retry(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(return_value=httpx.Response(503))

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        with pytest.raises(ShopifyApiProblem):
            await client.execute("query { shop { name } }")
    finally:
        await client.aclose()


async def test_fetch_baseline_snapshot_follows_cursor_pagination(respx_mock: respx.MockRouter):
    route = respx_mock.post(GRAPHQL_URL)
    route.side_effect = [
        httpx.Response(200, json=_page(True, "cursor-1", [{"id": "gid://1", "title": "A", "status": "ACTIVE"}])),
        httpx.Response(200, json=_page(False, None, [{"id": "gid://2", "title": "B", "status": "ACTIVE"}])),
    ]

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        snapshot = await client.fetch_baseline_snapshot()
    finally:
        await client.aclose()

    assert len(snapshot["products"]) == 2
    assert route.call_count == 2
    assert snapshot["themes"][0]["name"] == "Dawn"


async def test_exchange_authorization_code_success(respx_mock: respx.MockRouter):
    respx_mock.post(f"https://{SHOP}/admin/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "shpat_new", "scope": "read_products"})
    )

    result = await exchange_authorization_code(
        shop_domain=SHOP, client_id="cid", client_secret="secret", code="abc"
    )
    assert result["access_token"] == "shpat_new"


async def test_exchange_authorization_code_raises_502_on_upstream_5xx(respx_mock: respx.MockRouter):
    respx_mock.post(f"https://{SHOP}/admin/oauth/access_token").mock(return_value=httpx.Response(500))

    with pytest.raises(ShopifyApiProblem):
        await exchange_authorization_code(
            shop_domain=SHOP, client_id="cid", client_secret="secret", code="abc"
        )


async def test_exchange_authorization_code_raises_on_invalid_code(respx_mock: respx.MockRouter):
    respx_mock.post(f"https://{SHOP}/admin/oauth/access_token").mock(return_value=httpx.Response(400))

    with pytest.raises(ShopifyApiProblem):
        await exchange_authorization_code(
            shop_domain=SHOP, client_id="cid", client_secret="secret", code="bad-code"
        )


# --- Sprint 3: AI Trust & Execution — auto-fix mutations + verification ----


async def test_update_product_image_alt_text_success_returns_parsed_image(respx_mock: respx.MockRouter):
    # `fileUpdate`/`MediaImage`, not `productImageUpdate`/`ProductImage` —
    # the latter no longer exists on the Shopify Admin GraphQL API (confirmed
    # via live schema introspection during Sprint 3 E2E testing). See
    # `UPDATE_PRODUCT_IMAGE_ALT_TEXT_MUTATION`'s module comment.
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "fileUpdate": {
                        "files": [{"id": "gid://shopify/MediaImage/2", "alt": "Widget product photo"}],
                        "userErrors": [],
                    }
                }
            },
        )
    )

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        result = await client.update_product_image_alt_text(
            product_gid="gid://shopify/Product/1",
            image_gid="gid://shopify/MediaImage/2",
            alt_text="Widget product photo",
        )
    finally:
        await client.aclose()

    # Normalized back to {id, altText} — the shape the rest of the codebase
    # (verification comparison, DB records) already expects.
    assert result == {"id": "gid://shopify/MediaImage/2", "altText": "Widget product photo"}


async def test_update_product_image_alt_text_raises_on_user_errors(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "fileUpdate": {
                        "files": [],
                        "userErrors": [{"field": ["files", "0", "id"], "message": "File not found"}],
                    }
                }
            },
        )
    )

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        with pytest.raises(ShopifyApiProblem):
            await client.update_product_image_alt_text(
                product_gid="gid://shopify/Product/1",
                image_gid="gid://shopify/MediaImage/999",
                alt_text="Widget product photo",
            )
    finally:
        await client.aclose()


async def test_update_product_description_success_returns_parsed_product(respx_mock: respx.MockRouter):
    new_html = "<p>Widget is thoughtfully designed.</p>"
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "productUpdate": {
                        "product": {"id": "gid://shopify/Product/1", "descriptionHtml": new_html},
                        "userErrors": [],
                    }
                }
            },
        )
    )

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        result = await client.update_product_description(
            product_gid="gid://shopify/Product/1", description_html=new_html
        )
    finally:
        await client.aclose()

    assert result == {"id": "gid://shopify/Product/1", "descriptionHtml": new_html}


async def test_update_product_description_raises_on_user_errors(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "productUpdate": {
                        "product": None,
                        "userErrors": [{"field": ["descriptionHtml"], "message": "Invalid HTML"}],
                    }
                }
            },
        )
    )

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        with pytest.raises(ShopifyApiProblem):
            await client.update_product_description(
                product_gid="gid://shopify/Product/1", description_html="<p>bad</p>"
            )
    finally:
        await client.aclose()


async def test_fetch_product_state_returns_product_with_images_nodes_intact(respx_mock: respx.MockRouter):
    # Shopify returns `media`/`alt` (Files API) — `fetch_product_state`
    # normalizes it back to `images`/`altText` so `verify_execution.py`'s
    # `_compare_item` doesn't need to know about the distinction.
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "product": {
                        "id": "gid://shopify/Product/1",
                        "descriptionHtml": "<p>Current description.</p>",
                        "media": {
                            "nodes": [
                                {
                                    "id": "gid://shopify/MediaImage/2",
                                    "mediaContentType": "IMAGE",
                                    "alt": "Widget product photo",
                                },
                                {
                                    "id": "gid://shopify/MediaImage/3",
                                    "mediaContentType": "IMAGE",
                                    "alt": None,
                                },
                                {
                                    "id": "gid://shopify/Video/4",
                                    "mediaContentType": "VIDEO",
                                    "alt": "A video, not an image",
                                },
                            ]
                        },
                    }
                }
            },
        )
    )

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        product = await client.fetch_product_state(product_gid="gid://shopify/Product/1")
    finally:
        await client.aclose()

    assert product["id"] == "gid://shopify/Product/1"
    assert product["descriptionHtml"] == "<p>Current description.</p>"
    # Non-IMAGE media (e.g. the video) must not appear in the normalized list.
    assert product["images"]["nodes"] == [
        {"id": "gid://shopify/MediaImage/2", "altText": "Widget product photo"},
        {"id": "gid://shopify/MediaImage/3", "altText": None},
    ]


async def test_fetch_product_state_returns_empty_dict_when_product_missing(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(return_value=httpx.Response(200, json={"data": {"product": None}}))

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        product = await client.fetch_product_state(product_gid="gid://shopify/Product/999")
    finally:
        await client.aclose()

    assert product == {}


# --- Sprint 4 Step 1: Demo Incident Generator --------------------------------


async def test_create_basic_discount_code_success_returns_code_discount_node(respx_mock: respx.MockRouter):
    route = respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "discountCodeBasicCreate": {
                        "codeDiscountNode": {
                            "id": "gid://shopify/DiscountCodeNode/1",
                            "codeDiscount": {
                                "title": "NightShift Demo — Midnight 50 (A)",
                                "codes": {"nodes": [{"code": "NSDEMO-MIDNIGHT50-A-AB12CD"}]},
                            },
                        },
                        "userErrors": [],
                    }
                }
            },
        )
    )

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        result = await client.create_basic_discount_code(
            title="NightShift Demo — Midnight 50 (A)",
            code="NSDEMO-MIDNIGHT50-A-AB12CD",
            percentage=0.5,
            starts_at="2026-08-01T00:00:00+00:00",
            combines_with={"orderDiscounts": True, "productDiscounts": True, "shippingDiscounts": True},
        )
    finally:
        await client.aclose()

    assert result["id"] == "gid://shopify/DiscountCodeNode/1"
    # Confirm the exact variables shape sent to Shopify — required-on-create
    # fields plus the `combinesWith` stackability input.
    sent_variables = route.calls.last.request.content
    body = json.loads(sent_variables)
    sent = body["variables"]["basicCodeDiscount"]
    assert sent["title"] == "NightShift Demo — Midnight 50 (A)"
    assert sent["code"] == "NSDEMO-MIDNIGHT50-A-AB12CD"
    assert sent["context"] == {"all": "ALL"}
    assert sent["customerGets"] == {"items": {"all": True}, "value": {"percentage": 0.5}}
    assert sent["combinesWith"] == {
        "orderDiscounts": True,
        "productDiscounts": True,
        "shippingDiscounts": True,
    }


async def test_create_basic_discount_code_raises_on_user_errors(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "discountCodeBasicCreate": {
                        "codeDiscountNode": None,
                        "userErrors": [{"field": ["basicCodeDiscount", "code"], "message": "Code already exists"}],
                    }
                }
            },
        )
    )

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        with pytest.raises(ShopifyApiProblem):
            await client.create_basic_discount_code(
                title="NightShift Demo — Midnight 50 (A)",
                code="DUPLICATE-CODE",
                percentage=0.5,
                starts_at="2026-08-01T00:00:00+00:00",
            )
    finally:
        await client.aclose()


# --- Sprint 4 Step 2: Checkout Specialist — Duplicate Discount lifecycle ---


def _basic_discount_node(
    node_id: str,
    *,
    title: str,
    status: str,
    created_at: str,
    order_discounts: bool,
    product_discounts: bool,
    shipping_discounts: bool,
    all_items: bool,
    total_sales: str,
    code: str,
) -> dict:
    return {
        "id": node_id,
        "codeDiscount": {
            "title": title,
            "status": status,
            "createdAt": created_at,
            "combinesWith": {
                "orderDiscounts": order_discounts,
                "productDiscounts": product_discounts,
                "shippingDiscounts": shipping_discounts,
            },
            "customerGets": {"items": {"__typename": "AllDiscountItems", "allItems": all_items}},
            "totalSales": {"amount": total_sales},
            "codes": {"nodes": [{"code": code}]},
        },
    }


async def test_fetch_discount_codes_for_inspection_normalizes_and_paginates(respx_mock: respx.MockRouter):
    def _page(has_next: bool, cursor: str | None, nodes: list[dict]) -> dict:
        return {"data": {"codeDiscountNodes": {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "nodes": nodes}}}

    route = respx_mock.post(GRAPHQL_URL)
    route.side_effect = [
        httpx.Response(
            200,
            json=_page(
                True,
                "cursor-1",
                [
                    _basic_discount_node(
                        "gid://shopify/DiscountCodeNode/1",
                        title="A",
                        status="ACTIVE",
                        created_at="2026-08-01T00:00:00Z",
                        order_discounts=True,
                        product_discounts=True,
                        shipping_discounts=True,
                        all_items=True,
                        total_sales="10.00",
                        code="MIDNIGHT50-A",
                    )
                ],
            ),
        ),
        httpx.Response(
            200,
            json=_page(
                False,
                None,
                [
                    # A non-DiscountCodeBasic node (e.g. BXGY) — codeDiscount
                    # comes back without a `title` field since this query
                    # only requests `... on DiscountCodeBasic` fields.
                    {"id": "gid://shopify/DiscountCodeNode/999", "codeDiscount": {}},
                ],
            ),
        ),
    ]

    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        discounts = await client.fetch_discount_codes_for_inspection()
    finally:
        await client.aclose()

    assert route.call_count == 2
    assert len(discounts) == 1  # the non-DiscountCodeBasic node was dropped
    normalized = discounts[0]
    assert normalized["id"] == "gid://shopify/DiscountCodeNode/1"
    assert normalized["code"] == "MIDNIGHT50-A"
    assert normalized["status"] == "ACTIVE"
    assert normalized["targets_all_items"] is True
    assert normalized["combines_with"] == {
        "order_discounts": True,
        "product_discounts": True,
        "shipping_discounts": True,
    }
    assert normalized["total_sales_usd"] == 10.0


async def test_deactivate_discount_code_success(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "discountCodeDeactivate": {
                        "codeDiscountNode": {
                            "id": "gid://shopify/DiscountCodeNode/2",
                            "codeDiscount": {"status": "EXPIRED"},
                        },
                        "userErrors": [],
                    }
                }
            },
        )
    )
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        result = await client.deactivate_discount_code(discount_id="gid://shopify/DiscountCodeNode/2")
    finally:
        await client.aclose()
    assert result["codeDiscount"]["status"] == "EXPIRED"


async def test_deactivate_discount_code_raises_on_user_errors(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "discountCodeDeactivate": {
                        "codeDiscountNode": None,
                        "userErrors": [{"field": ["id"], "message": "Not found"}],
                    }
                }
            },
        )
    )
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        with pytest.raises(ShopifyApiProblem):
            await client.deactivate_discount_code(discount_id="gid://shopify/DiscountCodeNode/999")
    finally:
        await client.aclose()


async def test_activate_discount_code_success(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "discountCodeActivate": {
                        "codeDiscountNode": {
                            "id": "gid://shopify/DiscountCodeNode/2",
                            "codeDiscount": {"status": "ACTIVE"},
                        },
                        "userErrors": [],
                    }
                }
            },
        )
    )
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        result = await client.activate_discount_code(discount_id="gid://shopify/DiscountCodeNode/2")
    finally:
        await client.aclose()
    assert result["codeDiscount"]["status"] == "ACTIVE"


async def test_fetch_discount_state_returns_normalized_status(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "node": {
                        "id": "gid://shopify/DiscountCodeNode/2",
                        "codeDiscount": {"status": "EXPIRED"},
                    }
                }
            },
        )
    )
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        state = await client.fetch_discount_state(discount_id="gid://shopify/DiscountCodeNode/2")
    finally:
        await client.aclose()
    assert state == {"id": "gid://shopify/DiscountCodeNode/2", "status": "EXPIRED"}


async def test_fetch_discount_state_returns_empty_dict_when_node_missing(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(return_value=httpx.Response(200, json={"data": {"node": None}}))
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        state = await client.fetch_discount_state(discount_id="gid://shopify/DiscountCodeNode/999")
    finally:
        await client.aclose()
    assert state == {}


# --- Sprint 4 Step 3: Theme Guardian + Tracking Specialist ------------------


def test_shop_domain_property_exposes_the_configured_shop():
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    assert client.shop_domain == SHOP


async def test_fetch_active_theme_id_returns_the_main_role_theme(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"themes": {"nodes": [{"id": "gid://shopify/OnlineStoreTheme/1", "name": "Dawn", "role": "MAIN"}]}}},
        )
    )
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        theme_id = await client.fetch_active_theme_id()
    finally:
        await client.aclose()
    assert theme_id == "gid://shopify/OnlineStoreTheme/1"


async def test_fetch_active_theme_id_returns_none_when_no_theme_found(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(return_value=httpx.Response(200, json={"data": {"themes": {"nodes": []}}}))
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        theme_id = await client.fetch_active_theme_id()
    finally:
        await client.aclose()
    assert theme_id is None


async def test_fetch_theme_files_returns_filename_to_content_mapping(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "theme": {
                        "files": {
                            "nodes": [
                                {
                                    "filename": "sections/main-product.liquid",
                                    "body": {"content": "{% render 'buy-buttons' %}"},
                                    "checksumMd5": "abc123",
                                }
                            ]
                        }
                    }
                }
            },
        )
    )
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        files = await client.fetch_theme_files(
            theme_id="gid://shopify/OnlineStoreTheme/1", filenames=["sections/main-product.liquid"]
        )
    finally:
        await client.aclose()
    assert files == {"sections/main-product.liquid": "{% render 'buy-buttons' %}"}


async def test_fetch_theme_files_with_empty_filenames_makes_no_request(respx_mock: respx.MockRouter):
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        files = await client.fetch_theme_files(theme_id="gid://shopify/OnlineStoreTheme/1", filenames=[])
    finally:
        await client.aclose()
    assert files == {}
    assert not respx_mock.calls


async def test_fetch_script_tags_normalizes_and_paginates(respx_mock: respx.MockRouter):
    page1 = httpx.Response(
        200,
        json={
            "data": {
                "scriptTags": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "CURSOR1"},
                    "nodes": [{"id": "gid://shopify/ScriptTag/1", "src": "https://a.example.com/a.js", "displayScope": "ONLINE_STORE", "cache": True}],
                }
            }
        },
    )
    page2 = httpx.Response(
        200,
        json={
            "data": {
                "scriptTags": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"id": "gid://shopify/ScriptTag/2", "src": "https://b.example.com/b.js", "displayScope": "ALL", "cache": False}],
                }
            }
        },
    )
    respx_mock.post(GRAPHQL_URL).mock(side_effect=[page1, page2])
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        tags = await client.fetch_script_tags(max_tags=200)
    finally:
        await client.aclose()
    assert [t["src"] for t in tags] == ["https://a.example.com/a.js", "https://b.example.com/b.js"]
    assert tags[0]["display_scope"] == "ONLINE_STORE"


async def test_create_script_tag_success_returns_normalized_tag(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "scriptTagCreate": {
                        "scriptTag": {
                            "id": "gid://shopify/ScriptTag/1",
                            "src": "https://connect.facebook.net/en_US/fbevents.js",
                            "displayScope": "ONLINE_STORE",
                            "cache": True,
                            "createdAt": "2026-08-02T00:00:00Z",
                            "updatedAt": "2026-08-02T00:00:00Z",
                        },
                        "userErrors": [],
                    }
                }
            },
        )
    )
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        result = await client.create_script_tag(src="https://connect.facebook.net/en_US/fbevents.js")
    finally:
        await client.aclose()
    assert result["id"] == "gid://shopify/ScriptTag/1"
    assert result["src"] == "https://connect.facebook.net/en_US/fbevents.js"


async def test_create_script_tag_raises_on_user_errors(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"scriptTagCreate": {"scriptTag": None, "userErrors": [{"field": ["src"], "message": "Invalid"}]}}},
        )
    )
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        with pytest.raises(ShopifyApiProblem):
            await client.create_script_tag(src="not-a-url")
    finally:
        await client.aclose()


async def test_delete_script_tag_success(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": {"scriptTagDelete": {"deletedScriptTagId": "gid://shopify/ScriptTag/1", "userErrors": []}}},
        )
    )
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        result = await client.delete_script_tag(script_tag_id="gid://shopify/ScriptTag/1")
    finally:
        await client.aclose()
    assert result == {"deleted_script_tag_id": "gid://shopify/ScriptTag/1"}


async def test_delete_script_tag_raises_when_no_id_given():
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        with pytest.raises(ShopifyApiProblem):
            await client.delete_script_tag(script_tag_id="")
    finally:
        await client.aclose()


async def test_fetch_script_tag_state_returns_normalized_node_when_found(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "scriptTags": {
                        "nodes": [
                            {
                                "id": "gid://shopify/ScriptTag/1",
                                "src": "https://connect.facebook.net/en_US/fbevents.js",
                                "displayScope": "ONLINE_STORE",
                            }
                        ]
                    }
                }
            },
        )
    )
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        state = await client.fetch_script_tag_state(src="https://connect.facebook.net/en_US/fbevents.js")
    finally:
        await client.aclose()
    assert state["id"] == "gid://shopify/ScriptTag/1"
    assert state["src"] == "https://connect.facebook.net/en_US/fbevents.js"


async def test_fetch_script_tag_state_returns_empty_dict_when_not_found(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(return_value=httpx.Response(200, json={"data": {"scriptTags": {"nodes": []}}}))
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        state = await client.fetch_script_tag_state(src="https://gone.example.com/x.js")
    finally:
        await client.aclose()
    assert state == {}


# --- restore_theme_file (productionization phase: real automated restore) --


async def test_restore_theme_file_success_returns_restored_status(respx_mock: respx.MockRouter):
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "themeFilesUpsert": {
                        "upsertedThemeFiles": [{"filename": "sections/main-product.liquid"}],
                        "userErrors": [],
                    }
                }
            },
        )
    )
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        result = await client.restore_theme_file(
            theme_id="gid://shopify/OnlineStoreTheme/1",
            filename="sections/main-product.liquid",
            content="{% render 'buy-buttons' %}",
        )
    finally:
        await client.aclose()
    assert result["status"] == "restored"
    assert result["upserted"] is True
    assert result["filename"] == "sections/main-product.liquid"


async def test_restore_theme_file_raises_access_denied_on_user_errors(respx_mock: respx.MockRouter):
    """The realistic default outcome: this app installation has no
    Shopify-granted `themeFilesUpsert` exemption (see the module's own
    comment) — any `userErrors` entry is treated as a denial, never silently
    retried."""
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "themeFilesUpsert": {
                        "upsertedThemeFiles": [],
                        "userErrors": [
                            {
                                "code": "ACCESS_DENIED",
                                "field": ["files"],
                                "filename": "sections/main-product.liquid",
                                "message": "This app is not permitted to write theme files.",
                            }
                        ],
                    }
                }
            },
        )
    )
    client = ShopifyGraphQLClient(shop_domain=SHOP, access_token="shpat_x", api_version="2024-07")
    try:
        with pytest.raises(ThemeWriteAccessDeniedError) as exc_info:
            await client.restore_theme_file(
                theme_id="gid://shopify/OnlineStoreTheme/1",
                filename="sections/main-product.liquid",
                content="{% render 'buy-buttons' %}",
            )
    finally:
        await client.aclose()
    assert exc_info.value.user_errors[0]["code"] == "ACCESS_DENIED"
