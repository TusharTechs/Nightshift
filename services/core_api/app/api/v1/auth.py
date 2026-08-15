"""OAuth Ingress endpoints (Sprint 1 Feature 2 / Endpoints 1-2).

GET /api/v1/auth/shopify           — redirect to Shopify consent screen
GET /api/v1/auth/shopify/callback  — HMAC verify, token exchange, provision tenant
"""

from __future__ import annotations

import time
import urllib.parse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.api.deps import get_complete_oauth_installation_use_case
from app.api.errors import (
    InvalidHmacSignatureProblem,
    InvalidShopDomainProblem,
    ShopifyApiProblem,
    TimestampExpiredProblem,
)
from app.application.use_cases.complete_oauth_installation import (
    CompleteOAuthInstallation,
    ShopifyCallbackParams,
    ShopifyTokenExchangeResult,
    validate_shop_domain,
)
from app.config import Settings, get_settings
from app.domain.security import (
    InvalidHmacSignatureError,
    TimestampExpiredError,
)
from app.infrastructure.shopify_client import exchange_authorization_code
from app.logging import get_logger

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
logger = get_logger(component="auth")


@router.get("/shopify")
async def initiate_shopify_oauth(
    shop: str,
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Redirect merchant to Shopify's permission consent screen.

    Requests exactly the 7 scopes mandated by Sprint 1 Story 1's acceptance
    criteria — never a superset (critical scopes like write_payment_gateways
    are explicitly excluded, per SATDD Section 7.4).
    """
    if not validate_shop_domain(shop):
        logger.warning("oauth_install_failed", error_code="INVALID_SHOP_DOMAIN", shop=shop)
        raise InvalidShopDomainProblem(f"'{shop}' is not a valid myshopify.com domain")

    query = urllib.parse.urlencode(
        {
            "client_id": settings.shopify_app_client_id,
            "scope": ",".join(settings.shopify_oauth_scopes),
            "redirect_uri": f"{settings.shopify_app_url}/api/v1/auth/shopify/callback",
        }
    )
    logger.info("oauth_install_initiated", shop=shop)
    return RedirectResponse(url=f"https://{shop}/admin/oauth/authorize?{query}", status_code=302)


@router.get("/shopify/callback")
async def shopify_oauth_callback(
    request: Request,
    use_case: CompleteOAuthInstallation = Depends(get_complete_oauth_installation_use_case),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Validate HMAC, exchange the authorization code, provision the tenant,
    and enqueue the baseline discovery scan (OAuth Controller pseudocode,
    Section 1.12, steps 1-9)."""
    raw_params = dict(request.query_params)
    shop = raw_params.get("shop", "")
    code = raw_params.get("code", "")
    hmac_param = raw_params.get("hmac", "")
    timestamp = raw_params.get("timestamp", "0")
    host = raw_params.get("host", "")

    if not validate_shop_domain(shop):
        logger.warning("oauth_install_failed", error_code="INVALID_SHOP_DOMAIN", shop=shop)
        raise InvalidShopDomainProblem(f"'{shop}' is not a valid myshopify.com domain")

    params = ShopifyCallbackParams(
        shop=shop,
        code=code,
        hmac=hmac_param,
        timestamp=int(timestamp),
        host=host,
        raw_query_params=raw_params,
    )

    # Steps 3-4 happen BEFORE step 5 (token exchange): a request that fails
    # timestamp/HMAC verification must never reach Shopify's token endpoint.
    try:
        use_case.verify_callback(params, current_timestamp=int(time.time()))
    except InvalidHmacSignatureError as exc:
        logger.warning("oauth_install_failed", error_code="INVALID_HMAC_SIGNATURE", shop=shop)
        raise InvalidHmacSignatureProblem(str(exc)) from exc
    except TimestampExpiredError as exc:
        logger.warning("oauth_install_failed", error_code="TIMESTAMP_EXPIRED", shop=shop)
        raise TimestampExpiredProblem(str(exc)) from exc

    try:
        token_payload = await exchange_authorization_code(
            shop_domain=shop,
            client_id=settings.shopify_app_client_id,
            client_secret=settings.shopify_app_secret,
            code=code,
        )
    except ShopifyApiProblem:
        logger.warning("oauth_install_failed", error_code="SHOPIFY_API_ERROR", shop=shop)
        raise

    token_result = ShopifyTokenExchangeResult(
        access_token=token_payload["access_token"], scope=token_payload.get("scope", "")
    )

    result = await use_case.execute(params, token_result, current_timestamp=int(time.time()))

    logger.info(
        "oauth_install_success",
        shop=shop,
        store_id=str(result.store.id),
        discovery_task_id=result.discovery_task_id,
    )

    return RedirectResponse(
        url=f"https://{shop}/admin/apps/nightshift-ai", status_code=302
    )
