"""Tests for `Config.from_env` model-config resolution."""

from __future__ import annotations

import pytest

from hcode_v2.utils.config import Config

_MODEL_ENV_VARS = (
    "HCODE_MODEL_NAME",
    "HCODE_MODEL_BASE_URL",
    "HCODE_MODEL_API_KEY",
    "HCODE_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
)


@pytest.fixture(autouse=True)
def _clear_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every test from a clean slate so host env never leaks in."""
    for var in _MODEL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_prefers_hcode_model_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both new and old vars set — the HCODE_MODEL_* set must win.
    monkeypatch.setenv("HCODE_MODEL_NAME", "gpt-oss-120b")
    monkeypatch.setenv("HCODE_MODEL_BASE_URL", "https://new.example/v1")
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "key-new")
    monkeypatch.setenv("HCODE_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "key-old")

    config = Config.from_env()

    assert config.model == "gpt-oss-120b"
    assert config.base_url == "https://new.example/v1"
    assert config.api_key == "key-new"


def test_falls_back_to_openai_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the legacy vars are set — resolution must fall back to them.
    monkeypatch.setenv("HCODE_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "key-old")

    config = Config.from_env()

    assert config.model == "gpt-4o-mini"
    assert config.base_url == "https://old.example/v1"
    assert config.api_key == "key-old"


def test_defaults_when_nothing_set() -> None:
    config = Config.from_env()

    assert config.model == "gpt-4o-mini"
    assert config.api_key is None
    assert config.base_url is None


def test_each_field_resolves_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    # New api_key, but base_url only via the legacy var, model only via HCODE_MODEL.
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "key-new")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("HCODE_MODEL", "claude-haiku-4-5")

    config = Config.from_env()

    assert config.api_key == "key-new"
    assert config.base_url == "https://old.example/v1"
    assert config.model == "claude-haiku-4-5"


def test_model_name_takes_precedence_over_hcode_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HCODE_MODEL_NAME", "preferred")
    monkeypatch.setenv("HCODE_MODEL", "legacy")

    assert Config.from_env().model == "preferred"


def test_config_is_frozen() -> None:
    config = Config.from_env()
    with pytest.raises(Exception):  # noqa: B017,PT011 — frozen dataclass raises FrozenInstanceError
        config.model = "mutated"  # type: ignore[misc]
