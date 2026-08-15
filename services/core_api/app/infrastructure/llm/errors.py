"""Shared, provider-agnostic LLM client error.

`GeminiClient` raises this on any transport failure, refusal, truncation, or
schema-validation failure, so `ProductQualityAgent`'s fallback logic never
needs to know which provider is active.
"""

from __future__ import annotations


class LlmSchemaValidationError(Exception):
    """Raised when an LLM request fails outright, is refused, is truncated
    before completing, or its response fails schema validation. Callers
    MUST catch this and apply their approved deterministic fallback
    strategy — never proceed with malformed or incomplete AI output."""
