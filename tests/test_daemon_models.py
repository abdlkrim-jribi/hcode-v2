"""In-process tests for the daemon's list_models handler.

Covers the live-fetch path (filtered to free + tool-capable), the graceful
fallback when the fetch fails, the few-minute cache, and — critically — that the
API key never appears in the response. Driven in-process so the provider fetch
can be stubbed without a network or a real key.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from hcode_v2.daemon.server import JsonRpcDaemon


@pytest.fixture
def restore_stdout():
    orig = sys.stdout
    yield
    sys.stdout = orig


def _capture_daemon(monkeypatch, restore_stdout):
    daemon = JsonRpcDaemon(mock=False)
    responses: list = []
    monkeypatch.setattr(
        daemon, "send_response",
        lambda req_id, result=None, error=None: responses.append((req_id, result, error)),
    )
    return daemon, responses


# A realistic OpenRouter-shaped catalog: one free+tool-capable, one paid, one
# free-but-tool-less, one free+tool-capable but small context.
_FAKE_CATALOG = [
    {"id": "free/good", "name": "Free Good", "pricing": {"prompt": "0"},
     "supported_parameters": ["tools"], "context_length": 128000},
    {"id": "paid/model", "name": "Paid", "pricing": {"prompt": "0.000002"},
     "supported_parameters": ["tools"], "context_length": 128000},
    {"id": "free/notools", "name": "No Tools", "pricing": {"prompt": "0"},
     "supported_parameters": ["temperature"], "context_length": 128000},
    {"id": "free/tiny", "name": "Tiny", "pricing": {"prompt": "0"},
     "supported_parameters": ["tools"], "context_length": 8000},
]


def test_list_models_returns_filtered_live_list(monkeypatch, restore_stdout):
    monkeypatch.setenv("HCODE_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "sk-secret-LEAKTEST-123456")

    async def _fake_fetch(base_url, api_key, timeout=10.0):
        return _FAKE_CATALOG

    monkeypatch.setattr("hcode_v2.provider.models.fetch_models", _fake_fetch)

    daemon, responses = _capture_daemon(monkeypatch, restore_stdout)
    asyncio.run(daemon._handle_list_models(1))

    _id, result, error = responses[0]
    assert error is None
    ids = [m["id"] for m in result["models"]]
    # Only the free + tool-capable + large-context model survives.
    assert ids == ["free/good"]
    assert "paid/model" not in ids and "free/notools" not in ids and "free/tiny" not in ids


def test_list_models_never_leaks_api_key(monkeypatch, restore_stdout):
    secret = "sk-secret-LEAKTEST-abcdef123456"
    monkeypatch.setenv("HCODE_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("HCODE_MODEL_API_KEY", secret)

    async def _fake_fetch(base_url, api_key, timeout=10.0):
        # The daemon must pass the key here (header use) — but it must not appear
        # in the RESULT it sends back.
        assert api_key == secret
        return _FAKE_CATALOG

    monkeypatch.setattr("hcode_v2.provider.models.fetch_models", _fake_fetch)

    daemon, responses = _capture_daemon(monkeypatch, restore_stdout)
    asyncio.run(daemon._handle_list_models(1))

    import json
    serialized = json.dumps(responses[0][1])
    assert secret not in serialized


def test_list_models_falls_back_when_fetch_fails(monkeypatch, restore_stdout):
    monkeypatch.setenv("HCODE_MODEL_BASE_URL", "https://bad.endpoint/v1")
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "sk-whatever-123456")
    monkeypatch.setenv("HCODE_MODELS", "fallback/one,fallback/two")

    async def _boom(base_url, api_key, timeout=10.0):
        raise ConnectionError("offline")

    monkeypatch.setattr("hcode_v2.provider.models.fetch_models", _boom)

    daemon, responses = _capture_daemon(monkeypatch, restore_stdout)
    asyncio.run(daemon._handle_list_models(1))

    _id, result, error = responses[0]
    assert error is None
    ids = [m["id"] for m in result["models"]]
    assert ids == ["fallback/one", "fallback/two"]  # never empty, no crash


def test_list_models_falls_back_when_no_base_url(monkeypatch, restore_stdout):
    monkeypatch.delenv("HCODE_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("HCODE_MODEL_NAME", "configured/only")

    called = {"n": 0}

    async def _should_not_run(base_url, api_key, timeout=10.0):
        called["n"] += 1
        return _FAKE_CATALOG

    monkeypatch.setattr("hcode_v2.provider.models.fetch_models", _should_not_run)

    daemon, responses = _capture_daemon(monkeypatch, restore_stdout)
    asyncio.run(daemon._handle_list_models(1))

    _id, result, error = responses[0]
    assert [m["id"] for m in result["models"]] == ["configured/only"]
    assert called["n"] == 0  # no base_url → no fetch attempt


def test_list_models_caches_within_ttl(monkeypatch, restore_stdout):
    monkeypatch.setenv("HCODE_MODEL_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "sk-key-123456")

    calls = {"n": 0}

    async def _counting_fetch(base_url, api_key, timeout=10.0):
        calls["n"] += 1
        return _FAKE_CATALOG

    monkeypatch.setattr("hcode_v2.provider.models.fetch_models", _counting_fetch)

    daemon, responses = _capture_daemon(monkeypatch, restore_stdout)

    async def _drive() -> None:
        await daemon._handle_list_models(1)
        await daemon._handle_list_models(2)
        await daemon._handle_list_models(3)

    asyncio.run(_drive())
    assert calls["n"] == 1  # fetched once, served from cache thereafter


def test_list_models_via_router(monkeypatch, restore_stdout):
    monkeypatch.delenv("HCODE_MODEL_BASE_URL", raising=False)
    monkeypatch.setenv("HCODE_MODEL_NAME", "router/model")
    daemon, responses = _capture_daemon(monkeypatch, restore_stdout)

    asyncio.run(daemon.handle_request({"jsonrpc": "2.0", "id": 7, "method": "list_models", "params": {}}))

    _id, result, error = responses[0]
    assert _id == 7 and error is None
    assert [m["id"] for m in result["models"]] == ["router/model"]
