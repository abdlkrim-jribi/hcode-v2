"""Tests for `_build_model` — provider selection and config wiring.

The chat-model classes are replaced with lightweight recorders so no network
call is made; the tests assert which client is built and with what kwargs.
"""

from __future__ import annotations

import pytest

from hcode_v2.agent import factory

_ENV_VARS = (
    "HCODE_MODEL_NAME",
    "HCODE_MODEL_BASE_URL",
    "HCODE_MODEL_API_KEY",
    "HCODE_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "HCODE_MAX_TOKENS",
)


class _FakeOpenAI:
    """Stand-in for ChatOpenAI that records constructor kwargs."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeAnthropic:
    """Stand-in for ChatAnthropic that records constructor kwargs."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear model env vars and patch both chat clients with recorders."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("langchain_openai.ChatOpenAI", _FakeOpenAI)
    monkeypatch.setattr("langchain_anthropic.ChatAnthropic", _FakeAnthropic)


def test_openai_uses_hcode_model_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HCODE_MODEL_NAME", "gpt-oss-120b")
    monkeypatch.setenv("HCODE_MODEL_BASE_URL", "https://new.example/v1")
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "key-new")

    model = factory._build_model()

    assert isinstance(model, _FakeOpenAI)
    assert model.kwargs["model"] == "gpt-oss-120b"
    assert model.kwargs["base_url"] == "https://new.example/v1"
    assert model.kwargs["api_key"] == "key-new"


def test_openai_falls_back_to_legacy_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HCODE_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "key-old")

    model = factory._build_model()

    assert isinstance(model, _FakeOpenAI)
    assert model.kwargs["model"] == "gpt-4o-mini"
    assert model.kwargs["base_url"] == "https://old.example/v1"
    assert model.kwargs["api_key"] == "key-old"


def test_anthropic_selected_when_only_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-anthropic")
    monkeypatch.setenv("HCODE_MODEL_NAME", "claude-haiku-4-5")

    model = factory._build_model()

    assert isinstance(model, _FakeAnthropic)
    assert model.kwargs["model"] == "claude-haiku-4-5"
    assert model.kwargs["api_key"] == "key-anthropic"


def test_openai_wins_when_both_keys_present(monkeypatch: pytest.MonkeyPatch) -> None:
    # Anthropic is only chosen when NO OpenAI-compatible key is resolved.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-anthropic")
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "key-new")

    model = factory._build_model()

    assert isinstance(model, _FakeOpenAI)
    assert model.kwargs["api_key"] == "key-new"


def test_max_tokens_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "key-new")
    assert factory._build_model().kwargs["max_tokens"] == 2000

    monkeypatch.setenv("HCODE_MAX_TOKENS", "512")
    assert factory._build_model().kwargs["max_tokens"] == 512
