"""Tool-call schema repair — keyless proof a rejected tool call is retried once
told what was wrong, and that nothing else is retried.

The real failure is a provider 400 raised from inside the model call, which no
scripted model can produce. What is tested here is the middleware's contract
directly, with a stub handler standing in for the model: which errors are
repaired, which are re-raised untouched, how many attempts are made, and that a
successful call is invoked exactly once with the original request.
"""

from __future__ import annotations

import asyncio

import pytest

from hcode_v2.agent.tool_call_repair import (
    ToolCallRepairMiddleware,
    repair_note,
    schema_error_detail,
)

# The exact message measured from Groq when gpt-oss-120b omitted edit's args.
REAL_ERROR = (
    "Tool call validation failed: tool call validation failed: parameters for "
    "tool edit did not match schema: errors: [missing properties: "
    "'old_string', 'new_string']"
)


class _Req:
    def __init__(self, text="BASE"):
        from langchain_core.messages import SystemMessage
        self.system_message = SystemMessage(content=text)
        self.state = {}

    def override(self, **kw):
        import copy
        new = copy.copy(self)
        for k, v in kw.items():
            setattr(new, k, v)
        return new


def _drive(handler_factory):
    """Run the async hook with a stub handler; return (result, seen_requests)."""
    mw = ToolCallRepairMiddleware()
    seen: list = []
    handler = handler_factory(seen)
    return asyncio.run(mw.awrap_model_call(_Req(), handler)), seen


# ── the signature matcher (the whole safety story lives here) ─────────────────

def test_real_measured_error_is_recognised_as_repairable():
    detail = schema_error_detail(REAL_ERROR)
    assert detail is not None
    assert "edit" in detail
    assert "old_string" in detail and "new_string" in detail


@pytest.mark.parametrize("text", [
    "Rate limit reached for model gpt-oss-120b (429)",
    "Request too large: context_length exceeded (413)",
    "Authentication error: invalid api key (401)",
    "Connection error.",
    "Internal server error (500)",
])
def test_non_schema_errors_are_never_repaired(text):
    assert schema_error_detail(text) is None


def test_rate_limit_wins_even_if_a_schema_phrase_appears():
    """A 429 must reach ResilientChatModel, which owns backoff/failover —
    retrying it here would rob it of that."""
    assert schema_error_detail(
        "rate limit exceeded; also missing properties: 'x'"
    ) is None


def test_repair_note_names_the_problem_and_the_recovery():
    note = repair_note("tool `edit`; missing required argument(s): 'old_string'")
    assert "REJECTED" in note
    assert "old_string" in note
    assert "read" in note  # tells it how to get the exact text


# ── the retry behaviour ───────────────────────────────────────────────────────

def test_successful_call_is_invoked_once_and_unmodified():
    """Zero regression: a healthy model call never sees the middleware."""
    def factory(seen):
        async def handler(req):
            seen.append(req)
            return "OK"
        return handler

    result, seen = _drive(factory)
    assert result == "OK"
    assert len(seen) == 1
    assert seen[0].system_message.content == "BASE"


def test_schema_rejection_is_retried_with_a_corrective_note():
    """Fails once, then succeeds — and the retry carries the correction."""
    def factory(seen):
        async def handler(req):
            seen.append(req)
            if len(seen) == 1:
                raise RuntimeError(REAL_ERROR)
            return "RECOVERED"
        return handler

    result, seen = _drive(factory)
    assert result == "RECOVERED"
    assert len(seen) == 2
    assert seen[0].system_message.content == "BASE"          # first try untouched
    retry_text = seen[1].system_message.content
    assert "BASE" in str(retry_text)                          # original preserved
    assert "old_string" in str(retry_text)                    # correction appended


def test_non_schema_error_is_reraised_immediately_without_retry():
    def factory(seen):
        async def handler(req):
            seen.append(req)
            raise RuntimeError("Rate limit reached (429)")
        return handler

    with pytest.raises(RuntimeError, match="Rate limit"):
        _drive(factory)


def test_persistent_rejection_gives_up_and_reraises_honestly():
    """Bounded: it must not loop, and the final failure must still surface."""
    seen: list = []

    async def handler(req):
        seen.append(req)
        raise RuntimeError(REAL_ERROR)

    mw = ToolCallRepairMiddleware()
    with pytest.raises(RuntimeError, match="did not match schema"):
        asyncio.run(mw.awrap_model_call(_Req(), handler))
    assert len(seen) == 3  # original + _MAX_REPAIRS(2)


def test_sync_path_behaves_the_same():
    mw = ToolCallRepairMiddleware()
    seen: list = []

    def handler(req):
        seen.append(req)
        if len(seen) == 1:
            raise RuntimeError(REAL_ERROR)
        return "OK"

    assert mw.wrap_model_call(_Req(), handler) == "OK"
    assert len(seen) == 2


def test_factory_wires_it_first_and_honours_kill_switch(monkeypatch, tmp_path):
    """It must be the OUTERMOST wrap_model_call to see the model's exception."""
    from hcode_v2.agent import factory

    captured = {}

    def fake_create(**kw):
        captured.update(kw)
        return object()

    monkeypatch.setattr(factory, "create_deep_agent", fake_create)
    monkeypatch.setattr(factory, "_build_model", lambda *a, **k: object())
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    (tmp_path / "skills").mkdir(exist_ok=True)

    async def _build():
        await factory.create_hcode_agent(
            persist=False, mcp_config=str(tmp_path / "none.json"),
            skills_dir=str(tmp_path / "skills"), workflows_dir=str(tmp_path),
            work_dir=str(tmp_path),
        )

    monkeypatch.delenv("HCODE_TOOL_CALL_REPAIR", raising=False)
    asyncio.run(_build())
    names = [type(m).__name__ for m in captured["middleware"]]
    assert names[0] == "ToolCallRepairMiddleware", names[:3]

    monkeypatch.setenv("HCODE_TOOL_CALL_REPAIR", "0")
    asyncio.run(_build())
    assert "ToolCallRepairMiddleware" not in [
        type(m).__name__ for m in captured["middleware"]
    ]
