"""Unit tests: LLM client factory / provider selection.

Settings fields here use `validation_alias` (e.g. `llm_provider` only
accepts the `LLM_PROVIDER` env var name, not the Python attribute name, as
a constructor kwarg) — `monkeypatch.setenv` is used rather than passing
Settings(...) kwargs directly, so these tests exercise the exact same
resolution path a real deployment does.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.infrastructure.llm.factory import build_chief_ops_llm_client, build_llm_client
from app.infrastructure.llm.gemini_client import GeminiClient


def test_factory_defaults_to_gemini(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GOOGLE_AI_API_KEY", "fake-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL_NAME", "gemini-3.6-flash")

    client = build_llm_client(Settings())

    assert isinstance(client, GeminiClient)
    assert client.model_name == "gemini-3.6-flash"


def test_factory_builds_gemini_when_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "GEMINI")
    monkeypatch.setenv("GOOGLE_AI_API_KEY", "fake-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL_NAME", "gemini-2.5-pro")

    client = build_llm_client(Settings())

    assert isinstance(client, GeminiClient)
    assert client.model_name == "gemini-2.5-pro"


def test_factory_is_case_insensitive(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_AI_API_KEY", "fake-gemini-key")

    client = build_llm_client(Settings())

    assert isinstance(client, GeminiClient)


def test_factory_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "OPENAI")

    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        build_llm_client(Settings())


# --- Chief Ops AI / Executive Briefing provider (independent of LLM_PROVIDER) --


def test_chief_ops_factory_defaults_to_gemini(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CHIEF_OPS_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GOOGLE_AI_API_KEY", "fake-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL_NAME", "gemini-2.5-pro")

    chief_ops_client = build_chief_ops_llm_client(Settings())

    assert isinstance(chief_ops_client, GeminiClient)
    assert chief_ops_client.model_name == "gemini-2.5-pro"


def test_chief_ops_factory_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHIEF_OPS_LLM_PROVIDER", "OPENAI")

    with pytest.raises(ValueError, match="Unsupported CHIEF_OPS_LLM_PROVIDER"):
        build_chief_ops_llm_client(Settings())
