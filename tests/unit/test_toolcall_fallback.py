"""Tests for the JSON tool-calling fallback.

All tests are network-free: the inner model is replaced with a stub that returns
a canned response.  The tests verify:

1. JSON parsing from text in several formats.
2. That the wrapper injects the instruction and promotes parsed tool calls into
   ``AIMessage.tool_calls``.
3. That native ``tool_calls`` (if present) are preferred over JSON parsing.
4. That unknown tool names are filtered when ``known_names`` is supplied.
5. That ``bind_tools`` returns a ``RunnableBinding`` with the right kwargs shape
   (so LangGraph's ``_should_bind_tools`` sees it as already-bound).
6. A minimal agent-step round-trip: stub model emits JSON → wrapper parses it →
   the resulting ``AIMessage`` has the correct ``tool_calls`` structure.
7. ``maybe_wrap`` behaviour for all three modes.
8. ``Config.toolcall_mode`` resolution from env.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableBinding
from langchain_core.tools import tool

from hcode_v2.provider.fallback import (
    JsonToolCallWrapper,
    _build_instruction,
    _parse_json_tool_calls,
    maybe_wrap,
)
from hcode_v2.utils.config import Config


# ── Shared tools ───────────────────────────────────────────────────────────────


@tool
def add(a: int, b: int) -> int:
    """Add two integers and return the sum."""
    return a + b


@tool
def greet(name: str) -> str:
    """Return a greeting for the given name."""
    return f"Hello, {name}!"


_TOOLS = [add, greet]
_KNOWN = frozenset(t.name for t in _TOOLS)


# ── Minimal BaseChatModel stub ─────────────────────────────────────────────────


class _StubModel(BaseChatModel):
    """A real BaseChatModel subclass that returns a fixed canned response.

    Pydantic validates ``JsonToolCallWrapper.inner`` is a ``BaseChatModel``, so
    ``MagicMock`` is rejected.  This minimal subclass satisfies the type contract
    while staying entirely in-memory.
    """

    response_text: str = ""
    native_calls: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "stub"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        msg = AIMessage(content=self.response_text, tool_calls=list(self.native_calls))
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _make_inner(response_text: str, native_tool_calls: list | None = None) -> _StubModel:
    """Return a minimal BaseChatModel stub that returns a fixed response."""
    return _StubModel(
        response_text=response_text,
        native_calls=native_tool_calls or [],
    )


def _invoke_wrapper(inner_text: str, native_tool_calls: list | None = None) -> AIMessage:
    """Build a wrapper, bind tools, and invoke with a HumanMessage."""
    inner = _make_inner(inner_text, native_tool_calls)
    wrapper = JsonToolCallWrapper(inner=inner, mode="json")
    bound = wrapper.bind_tools(_TOOLS)
    messages: list[BaseMessage] = [HumanMessage(content="Use the add tool: 2 + 3")]
    result = bound.invoke(messages)
    return result  # type: ignore[return-value]


# ── 1. JSON parser unit tests ──────────────────────────────────────────────────


class TestParseJsonToolCalls:
    def test_bare_json_object(self) -> None:
        tc = _parse_json_tool_calls('{"name": "add", "arguments": {"a": 2, "b": 3}}')
        assert len(tc) == 1
        assert tc[0]["name"] == "add"
        assert tc[0]["args"] == {"a": 2, "b": 3}
        assert tc[0]["type"] == "tool_call"
        assert tc[0]["id"].startswith("json-")

    def test_code_fenced_json(self) -> None:
        text = '```json\n{"name": "greet", "arguments": {"name": "Alice"}}\n```'
        tc = _parse_json_tool_calls(text)
        assert tc[0]["name"] == "greet"
        assert tc[0]["args"] == {"name": "Alice"}

    def test_json_embedded_in_prose(self) -> None:
        text = 'I will call the tool now:\n{"name": "add", "arguments": {"a": 1, "b": 1}}\nDone.'
        tc = _parse_json_tool_calls(text)
        assert tc[0]["name"] == "add"

    def test_unknown_name_filtered_with_known_set(self) -> None:
        text = '{"name": "evil_tool", "arguments": {}}'
        tc = _parse_json_tool_calls(text, known_names=_KNOWN)
        assert tc == []

    def test_unknown_name_accepted_without_known_set(self) -> None:
        text = '{"name": "anything", "arguments": {}}'
        tc = _parse_json_tool_calls(text, known_names=None)
        assert tc[0]["name"] == "anything"

    def test_args_alias_arguments(self) -> None:
        tc = _parse_json_tool_calls('{"name": "add", "arguments": {"a": 5, "b": 5}}')
        assert tc[0]["args"] == {"a": 5, "b": 5}

    def test_args_alias_parameters(self) -> None:
        tc = _parse_json_tool_calls('{"name": "add", "parameters": {"a": 1, "b": 2}}')
        assert tc[0]["args"] == {"a": 1, "b": 2}

    def test_empty_text_returns_empty(self) -> None:
        assert _parse_json_tool_calls("") == []

    def test_plain_prose_returns_empty(self) -> None:
        assert _parse_json_tool_calls("I don't need any tools here.") == []

    def test_only_first_object_returned(self) -> None:
        text = (
            '{"name": "add", "arguments": {"a": 1, "b": 2}}\n'
            '{"name": "greet", "arguments": {"name": "Bob"}}'
        )
        tc = _parse_json_tool_calls(text)
        assert len(tc) == 1
        assert tc[0]["name"] == "add"


# ── 2. Instruction builder ─────────────────────────────────────────────────────


class TestBuildInstruction:
    def test_contains_tool_names(self) -> None:
        instr = _build_instruction(_TOOLS)
        assert "add" in instr
        assert "greet" in instr

    def test_contains_json_format_hint(self) -> None:
        instr = _build_instruction(_TOOLS)
        assert '"name"' in instr
        assert '"arguments"' in instr


# ── 3. Wrapper inject + parse ──────────────────────────────────────────────────


class TestJsonToolCallWrapper:
    def test_json_response_becomes_tool_call(self) -> None:
        msg = _invoke_wrapper('{"name": "add", "arguments": {"a": 2, "b": 3}}')
        assert msg.tool_calls
        tc = msg.tool_calls[0]
        assert tc["name"] == "add"
        assert tc["args"] == {"a": 2, "b": 3}

    def test_native_tool_calls_take_priority(self) -> None:
        native = [{"id": "native-1", "name": "add", "args": {"a": 9, "b": 9}, "type": "tool_call"}]
        msg = _invoke_wrapper(
            '{"name": "add", "arguments": {"a": 0, "b": 0}}',
            native_tool_calls=native,
        )
        # The native call (a=9) must win over the text-parsed one (a=0).
        assert msg.tool_calls[0]["args"]["a"] == 9

    def test_plain_text_no_tool_call_passes_through(self) -> None:
        msg = _invoke_wrapper("I don't need to use a tool.")
        assert not msg.tool_calls

    def test_instruction_injected_into_messages(self) -> None:
        # Verify instruction reaches the inner model by reading what _StubModel's
        # _generate receives through the wrapper.
        received: list[list[BaseMessage]] = []

        class _TracingStub(_StubModel):
            def _generate(
                self,
                messages: list[BaseMessage],
                stop: list[str] | None = None,
                run_manager: Any = None,
                **kwargs: Any,
            ) -> ChatResult:
                received.append(messages)
                return super()._generate(messages, stop, run_manager, **kwargs)

        inner = _TracingStub()
        wrapper = JsonToolCallWrapper(inner=inner, mode="json")
        bound = wrapper.bind_tools(_TOOLS)
        bound.invoke([HumanMessage(content="hi")])

        from langchain_core.messages import SystemMessage

        assert received, "inner _generate was never called"
        assert isinstance(received[0][0], SystemMessage)
        assert "add" in str(received[0][0].content)

    def test_bind_tools_returns_runnable_binding(self) -> None:
        inner = _make_inner("x")
        wrapper = JsonToolCallWrapper(inner=inner)
        bound = wrapper.bind_tools(_TOOLS)
        assert isinstance(bound, RunnableBinding)

    def test_bind_tools_kwargs_has_tools_with_names(self) -> None:
        inner = _make_inner("x")
        wrapper = JsonToolCallWrapper(inner=inner)
        bound = wrapper.bind_tools(_TOOLS)
        # LangGraph's _should_bind_tools checks bound.kwargs["tools"][*].get("name")
        assert "tools" in bound.kwargs
        names = {t["name"] for t in bound.kwargs["tools"]}
        assert "add" in names
        assert "greet" in names


# ── 4. maybe_wrap ──────────────────────────────────────────────────────────────


class TestMaybeWrap:
    def _stub(self) -> _StubModel:
        return _StubModel()

    def test_native_returns_model_unchanged(self) -> None:
        m = self._stub()
        result = maybe_wrap(m, "native")
        assert result is m

    def test_json_wraps_model(self) -> None:
        result = maybe_wrap(self._stub(), "json")
        assert isinstance(result, JsonToolCallWrapper)
        assert result.mode == "json"

    def test_auto_wraps_model(self) -> None:
        result = maybe_wrap(self._stub(), "auto")
        assert isinstance(result, JsonToolCallWrapper)
        assert result.mode == "auto"

    def test_unknown_mode_treated_as_auto(self) -> None:
        result = maybe_wrap(self._stub(), "typo")
        assert isinstance(result, JsonToolCallWrapper)


# ── 5. Config toolcall_mode from env ──────────────────────────────────────────


class TestConfigToolcallMode:
    def test_default_is_native(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HCODE_TOOLCALL_MODE", raising=False)
        assert Config.from_env().toolcall_mode == "native"

    def test_json_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HCODE_TOOLCALL_MODE", "json")
        assert Config.from_env().toolcall_mode == "json"

    def test_auto_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HCODE_TOOLCALL_MODE", "auto")
        assert Config.from_env().toolcall_mode == "auto"

    def test_invalid_value_defaults_to_native(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HCODE_TOOLCALL_MODE", "garbage")
        assert Config.from_env().toolcall_mode == "native"
