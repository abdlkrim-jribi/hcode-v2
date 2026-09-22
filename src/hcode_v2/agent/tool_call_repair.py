"""Tool-call schema repair — survive a provider rejecting a malformed tool call.

## The failure this fixes (measured, not hypothetical)

``gpt-oss-120b`` calls a tool without its required arguments. The provider
validates the model's tool call against the schema WE sent and rejects the
ENTIRE completion with a 400:

    Tool call validation failed: parameters for tool edit did not match schema:
    errors: [missing properties: 'old_string', 'new_string']

Because the request fails, there is **no AIMessage and no tool call to execute**
— so the post-edit gate's "feed the error back as a tool result" pattern cannot
apply here; there is no tool result to attach anything to. The exception
propagates out of ``astream_events`` to ``server.py``'s catch-all, which emits
``{"recoverable": false}`` and **kills the whole task**.

Measured in the experimental evaluation: this ended the run in smoke case
``S1xA`` and in BOTH arms of the live plan-phase A/B — i.e. a single malformed
tool call is currently fatal, no matter how much correct work preceded it.

## The fix

Intercept at the MODEL call (the only place the failure exists) rather than at
the tool result. On a tool-schema rejection, append a short corrective note
naming the provider's own complaint and re-issue the call, bounded by
``_MAX_REPAIRS``. A model told exactly which properties it omitted has a real
chance of emitting the call correctly on the next attempt — which is the
difference between a dead run and a recovered one.

The note goes onto the *request's* system message, never into the thread
history, so a repaired run leaves no residue in the transcript and the
checkpointed conversation is unchanged.

## Why this is narrow on purpose

Only errors matching a tool-schema signature are retried. Everything else —
rate limits, auth failures, context-length errors, network faults — is re-raised
immediately and unchanged, because retrying those is either useless or actively
harmful (a 429 must reach ``ResilientChatModel``, which owns backoff and
failover). A wrong retry costs one model call; the failure it prevents costs
the entire task, so the asymmetry favours retrying — but only on the narrow
signature.

Kill-switch: ``HCODE_TOOL_CALL_REPAIR=0``/``false``/``off`` omits it entirely.
HCode-side; vendored deepagents untouched.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse

logger = logging.getLogger(__name__)

_MAX_REPAIRS: int = 2
"""Repair attempts per model call before giving up and re-raising honestly."""

# Signatures of "the model's tool call did not match the schema we sent".
# Deliberately specific: a rate limit, an auth error or a context-length error
# must NOT match, or we would retry something retrying cannot fix and would rob
# ResilientChatModel of the 429 it owns.
_SCHEMA_SIGNS: tuple[str, ...] = (
    "did not match schema",
    "tool call validation failed",
    "invalid tool call",
    "missing properties",
    "required property",
)

# Never treat these as repairable even if a schema phrase also appears.
_NEVER_REPAIR: tuple[str, ...] = (
    "rate limit", "rate_limit", "429", "quota",
    "context_length", "too large", "413",
    "authentication", "api key", "401", "403",
)

_MISSING_RE = re.compile(r"missing propert(?:y|ies):?\s*\[?([^\]\n]+)", re.I)
_TOOL_RE = re.compile(r"for tool\s+([A-Za-z0-9_\-]+)", re.I)


def schema_error_detail(text: str) -> str | None:
    """Return a short description when ``text`` is a tool-schema rejection.

    ``None`` means "not a schema error" — the caller must re-raise untouched.
    """
    low = (text or "").lower()
    if any(bad in low for bad in _NEVER_REPAIR):
        return None
    if not any(sign in low for sign in _SCHEMA_SIGNS):
        return None

    tool = _TOOL_RE.search(text)
    missing = _MISSING_RE.search(text)
    parts = []
    if tool:
        parts.append(f"tool `{tool.group(1)}`")
    if missing:
        parts.append(f"missing required argument(s): {missing.group(1).strip()}")
    return "; ".join(parts) if parts else text.strip()[:200]


def repair_note(detail: str) -> str:
    return (
        "## Tool call rejected — correct it now\n\n"
        f"Your previous tool call was REJECTED by the provider ({detail}). "
        "It was not executed and nothing changed.\n"
        "Re-issue the call with EVERY required argument present and non-empty. "
        "For an `edit` call that means both `old_string` (text that currently "
        "exists in the file, copied exactly) and `new_string`. If you do not "
        "have the exact current text, call `read` on the file first, then edit."
    )


class ToolCallRepairMiddleware(AgentMiddleware):
    """Retry a model call whose tool call the provider rejected as malformed.

    A model call that succeeds is untouched: the handler is invoked exactly once
    with the original request, so a well-behaved run is byte-identical.
    """

    def _retry_request(self, request: ModelRequest, detail: str) -> ModelRequest:
        from deepagents.middleware._utils import append_to_system_message
        return request.override(
            system_message=append_to_system_message(request.system_message, repair_note(detail))
        )

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        attempt, current = 0, request
        while True:
            try:
                return handler(current)
            except Exception as exc:  # noqa: BLE001 — re-raised unless repairable
                detail = schema_error_detail(str(exc))
                if detail is None or attempt >= _MAX_REPAIRS:
                    raise
                attempt += 1
                logger.info(
                    "[tool-call-repair] retrying rejected tool call (%d/%d): %s",
                    attempt, _MAX_REPAIRS, detail,
                )
                current = self._retry_request(request, detail)

    async def awrap_model_call(
        self, request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        attempt, current = 0, request
        while True:
            try:
                return await handler(current)
            except Exception as exc:  # noqa: BLE001 — re-raised unless repairable
                detail = schema_error_detail(str(exc))
                if detail is None or attempt >= _MAX_REPAIRS:
                    raise
                attempt += 1
                logger.info(
                    "[tool-call-repair] retrying rejected tool call (%d/%d): %s",
                    attempt, _MAX_REPAIRS, detail,
                )
                current = self._retry_request(request, detail)


__all__ = ["ToolCallRepairMiddleware", "schema_error_detail", "repair_note"]
