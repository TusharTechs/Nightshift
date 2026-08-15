"""Internal/server-to-server endpoints — Cloud Run migration.

`POST /internal/dispatch-nightly-shifts` replaces Celery Beat's role.
Celery Beat is a persistent, always-running scheduler process — Cloud Run
only runs request/event-triggered services and has no direct equivalent, so
`beat` is not deployed there at all. Cloud Scheduler (a real, serverless GCP
service — pay-per-invocation, no idle cost) calls this endpoint on the same
cadence Beat used to fire on its own, and this endpoint does exactly what
`dispatch_nightly_shifts_task` already did: enqueue `tasks.dispatch_nightly_
shifts` onto celery:cron, consumed by the Cloud Run worker service exactly
as before. No dispatch logic is duplicated here — this is a thin trigger,
not a reimplementation.

Never called by the embedded Shopify app or a Shopify webhook — authenticated
by a shared secret (`Settings.internal_dispatch_secret`), not a merchant
session, since Cloud Scheduler has no Shopify session token to present.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header

from app.api.deps import get_task_dispatcher
from app.api.errors import InternalDispatchUnauthorizedProblem
from app.application.ports import TaskDispatcher
from app.config import Settings, get_settings

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/dispatch-nightly-shifts")
async def dispatch_nightly_shifts(
    x_internal_secret: str = Header(default="", alias="X-Internal-Secret"),
    settings: Settings = Depends(get_settings),
    task_dispatcher: TaskDispatcher = Depends(get_task_dispatcher),
) -> dict:
    # Constant-time comparison — same discipline as
    # `domain/security.py::verify_webhook_hmac` for the same reason: a
    # shared-secret check must never leak timing information about how many
    # leading characters matched.
    if not settings.internal_dispatch_secret or not hmac.compare_digest(
        x_internal_secret, settings.internal_dispatch_secret
    ):
        raise InternalDispatchUnauthorizedProblem(
            "Missing, empty, or incorrect X-Internal-Secret header. Set INTERNAL_DISPATCH_SECRET "
            "to enable this endpoint, and configure Cloud Scheduler's HTTP target to send the "
            "same value in that header."
        )

    task_id = task_dispatcher.dispatch_nightly_shifts()
    return {"dispatched": True, "task_id": task_id}
