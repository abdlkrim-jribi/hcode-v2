"""JSON tool-calling fallback for models that lack native function calling.

DeepAgents binds tools via LangChain's ``bind_tools`` / native function-calling.
If the company model (``gpt-oss``) does not support the OpenAI functions API,
every agent invocation silently produces no tool calls and the agent loops until
it hits the circuit-breaker — a hard, invisible failure.

This module provides ``JsonToolCallWrapper``, a ``BaseChatModel`` subclass that
wraps any chat model and adds a second path:

1. It injects a plain-text instruction into the system message that describes
   each tool schema and asks the model to respond with a JSON object when it
   wants to call a tool.
2. After the inner model responds, if the response contains no native
   ``tool_calls`` the wrapper scans the text for a JSON object matching the
   expected shape and promotes it into ``AIMessage.tool_calls`` — the structure
   LangGraph consumes.

Modes (controlled by ``HCODE_TOOLCALL_MODE`` env var, read by ``Config``):
    native  Wrapper is never applied; the inner model is returned as-is.
            Use this once the probe confirms native function-calling works.
    json    Always inject the instruction and parse JSON from text.
            Use this when the probe confirms the model lacks native tool-calling.
    auto    Inject the instruction; if the response already has native
            ``tool_calls``, use them. Otherwise fall back to JSON parsing.
            Good default when the endpoint behaviour is unknown.

The probe script (``scripts/probe_model.py``) tests both paths against the
configured endpoint and recommends which mode to set in ``.env``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import TYPE_CHECKING, Any, Callable, Literal, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict

if TYPE_CHECKING:
    from langchain_core.callbacks import CallbackManagerForLLMRun
    from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

# ── Private sentinel keys stored in bind() kwargs ─────────────────────────────
_KEY_INSTRUCTION = "_hcode_json_instruction"
_KEY_KNOWN_TOOLS = "_hcode_known_tools"


# ── JSON instruction building ──────────────────────────────────────────────────

_INSTRUCTION_TEMPLATE = """\
You have access to the following tools. When you want to call a tool, respond \
ONLY with a JSON object — no other text before or after it:

{{"name": "<tool_name>", "arguments": {{<argument_name>: <value>, ...}}}}

Available tools:
{tool_list}

