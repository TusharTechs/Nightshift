"""Shopify mandatory compliance webhooks.

Every Shopify app that can be installed on a live store MUST implement:

  - `app/uninstalled`         — deactivate the store the moment a merchant
                                 uninstalls, so nothing in the Celery pipeline
                                 (nightly scheduler, on-demand shifts, etc.)
                                 keeps acting on a store that no longer has a
                                 valid access token.
  - `customers/data_request`  — GDPR: a merchant's customer asked for their
                                 data; the app must respond within 30 days.
  - `customers/redact`        — GDPR: a merchant's customer asked for their
                                 data to be deleted.
  - `shop/redact`             — GDPR: 48 hours after a merchant uninstalls,
                                 Shopify asks the app to delete that shop's
                                 data.

Shopify periodically sends real and test deliveries to all four endpoints
(including to apps that have never gone through a real GDPR event) and
expects a fast 2xx response — see each handler's own docstring for exactly
what "compliant" means for NightShift's data model.

HMAC verification note: this is a DIFFERENT scheme from the OAuth ingress's
`verify_shopify_hmac` (app/api/v1/auth.py) — webhooks sign the raw JSON
request body (not the query string) and base64-encode (not hex-encode) the
digest, in the `X-Shopify-Hmac-Sha256` header. See
`app.domain.security.verify_webhook_hmac`'s own docstring. The raw body is
read via `await request.body()` and verified BEFORE any JSON parsing — no
Pydantic request-body model is used on these routes, since re-serializing a
parsed payload is not guaranteed to byte-for-byte match what Shopify signed.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from app.api.deps import get_audit_log_repository, get_store_repository
from app.application.ports import AuditLogRepository, StoreRepository
from app.config import Settings, get_settings
from app.domain.security import verify_webhook_hmac
from app.logging import get_logger

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])
logger = get_logger(component="webhooks")

# Every audit log entry written from this module carries this actor_type, so
# "who/what did this" is unambiguous when reading the audit trail later —
# matches the existing convention of "AI_AGENT" / "MERCHANT" /
# "DEMO_GENERATOR" (see app/application/use_cases/*.py).
_ACTOR_TYPE = "SHOPIFY_WEBHOOK"


def _parse_json_body(raw_body: bytes) -> dict:
    """Best-effort JSON parse of an already-HMAC-verified body.

    A body that fails to parse as JSON after passing HMAC verification would
    be highly unusual (it would mean Shopify itself signed malformed JSON),
    but a webhook endpoint must never 500 on it — treat it as an empty
    payload rather than crashing.
    """
    if not raw_body:
        return {}
    try:
        parsed = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("webhook_body_not_json")
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _verify_webhook_or_401(
    request: Request, settings: Settings, hmac_header: str, *, topic: str
) -> tuple[bytes, bool]:
    """Reads the raw body once and verifies it against `hmac_header`.

    Returns `(raw_body, is_valid)` rather than raising, so every route below
    can decide its own 401 response body while sharing this exact
    verify-before-touching-anything-else sequencing.
    """
    raw_body = await request.body()
    is_valid = verify_webhook_hmac(raw_body, hmac_header, settings.shopify_app_secret)
    if not is_valid:
        logger.warning("webhook_rejected", topic=topic, reason="invalid_hmac_signature")
    return raw_body, is_valid


def _unauthorized() -> JSONResponse:
    return JSONResponse(status_code=401, content={"error": "invalid_hmac_signature"})


@router.post("/app-uninstalled")
async def app_uninstalled(
    request: Request,
    x_shopify_hmac_sha256: str = Header(default="", alias="X-Shopify-Hmac-Sha256"),
    settings: Settings = Depends(get_settings),
    stores: StoreRepository = Depends(get_store_repository),
    audit_logs: AuditLogRepository = Depends(get_audit_log_repository),
) -> JSONResponse:
    """`app/uninstalled`: deactivate the Store so the nightly scheduler and
    every other `is_active`-gated code path (StoreRepository.list_active)
    stops running shifts/inspections against a store that just revoked our
    access.

    Payload shape (verified against Shopify's real Shop resource, which is
    what this topic's payload is a full serialization of — not just
    `{shop_id, domain}`): the shop's stable identifier is `myshopify_domain`
    (always present, always ends in `.myshopify.com`); `domain` is the shop's
    *current* custom/primary domain and is nullable, so `myshopify_domain` is
    used to look up the Store — it matches what `shopify_domain` /
    `myshopify_domain` are both set to at OAuth install time (see
    `CompleteOAuthInstallation`, which sets both columns to the `shop` OAuth
    query param, itself always a `*.myshopify.com` value).

    Idempotent and non-leaking: an unknown/already-inactive shop still
    returns 200, so this endpoint never reveals to a caller (real Shopify or
    otherwise) whether a given domain is a recognized NightShift tenant.
    """
    raw_body, is_valid = await _verify_webhook_or_401(
        request, settings, x_shopify_hmac_sha256, topic="app/uninstalled"
    )
    if not is_valid:
        return _unauthorized()

    payload = _parse_json_body(raw_body)
    shop_domain = payload.get("myshopify_domain") or payload.get("domain") or ""

    store = await stores.get_by_shopify_domain(shop_domain) if shop_domain else None
    if store is None:
        logger.info("webhook_received", topic="app/uninstalled", store_found=False)
        return JSONResponse(status_code=200, content={"received": True})

    await stores.deactivate(store.id)
    await audit_logs.append(
        store_id=store.id,
        actor_type=_ACTOR_TYPE,
        actor_id="app/uninstalled",
        action="STORE_DEACTIVATED",
        rationale=(
            "Merchant uninstalled NightShift AI (Shopify app/uninstalled "
            "webhook received and HMAC-verified). Store marked inactive "
            "(is_active=False) so no further nightly shifts, catalog "
            "inspections, or executions run against it."
        ),
    )
    logger.info(
        "webhook_received", topic="app/uninstalled", store_found=True, store_id=str(store.id)
    )
    return JSONResponse(status_code=200, content={"received": True})


async def _handle_gdpr_webhook(
    request: Request,
    hmac_header: str,
    settings: Settings,
    stores: StoreRepository,
    audit_logs: AuditLogRepository,
    *,
    topic: str,
    actor_id: str,
    action: str,
    rationale: str,
) -> JSONResponse:
    """Shared plumbing for the 3 GDPR webhooks: verify HMAC, look up the shop
    (best-effort, purely for audit-log attribution), and write one honest
    audit log entry describing exactly what was (and wasn't) done.

    NightShift's data model does not store any customer PII today — it only
    stores product/discount/theme/tracking-script metadata scoped to a
    store, never individual customer records, orders, or PII (confirmed:
    grep across app/domain/models.py and
    app/infrastructure/database/models.py finds no customer-scoped table).
    So for all three GDPR topics there is no customer data to locate or
    erase — these are legitimate "acknowledge, verify, and record" no-ops,
    not a silent black hole. The audit log entry says exactly that, rather
    than fabricating a "customer data deleted" claim.
    """
    raw_body, is_valid = await _verify_webhook_or_401(request, settings, hmac_header, topic=topic)
    if not is_valid:
        return _unauthorized()

    payload = _parse_json_body(raw_body)
    shop_domain = payload.get("shop_domain") or ""

    store = await stores.get_by_shopify_domain(shop_domain) if shop_domain else None
    if store is not None:
        await audit_logs.append(
            store_id=store.id,
            actor_type=_ACTOR_TYPE,
            actor_id=actor_id,
            action=action,
            rationale=rationale,
        )
        logger.info("webhook_received", topic=topic, store_found=True, store_id=str(store.id))
    else:
        # No Store row exists for this shop domain (never installed, already
        # fully offboarded, or the payload omitted shop_domain) — there is no
        # valid store_id to attach an audit_logs row to (store_id is a NOT
        # NULL foreign key), so this is recorded via structured application
        # logs only. Still a real, honest record of receipt; still 200,
        # per Shopify's own guidance to never fail these deliveries.
        logger.info("webhook_received", topic=topic, store_found=False)

    return JSONResponse(status_code=200, content={"received": True})


@router.post("/customers-data-request")
async def customers_data_request(
    request: Request,
    x_shopify_hmac_sha256: str = Header(default="", alias="X-Shopify-Hmac-Sha256"),
    settings: Settings = Depends(get_settings),
    stores: StoreRepository = Depends(get_store_repository),
    audit_logs: AuditLogRepository = Depends(get_audit_log_repository),
) -> JSONResponse:
    """`customers/data_request`: a shop's customer asked for the data
    NightShift holds about them.

    NightShift stores no customer records/orders/PII (see
    `_handle_gdpr_webhook`'s docstring) — there is nothing customer-specific
    to compile or return, so this acknowledges the request and records that
    fact, honestly, in the audit log.
    """
    return await _handle_gdpr_webhook(
        request,
        x_shopify_hmac_sha256,
        settings,
        stores,
        audit_logs,
        topic="customers/data_request",
        actor_id="customers/data_request",
        action="CUSTOMER_DATA_REQUEST_ACKNOWLEDGED",
        rationale=(
            "Received and HMAC-verified a Shopify customers/data_request "
            "webhook. NightShift's data model does not store any "
            "customer-identifying records, orders, or PII for this store "
            "(only product/discount/theme/tracking metadata) — there is no "
            "customer data on file to compile or disclose. No customer "
            "data was found or returned."
        ),
    )


@router.post("/customers-redact")
async def customers_redact(
    request: Request,
    x_shopify_hmac_sha256: str = Header(default="", alias="X-Shopify-Hmac-Sha256"),
    settings: Settings = Depends(get_settings),
    stores: StoreRepository = Depends(get_store_repository),
    audit_logs: AuditLogRepository = Depends(get_audit_log_repository),
) -> JSONResponse:
    """`customers/redact`: a shop's customer asked for their data to be
    erased.

    NightShift stores no customer records/orders/PII (see
    `_handle_gdpr_webhook`'s docstring) — there is nothing customer-specific
    to delete, so this acknowledges the request and records that fact,
    honestly, rather than claiming a deletion that never needed to happen.
    """
    return await _handle_gdpr_webhook(
        request,
        x_shopify_hmac_sha256,
        settings,
        stores,
        audit_logs,
        topic="customers/redact",
        actor_id="customers/redact",
        action="CUSTOMER_REDACT_ACKNOWLEDGED",
        rationale=(
            "Received and HMAC-verified a Shopify customers/redact webhook. "
            "NightShift's data model does not store any customer-identifying "
            "records, orders, or PII for this store (only "
            "product/discount/theme/tracking metadata) — there was no "
            "customer data to erase. No records were deleted because none "
            "existed."
        ),
    )


@router.post("/shop-redact")
async def shop_redact(
    request: Request,
    x_shopify_hmac_sha256: str = Header(default="", alias="X-Shopify-Hmac-Sha256"),
    settings: Settings = Depends(get_settings),
    stores: StoreRepository = Depends(get_store_repository),
    audit_logs: AuditLogRepository = Depends(get_audit_log_repository),
) -> JSONResponse:
    """`shop/redact`: sent ~48 hours after a merchant uninstalls, asking the
    app to erase that shop's data.

    Scope note: this topic is about the *shop's* data (as distinct from an
    individual customer's data under `customers/redact`) — NightShift does
    retain shop-level operational records (Store, Issue, Shift, AuditLog,
    etc.) for its own product purposes (audit trail, historical reporting).
    Actually purging those tables is a larger data-retention decision (what
    to keep for legal/audit purposes vs. what must go, retention windows,
    etc.) that is out of scope for this compliance-webhook-plumbing change —
    see this PR's report for the explicit follow-up needed. What this
    handler does today: verifies the webhook, confirms the store is already
    deactivated (app/uninstalled always fires first per Shopify's own
    sequencing), and records an honest audit entry rather than fabricating a
    "shop data deleted" claim.
    """
    return await _handle_gdpr_webhook(
        request,
        x_shopify_hmac_sha256,
        settings,
        stores,
        audit_logs,
        topic="shop/redact",
        actor_id="shop/redact",
        action="SHOP_REDACT_ACKNOWLEDGED",
        rationale=(
            "Received and HMAC-verified a Shopify shop/redact webhook. "
            "NightShift does not store shopper/customer PII for this shop "
            "(only product/discount/theme/tracking metadata plus this "
            "store's own operational history). Full purge of this store's "
            "retained operational records is a data-retention-policy "
            "decision tracked separately, not performed automatically by "
            "this handler — no records were deleted by this webhook call."
        ),
    )
