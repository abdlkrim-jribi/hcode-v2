"""Resilient model wrapper — retry-with-backoff + fall back to another model.

A free OpenRouter model can return HTTP 429 ("temporarily rate-limited upstream")
mid-run. Most clear in seconds; some persist. ``ResilientChatModel`` makes a 429 a
hiccup, not a hard failure:

1. **Retry**: on a transient error (429 / 5xx / timeout) it retries the SAME model
   with exponential backoff (a couple of attempts).
2. **Fallback**: if retries are exhausted it advances to the NEXT model in an
   ordered list (the user's chosen model is always first) and continues there.
   It is *sticky* — once a fallback works it stays active, so a sustained rate
   limit does not re-hammer the dead primary on every step.

Boundary discipline (why this is safe):
  - A 429 surfaces when a model call is made — before any tokens stream. The retry
    and the fallback both happen AT that call boundary, so the agent graph
    continues seamlessly on the fallback from the next model call. Already-finished
    steps (tool calls, prior messages) stay in graph state — no restart, no
    re-emit, no corruption.
  - If a transient error somehow lands AFTER streaming started for a step, it is
    NOT swallowed mid-stream — it propagates as a clean error (no mid-stream model
    swap). 429s are request-time, so this is rare.
  - The attempt count is capped across all models, so "every model is 429" ends in
    a single clean error, never an infinite loop.

Cancellation: the backoff uses ``asyncio.sleep`` and the wrapper NEVER catches
``CancelledError`` — so Abort cancels a run that is mid-retry, exactly as before.

This wraps existing model clients HCode-side; ``libs/deepagents`` is untouched.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import ConfigDict, PrivateAttr

logger = logging.getLogger(__name__)

# Bound-tools payload smuggled through ``self.bind(...)`` into _generate/_astream,
# mirroring JsonToolCallWrapper's sentinel-kwarg pattern.
_KEY_BOUND = "_hcode_resilient_bound"

# HTTP statuses we treat as transient (retry, then fall back).
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}

# Substrings that mark a transient/rate-limit failure when no status code is
# exposed (langchain sometimes re-wraps provider errors as plain strings).
_TRANSIENT_MARKERS = (
    "429", "rate limit", "rate-limit", "ratelimit", "too many requests",
    "temporarily rate-limited", "overloaded", "service unavailable",
    "503", "502", "504",
)


def is_transient_error(exc: BaseException) -> bool:
    """True if *exc* looks like a transient rate-limit / upstream error.

    Duck-typed (no hard openai import): checks a ``status_code`` on the exception
    or its ``response``, the exception class name, then the message text. Errs
    toward retrying — a false positive costs one extra attempt, a false negative
    turns a recoverable 429 into a hard failure.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and status in _TRANSIENT_STATUS:
        return True

    name = type(exc).__name__.lower()
    if any(k in name for k in (
        "ratelimit", "internalservererror", "serviceunavailable",
        "apitimeout", "overloaded",
    )):
        return True

    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


def default_retry_config(
    max_retries: int | None = None, backoff_base: float | None = None
) -> tuple[int, float]:
    """Resolve retry knobs from args → env → defaults.

    ``HCODE_MODEL_MAX_RETRIES`` (default 2 retries = 3 attempts per model),
    ``HCODE_MODEL_BACKOFF_BASE`` seconds (default 0.5 → 0.5s, 1s, 2s, …).
    """
    if max_retries is None:
        try:
            max_retries = int(os.getenv("HCODE_MODEL_MAX_RETRIES", "2"))
        except ValueError:
            max_retries = 2
    if backoff_base is None:
        try:
            backoff_base = float(os.getenv("HCODE_MODEL_BACKOFF_BASE", "0.5"))
        except ValueError:
            backoff_base = 0.5
    return max(0, max_retries), max(0.0, backoff_base)


