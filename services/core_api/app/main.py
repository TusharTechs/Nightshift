"""FastAPI application factory (Sprint 1 Feature 1 / Feature 6)."""

from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, Request
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.api.errors import register_error_handlers
from app.api.v1 import approvals, ask, auth, billing, demo, internal, shifts, stores, tasks, webhooks, work_log
from app.config import get_settings
from app.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(component="http")


def create_app() -> FastAPI:
    app = FastAPI(
        title="NightShift AI Core API",
        version="0.1.0",
        description="Sprint 1: Platform Foundation, OAuth Ingress, & Baseline Store Ingestion",
    )

    register_error_handlers(app)

    app.include_router(auth.router)
    app.include_router(stores.router)
    app.include_router(shifts.router)
    app.include_router(approvals.router)
    app.include_router(tasks.router)
    app.include_router(work_log.router)
    app.include_router(demo.router)
    app.include_router(ask.router)
    app.include_router(billing.router)
    app.include_router(webhooks.router)
    app.include_router(internal.router)

    @app.middleware("http")
    async def trace_context_middleware(request: Request, call_next):
        trace_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
        clear_contextvars()
        bind_contextvars(trace_id=trace_id)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        response.headers["X-Trace-Id"] = trace_id
        logger.info(
            "http_request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response

    @app.get("/health")
    async def health_check() -> dict:
        return {"status": "ok"}

    FastAPIInstrumentor.instrument_app(app)

    return app


app = create_app()
