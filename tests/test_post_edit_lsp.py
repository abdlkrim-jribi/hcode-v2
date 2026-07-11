"""Tests for the post-edit LSP gate — type-check safety net at the tool seam.

Middleware tests monkeypatch the diagnostics provider (the real one needs a
language server); the provider itself is exercised for its cheap no-op paths.
Factory tests prove default-on wiring + the kill-switch, and ordering relative
to path containment.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from hcode_v2.agent import post_edit_lsp as pel
from hcode_v2.agent.post_edit_lsp import PostEditLspMiddleware, _MAX_FIX_ATTEMPTS
from langchain_core.messages import ToolMessage


def _request(tool="edit", path="hello.py", state=None):
    return SimpleNamespace(
        tool_call={"name": tool, "args": {"path": path}, "id": "tc1"},
        tool=None,
        state=state or {},
        runtime=None,
    )


def _result(content="wrote 3 lines to hello.py"):
    return ToolMessage(content=content, tool_call_id="tc1", name="edit",
                       artifact={"path": "hello.py", "diff": "@@"})


def _run(mw, request, result, provider):
    """Drive awrap_tool_call with a fake handler + patched provider."""
    async def handler(_req):
        return result

    async def _go():
        return await mw.awrap_tool_call(request, handler)

    orig = pel.post_edit_diagnostics
    pel.post_edit_diagnostics = provider
    try:
        return asyncio.run(_go())
    finally:
        pel.post_edit_diagnostics = orig


def _provider_returning(value, calls=None):
    async def provider(paths):
        if calls is not None:
            calls.append(list(paths))
        return value
    return provider


# ── Pass-through paths (zero regression) ───────────────────────────────────────

def test_non_file_tool_passes_through_without_check():
    calls = []
    result = _result("ran fine")
    out = _run(PostEditLspMiddleware(), _request(tool="bash"), result,
               _provider_returning("SHOULD NOT APPEAR", calls))
    assert out is result
    assert calls == []          # provider never invoked


def test_clean_edit_returns_result_object_unchanged():
    calls = []
    result = _result()
    out = _run(PostEditLspMiddleware(), _request(), result, _provider_returning(None, calls))
    assert out is result        # exact same object — nothing rebuilt
    assert calls == [["hello.py"]]


def test_failed_edit_skips_check():
    calls = []
    result = _result("Error: old_string not found in hello.py.")
    out = _run(PostEditLspMiddleware(), _request(), result,
               _provider_returning("X", calls))
    assert out is result
    assert calls == []          # file unchanged -> no check


def test_verify_phase_skips_check():
    calls = []
    result = _result()
    out = _run(PostEditLspMiddleware(), _request(state={"_pev_phase": "verify"}),
               result, _provider_returning("X", calls))
    assert out is result
    assert calls == []          # verify lane owns diagnostics there


# ── Error feedback ─────────────────────────────────────────────────────────────

def test_errors_append_addendum_preserving_result_fields():
    result = _result()
    out = _run(PostEditLspMiddleware(), _request(), result,
               _provider_returning("\n--- LSP: 1 ERROR ---"))
    assert isinstance(out, ToolMessage)
    assert out.content.startswith("wrote 3 lines")
    assert "--- LSP: 1 ERROR ---" in out.content
    assert out.tool_call_id == "tc1"
    assert out.artifact == {"path": "hello.py", "diff": "@@"}   # diff card intact


def test_cap_then_silent_then_reset_on_clean():
    mw = PostEditLspMiddleware()
    calls = []
    err = _provider_returning("\nERRS", calls)

    out1 = _run(mw, _request(), _result(), err)
    assert "ERRS" in out1.content and "final automatic check" not in out1.content
    out2 = _run(mw, _request(), _result(), err)
    assert "ERRS" in out2.content and "final automatic check" in out2.content
    assert _MAX_FIX_ATTEMPTS == 2

    # Third: capped — provider not even called, result untouched.
    r3 = _result()
    out3 = _run(mw, _request(), r3, err)
    assert out3 is r3
    assert len(calls) == 2

    # A clean check re-arms the counter...
    mw._attempts.pop("hello.py", None)  # simulate what a clean pass does
    out4 = _run(mw, _request(), _result(), err)
    assert "ERRS" in out4.content


def test_clean_check_pops_counter():
    mw = PostEditLspMiddleware()
    _run(mw, _request(), _result(), _provider_returning("\nERRS"))
    assert mw._attempts.get("hello.py") == 1
    _run(mw, _request(), _result(), _provider_returning(None))
    assert "hello.py" not in mw._attempts


def test_before_agent_resets_counters():
    mw = PostEditLspMiddleware()
    mw._attempts["hello.py"] = 2
    assert mw.before_agent({}, None) is None
    assert mw._attempts == {}


def test_provider_exception_never_breaks_the_tool_call():
    async def boom(paths):
        raise RuntimeError("lsp exploded")
    result = _result()
    out = _run(PostEditLspMiddleware(), _request(), result, boom)
    assert out is result


# ── Provider no-op paths (real function, no server needed) ─────────────────────

def test_provider_none_for_unsupported_file(tmp_path, monkeypatch):
    """A .txt edit resolves but has no language config -> None, fast."""
    from hcode_v2.tools.lsp_tools import post_edit_diagnostics
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    assert asyncio.run(post_edit_diagnostics(["notes.txt"])) is None


def test_provider_none_for_missing_file(tmp_path, monkeypatch):
    from hcode_v2.tools.lsp_tools import post_edit_diagnostics
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    assert asyncio.run(post_edit_diagnostics(["ghost.py"])) is None


# ── Factory wiring: default-on + kill-switch + ordering ────────────────────────

def _factory_middleware(monkeypatch, tmp_path, env_value=None) -> list:
    from hcode_v2.agent import factory

    captured: dict = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(factory, "create_deep_agent", fake_create_deep_agent)
    monkeypatch.setattr(factory, "_build_model", lambda *a, **k: object())
    # create_hcode_agent writes os.environ["HCODE_ROOT_DIR"] (factory.py) —
    # register the var with monkeypatch FIRST so teardown restores it and the
    # tmp_path root cannot leak into later tests (e.g. test_rich_feed).
    monkeypatch.setenv("HCODE_ROOT_DIR", str(tmp_path))
    if env_value is None:
        monkeypatch.delenv("HCODE_POST_EDIT_LSP", raising=False)
    else:
        monkeypatch.setenv("HCODE_POST_EDIT_LSP", env_value)

    async def _build() -> None:
        await factory.create_hcode_agent(
            persist=False,
            mcp_config=str(tmp_path / "no_such_mcp.json"),
            skills_dir=str(tmp_path),
            workflows_dir=str(tmp_path),
            work_dir=str(tmp_path),
        )

    asyncio.run(_build())
    return captured["middleware"]


def test_factory_attaches_gate_by_default_before_containment(tmp_path, monkeypatch):
    from hcode_v2.agent.path_containment import _PathContainmentMiddleware

    middleware = _factory_middleware(monkeypatch, tmp_path)
    types_ = [type(m) for m in middleware]
    assert PostEditLspMiddleware in types_
    assert types_.index(PostEditLspMiddleware) < types_.index(_PathContainmentMiddleware)


@pytest.mark.parametrize("off", ["0", "false", "OFF"])
def test_factory_kill_switch(tmp_path, monkeypatch, off):
    middleware = _factory_middleware(monkeypatch, tmp_path, env_value=off)
    assert PostEditLspMiddleware not in [type(m) for m in middleware]