class ResilientChatModel(BaseChatModel):
    """Wrap an ordered list of chat models with retry-with-backoff + fallback.

    ``clients[0]`` is the primary (the user's chosen model); the rest are
    fallbacks tried in order. All share one provider account (same key/base_url);
    only the model NAME differs. See the module docstring for the boundary rules.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    clients: list[Any]                       # BaseChatModel per candidate (primary first)
    model_names: list[str]                   # parallel display names, for events/logs
    max_retries: int = 2                     # retries PER model (attempts = max_retries + 1)
    backoff_base: float = 0.5                # seconds; exponential: base * 2**attempt
    backoff_max: float = 8.0                 # cap a single backoff sleep
    on_fallback: Optional[Callable[[str, str], None]] = None  # (from_name, to_name)

    # Sticky active index — persists across calls on the SAME instance so a run
    # that fell back stays on the fallback instead of re-probing the dead primary.
    _active: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "hcode-resilient"

    # ── backoff / fallback helpers ────────────────────────────────────────────

    def _backoff_seconds(self, attempt: int) -> float:
        return min(self.backoff_base * (2 ** attempt), self.backoff_max)

    def _announce_fallback(self, from_idx: int, to_idx: int) -> None:
        frm = self.model_names[from_idx] if from_idx < len(self.model_names) else "?"
        to = self.model_names[to_idx] if to_idx < len(self.model_names) else "?"
        logger.warning("Model %s rate-limited/exhausted — falling back to %s", frm, to)
        if self.on_fallback is not None:
            try:
                self.on_fallback(frm, to)
            except Exception:  # pragma: no cover - never let an emit hook break a run
                pass

    # ── tool binding ──────────────────────────────────────────────────────────

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
        """Bind tools to every candidate; smuggle the bound runnables to _generate.

        deepagents/langchain re-binds per model call, so the bound list is rebuilt
        each call (cheap) and the sticky ``_active`` lives on this instance, not on
        the binding. Mirrors JsonToolCallWrapper's ``self.bind(...)`` pattern so
        ``_should_bind_tools`` sees a RunnableBinding and does not double-bind.
        """
        bound = [c.bind_tools(tools, **kwargs) for c in self.clients]
        return self.bind(**{_KEY_BOUND: bound})

    def _candidates(self, kwargs: dict) -> list[Any]:
        """The per-call invocation targets: bound runnables if tools were bound,
        else the raw clients (no-tools path)."""
        bound = kwargs.pop(_KEY_BOUND, None)
        return bound if bound is not None else self.clients

    # ── async streaming (the daemon's astream_events path) ────────────────────

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """Stream the active model; retry/fall back BEFORE the first token only.

        We pull the first chunk inside the try: a 429 raises there (request time)
        and triggers retry/fallback with nothing yielded yet. Once the first chunk
        is out we stream the rest verbatim — a late transient propagates cleanly
        rather than swapping models mid-stream.
        """
        candidates = self._candidates(kwargs)
        n = len(candidates)
        idx = min(self._active, n - 1)
        last_exc: BaseException | None = None

        while idx < n:
            for attempt in range(self.max_retries + 1):
                gen = candidates[idx].astream(messages, **kwargs)
                try:
                    first = await gen.__anext__()
                except StopAsyncIteration:
                    self._active = idx
                    return
                except asyncio.CancelledError:
                    raise  # Abort — never swallow
                except BaseException as exc:  # noqa: BLE001
                    if not is_transient_error(exc):
                        raise
                    last_exc = exc
                    if attempt < self.max_retries:
                        await asyncio.sleep(self._backoff_seconds(attempt))
                    continue  # retry same model (or, retries spent, exit to fallback)
                # First chunk obtained — commit to this client and stream the rest.
                self._active = idx
                yield _as_chunk(first)
                async for chunk in gen:
                    yield _as_chunk(chunk)
                return
            # retries on this model exhausted → fall back to the next, if any
            if idx + 1 < n:
                self._announce_fallback(idx, idx + 1)
            idx += 1

        raise last_exc if last_exc is not None else RuntimeError("no model candidates")

    # ── async non-streaming ───────────────────────────────────────────────────

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        candidates = self._candidates(kwargs)
        n = len(candidates)
        idx = min(self._active, n - 1)
        last_exc: BaseException | None = None

        while idx < n:
            for attempt in range(self.max_retries + 1):
                try:
                    msg = await candidates[idx].ainvoke(messages, **kwargs)
                    self._active = idx
                    return _as_result(msg)
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:  # noqa: BLE001
                    if not is_transient_error(exc):
                        raise
                    last_exc = exc
                    if attempt < self.max_retries:
                        await asyncio.sleep(self._backoff_seconds(attempt))
            if idx + 1 < n:
                self._announce_fallback(idx, idx + 1)
            idx += 1

        raise last_exc if last_exc is not None else RuntimeError("no model candidates")

    # ── sync non-streaming (CLI parity; uses time.sleep) ──────────────────────

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        candidates = self._candidates(kwargs)
        n = len(candidates)
        idx = min(self._active, n - 1)
        last_exc: BaseException | None = None

        while idx < n:
            for attempt in range(self.max_retries + 1):
                try:
                    msg = candidates[idx].invoke(messages, **kwargs)
                    self._active = idx
                    return _as_result(msg)
                except BaseException as exc:  # noqa: BLE001
                    if not is_transient_error(exc):
                        raise
                    last_exc = exc
                    if attempt < self.max_retries:
                        time.sleep(self._backoff_seconds(attempt))
            if idx + 1 < n:
                self._announce_fallback(idx, idx + 1)
            idx += 1

        raise last_exc if last_exc is not None else RuntimeError("no model candidates")


def _as_chunk(message: Any) -> ChatGenerationChunk:
    """Wrap an AIMessageChunk from an inner stream as a ChatGenerationChunk."""
    return ChatGenerationChunk(message=message)


def _as_result(message: Any) -> ChatResult:
    """Wrap an AIMessage from ``ainvoke``/``invoke`` as a ChatResult."""
    if not isinstance(message, AIMessage):
        message = AIMessage(content=str(getattr(message, "content", message)))
    return ChatResult(generations=[ChatGeneration(message=message)])


__all__ = ["ResilientChatModel", "is_transient_error", "default_retry_config"]
