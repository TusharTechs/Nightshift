"""Unit tests: GeminiClient structured-output wrapper.

Mocks the `google-genai` SDK's async surface at
`genai.Client.aio.models.generate_content` — no live API key or network
access required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from app.infrastructure.llm.gemini_client import GeminiClient, GeminiSchemaValidationError


class _Schema(BaseModel):
    value: str


class _FakeResponse:
    def __init__(self, text: str | None, usage=None) -> None:
        self.text = text
        self.usage_metadata = usage


def _make_client(generate_content: AsyncMock) -> GeminiClient:
    fake_client = MagicMock()
    fake_client.aio.models.generate_content = generate_content
    with patch("app.infrastructure.llm.gemini_client.genai.Client", return_value=fake_client):
        return GeminiClient(api_key="fake-key", model_name="gemini-2.5-pro")


@pytest.mark.asyncio
async def test_gemini_client_generate_structured_success():
    generate_content = AsyncMock(return_value=_FakeResponse('{"value": "ok"}'))
    client = _make_client(generate_content)

    result = await client.generate_structured(prompt="hi", response_schema=_Schema)

    assert result.value == "ok"
    generate_content.assert_awaited_once()
    _, kwargs = generate_content.call_args
    assert kwargs["model"] == "gemini-2.5-pro"
    assert kwargs["config"].response_mime_type == "application/json"
    assert kwargs["config"].response_schema is _Schema


@pytest.mark.asyncio
async def test_gemini_client_raises_on_transport_failure():
    generate_content = AsyncMock(side_effect=RuntimeError("network down"))
    client = _make_client(generate_content)

    with pytest.raises(GeminiSchemaValidationError):
        await client.generate_structured(prompt="hi", response_schema=_Schema)


@pytest.mark.asyncio
async def test_gemini_client_raises_on_schema_validation_failure():
    # Missing the required `value` field — must be rejected, never silently
    # passed through (AI Architecture: "unparseable outputs are immediately
    # rejected").
    generate_content = AsyncMock(return_value=_FakeResponse('{"wrong_field": 1}'))
    client = _make_client(generate_content)

    with pytest.raises(GeminiSchemaValidationError):
        await client.generate_structured(prompt="hi", response_schema=_Schema)


@pytest.mark.asyncio
async def test_gemini_client_raises_on_malformed_json():
    generate_content = AsyncMock(return_value=_FakeResponse("not json at all"))
    client = _make_client(generate_content)

    with pytest.raises(GeminiSchemaValidationError):
        await client.generate_structured(prompt="hi", response_schema=_Schema)


@pytest.mark.asyncio
async def test_gemini_client_raises_on_empty_response_text():
    generate_content = AsyncMock(return_value=_FakeResponse(None))
    client = _make_client(generate_content)

    with pytest.raises(GeminiSchemaValidationError):
        await client.generate_structured(prompt="hi", response_schema=_Schema)


def test_gemini_client_uses_api_key_mode_when_no_project_configured():
    """Backward-compatible default — no GCP project set, so the original
    Gemini Developer API (AI Studio) path is used, unchanged."""
    with patch("app.infrastructure.llm.gemini_client.genai.Client") as mock_client_cls:
        GeminiClient(api_key="fake-key", model_name="gemini-3.6-flash")

    mock_client_cls.assert_called_once_with(api_key="fake-key")


def test_gemini_client_uses_vertex_ai_mode_when_project_configured():
    """Hackathon requirement: Vertex AI billing flows through the project's
    normal Google Cloud Billing account, not the Gemini Developer API's own
    separate Prepay balance — see `Settings.gcp_project_id`'s own comment.
    A non-empty `project` must select Vertex mode, never the api_key path."""
    with patch("app.infrastructure.llm.gemini_client.genai.Client") as mock_client_cls:
        GeminiClient(
            api_key="fake-key", model_name="gemini-3.6-flash", project="my-gcp-project", location="us-central1"
        )

    mock_client_cls.assert_called_once_with(vertexai=True, project="my-gcp-project", location="us-central1")
