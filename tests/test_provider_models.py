"""Unit tests for live model discovery (provider.models).

Covers the pure filter (free + tool-capable + context gate), the fallback list,
and the async fetch with a stubbed httpx transport — no real network.
"""

from __future__ import annotations

import pytest

from hcode_v2.provider.models import (
    fallback_models,
    fetch_models,
    filter_usable_models,
)


# ── filter_usable_models ──────────────────────────────────────────────────────

def _model(mid, *, prompt="0", tools=True, ctx=64000, name=None):
    m = {
        "id": mid,
        "name": name or mid,
        "pricing": {"prompt": prompt},
        "context_length": ctx,
    }
    if tools:
        m["supported_parameters"] = ["tools", "temperature"]
    else:
        m["supported_parameters"] = ["temperature"]
    return m


def test_keeps_free_toolcapable_largecontext():
    catalog = [_model("free/good", prompt="0", tools=True, ctx=64000)]
    out = filter_usable_models(catalog)
    assert out == [{"id": "free/good", "name": "free/good", "context_length": 64000}]


def test_excludes_paid_models():
    catalog = [_model("paid/model", prompt="0.0000005", tools=True, ctx=64000)]
    assert filter_usable_models(catalog) == []


def test_excludes_tool_less_models():
    # CRITICAL: a model without tool support breaks the agent's plan/execute loop.
    catalog = [_model("free/notools", prompt="0", tools=False, ctx=64000)]
    assert filter_usable_models(catalog) == []


def test_excludes_small_context():
    catalog = [_model("free/tiny", prompt="0", tools=True, ctx=8000)]
    assert filter_usable_models(catalog) == []


def test_free_price_as_number_zero():
    # Some providers report pricing as a number, not a string.
    catalog = [{"id": "free/num", "pricing": {"prompt": 0}, "supported_parameters": ["tools"], "context_length": 40000}]
    out = filter_usable_models(catalog)
    assert [m["id"] for m in out] == ["free/num"]


def test_missing_pricing_is_not_free():
    catalog = [{"id": "x", "supported_parameters": ["tools"], "context_length": 40000}]
    assert filter_usable_models(catalog) == []


def test_garbage_rows_skipped():
    catalog = ["not a dict", {"no": "id"}, {"id": ""}, _model("free/ok")]
    out = filter_usable_models(catalog)
    assert [m["id"] for m in out] == ["free/ok"]


def test_sorted_by_context_desc_then_id():
    catalog = [
        _model("b/model", ctx=40000),
        _model("a/model", ctx=128000),
        _model("c/model", ctx=128000),
    ]
    out = filter_usable_models(catalog)
    assert [m["id"] for m in out] == ["a/model", "c/model", "b/model"]


def test_result_never_contains_secret_keys():
    # The filtered rows must carry only id/name/context_length — never anything
    # resembling a key, even if the catalog row had extra fields.
    catalog = [{
        "id": "free/ok", "name": "Free OK", "pricing": {"prompt": "0"},
        "supported_parameters": ["tools"], "context_length": 50000,
        "api_key": "sk-LEAK", "secret": "should-not-survive",
    }]
    out = filter_usable_models(catalog)
    assert out == [{"id": "free/ok", "name": "Free OK", "context_length": 50000}]
    assert "api_key" not in out[0] and "secret" not in out[0]


# ── fallback_models ───────────────────────────────────────────────────────────

def test_fallback_from_hcode_models(monkeypatch):
    monkeypatch.setenv("HCODE_MODELS", "a/one, b/two , a/one")
    out = fallback_models()
    assert [m["id"] for m in out] == ["a/one", "b/two"]  # de-duped, trimmed
    assert all(m["context_length"] == 0 for m in out)


def test_fallback_from_configured_model(monkeypatch):
    monkeypatch.delenv("HCODE_MODELS", raising=False)
    monkeypatch.setenv("HCODE_MODEL_NAME", "configured/model")
    out = fallback_models()
    assert [m["id"] for m in out] == ["configured/model"]


def test_fallback_never_empty(monkeypatch):
    monkeypatch.delenv("HCODE_MODELS", raising=False)
    monkeypatch.delenv("HCODE_MODEL_NAME", raising=False)
    monkeypatch.delenv("HCODE_MODEL", raising=False)
    out = fallback_models()
    assert len(out) >= 1  # the built-in default


# ── fetch_models (stubbed transport) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_models_sends_bearer_and_parses_data(monkeypatch):
    captured = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"data": [{"id": "m1"}, {"id": "m2"}, "garbage"]}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    out = await fetch_models("https://openrouter.ai/api/v1", "sk-secret-key")
    assert captured["url"] == "https://openrouter.ai/api/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer sk-secret-key"
    assert [m["id"] for m in out] == ["m1", "m2"]  # dict rows only


@pytest.mark.asyncio
async def test_fetch_models_no_key_omits_auth_header(monkeypatch):
    captured = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return []

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            captured["headers"] = headers
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    await fetch_models("https://x/v1", None)
    assert "Authorization" not in captured["headers"]
