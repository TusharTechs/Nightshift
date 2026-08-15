"""RFC 7807 Problem Details error handling (Sprint 1 Technical Objective,
Section 8.2).

Every failure returns `application/problem+json` — no plain strings, no
generic 500 pages.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

PROBLEM_BASE_URI = "https://api.nightshift.ai/v1/errors"


class NightShiftProblem(Exception):
    """Base exception carrying enough detail to render an RFC 7807 body."""

    code: str = "INTERNAL_ERROR"
    status: int = 500
    title: str = "Internal Server Error"

    def __init__(self, detail: str, *, instance: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.instance = instance


class InvalidHmacSignatureProblem(NightShiftProblem):
    code = "INVALID_HMAC_SIGNATURE"
    status = 400
    title = "Invalid HMAC Signature"


class TimestampExpiredProblem(NightShiftProblem):
    code = "TIMESTAMP_EXPIRED"
    status = 400
    title = "Timestamp Expired"


class InvalidShopDomainProblem(NightShiftProblem):
    code = "INVALID_SHOP_DOMAIN"
    status = 400
    title = "Invalid Shop Domain"


class ShopifyApiProblem(NightShiftProblem):
    """Upstream Shopify failure during token exchange or GraphQL calls.

    Sprint 1's own Endpoint 2 spec and Story 1 Failure Cases both call for
    HTTP 502 on Shopify API timeout during token exchange (Section 7.3 of the
    engineering brief resolves this in favor of 502 over the API Contract
    doc's stale 500).
    """

    code = "SHOPIFY_API_ERROR"
    status = 502
    title = "Shopify API Error"


class StoreNotFoundProblem(NightShiftProblem):
    code = "STORE_NOT_FOUND"
    status = 404
    title = "Store Not Found"


class UnauthorizedProblem(NightShiftProblem):
    code = "UNAUTHORIZED"
    status = 401
    title = "Unauthorized"


class NoCompletedShiftProblem(NightShiftProblem):
    """Sprint 2 `GET /api/v1/shifts/latest`, verbatim status codes: "200 OK,
    401 Unauthorized, 404 Not Found (No shifts run yet)"."""

    code = "NO_COMPLETED_SHIFT"
    status = 404
    title = "No Completed Shift"


class ShiftNotFoundProblem(NightShiftProblem):
    """Sprint 4 Step 5: `GET /api/v1/shifts/{shift_id}/replay` — same
    tenant-ownership-check pattern as `TaskNotFoundProblem`."""

    code = "SHIFT_NOT_FOUND"
    status = 404
    title = "Shift Not Found"


# --- Sprint 3: AI Trust & Execution -----------------------------------------


class ApprovalNotFoundProblem(NightShiftProblem):
    code = "APPROVAL_NOT_FOUND"
    status = 404
    title = "Approval Not Found"


class ApprovalAlreadyDecidedProblem(NightShiftProblem):
    code = "APPROVAL_ALREADY_DECIDED"
    status = 409
    title = "Approval Already Decided"


class ApprovalExpiredProblem(NightShiftProblem):
    code = "TASK_APPROVAL_EXPIRED"
    status = 409
    title = "Approval Request Expired"


class InvalidApprovalActionProblem(NightShiftProblem):
    code = "INVALID_APPROVAL_ACTION"
    status = 400
    title = "Invalid Approval Action"


class TaskNotFoundProblem(NightShiftProblem):
    code = "TASK_NOT_FOUND"
    status = 404
    title = "Task Not Found"


class TaskNotRollbackableProblem(NightShiftProblem):
    code = "TASK_NOT_ROLLBACKABLE"
    status = 400
    title = "Task Not Rollbackable"


class MissingIdempotencyKeyProblem(NightShiftProblem):
    code = "MISSING_IDEMPOTENCY_KEY"
    status = 400
    title = "Missing Idempotency Key"


# --- Sprint 4 Step 1: Demo Incident Generator --------------------------------


class UnknownDemoScenarioProblem(NightShiftProblem):
    code = "UNKNOWN_DEMO_SCENARIO"
    status = 404
    title = "Unknown Demo Scenario"


class DemoModeDisabledProblem(NightShiftProblem):
    code = "DEMO_MODE_DISABLED"
    status = 404
    title = "Demo Mode Disabled"


class InternalDispatchUnauthorizedProblem(NightShiftProblem):
    """Cloud Run migration: `POST /internal/dispatch-nightly-shifts` (Cloud
    Scheduler's replacement for the Celery Beat process, which has no
    long-running-process analog on Cloud Run) is protected by a shared
    secret header, not a merchant session — this is a server-to-server
    trigger, never called by the embedded app or a Shopify webhook. Missing
    or wrong secret is treated identically (never distinguishes the two, so
    a caller can't tell whether the secret is merely wrong or the endpoint
    is unconfigured)."""

    code = "INTERNAL_DISPATCH_UNAUTHORIZED"
    status = 401
    title = "Unauthorized Internal Dispatch Request"


class DemoScenarioNoEligibleProductProblem(NightShiftProblem):
    """Sprint 5 Phase 4: Catalog SEO Collapse needs at least one active
    product to corrupt — an empty/newly-installed store genuinely has
    nothing for this scenario to act on. A real, honest failure rather than
    a silent no-op."""

    code = "DEMO_SCENARIO_NO_ELIGIBLE_PRODUCT"
    status = 422
    title = "No Eligible Product For This Demo Scenario"


# --- Billing: NightShift Free / Pro / Business monetization -----------------


class InvalidPlanProblem(NightShiftProblem):
    """`POST /api/v1/billing/subscribe` with a `plan` that isn't a real,
    subscribable tier (FREE is the default — never something Shopify's
    Billing API is invoked for)."""

    code = "INVALID_PLAN"
    status = 400
    title = "Invalid Plan"


class SubscriptionNotFoundProblem(NightShiftProblem):
    """Covers three cases with one identical response, deliberately never
    distinguishing them: no subscription on file for this store at all, a
    `charge_id` that doesn't exist, or (tenant isolation) a `charge_id` that
    exists but belongs to a DIFFERENT store's subscription row."""

    code = "SUBSCRIPTION_NOT_FOUND"
    status = 404
    title = "Subscription Not Found"


class BillingDisabledProblem(NightShiftProblem):
    """Ops-level kill switch for `POST /api/v1/billing/subscribe` — mirrors
    `DemoModeDisabledProblem`'s own pattern. Defaults to enabled
    (`Settings.billing_enabled = True`); this exists only so an operator can
    turn the paid-upgrade path off without a deploy."""

    code = "BILLING_DISABLED"
    status = 404
    title = "Billing Disabled"


def _problem_body(exc: NightShiftProblem, instance: str) -> dict:
    return {
        "type": f"{PROBLEM_BASE_URI}/{exc.code}",
        "title": exc.title,
        "status": exc.status,
        "detail": exc.detail,
        "instance": exc.instance or instance,
        "code": exc.code,
        "invalid_params": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NightShiftProblem)
    async def handle_nightshift_problem(request: Request, exc: NightShiftProblem):
        return JSONResponse(
            status_code=exc.status,
            content=_problem_body(exc, str(request.url.path)),
            media_type="application/problem+json",
        )

    @app.exception_handler(Exception)
    async def handle_unhandled_exception(request: Request, exc: Exception):
        fallback = NightShiftProblem("An unexpected error occurred.")
        return JSONResponse(
            status_code=500,
            content=_problem_body(fallback, str(request.url.path)),
            media_type="application/problem+json",
        )
