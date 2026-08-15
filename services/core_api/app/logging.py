"""Structured JSON logging (Sprint 1 Feature 6 / Section 8.1).

Emits machine-readable JSON logs with timestamp, level, trace_id, store_id,
and event, via structlog, with a redaction filter so access tokens and
secrets are never written to stdout.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_REDACTED = "***REDACTED***"

# Field names that must never reach stdout in plaintext, regardless of where
# in the event dict they appear. Matches Sprint 1's Definition of Done gate:
# "No plaintext access tokens or secrets present in code or log output."
_SENSITIVE_KEYS = {
    "access_token",
    "access_token_encrypted",
    "encrypted_token",
    "hmac",
    "shopify_app_secret",
    "client_secret",
    "kms_key",
    "password",
    "password_hash",
    "authorization",
}


def _redact_sensitive(_logger: Any, _method_name: str, event_dict: dict) -> dict:
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(log_level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_sensitive,
            structlog.processors.EventRenamer("event"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**initial_values: Any) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(**initial_values)