If you do not need to use a tool, respond normally in plain text.\
"""


def _tool_description_line(tool: Any) -> str:
    """Return a single-line description of a tool for the JSON instruction."""
    name: str = getattr(tool, "name", str(tool))
    desc: str = (getattr(tool, "description", "") or "").strip().split("\n")[0]

    # Extract parameter names from the Pydantic schema if available
    params: list[str] = []
    schema_obj = getattr(tool, "args_schema", None)
    if schema_obj is not None:
        try:
            schema = schema_obj.model_json_schema()
            params = list(schema.get("properties", {}).keys())
        except Exception:  # noqa: BLE001
            pass

    param_str = ", ".join(f'"{p}"' for p in params) if params else ""
    args_hint = f" — args: {{{param_str}}}" if param_str else ""
    return f"  - {name}: {desc}{args_hint}"


def _build_instruction(tools: Sequence[Any]) -> str:
    """Return the system-message text instructing JSON tool-call format.

    Args:
        tools: Sequence of LangChain tool objects (must have a ``name`` attribute).

    Returns:
        Plain-text instruction for the system message.
    """
    lines = [_tool_description_line(t) for t in tools]
    return _INSTRUCTION_TEMPLATE.format(tool_list="\n".join(lines))


def _tool_to_schema(tool: Any) -> dict[str, Any]:
    """Return a minimal tool schema dict recognised by LangGraph's ``_should_bind_tools``.

    LangGraph checks ``bound_tool.get("name")`` to detect already-bound tools and
    avoid double-binding. This minimal dict satisfies that check.

    Args:
        tool: A LangChain tool object.

    Returns:
        Dict with at least ``"name"`` and ``"description"`` keys.
    """
    return {
        "name": getattr(tool, "name", str(tool)),
        "description": (getattr(tool, "description", "") or "").strip(),
    }


# ── JSON response parsing ──────────────────────────────────────────────────────

def _parse_json_tool_calls(
    text: str,
    known_names: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Extract a single tool-call from ``text`` and return it in LangChain shape.

    The parser looks for a JSON object with ``"name"`` and ``"arguments"`` keys
    (the format instructed in ``_INSTRUCTION_TEMPLATE``).  It tries code-fenced
    blocks first, then bare objects anywhere in the text.  Only the first valid
    match is returned — one tool call per model response.

    Args:
        text: Model response text, potentially containing a JSON tool call.
        known_names: Optional set of expected tool names used to filter candidates.
            If ``None``, any parsable object with a ``"name"`` key is accepted.

    Returns:
        A list of zero or one tool-call dicts in LangChain format
        ``[{"id": ..., "name": ..., "args": {...}, "type": "tool_call"}]``.
    """
    text = text.strip()
    if not text:
        return []

    candidates: list[dict[str, Any]] = []

    # Strategy 1: code-fenced JSON blocks (```json ... ```)
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        try:
            candidates.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            pass

    # Strategy 2: bare JSON objects anywhere in the text
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text):
        idx = text.find("{", pos)
        if idx == -1:
            break
        try:
            obj, end_pos = decoder.raw_decode(text, idx)
            if isinstance(obj, dict):
                candidates.append(obj)
            pos = idx + end_pos
        except json.JSONDecodeError:
            pos = idx + 1

    for obj in candidates:
        # Normalise key names: support "name"/"tool"/"function.name"
        name: str = (
            obj.get("name")
            or obj.get("tool")
            or (obj.get("function") or {}).get("name")
            or ""
        )
        if not name:
            continue
        if known_names is not None and name not in known_names:
            continue

        # Normalise args: support "arguments"/"args"/"parameters"/"input"
        args: dict[str, Any] = (
            obj.get("arguments")
            or obj.get("args")
            or obj.get("parameters")
            or obj.get("input")
            or {}
        )
        if not isinstance(args, dict):
            args = {}

        return [{
            "id": f"json-{uuid.uuid4().hex[:8]}",
            "name": name,
            "args": args,
            "type": "tool_call",
        }]

    return []


# ── Message injection ──────────────────────────────────────────────────────────

def _prepend_system(
    messages: list[BaseMessage],
    instruction: str,
) -> list[BaseMessage]:
    """Return a copy of ``messages`` with the JSON instruction prepended.

    If the first message is already a ``SystemMessage``, the instruction is
    appended to it (separated by two newlines) so the model sees a single
    consolidated system prompt.

    Args:
        messages: Existing message list.
        instruction: JSON tool-calling instruction text.

    Returns:
        New list of messages with the instruction injected.
    """
    if not messages:
        return [SystemMessage(content=instruction)]

    if isinstance(messages[0], SystemMessage):
        combined = str(messages[0].content) + "\n\n" + instruction
        return [SystemMessage(content=combined)] + list(messages[1:])

    return [SystemMessage(content=instruction)] + list(messages)


# ── Wrapper ────────────────────────────────────────────────────────────────────

