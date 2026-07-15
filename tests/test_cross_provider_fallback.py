"""Tests for cross-provider fallback (M6) — timeout-triggered failover + the
provider-qualified chain.

The critical case from the M5 bake-off: a provider can fail by SILENT STALL
(connection open, zero bytes, NO 429). A 429-only design never fires there —
these tests pin the client-side first-token timeout that does.
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from hcode_v2.provider.resilient import (
    ResilientChatModel,
    default_call_timeout,
    is_transient_error,
)


class FakeRateLimit(Exception):
    def __init__(self, msg="429 temporarily rate-limited upstream", status_code=429):
        super().__init__(msg)
        self.status_code = status_code


class StallModel:
    """A provider that hangs forever producing no bytes — the Cerebras failure
    mode. Records how many times a call was ATTEMPTED (each attempt hangs)."""

    def __init__(self, name="stalled"):
        self.name = name
        self.calls = 0

    async def ainvoke(self, messages, **kwargs):
        self.calls += 1
        await asyncio.Event().wait()  # never returns

    async def astream(self, messages, **kwargs):
        self.calls += 1
        await asyncio.Event().wait()  # hang BEFORE any chunk
        yield  # pragma: no cover — unreachable; makes this an async generator

    def bind_tools(self, tools, **kwargs):
        return self


class GoodModel:
    def __init__(self, name="good"):
        self.name = name
        self.calls = 0

    async def ainvoke(self, messages, **kwargs):
        self.calls += 1
        return AIMessage(content=f"hello from {self.name}")

    async def astream(self, messages, **kwargs):
        self.calls += 1
        yield AIMessageChunk(content="hi ")
        yield AIMessageChunk(content=f"from {self.name}")

    def bind_tools(self, tools, **kwargs):
        return self


def _resilient(*models, timeout=0.25, on_fallback=None, max_retries=2):
    return ResilientChatModel(
        clients=list(models),
        model_names=[getattr(m, "name", f"m{i}") for i, m in enumerate(models)],
        max_retries=max_retries,
        backoff_base=0.0,
        call_timeout=timeout,
        on_fallback=on_fallback,
    )


# ── the stall → timeout → failover path (the case 429-only misses) ────────────

def test_stream_stall_times_out_and_fails_over():
    stalled, good = StallModel(), GoodModel()
    events = []
    rm = _resilient(stalled, good, on_fallback=lambda f, t, r=None: events.append((f, t, r)))

    async def run():
        return [c async for c in rm._astream([])]

    chunks = asyncio.run(run())
    assert "from good" in "".join(c.message.content for c in chunks)
    assert events and events[0][0] == "stalled" and events[0][1] == "good"
    assert "timed out" in (events[0][2] or "")          # the reason names the stall
    assert stalled.calls == 1                            # NO retries on a stall
    assert rm._active == 1                               # sticky on the fallback


def test_agenerate_stall_times_out_and_fails_over():
    stalled, good = StallModel(), GoodModel()
    events = []
    rm = _resilient(stalled, good, on_fallback=lambda f, t, r=None: events.append(r))
    result = asyncio.run(rm._agenerate([]))
    assert "from good" in result.generations[0].message.content
    assert stalled.calls == 1                            # timeout skips retries
    assert events and "timed out" in events[0]


def test_sticky_after_stall_failover():
    """The NEXT call goes straight to the fallback — no re-probing the stall."""
    stalled, good = StallModel(), GoodModel()
    rm = _resilient(stalled, good)
    asyncio.run(rm._agenerate([]))
    asyncio.run(rm._agenerate([]))
    assert stalled.calls == 1                            # not re-hammered
    assert good.calls == 2


# ── 429 path unchanged: retries THEN advance ──────────────────────────────────

def test_rate_limit_still_retries_before_advancing():
    class Limited(GoodModel):
        async def ainvoke(self, messages, **kwargs):
            self.calls += 1
            raise FakeRateLimit()

    limited, good = Limited("limited"), GoodModel()
    rm = _resilient(limited, good, max_retries=2)
    result = asyncio.run(rm._agenerate([]))
    assert limited.calls == 3                            # 1 + 2 retries — unchanged #104
    assert "from good" in result.generations[0].message.content


# ── cap / no-infinite-loop ────────────────────────────────────────────────────

def test_all_candidates_stall_raises_cleanly():
    a, b = StallModel("a"), StallModel("b")
    rm = _resilient(a, b)
    with pytest.raises(TimeoutError):
        asyncio.run(rm._agenerate([]))
    assert a.calls == 1 and b.calls == 1                 # one attempt each, then stop


# ── abort coexistence: outer cancel ≠ timeout ─────────────────────────────────

def test_abort_cancels_mid_timeout_wait():
    """Cancelling the task while waiting on a stalled provider raises
    CancelledError promptly — it is never converted into a failover."""
    stalled, good = StallModel(), GoodModel()
    rm = _resilient(stalled, good, timeout=30)           # long budget; cancel wins

    async def run():
        task = asyncio.ensure_future(rm._agenerate([]))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert good.calls == 0                               # never fell over on cancel


# ── timeout disabled → prior behaviour ────────────────────────────────────────

def test_no_timeout_means_no_stall_detection():
    """call_timeout=None (the pre-M6 default): a stall just hangs — pinned so
    the zero-regression claim is explicit, not accidental."""
    stalled, good = StallModel(), GoodModel()
    rm = _resilient(stalled, good, timeout=None)

    async def run():
        with pytest.raises(asyncio.TimeoutError):
            # Outer guard only for the test itself — the wrapper never times out.
            await asyncio.wait_for(rm._agenerate([]), timeout=0.3)

    asyncio.run(run())
    assert good.calls == 0                               # no failover happened


def test_default_call_timeout_env(monkeypatch):
    monkeypatch.delenv("HCODE_FALLBACK_TIMEOUT", raising=False)
    assert default_call_timeout() == 90.0
    monkeypatch.setenv("HCODE_FALLBACK_TIMEOUT", "45.5")
    assert default_call_timeout() == 45.5
    monkeypatch.setenv("HCODE_FALLBACK_TIMEOUT", "0")
    assert default_call_timeout() is None
    monkeypatch.setenv("HCODE_FALLBACK_TIMEOUT", "junk")
    assert default_call_timeout() == 90.0


def test_timeout_error_is_transient():
    assert is_transient_error(TimeoutError())
    assert is_transient_error(asyncio.TimeoutError())


# ── legacy 2-arg on_fallback still works ──────────────────────────────────────

def test_legacy_two_arg_callback_still_invoked():
    stalled, good = StallModel(), GoodModel()
    seen = []
    rm = _resilient(stalled, good, on_fallback=lambda f, t: seen.append((f, t)))
    asyncio.run(rm._agenerate([]))
    assert seen == [("stalled", "good")]


# ── factory: provider-qualified chain construction ────────────────────────────

def test_parse_fallback_entry():
    from hcode_v2.agent.factory import _parse_fallback_entry
    assert _parse_fallback_entry("openai/gpt-oss-20b") == ("openai/gpt-oss-20b", None)
    assert _parse_fallback_entry("gpt-oss-120b@cerebras") == ("gpt-oss-120b", "cerebras")
    assert _parse_fallback_entry("openai/gpt-oss-120b@groq") == ("openai/gpt-oss-120b", "groq")
    assert _parse_fallback_entry("@cerebras") == ("@cerebras", None)     # malformed → plain
    assert _parse_fallback_entry("model@") == ("model@", None)           # malformed → plain


def test_provider_overrides_env(monkeypatch):
    from hcode_v2.agent.factory import _provider_overrides
    monkeypatch.delenv("HCODE_PROVIDER_CEREBRAS_BASE_URL", raising=False)
    monkeypatch.delenv("HCODE_PROVIDER_CEREBRAS_API_KEY", raising=False)
    assert _provider_overrides("cerebras") is None                       # missing → skip
    monkeypatch.setenv("HCODE_PROVIDER_CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1")
    assert _provider_overrides("cerebras") is None                       # key still missing
    monkeypatch.setenv("HCODE_PROVIDER_CEREBRAS_API_KEY", "csk-test")
    assert _provider_overrides("cerebras") == ("https://api.cerebras.ai/v1", "csk-test")


def _capture_chain(monkeypatch, fallback_models):
    """Run _build_model with a recording _build_one_model; return (calls, wrapper)."""
    from hcode_v2.agent import factory
    calls = []

    def fake_build_one(model_name, config, max_tokens, anthropic_key, inner_retries,
                       base_url_override=None, api_key_override=None):
        calls.append({"model": model_name, "base_url": base_url_override,
                      "key": api_key_override})
        return GoodModel(model_name)

    monkeypatch.setattr(factory, "_build_one_model", fake_build_one)
    monkeypatch.setenv("HCODE_MODEL_NAME", "openai/gpt-oss-120b")
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "gsk-primary")
    monkeypatch.setenv("HCODE_MODEL_BASE_URL", "https://api.groq.com/openai/v1")
    wrapper = factory._build_model(fallback_models=fallback_models)
    return calls, wrapper


def test_build_model_cross_provider_chain(monkeypatch):
    monkeypatch.setenv("HCODE_PROVIDER_CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1")
    monkeypatch.setenv("HCODE_PROVIDER_CEREBRAS_API_KEY", "csk-test")
    calls, wrapper = _capture_chain(monkeypatch, ["gpt-oss-120b@cerebras"])

    assert len(calls) == 2
    assert calls[0]["base_url"] is None                  # primary: active Config
    assert calls[1] == {"model": "gpt-oss-120b",
                        "base_url": "https://api.cerebras.ai/v1", "key": "csk-test"}
    assert wrapper.model_names == ["openai/gpt-oss-120b", "gpt-oss-120b @ cerebras"]
    assert wrapper.call_timeout == 90.0                  # stall budget wired in


def test_build_model_skips_unconfigured_provider(monkeypatch):
    monkeypatch.delenv("HCODE_PROVIDER_NOWHERE_BASE_URL", raising=False)
    monkeypatch.delenv("HCODE_PROVIDER_NOWHERE_API_KEY", raising=False)
    calls, wrapper = _capture_chain(
        monkeypatch, ["model@nowhere", "openai/gpt-oss-20b"])
    # the unconfigured provider entry is dropped; the plain entry survives
    assert [c["model"] for c in calls] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    assert wrapper.model_names == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def test_build_model_no_fallbacks_is_bare_client(monkeypatch):
    """Zero regression: absent chain → no ResilientChatModel, no timeout."""
    from hcode_v2.agent import factory
    built = []
    monkeypatch.setattr(
        factory, "_build_one_model",
        lambda *a, **k: built.append(k) or GoodModel("bare"))
    monkeypatch.setenv("HCODE_MODEL_NAME", "openai/gpt-oss-120b")
    monkeypatch.setenv("HCODE_MODEL_API_KEY", "gsk-primary")
    out = factory._build_model(fallback_models=None)
    assert isinstance(out, GoodModel)                    # unwrapped
    assert len(built) == 1
