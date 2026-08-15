"""Async Shopify Admin API client wrapper.

Handles OAuth token exchange (REST) and Admin GraphQL queries with
cursor pagination and cost-bucket-aware rate-limit backoff, per Sprint 1
Feature 3's edge cases and Risk 2 mitigation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.api.errors import ShopifyApiProblem

GRAPHQL_API_VERSION_PATH = "admin/api/{version}/graphql.json"

BASELINE_DISCOVERY_QUERY = """
query BaselineDiscovery($productsCursor: String, $discountsCursor: String) {
  shop {
    name
    currencyCode
    ianaTimezone
  }
  products(first: 250, after: $productsCursor) {
    pageInfo { hasNextPage endCursor }
    nodes { id title status }
  }
  discountNodes(first: 50, after: $discountsCursor) {
    pageInfo { hasNextPage endCursor }
    nodes { id }
  }
  themes(first: 10) {
    nodes { id name role }
  }
  scriptTags(first: 50) {
    nodes { id src }
  }
}
"""


CATALOG_INSPECTION_QUERY = """
query CatalogInspection($cursor: String) {
  products(first: 250, after: $cursor, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      title
      descriptionHtml
      featuredImage { id altText }
      media(first: 10) { nodes { id mediaContentType ... on MediaImage { alt } } }
      variants(first: 50) {
        nodes { id sku price inventoryQuantity }
      }
    }
  }
}
"""


# --- Sprint 3: AI Trust & Execution — auto-fix mutations + verification ----
#
# `productImageUpdate` (and the `ProductImage`-typed `images` field's write
# side) no longer exists on the Shopify Admin GraphQL API as of the
# configured API version — confirmed via live schema introspection during
# Sprint 3 E2E testing (it raises `Field 'productImageUpdate' doesn't exist
# on type 'Mutation'`). Shopify moved image ALT text under the unified Files
# API: the mutation is `fileUpdate(files: [FileUpdateInput!]!)`, and it only
# accepts a `MediaImage` GID (from `product.media`), NOT the `ProductImage`
# GID `product.images` returns — the two have different numeric IDs
# entirely, not just different type names for the same ID. So both the
# inspection query above and the verify query below fetch `media` (not
# `images`), and `alt` (not `altText`) is the Files API's field name.

UPDATE_PRODUCT_IMAGE_ALT_TEXT_MUTATION = """
mutation UpdateProductImageAltText($files: [FileUpdateInput!]!) {
  fileUpdate(files: $files) {
    files { id ... on MediaImage { alt } }
    userErrors { field message }
  }
}
"""

UPDATE_PRODUCT_DESCRIPTION_MUTATION = """
mutation UpdateProductDescription($input: ProductInput!) {
  productUpdate(input: $input) {
    product { id descriptionHtml }
    userErrors { field message }
  }
}
"""

VERIFY_PRODUCT_STATE_QUERY = """
query VerifyProductState($productId: ID!) {
  product(id: $productId) {
    id
    descriptionHtml
    media(first: 20) {
      nodes { id mediaContentType ... on MediaImage { alt } }
    }
  }
}
"""


# --- Sprint 4 Step 1: Demo Incident Generator -------------------------------
#
# `discountCodeBasicCreate` is used ONLY by the Demo Incident Generator
# (`TriggerDemoIncident`) to deliberately create a duplicate/stackable
# discount code on cue for Scenario 1 ("Midnight Pricing Disaster") — never
# by any real detection/fix pipeline. Confirmed via Shopify's live Admin
# GraphQL schema docs (2026-08-01, pinned to this codebase's default
# `shopify_api_version` of 2024-07): `DiscountCodeBasicInput`'s
# required-on-create fields are `code`, `context` (or the deprecated
# `customerSelection`), `customerGets`, `startsAt`, `title`.
# `context: DiscountContextInput { all: DiscountBuyerSelection }` — `ALL` is
# the only enum value `DiscountBuyerSelection` defines, so `{all: ALL}`
# targets every customer. `customerGets.items.all: Boolean` selects every
# product; `customerGets.value.percentage: Float` is 0.00-1.00 (0.5 = 50%
# off). `combinesWith: DiscountCombinesWithInput` (orderDiscounts /
# productDiscounts / shippingDiscounts booleans, all default false) is what
# makes a created code genuinely *stackable* with another active discount —
# the "stackable" half of Scenario 1's name.
CREATE_BASIC_DISCOUNT_CODE_MUTATION = """
mutation CreateBasicDiscountCode($basicCodeDiscount: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $basicCodeDiscount) {
    codeDiscountNode {
      id
      codeDiscount {
        ... on DiscountCodeBasic {
          title
          codes(first: 1) { nodes { code } }
        }
      }
    }
    userErrors { field message }
  }
}
"""


# --- Sprint 4 Step 2: Checkout Specialist — Duplicate Discount lifecycle ----
#
# `codeDiscountNodes` (the bulk-listing query) is marked **Deprecated** in
# Shopify's current live Admin GraphQL schema docs (confirmed 2026-08-02,
# same fetch method that caught `productImageUpdate`'s full removal in
# Sprint 3) — but unlike that removed mutation, "Deprecated" here means
# still-functional-with-a-warning, not gone; the only non-deprecated
# alternative, `codeDiscountNodeByCode`, only supports an exact-code
# single-item lookup and cannot list/scan all active discounts, which this
# specialist's detection step requires. Used deliberately, flagged here as a
# known risk to revalidate (via live schema introspection against a real
# store) before any production use beyond this hackathon MVP — see
# CONFLICTS.md Sprint 4 Step 2 entry.
#
# `... on DiscountCodeBasic` is the only variant fetched: BXGY/free-shipping/
# app-managed discount codes simply return an empty `codeDiscount` object
# from this query and are skipped during normalization (see
# `_normalize_discount_node`) — the duplicate/stackable incident this
# specialist detects is specifically an amount-off (`DiscountCodeBasic`)
# phenomenon, matching exactly what the Demo Incident Generator's Scenario 1
# creates.
DISCOUNT_INSPECTION_QUERY = """
query DiscountInspection($cursor: String) {
  codeDiscountNodes(first: 100, after: $cursor, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      codeDiscount {
        ... on DiscountCodeBasic {
          title
          status
          createdAt
          combinesWith {
            orderDiscounts
            productDiscounts
            shippingDiscounts
          }
          customerGets {
            items {
              __typename
              ... on AllDiscountItems { allItems }
            }
          }
          totalSales { amount }
          codes(first: 1) { nodes { code } }
        }
      }
    }
  }
}
"""

DEACTIVATE_DISCOUNT_CODE_MUTATION = """
mutation DeactivateDiscountCode($id: ID!) {
  discountCodeDeactivate(id: $id) {
    codeDiscountNode {
      id
      codeDiscount {
        ... on DiscountCodeBasic { status }
      }
    }
    userErrors { field message }
  }
}
"""

ACTIVATE_DISCOUNT_CODE_MUTATION = """
mutation ActivateDiscountCode($id: ID!) {
  discountCodeActivate(id: $id) {
    codeDiscountNode {
      id
      codeDiscount {
        ... on DiscountCodeBasic { status }
      }
    }
    userErrors { field message }
  }
}
"""

# Uses the generic, non-deprecated `node(id:)` query (the `Node` interface
# every GID-identified object implements) rather than the deprecated
# single-item `codeDiscountNode(id:)` query — same reasoning as above, but
# here a non-deprecated path actually exists, so it's used.
VERIFY_DISCOUNT_STATE_QUERY = """
query VerifyDiscountState($id: ID!) {
  node(id: $id) {
    ... on DiscountCodeNode {
      id
      codeDiscount {
        ... on DiscountCodeBasic { status }
      }
    }
  }
}
"""


# --- Sprint 4 Step 3: Theme Guardian + Tracking Specialist -----------------
#
# Theme file WRITES (`themeFilesUpsert`, and the legacy REST Asset PUT/DELETE
# endpoint alike) require not just the `write_themes` scope this app already
# has, but a separate, manually-granted "exemption from Shopify" (a Google
# Form application + case-by-case review) — confirmed via two independent
# live-docs fetches 2026-08-02 (shopify.dev's `themeFilesUpsert` mutation
# page and its Asset-resource legacy page both state this explicitly; see
# CONFLICTS.md's Step 3 entry). NO write mutation for theme files is defined
# in this client as a result — Theme Guardian's restore is a guided,
# human-applied bundle, never an autonomous write (user-approved scope
# decision, 2026-08-02).
#
# Theme file READS have no such restriction — `read_themes` alone (already
# in this app's granted scope list) is sufficient.
#
# --- Productionization phase: real, approval-gated automated restore -------
#
# The exemption requirement above is real (confirmed again via live
# shopify.dev docs, `themeFilesUpsert`'s own "Requires" line: "The user needs
# write_themes and an exemption from Shopify to modify theme files"), but it
# is a per-app-instance grant, not a hard platform block — an app that HAS
# been granted the exemption can call this mutation and have it genuinely
# succeed. `restore_theme_file` below makes the real attempt on every
# merchant-approved restore rather than assuming denial in advance: most
# stores (this one included, by default) will see it rejected with
# `userErrors` and `ExecuteCognitiveTask` falls back to the guided-restore
# bundle exactly as before — but a store whose app instance does have the
# exemption gets a genuinely autonomous, verified restore. Never silently
# retried past the first rejection: any `userErrors` entry here is treated as
# a (likely permanent, for this store) denial, not a transient failure.
RESTORE_THEME_FILE_MUTATION = """
mutation RestoreThemeFile($themeId: ID!, $files: [OnlineStoreThemeFilesUpsertFileInput!]!) {
  themeFilesUpsert(themeId: $themeId, files: $files) {
    upsertedThemeFiles { filename }
    userErrors { code field filename message }
  }
}
"""

ACTIVE_THEME_QUERY = """
query ActiveTheme {
  themes(first: 1, roles: [MAIN]) {
    nodes { id name role }
  }
}
"""

THEME_FILES_QUERY = """
query ThemeFiles($themeId: ID!, $filenames: [String!]!) {
  theme(id: $themeId) {
    files(filenames: $filenames, first: 50) {
      nodes {
        filename
        body { ... on OnlineStoreThemeFileBodyText { content } }
        checksumMd5
      }
    }
  }
}
"""

# `write_script_tags` is a NEW scope this step needs — Steps 1/2 both landed
# with zero new scope; Tracking Specialist is the first specialist this
# sprint that genuinely requires one beyond what was already granted (see
# CONFLICTS.md / README's updated scope list).
SCRIPT_TAGS_QUERY = """
query ScriptTagsList($cursor: String) {
  scriptTags(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes { id src displayScope cache }
  }
}
"""

CREATE_SCRIPT_TAG_MUTATION = """
mutation CreateScriptTag($input: ScriptTagInput!) {
  scriptTagCreate(input: $input) {
    scriptTag { id src displayScope cache createdAt updatedAt }
    userErrors { field message }
  }
}
"""

DELETE_SCRIPT_TAG_MUTATION = """
mutation DeleteScriptTag($id: ID!) {
  scriptTagDelete(id: $id) {
    deletedScriptTagId
    userErrors { field message }
  }
}
"""

VERIFY_SCRIPT_TAG_STATE_QUERY = """
query VerifyScriptTagState($src: URL!) {
  scriptTags(first: 1, src: $src) {
    nodes { id src displayScope cache }
  }
}
"""


# --- Billing: NightShift Free / Pro / Business monetization -----------------
#
# Confirmed via live shopify.dev docs fetches (2026-08-09):
#   - https://shopify.dev/docs/api/admin-graphql/latest/mutations/appSubscriptionCreate
#   - https://shopify.dev/docs/api/admin-graphql/latest/objects/AppSubscription
#   - https://shopify.dev/docs/api/admin-graphql/latest/enums/AppSubscriptionStatus
#
# `appSubscriptionCreate(name: String!, returnUrl: URL!, lineItems:
# [AppSubscriptionLineItemInput!]!, test: Boolean, trialDays: Int,
# replacementBehavior: AppSubscriptionReplacementBehavior)`. Each line
# item's recurring price is nested TWO levels deep —
# `plan.appRecurringPricingDetails.price.{amount,currencyCode}` plus a
# sibling `plan.appRecurringPricingDetails.interval: AppPricingInterval`
# (only `EVERY_30_DAYS` / `ANNUAL` exist; this app only ever uses monthly).
# No new Shopify OAuth scope is required — the Billing API is available to
# any public/custom app without one (confirmed via the same docs fetch),
# unlike e.g. Tracking Specialist's `write_script_tags` (Sprint 4 Step 3).
#
# The payload's `confirmationUrl` is the URL the merchant must be redirected
# to so THEY approve the charge in Shopify's own UI — this app never
# bypasses that merchant-approval step.
CREATE_APP_SUBSCRIPTION_MUTATION = """
mutation CreateAppSubscription(
  $name: String!
  $returnUrl: URL!
  $test: Boolean!
  $lineItems: [AppSubscriptionLineItemInput!]!
) {
  appSubscriptionCreate(name: $name, returnUrl: $returnUrl, test: $test, lineItems: $lineItems) {
    appSubscription { id name status test }
    confirmationUrl
    userErrors { field message }
  }
}
"""

# `AppSubscription` implements the generic `Node` interface (same as
# `DiscountCodeNode`), so re-querying its real, current state after the
# merchant approves/declines uses the same non-deprecated `node(id:)` query
# `VERIFY_DISCOUNT_STATE_QUERY` already uses — never trust the `returnUrl`
# redirect's own `charge_id` query param alone (Sprint 3's Verification
# Engine "always re-query Shopify" axiom, applied here identically).
VERIFY_APP_SUBSCRIPTION_STATE_QUERY = """
query VerifyAppSubscriptionState($id: ID!) {
  node(id: $id) {
    ... on AppSubscription {
      id
      name
      status
      test
      currentPeriodEnd
    }
  }
}
"""


class ThemeWriteAccessDeniedError(Exception):
    """Raised by `restore_theme_file` when Shopify rejects `themeFilesUpsert`
    with a `userErrors` entry — in practice, almost always because this
    store's app installation lacks Shopify's manually-granted theme-file-write
    exemption (see this module's own comment above). Deliberately a distinct
    type from `ShopifyApiProblem` (transport/5xx/throttling failures, which
    ARE worth retrying): a denied write should never be retried, only ever
    handled by falling back to the guided-restore bundle."""

    def __init__(self, message: str, *, user_errors: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.user_errors = user_errors


@dataclass
class ThrottleStatus:
    currently_available: float
    maximum_available: float
    restore_rate: float

    @classmethod
    def from_extensions(cls, extensions: dict[str, Any]) -> "ThrottleStatus | None":
        cost = extensions.get("cost") if extensions else None
        throttle = cost.get("throttleStatus") if cost else None
        if not throttle:
            return None
        return cls(
            currently_available=throttle.get("currentlyAvailable", 0),
            maximum_available=throttle.get("maximumAvailable", 1000),
            restore_rate=throttle.get("restoreRate", 50),
        )

    def seconds_until_available(self, required_points: float) -> float:
        if self.currently_available >= required_points:
            return 0.0
        deficit = required_points - self.currently_available
        return deficit / max(self.restore_rate, 1)


@dataclass
class GraphQLResult:
    data: dict[str, Any]
    throttle_status: ThrottleStatus | None = None


class ShopifyGraphQLClient:
    """Thin async wrapper with adaptive backoff on cost-bucket exhaustion.

    Rate-limit handling: "Shopify GraphQL rate limiting (cost bucket
    exhausted; pause execution dynamically based on
    extensions.cost.throttleStatus)" and the Risk 2 mitigation: "adaptive
    backoff reading extensions.cost.throttleStatus headers."
    """

    def __init__(
        self,
        *,
        shop_domain: str,
        access_token: str,
        api_version: str,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
    ) -> None:
        self._shop_domain = shop_domain
        self._access_token = access_token
        self._api_version = api_version
        self._http_client = http_client or httpx.AsyncClient(timeout=15.0)
        self._max_retries = max_retries

    @property
    def _endpoint(self) -> str:
        path = GRAPHQL_API_VERSION_PATH.format(version=self._api_version)
        return f"https://{self._shop_domain}/{path}"

    @property
    def shop_domain(self) -> str:
        """Sprint 4 Step 3: `ExecuteCognitiveTask` needs this to build the
        Theme Editor deep link for a guided theme restore bundle — the
        client already knows it internally; this just exposes it rather
        than threading `shop_domain` through as a second parameter
        everywhere `ShopifyGraphQLClient` is already passed around."""
        return self._shop_domain

    async def execute(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> GraphQLResult:
        attempt = 0
        while True:
            response = await self._http_client.post(
                self._endpoint,
                json={"query": query, "variables": variables or {}},
                headers={
                    "X-Shopify-Access-Token": self._access_token,
                    "Content-Type": "application/json",
                },
            )
            if response.status_code >= 500:
                raise ShopifyApiProblem(
                    f"Shopify GraphQL API returned {response.status_code}"
                )

            payload = response.json()
            throttle = ThrottleStatus.from_extensions(payload.get("extensions", {}))

            if response.status_code == 200 and "errors" not in payload:
                return GraphQLResult(data=payload.get("data", {}), throttle_status=throttle)

            if attempt >= self._max_retries:
                raise ShopifyApiProblem(
                    f"Shopify GraphQL request failed after {attempt} retries: "
                    f"{payload.get('errors')}"
                )

            wait_seconds = throttle.seconds_until_available(50) if throttle else (2 ** attempt)
            await asyncio.sleep(wait_seconds)
            attempt += 1

    async def fetch_baseline_snapshot(self) -> dict[str, Any]:
        """Executes the Sprint 1 baseline discovery queries, following
        pageInfo.hasNextPage cursors for large catalogs (>10,000 SKUs edge
        case) with a batch size of 250 entities per Feature 3."""
        products: list[dict[str, Any]] = []
        products_cursor: str | None = None
        shop_info: dict[str, Any] = {}
        discounts: list[dict[str, Any]] = []
        themes: list[dict[str, Any]] = []
        script_tags: list[dict[str, Any]] = []

        while True:
            result = await self.execute(
                BASELINE_DISCOVERY_QUERY,
                {"productsCursor": products_cursor, "discountsCursor": None},
            )
            data = result.data
            shop_info = data.get("shop", shop_info)
            products_page = data.get("products", {})
            products.extend(products_page.get("nodes", []))
            discounts = data.get("discountNodes", {}).get("nodes", discounts)
            themes = data.get("themes", {}).get("nodes", themes)
            script_tags = data.get("scriptTags", {}).get("nodes", script_tags)

            page_info = products_page.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            products_cursor = page_info.get("endCursor")

        return {
            "shop": shop_info,
            "products": products,
            "discounts": discounts,
            "themes": themes,
            "script_tags": script_tags,
        }

    async def fetch_catalog_for_inspection(self, *, max_products: int = 500) -> list[dict[str, Any]]:
        """Cursored batch fetch for the Product Inspection Engine (Sprint 2
        Feature 1): 250 items/batch, capped at `max_products` (Sprint 2
        Risk 2 mitigation, verbatim: "limit hackathon MVP scanning depth to
        top 500 active products")."""
        products: list[dict[str, Any]] = []
        cursor: str | None = None

        while len(products) < max_products:
            result = await self.execute(CATALOG_INSPECTION_QUERY, {"cursor": cursor})
            page = result.data.get("products", {})
            products.extend(page.get("nodes", []))

            page_info = page.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return products[:max_products]

    async def update_product_image_alt_text(
        self, *, product_gid: str, image_gid: str, alt_text: str
    ) -> dict[str, Any]:
        """Execute the ALT text mutation and raise ShopifyApiProblem if
        Shopify returns userErrors (mutation-level errors are NOT the same
        as transport-level errors/throttling, which `execute()` already
        handles — userErrors indicate a semantically rejected mutation,
        e.g. invalid GID, and should never be silently swallowed).

        `image_gid` must be a `MediaImage` GID (see the module-level comment
        above `UPDATE_PRODUCT_IMAGE_ALT_TEXT_MUTATION`) — `product_gid` isn't
        needed by `fileUpdate` itself, but stays a parameter for interface
        consistency with `update_product_description` and its caller
        (`execute_cognitive_task.py`, which doesn't otherwise know which
        mutation needs which arguments)."""
        result = await self.execute(
            UPDATE_PRODUCT_IMAGE_ALT_TEXT_MUTATION,
            {"files": [{"id": image_gid, "alt": alt_text}]},
        )
        payload = result.data.get("fileUpdate", {})
        user_errors = payload.get("userErrors", [])
        if user_errors:
            raise ShopifyApiProblem(f"fileUpdate rejected: {user_errors}")
        files = payload.get("files") or [{}]
        # Normalize back to the `{id, altText}` shape the rest of the
        # codebase (verification comparison, DB records) already expects —
        # confines the Files-API-vs-ProductImage-API difference to this
        # client rather than rippling through `verify_execution.py`.
        return {"id": files[0].get("id"), "altText": files[0].get("alt")}

    async def update_product_description(
        self, *, product_gid: str, description_html: str
    ) -> dict[str, Any]:
        result = await self.execute(
            UPDATE_PRODUCT_DESCRIPTION_MUTATION,
            {"input": {"id": product_gid, "descriptionHtml": description_html}},
        )
        payload = result.data.get("productUpdate", {})
        user_errors = payload.get("userErrors", [])
        if user_errors:
            raise ShopifyApiProblem(f"productUpdate rejected: {user_errors}")
        return payload.get("product", {})

    async def fetch_product_state(self, *, product_gid: str) -> dict[str, Any]:
        """Read-after-write verification fetch — always re-queries Shopify,
        never trusts the mutation response alone (Sprint 3 Verification
        Engine axiom)."""
        result = await self.execute(VERIFY_PRODUCT_STATE_QUERY, {"productId": product_gid})
        product = result.data.get("product", {}) or {}
        if not product:
            return {}
        # Normalize `media`/`alt` (Files API) back to the `images`/`altText`
        # shape `verify_execution.py`'s `_compare_item` already expects —
        # see `UPDATE_PRODUCT_IMAGE_ALT_TEXT_MUTATION`'s module comment for
        # why this client fetches `media`, not `images`, in the first place.
        media_nodes = product.get("media", {}).get("nodes", [])
        product["images"] = {
            "nodes": [
                {"id": node.get("id"), "altText": node.get("alt")}
                for node in media_nodes
                if node.get("mediaContentType") == "IMAGE"
            ]
        }
        return product

    async def create_basic_discount_code(
        self,
        *,
        title: str,
        code: str,
        percentage: float,
        starts_at: str,
        combines_with: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        """Sprint 4 Step 1: Demo Incident Generator only — see the module
        comment above `CREATE_BASIC_DISCOUNT_CODE_MUTATION` for the exact
        confirmed schema shape. `percentage` is 0.00-1.00 (0.5 = 50% off);
        `combines_with` is passed straight through as `DiscountCombinesWithInput`
        (e.g. `{"orderDiscounts": True, "productDiscounts": True,
        "shippingDiscounts": True}` to make the created code combine with
        everything, reproducing a genuinely stackable duplicate discount)."""
        variables = {
            "basicCodeDiscount": {
                "title": title,
                "code": code,
                "startsAt": starts_at,
                "context": {"all": "ALL"},
                "customerGets": {
                    "items": {"all": True},
                    "value": {"percentage": percentage},
                },
                "combinesWith": combines_with or {},
            }
        }
        result = await self.execute(CREATE_BASIC_DISCOUNT_CODE_MUTATION, variables)
        payload = result.data.get("discountCodeBasicCreate", {})
        user_errors = payload.get("userErrors", [])
        if user_errors:
            raise ShopifyApiProblem(f"discountCodeBasicCreate rejected: {user_errors}")
        return payload.get("codeDiscountNode") or {}

    @staticmethod
    def _normalize_discount_node(node: dict[str, Any]) -> dict[str, Any] | None:
        """Flattens one `codeDiscountNodes` node into the shape
        `domain/discount_inspection.py::inspect_discounts` expects. Returns
        None for non-`DiscountCodeBasic` nodes (BXGY/free-shipping/app
        discounts), which come back with an empty `codeDiscount` object
        since this query only requests `... on DiscountCodeBasic` fields."""
        code_discount = node.get("codeDiscount") or {}
        if "title" not in code_discount:
            return None  # Not a DiscountCodeBasic — no matching inline fragment fields.

        items = (code_discount.get("customerGets") or {}).get("items") or {}
        codes = (code_discount.get("codes") or {}).get("nodes") or []
        combines_with = code_discount.get("combinesWith") or {}
        total_sales = code_discount.get("totalSales") or {}

        return {
            "id": node.get("id"),
            "title": code_discount.get("title"),
            "code": codes[0].get("code") if codes else None,
            "status": code_discount.get("status"),
            "created_at": code_discount.get("createdAt"),
            "targets_all_items": items.get("__typename") == "AllDiscountItems" and bool(items.get("allItems")),
            "combines_with": {
                "order_discounts": bool(combines_with.get("orderDiscounts")),
                "product_discounts": bool(combines_with.get("productDiscounts")),
                "shipping_discounts": bool(combines_with.get("shippingDiscounts")),
            },
            "total_sales_usd": float(total_sales.get("amount") or 0.0),
        }

    async def fetch_discount_codes_for_inspection(self, *, max_discounts: int = 200) -> list[dict[str, Any]]:
        """Sprint 4 Step 2: Checkout Specialist's Observe step. Cursored
        batch fetch of currently-active discount codes, capped at
        `max_discounts` for the same bounded-scan reason
        `fetch_catalog_for_inspection` caps at `max_products` (Sprint 2 Risk
        2 mitigation). Non-`DiscountCodeBasic` nodes are silently dropped by
        `_normalize_discount_node` — see its own docstring."""
        discounts: list[dict[str, Any]] = []
        cursor: str | None = None

        while len(discounts) < max_discounts:
            result = await self.execute(DISCOUNT_INSPECTION_QUERY, {"cursor": cursor})
            page = result.data.get("codeDiscountNodes", {})
            for node in page.get("nodes", []):
                normalized = self._normalize_discount_node(node)
                if normalized is not None:
                    discounts.append(normalized)

            page_info = page.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return discounts[:max_discounts]

    async def deactivate_discount_code(self, *, discount_id: str) -> dict[str, Any]:
        """Execute step for `DEACTIVATE_DUPLICATE_DISCOUNT`. Per Shopify's
        own documented behavior, deactivating sets the discount's `endsAt`
        to now, which is why the resulting `status` reads back as `EXPIRED`
        — there is no separate `INACTIVE` value in `DiscountStatus` (only
        `ACTIVE`/`EXPIRED`/`SCHEDULED`, confirmed via live schema docs)."""
        result = await self.execute(DEACTIVATE_DISCOUNT_CODE_MUTATION, {"id": discount_id})
        payload = result.data.get("discountCodeDeactivate", {})
        user_errors = payload.get("userErrors", [])
        if user_errors:
            raise ShopifyApiProblem(f"discountCodeDeactivate rejected: {user_errors}")
        return payload.get("codeDiscountNode") or {}

    async def activate_discount_code(self, *, discount_id: str) -> dict[str, Any]:
        """Rollback path for `DEACTIVATE_DUPLICATE_DISCOUNT` — genuinely
        restores the discount to active (unlike the Product Quality fix
        types' rollback, which restores to an empty placeholder since the
        true prior value was never captured; here the "prior state" is
        simply "active," which `discountCodeActivate` reproduces exactly)."""
        result = await self.execute(ACTIVATE_DISCOUNT_CODE_MUTATION, {"id": discount_id})
        payload = result.data.get("discountCodeActivate", {})
        user_errors = payload.get("userErrors", [])
        if user_errors:
            raise ShopifyApiProblem(f"discountCodeActivate rejected: {user_errors}")
        return payload.get("codeDiscountNode") or {}

    async def fetch_discount_state(self, *, discount_id: str) -> dict[str, Any]:
        """Read-after-write verification fetch for the discount lifecycle —
        same axiom as `fetch_product_state`: always re-query Shopify, never
        trust the mutation response alone."""
        result = await self.execute(VERIFY_DISCOUNT_STATE_QUERY, {"id": discount_id})
        node = result.data.get("node") or {}
        if not node:
            return {}
        code_discount = node.get("codeDiscount") or {}
        return {"id": node.get("id"), "status": code_discount.get("status")}

    async def fetch_active_theme_id(self) -> str | None:
        """Sprint 4 Step 3: resolves the live/published (`MAIN`-role) theme's
        GID — Theme Guardian always watches the currently-published theme,
        not drafts/unpublished themes a merchant may also have."""
        result = await self.execute(ACTIVE_THEME_QUERY)
        nodes = result.data.get("themes", {}).get("nodes", [])
        return nodes[0]["id"] if nodes else None

    async def fetch_theme_files(self, *, theme_id: str, filenames: list[str]) -> dict[str, str]:
        """Read-only theme file fetch (`read_themes` scope only — no
        exemption needed, unlike any write path; see this module's own
        Step 3 comment). Returns `{filename: content}`; a requested filename
        that doesn't exist in the theme (e.g. deleted entirely, not just
        edited) is simply absent from the returned dict rather than raising
        — callers (the inspection engine) treat "file missing" the same as
        any other divergence from baseline."""
        if not filenames:
            return {}
        result = await self.execute(THEME_FILES_QUERY, {"themeId": theme_id, "filenames": filenames})
        theme = result.data.get("theme") or {}
        nodes = (theme.get("files") or {}).get("nodes") or []
        contents: dict[str, str] = {}
        for node in nodes:
            body = node.get("body") or {}
            content = body.get("content")
            if content is not None:
                contents[node["filename"]] = content
        return contents

    async def restore_theme_file(self, *, theme_id: str, filename: str, content: str) -> dict[str, Any]:
        """Real, live-write restore attempt for `GENERATE_THEME_RESTORE_GUIDE`
        (productionization phase) — see the module comment above
        `RESTORE_THEME_FILE_MUTATION`. Raises `ThemeWriteAccessDeniedError`
        (never `ShopifyApiProblem`) if Shopify returns `userErrors`, so the
        caller can distinguish "this store can't do automated restores" from
        a genuine transport failure worth retrying.
        """
        result = await self.execute(
            RESTORE_THEME_FILE_MUTATION,
            {
                "themeId": theme_id,
                "files": [{"filename": filename, "body": {"type": "TEXT", "value": content}}],
            },
        )
        payload = result.data.get("themeFilesUpsert", {})
        user_errors = payload.get("userErrors", [])
        if user_errors:
            raise ThemeWriteAccessDeniedError(
                f"themeFilesUpsert rejected for {filename!r}: {user_errors}", user_errors=user_errors
            )
        upserted = payload.get("upsertedThemeFiles") or []
        return {"filename": filename, "upserted": bool(upserted), "status": "restored"}

    async def fetch_script_tags(self, *, max_tags: int = 200) -> list[dict[str, Any]]:
        """Sprint 4 Step 3: Tracking Specialist's Observe step. Cursored
        batch fetch, same bounded-scan convention as
        `fetch_discount_codes_for_inspection`/`fetch_catalog_for_inspection`."""
        tags: list[dict[str, Any]] = []
        cursor: str | None = None

        while len(tags) < max_tags:
            result = await self.execute(SCRIPT_TAGS_QUERY, {"cursor": cursor})
            page = result.data.get("scriptTags", {})
            for node in page.get("nodes", []):
                tags.append(
                    {
                        "id": node.get("id"),
                        "src": node.get("src"),
                        "display_scope": node.get("displayScope"),
                        "cache": node.get("cache"),
                    }
                )
            page_info = page.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return tags[:max_tags]

    async def create_script_tag(self, *, src: str, display_scope: str = "ONLINE_STORE") -> dict[str, Any]:
        """Execute step for `RECREATE_TRACKING_SCRIPT_TAG` — recreates a
        script tag from a `tracking_snapshots` row's own `src`/`display_scope`.
        Requires `write_script_tags` (a new scope this step needs — see this
        module's own Step 3 comment)."""
        result = await self.execute(
            CREATE_SCRIPT_TAG_MUTATION,
            {"input": {"src": src, "displayScope": display_scope}},
        )
        payload = result.data.get("scriptTagCreate", {})
        user_errors = payload.get("userErrors", [])
        if user_errors:
            raise ShopifyApiProblem(f"scriptTagCreate rejected: {user_errors}")
        tag = payload.get("scriptTag") or {}
        return {"id": tag.get("id"), "src": tag.get("src"), "display_scope": tag.get("displayScope")}

    async def delete_script_tag(self, *, script_tag_id: str) -> dict[str, Any]:
        """Rollback path for `RECREATE_TRACKING_SCRIPT_TAG`. `script_tag_id`
        is the id Shopify assigned when `create_script_tag` ran — never known
        at Plan time, so it must be the live-captured id patched into the
        task's `execution_plan["items"][i]["rollback"]` post-execution (see
        `execute_cognitive_task.py`'s own comment), not a Plan-time value."""
        if not script_tag_id:
            raise ShopifyApiProblem("scriptTagDelete rejected: no script_tag_id available to delete")
        result = await self.execute(DELETE_SCRIPT_TAG_MUTATION, {"id": script_tag_id})
        payload = result.data.get("scriptTagDelete", {})
        user_errors = payload.get("userErrors", [])
        if user_errors:
            raise ShopifyApiProblem(f"scriptTagDelete rejected: {user_errors}")
        return {"deleted_script_tag_id": payload.get("deletedScriptTagId")}

    async def fetch_script_tag_state(self, *, src: str) -> dict[str, Any]:
        """Read-after-write verification fetch for the tracking script
        lifecycle — same axiom as `fetch_product_state`/`fetch_discount_state`:
        always re-query Shopify, never trust the mutation response alone.
        Looked up by `src` (via the `scriptTags(src:)` filter arg), not by
        id — the id returned by `create_script_tag` is trusted for rollback
        bookkeeping only, verification independently re-confirms the tag is
        actually live."""
        result = await self.execute(VERIFY_SCRIPT_TAG_STATE_QUERY, {"src": src})
        nodes = result.data.get("scriptTags", {}).get("nodes", [])
        if not nodes:
            return {}
        node = nodes[0]
        return {"id": node.get("id"), "src": node.get("src"), "display_scope": node.get("displayScope")}

    async def create_recurring_subscription(
        self,
        *,
        name: str,
        return_url: str,
        monthly_price_usd: float,
        interval: str = "EVERY_30_DAYS",
        test: bool = True,
        currency_code: str = "USD",
    ) -> dict[str, Any]:
        """Billing: the one Shopify Billing mutation this app needs —
        `appSubscriptionCreate` (no third-party payment processor, per the
        product decision; see this module's own Billing comment above for
        the exact confirmed schema shape).

        `test=True` marks a dev-store test transaction, per Shopify's own
        documented behavior never actually billed to the merchant — the
        caller (`api/v1/billing.py`) drives this from
        `Settings.shopify_billing_test_mode`, which defaults to True so a
        hackathon/dev deployment can never accidentally create a real,
        billed charge.
        """
        variables = {
            "name": name,
            "returnUrl": return_url,
            "test": test,
            "lineItems": [
                {
                    "plan": {
                        "appRecurringPricingDetails": {
                            "price": {"amount": monthly_price_usd, "currencyCode": currency_code},
                            "interval": interval,
                        }
                    }
                }
            ],
        }
        result = await self.execute(CREATE_APP_SUBSCRIPTION_MUTATION, variables)
        payload = result.data.get("appSubscriptionCreate", {})
        user_errors = payload.get("userErrors", [])
        if user_errors:
            raise ShopifyApiProblem(f"appSubscriptionCreate rejected: {user_errors}")
        subscription = payload.get("appSubscription") or {}
        return {
            "id": subscription.get("id"),
            "name": subscription.get("name"),
            "status": subscription.get("status"),
            "confirmation_url": payload.get("confirmationUrl"),
        }

    async def fetch_app_subscription_state(self, *, subscription_gid: str) -> dict[str, Any]:
        """Read-after-redirect verification fetch — same "always re-query
        Shopify, never trust the response/redirect alone" axiom as
        `fetch_discount_state`/`fetch_product_state`/`fetch_script_tag_state`.
        `GET /api/v1/billing/confirm` calls this rather than trusting the
        `returnUrl` redirect's own `charge_id` query param."""
        result = await self.execute(VERIFY_APP_SUBSCRIPTION_STATE_QUERY, {"id": subscription_gid})
        node = result.data.get("node") or {}
        if not node:
            return {}
        return {
            "id": node.get("id"),
            "name": node.get("name"),
            "status": node.get("status"),
            "test": node.get("test"),
            "current_period_end": node.get("currentPeriodEnd"),
        }

    async def aclose(self) -> None:
        await self._http_client.aclose()


async def exchange_authorization_code(
    *,
    shop_domain: str,
    client_id: str,
    client_secret: str,
    code: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST https://{shop}/admin/oauth/access_token — Sprint 1 OAuth
    Controller pseudocode step 5."""
    client = http_client or httpx.AsyncClient(timeout=15.0)
    try:
        response = await client.post(
            f"https://{shop_domain}/admin/oauth/access_token",
            json={"client_id": client_id, "client_secret": client_secret, "code": code},
        )
    except httpx.HTTPError as exc:
        raise ShopifyApiProblem(f"Shopify token exchange request failed: {exc}") from exc

    if response.status_code >= 500 or response.status_code == 408:
        raise ShopifyApiProblem(
            f"Shopify token exchange returned {response.status_code}"
        )
    if response.status_code != 200:
        raise ShopifyApiProblem(
            f"Shopify token exchange rejected the authorization code "
            f"({response.status_code})"
        )
    return response.json()
