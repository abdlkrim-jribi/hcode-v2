"""Tests for ResilientChatModel — 429 retry-with-backoff + model fallback.

Uses fake chat models that raise a simulated 429 on demand, so no network. Covers:
  - retry-then-success on the SAME model (transient 429 clears)
  - persistent 429 on primary → fall back to the next model, run completes there
  - all models 429 → clean final error, capped (no infinite loop)
  - non-transient error propagates immediately (no retry/fallback)
  - CancelledError is never swallowed (Abort coexistence)
  - the fallback switch is announced via on_fallback
  - streaming path (_astream) retries/falls back before the first token
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from hcode_v2.provider.resilient import (
    ResilientChatModel,
    default_retry_config,
    is_transient_error,
)


# ── A fake rate-limit error mimicking the openai 429 shape ─────────────────────

class FakeRateLimit(Exception):
    def __init__(self, msg="429 temporarily rate-limited upstream", status_code=429):
        super().__init__(msg)
        self.status_code = status_code


class FakeFatal(Exception):
    """A non-transient error (e.g. bad request) — must NOT be retried."""


# ── A minimal fake chat model with scripted behaviour ─────────────────────────

class FakeModel:
    """Stands in for a bound chat client. ``script`` is a list of outcomes consumed
    per call: 'ok' → return an AIMessage; an Exception class/instance → raise it.
    Records how many times it was invoked."""

    def __init__(self, name: str, script: list):
        self.name = name
        self.script = list(script)
        self.calls = 0

    def _next(self):
        self.calls += 1
        outcome = self.script.pop(0) if self.script else "ok"
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, type) and issubclass(outcome, BaseException):
            raise outcome()
        return AIMessage(content=f"hello from {self.name}")

    async def ainvoke(self, messages, **kwargs):
        return self._next()

    def invoke(self, messages, **kwargs):
        return self._next()

    async def astream(self, messages, **kwargs):
        # Raise (if scripted) at stream-establish time, before any chunk — exactly
        # how a 429 manifests. Otherwise yield two chunks.
        msg = self._next()  # may raise
        yield AIMessageChunk(content="hi ")
        yield AIMessageChunk(content=f"from {self.name}")

    def bind_tools(self, tools, **kwargs):
        return self  # tools are irrelevant to these fakes


def _resilient(*models, max_retries=2, backoff_base=0.0, on_fallback=None):
    return ResilientChatModel(
        clients=list(models),
        model_names=[m.name for m in models],
        max_retries=max_retries,
        backoff_base=backoff_base,   # 0 → tests don't actually sleep
        on_fallback=on_fallback,
    )


# ── is_transient_error ─────────────────────────────────────────────────────────

def test_detects_429_by_status_code():
    assert is_transient_error(FakeRateLimit()) is True


def test_detects_429_by_message_without_status():
    assert is_transient_error(Exception("HTTP 429: rate limit exceeded")) is True


def test_detects_5xx():
    assert is_transient_error(Exception("503 service unavailable")) is True


def test_non_transient_is_false():
    assert is_transient_error(FakeFatal("400 bad request: invalid schema")) is False


# ── retry-then-success (verify #1) ─────────────────────────────────────────────

def test_retry_then_success_same_model():
    # 429 once, then ok → one retry, completes on the SAME (primary) model.
    primary = FakeModel("primary", [FakeRateLimit(), "ok"])
    fb = FakeModel("fallback", ["ok"])
    model = _resilient(primary, fb)

    result = asyncio.run(model._agenerate([]))
    assert "primary" in result.generations[0].message.content
    assert primary.calls == 2     # failed once, succeeded on retry
    assert fb.calls == 0          # never needed the fallback


# ── persistent 429 → fallback (verify #2) ──────────────────────────────────────

def test_falls_back_when_primary_persistently_429():
    switches: list[tuple] = []
    # primary 429s on all 3 attempts (max_retries=2); fallback succeeds.
    primary = FakeModel("primary", [FakeRateLimit(), FakeRateLimit(), FakeRateLimit()])
    fb = FakeModel("fallback", ["ok"])
    model = _resilient(primary, fb, on_fallback=lambda f, t: switches.append((f, t)))

    result = asyncio.run(model._agenerate([]))
    assert "fallback" in result.generations[0].message.content
    assert primary.calls == 3                      # exhausted its retries
    assert fb.calls == 1                           # completed on the fallback
    assert switches == [("primary", "fallback")]   # the switch was announced
    assert model._active == 1                       # sticky: now on the fallback


def test_sticky_fallback_skips_dead_primary_on_next_call():
    primary = FakeModel("primary", [FakeRateLimit(), FakeRateLimit(), FakeRateLimit(), "ok"])
    fb = FakeModel("fallback", ["ok", "ok"])
    model = _resilient(primary, fb)

    asyncio.run(model._agenerate([]))   # falls back, _active → 1
    primary_calls_after_first = primary.calls
    asyncio.run(model._agenerate([]))   # should start at the fallback, not re-probe primary
    assert primary.calls == primary_calls_after_first   # primary untouched second time
    assert fb.calls == 2


# ── all models 429 → clean error, capped (verify #3) ───────────────────────────

def test_all_models_429_raises_clean_error_no_infinite_loop():
    # Every attempt 429s on both models. Must terminate with the last error.
    primary = FakeModel("primary", [FakeRateLimit()] * 10)
    fb = FakeModel("fallback", [FakeRateLimit()] * 10)
    model = _resilient(primary, fb, max_retries=2)

    with pytest.raises(FakeRateLimit):
        asyncio.run(model._agenerate([]))
    # Cap = (max_retries+1) per model = 3 each, exactly — no infinite loop.
    assert primary.calls == 3
    assert fb.calls == 3


# ── non-transient propagates immediately (no retry/fallback) ───────────────────

def test_non_transient_error_propagates_without_retry_or_fallback():
    primary = FakeModel("primary", [FakeFatal("400 bad request")])
    fb = FakeModel("fallback", ["ok"])
    model = _resilient(primary, fb)

    with pytest.raises(FakeFatal):
        asyncio.run(model._agenerate([]))
    assert primary.calls == 1   # no retry
    assert fb.calls == 0        # no fallback on a real (non-429) error


# ── Abort coexistence: CancelledError is never swallowed (verify #5) ───────────

def test_cancelled_error_not_swallowed():
    class Canceller(FakeModel):
        async def ainvoke(self, messages, **kwargs):
            self.calls += 1
            raise asyncio.CancelledError()

    primary = Canceller("primary", [])
    fb = FakeModel("fallback", ["ok"])
    model = _resilient(primary, fb)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(model._agenerate([]))
    assert fb.calls == 0   # cancellation does NOT trigger a fallback


# ── streaming path retries/falls back before the first token ───────────────────

def test_astream_falls_back_before_first_token():
    switches: list[tuple] = []
    primary = FakeModel("primary", [FakeRateLimit(), FakeRateLimit(), FakeRateLimit()])
    fb = FakeModel("fallback", ["ok"])
    model = _resilient(primary, fb, on_fallback=lambda f, t: switches.append((f, t)))

    async def _collect():
        chunks = []
        async for ch in model._astream([]):
            chunks.append(ch)
        return chunks

    chunks = asyncio.run(_collect())
    text = "".join(
        c.message.content for c in chunks if isinstance(c, ChatGenerationChunk)
    )
    assert "from fallback" in text
    assert switches == [("primary", "fallback")]


def test_astream_retry_then_success():
    primary = FakeModel("primary", [FakeRateLimit(), "ok"])
    fb = FakeModel("fallback", ["ok"])
    model = _resilient(primary, fb)

    async def _collect():
        return [c async for c in model._astream([])]

    chunks = asyncio.run(_collect())
    text = "".join(
        c.message.content for c in chunks if isinstance(c, ChatGenerationChunk)
    )
    assert "from primary" in text
    assert primary.calls == 2
    assert fb.calls == 0


# ── retry config resolution ────────────────────────────────────────────────────

def test_default_retry_config_from_env(monkeypatch):
    monkeypatch.setenv("HCODE_MODEL_MAX_RETRIES", "5")
    monkeypatch.setenv("HCODE_MODEL_BACKOFF_BASE", "1.5")
    mr, bb = default_retry_config()
    assert mr == 5 and bb == 1.5


def test_default_retry_config_args_win(monkeypatch):
    monkeypatch.setenv("HCODE_MODEL_MAX_RETRIES", "5")
    mr, bb = default_retry_config(max_retries=1, backoff_base=0.25)
    assert mr == 1 and bb == 0.25
