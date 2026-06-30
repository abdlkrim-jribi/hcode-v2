"""Wiring tests for 429 fallback: factory _build_model + daemon _resolve_fallbacks.

Verifies the OPT-IN boundary (zero regression when off) and the daemon's
fallback-order resolution. No network: model construction is checked by type,
not by calling the provider.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from hcode_v2.agent.factory import _build_model
from hcode_v2.daemon.server import JsonRpcDaemon
from hcode_v2.provider.resilient import ResilientChatModel


@pytest.fixture
def restore_stdout():
    orig = sys.stdout
    yield
    sys.stdout = orig


# ── _build_model opt-in boundary (verify #4 zero regression) ───────────────────

def test_build_model_off_returns_single_client(monkeypatch):
    """No fallback_models → NOT a ResilientChatModel (exact prior behaviour)."""
    monkeypatch.setenv("HCODE_MODEL_NAME", "primary/model")
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "sk-test-123456")
    monkeypatch.setenv("HCODE_MODEL_BASE_URL", "https://openrouter.ai/api/v1")

    model = _build_model()  # default: fallback off
    assert not isinstance(model, ResilientChatModel)


def test_build_model_off_with_empty_list_returns_single_client(monkeypatch):
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "sk-test-123456")
    monkeypatch.setenv("HCODE_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    model = _build_model(fallback_models=[])
    assert not isinstance(model, ResilientChatModel)


def test_build_model_on_returns_resilient_with_primary_first(monkeypatch):
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "sk-test-123456")
    monkeypatch.setenv("HCODE_MODEL_BASE_URL", "https://openrouter.ai/api/v1")

    model = _build_model(
        model_override="primary/model",
        fallback_models=["fb/one", "fb/two"],
    )
    assert isinstance(model, ResilientChatModel)
    # Primary is index 0; fallbacks follow, in order.
    assert model.model_names == ["primary/model", "fb/one", "fb/two"]


def test_build_model_dedups_primary_from_fallbacks(monkeypatch):
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "sk-test-123456")
    monkeypatch.setenv("HCODE_MODEL_BASE_URL", "https://openrouter.ai/api/v1")

    model = _build_model(
        model_override="primary/model",
        fallback_models=["primary/model", "fb/one"],  # primary repeated
    )
    assert model.model_names == ["primary/model", "fb/one"]


# ── daemon _resolve_fallbacks ──────────────────────────────────────────────────

def test_resolve_fallbacks_off_by_default(monkeypatch, restore_stdout):
    monkeypatch.delenv("HCODE_FALLBACK_MODELS", raising=False)
    monkeypatch.delenv("HCODE_MODEL_FALLBACK", raising=False)
    daemon = JsonRpcDaemon(mock=False)
    out = asyncio.run(daemon._resolve_fallbacks("primary/model"))
    assert out == []   # feature off → no fallbacks


def test_resolve_fallbacks_explicit_env_order(monkeypatch, restore_stdout):
    monkeypatch.setenv("HCODE_FALLBACK_MODELS", "a/one, b/two , primary/model, a/one")
    daemon = JsonRpcDaemon(mock=False)
    out = asyncio.run(daemon._resolve_fallbacks("primary/model"))
    # primary dropped, dups removed, order + trim preserved
    assert out == ["a/one", "b/two"]


def test_resolve_fallbacks_auto_from_list_models(monkeypatch, restore_stdout):
    monkeypatch.delenv("HCODE_FALLBACK_MODELS", raising=False)
    monkeypatch.setenv("HCODE_MODEL_FALLBACK", "auto")
    daemon = JsonRpcDaemon(mock=False)

    async def _fake_models():
        return [
            {"id": "primary/model"}, {"id": "free/a"},
            {"id": "free/b"}, {"id": "free/c"}, {"id": "free/d"},
        ]
    monkeypatch.setattr(daemon, "_get_models", _fake_models)

    out = asyncio.run(daemon._resolve_fallbacks("primary/model"))
    # primary excluded; capped to _MAX_AUTO_FALLBACKS (3)
    assert out == ["free/a", "free/b", "free/c"]


def test_resolve_fallbacks_auto_survives_discovery_failure(monkeypatch, restore_stdout):
    monkeypatch.delenv("HCODE_FALLBACK_MODELS", raising=False)
    monkeypatch.setenv("HCODE_MODEL_FALLBACK", "auto")
    daemon = JsonRpcDaemon(mock=False)

    async def _boom():
        raise ConnectionError("offline")
    monkeypatch.setattr(daemon, "_get_models", _boom)

    out = asyncio.run(daemon._resolve_fallbacks("primary/model"))
    assert out == []   # discovery failure → no fallbacks, no crash