class JsonToolCallWrapper(BaseChatModel):
    """Wraps a ``BaseChatModel`` to add JSON-based tool-calling support.

    Designed for models (e.g. self-hosted ``gpt-oss``) that do not support
    OpenAI's native function-calling API.  The wrapper injects a structured
    instruction into the system message and parses the model's plain-text JSON
    response back into the ``tool_calls`` list that LangGraph expects.

    It is transparent in ``native`` mode (just delegates) and active in ``json``
    and ``auto`` modes.  ``auto`` is the recommended default: it injects the
    instruction but prefers native ``tool_calls`` when they are present, so the
    same wrapper works for both capable and limited endpoints.

    Args:
        inner: The underlying chat model to delegate all LLM calls to.
        mode: Tool-calling strategy.  ``"json"`` always uses text-parsing;
            ``"auto"`` uses native tool_calls when present, text-parsing otherwise.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    inner: BaseChatModel
    mode: Literal["json", "auto"] = "auto"

    @property
    def _llm_type(self) -> str:
        inner_type = getattr(self.inner, "_llm_type", "unknown")
        return f"hcode-json-fallback[{inner_type}]"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Generate a response, injecting JSON instruction and parsing tool calls.

        Pops the two private sentinel keys (``_hcode_json_instruction``,
        ``_hcode_known_tools``) injected by :meth:`bind_tools` so they are never
        forwarded to the inner model.

        Args:
            messages: Input message list.
            stop: Optional stop sequences.
            run_manager: Optional LangChain callback manager.
            **kwargs: Extra kwargs forwarded to ``inner._generate``.

        Returns:
            ``ChatResult`` with ``tool_calls`` populated from JSON parsing when
            the inner model does not return native tool calls.
        """
        instruction: str | None = kwargs.pop(_KEY_INSTRUCTION, None)
        known_names: frozenset[str] | None = kwargs.pop(_KEY_KNOWN_TOOLS, None)

        injected_messages = _prepend_system(messages, instruction) if instruction else messages
        result = self.inner._generate(injected_messages, stop, run_manager, **kwargs)

        if instruction:
            for i, gen in enumerate(result.generations):
                msg = gen.message
                # Skip if the model already returned native tool_calls
                if msg.tool_calls:
                    logger.debug("JsonToolCallWrapper: native tool_calls present — skipping JSON parse")
                    continue
                content = msg.content if isinstance(msg.content, str) else ""
                if not content.strip():
                    continue
                parsed = _parse_json_tool_calls(content, known_names)
                if parsed:
                    logger.debug(
                        "JsonToolCallWrapper: parsed %d JSON tool call(s) from text", len(parsed)
                    )
                    result.generations[i] = ChatGeneration(
                        message=AIMessage(content="", tool_calls=parsed),
                        generation_info=gen.generation_info,
                    )

        return result

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        """Bind tools and return a ``RunnableBinding`` ready for LangGraph.

        Formats tool schemas in the shape ``_should_bind_tools`` checks for (a
        list of dicts with at least ``"name"``), stores the JSON instruction in
        a private kwarg, and delegates to ``self.bind()`` which produces a
        standard ``_ChatModelBinding``.

        Args:
            tools: Tools to make available to the model.
            tool_choice: Unused (kept for interface compatibility).
            **kwargs: Extra kwargs forwarded to ``self.bind()``.

        Returns:
            A ``_ChatModelBinding`` (``RunnableBinding`` subclass) with tool
            schemas and the JSON instruction bound.
        """
        schemas = [_tool_to_schema(t) for t in tools]
        known_names = frozenset(s["name"] for s in schemas)
        instruction = _build_instruction(tools)
        return self.bind(
            tools=schemas,
            **{_KEY_INSTRUCTION: instruction, _KEY_KNOWN_TOOLS: known_names},
            **kwargs,
        )


# ── Factory helper ─────────────────────────────────────────────────────────────

def maybe_wrap(
    model: BaseChatModel,
    mode: str,
) -> BaseChatModel:
    """Conditionally wrap ``model`` with ``JsonToolCallWrapper``.

    Args:
        model: The base chat model to optionally wrap.
        mode: Value of ``HCODE_TOOLCALL_MODE`` (``"native"``, ``"json"``,
            or ``"auto"``).

    Returns:
        ``model`` unchanged for ``"native"`` mode; a ``JsonToolCallWrapper``
        for ``"json"`` or ``"auto"`` modes.
    """
    if mode == "native":
        return model
    safe_mode: Literal["json", "auto"] = "json" if mode == "json" else "auto"
    return JsonToolCallWrapper(inner=model, mode=safe_mode)


__all__ = ["JsonToolCallWrapper", "maybe_wrap", "_parse_json_tool_calls", "_build_instruction"]
