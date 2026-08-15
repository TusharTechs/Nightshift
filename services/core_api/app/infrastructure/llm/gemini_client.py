"""Gemini structured-output client & schema enforcer.

Wraps Google's current, actively-maintained `google-genai` SDK to guarantee
strict Pydantic-parsed JSON output, matching the AI Architecture's
prompt-injection hardening axiom: "All model outputs must pass structured
JSON schema validation; unparseable outputs are immediately rejected."
Temperature is fixed at 0.1 for deterministic JSON output and stable impact
estimations (AI Specification, verbatim).

Model name note (see CONFLICTS.md item 12 / DECISIONS.md ADR-016): every
source document hardcodes `gemini-1.5-pro`, which is retired as of 2026
(Google's own model lifecycle docs list both dated 1.5 Pro versions as
retired in 2025) — a literal implementation would fail at request time.
Approved substitution: default to `gemini-2.5-pro`, kept fully configurable
via `Settings.gemini_model_name` rather than hardcoded, so swapping models
again never requires a code change.

SDK migration note (productionization phase, 2026-08): this module originally
wrapped `google-generativeai` (Google's now-fully-EOL legacy SDK — as of this
migration it logs "All support ... has ended. It will no longer be receiving
updates or bug fixes"). Since Gemini stopped being a dormant/secondary
provider and became the load-bearing, always-on provider for Chief Ops AI's
Executive Briefing (see `infrastructure/llm/factory.py::build_chief_ops_llm_client`),
shipping the hackathon's headline Gemini integration on a dead SDK was no
longer an acceptable risk. Migrated to `google-genai` (the actively
maintained, unified Google GenAI SDK) — same public interface
(`generate_structured`), same enforced-JSON-schema behavior, same error
type, so no caller anywhere else in the codebase needed to change.
"""

from __future__ import annotations

import time
from typing import TypeVar

import structlog
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError

from app.infrastructure.llm.errors import LlmSchemaValidationError

logger = structlog.get_logger(component="gemini_client")

SchemaT = TypeVar("SchemaT", bound=BaseModel)

GENERATION_TEMPERATURE = 0.1

# Backward-compatible alias — this module's original name for the shared
# error, kept so any existing import of `GeminiSchemaValidationError`
# continues to work unchanged.
GeminiSchemaValidationError = LlmSchemaValidationError


class GeminiClient:
    """Thin wrapper around `google.genai.Client`'s async `models.generate_content`,
    enforcing `response_mime_type="application/json"` + `response_schema`
    structured output (AI Specification: "Enforce strict JSON output matching
    Pydantic response schema").

    Two access paths, both via this same `google-genai` SDK (Google/XPRIZE
    hackathon requirement #2 either way — see `Settings.gcp_project_id`'s own
    comment for why Vertex is preferred once a GCP project is configured):
    Gemini Developer API (api_key, AI Studio's own separate Prepay credit
    balance) or Vertex AI (project/location, billed through the project's
    normal Google Cloud Billing account, ADC-authenticated). `project` being
    non-empty selects Vertex mode; empty keeps the original api_key path.
    """

    def __init__(
        self, *, api_key: str, model_name: str, project: str = "", location: str = "us-central1"
    ) -> None:
        if project:
            self._client = genai.Client(vertexai=True, project=project, location=location)
        else:
            self._client = genai.Client(api_key=api_key)
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    async def generate_structured(
        self, *, prompt: str, response_schema: type[SchemaT]
    ) -> SchemaT:
        """Requests structured JSON conforming to `response_schema`.

        Raises `GeminiSchemaValidationError` on any transport, JSON, or
        Pydantic validation failure so the caller can apply its approved
        fallback strategy rather than silently proceeding with unusable
        output.
        """
        start = time.perf_counter()
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model_name,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    temperature=GENERATION_TEMPERATURE,
                ),
            )
        except Exception as exc:  # noqa: BLE001 — any SDK/transport failure triggers fallback
            duration = time.perf_counter() - start
            logger.warning(
                "gemini_request_failed",
                model=self._model_name,
                duration_seconds=round(duration, 3),
                status="error",
                error=str(exc),
            )
            raise GeminiSchemaValidationError(f"Gemini request failed: {exc}") from exc

        duration = time.perf_counter() - start
        response_text = response.text
        if response_text is None:
            logger.warning(
                "gemini_response_empty",
                model=self._model_name,
                duration_seconds=round(duration, 3),
                status="error",
            )
            raise GeminiSchemaValidationError("Gemini response had no text content")

        try:
            parsed = response_schema.model_validate_json(response_text)
        except ValidationError as exc:
            logger.warning(
                "gemini_schema_validation_failed",
                model=self._model_name,
                duration_seconds=round(duration, 3),
                status="error",
                error=str(exc),
            )
            raise GeminiSchemaValidationError(
                f"Gemini response failed schema validation: {exc}"
            ) from exc

        usage = getattr(response, "usage_metadata", None)
        logger.info(
            "gemini_generation_completed",
            model=self._model_name,
            duration_seconds=round(duration, 3),
            status="success",
            prompt_token_count=getattr(usage, "prompt_token_count", None),
            candidates_token_count=getattr(usage, "candidates_token_count", None),
        )
        return parsed
