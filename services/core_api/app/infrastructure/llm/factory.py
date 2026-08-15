"""LLM client factory.

`build_llm_client` selects the active per-specialist-agent provider from
`Settings.llm_provider` so `ProductQualityAgent` (and every other specialist
agent) never needs to know which backend is active.

`build_chief_ops_llm_client` is a second, independent factory for exactly one
call site — Chief Ops AI's Executive Briefing synthesis
(`workers/tasks/shift_report.py`). Both clients implement the same
`StructuredLlmClient` protocol, so this split is invisible to every caller
except the one line in `shift_report.py` that picks which factory to call.

Gemini (via the Google GenAI SDK, `google-genai`) is the sole supported
provider — every LLM call in this codebase, for every specialist agent and
for Chief Ops AI, goes through Gemini, optionally routed through Vertex AI
when `Settings.gcp_project_id` is set (see `gemini_client.py`'s own
docstring for why Vertex AI is preferred once a GCP project is configured).
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.config import Settings
from app.infrastructure.llm.gemini_client import GeminiClient

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class StructuredLlmClient(Protocol):
    """Structural contract every LLM client implements: a `model_name` for
    logging, and a `generate_structured` coroutine that returns a validated
    Pydantic model or raises `LlmSchemaValidationError`."""

    @property
    def model_name(self) -> str: ...

    async def generate_structured(self, *, prompt: str, response_schema: type[SchemaT]) -> SchemaT: ...


def _build_client_for_provider(provider_value: str, settings: Settings, *, source_field: str) -> StructuredLlmClient:
    provider = provider_value.strip().upper()

    if provider == "GEMINI":
        return GeminiClient(
            api_key=settings.google_ai_api_key,
            model_name=settings.gemini_model_name,
            project=settings.gcp_project_id,
            location=settings.gcp_location,
        )

    raise ValueError(f"Unsupported {source_field}: {provider_value!r} (expected GEMINI)")


def build_llm_client(settings: Settings) -> StructuredLlmClient:
    """Provider for the per-specialist detection agents (Product Quality,
    Checkout Specialist, Tracking Specialist, Theme Guardian) — selected by
    `Settings.llm_provider`."""
    return _build_client_for_provider(settings.llm_provider, settings, source_field="LLM_PROVIDER")


def build_chief_ops_llm_client(settings: Settings) -> StructuredLlmClient:
    """Provider for Chief Ops AI's shift-level Executive Briefing synthesis
    ONLY — a single, separate call site from `build_llm_client` above.
    Selected independently by `Settings.chief_ops_llm_provider` so the two
    call sites can be pointed at different models/regions if ever needed,
    even though both resolve to Gemini today. See
    `workers/tasks/shift_report.py` for the one place this is called."""
    return _build_client_for_provider(
        settings.chief_ops_llm_provider, settings, source_field="CHIEF_OPS_LLM_PROVIDER"
    )
