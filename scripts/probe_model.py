#!/usr/bin/env python
"""Probe the configured model for the two capabilities DeepAgents depends on.

This is the **provider gate** check. DeepAgents binds tools via native
function-calling, so before porting work onto a new endpoint we must confirm the
model can (a) hold a basic chat completion and (b) emit a tool call when given a
bound tool. Run it once endpoint access is granted.

Usage::

    # 1. put the endpoint config in .env (see .env.example):
    #    HCODE_MODEL_NAME / HCODE_MODEL_BASE_URL / HCODE_MODEL_API_KEY
    # 2. then, from the repo root:
    uv run python scripts/probe_model.py

It is intentionally safe to run only when an endpoint is configured: with no key
or base URL it prints setup instructions and exits without making any network
call.

Interpreting the result:

- Both checks pass  -> set ``HCODE_TOOLCALL_MODE=native``.
- Chat passes, tool call missing -> the model lacks native tool-calling; use the
  JSON fallback (``HCODE_TOOLCALL_MODE=json``) once it exists (issue A2).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a bare script (python scripts/probe_model.py) by putting the
# package src/ on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from hcode_v2.agent.factory import _build_model
from hcode_v2.utils.config import Config


@tool
def add(a: int, b: int) -> int:
    """Add two integers and return the sum."""
    return a + b


def _mask(secret: str | None) -> str:
    """Return a masked preview of a secret for safe printing."""
    if not secret:
        return "(unset)"
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}…{secret[-2:]}"


def _check_chat(model) -> bool:
    """Send a trivial chat completion and print the reply."""
    print("\n[1/2] chat completion — sending 'say hello'…")
    try:
        reply = model.invoke([HumanMessage(content="Say hello in five words or fewer.")])
    except Exception as exc:  # noqa: BLE001 — surface any endpoint/auth error verbatim
        print(f"    FAIL: {type(exc).__name__}: {exc}")
        return False
    text = getattr(reply, "content", reply)
    print(f"    PASS: {text!r}")
    return True


def _check_tool_call(model) -> bool:
    """Bind one trivial tool and check whether the model emits a tool call."""
    print("\n[2/2] native tool-calling — binding `add` and asking for 2 + 3…")
    try:
        bound = model.bind_tools([add])
        reply = bound.invoke([HumanMessage(content="Use the add tool to compute 2 + 3.")])
    except Exception as exc:  # noqa: BLE001 — surface any bind/endpoint error verbatim
        print(f"    FAIL: {type(exc).__name__}: {exc}")
        return False
    tool_calls = getattr(reply, "tool_calls", None) or []
    if tool_calls:
        print(f"    PASS: model emitted {len(tool_calls)} tool call(s): "
              f"{[tc.get('name') for tc in tool_calls]}")
        return True
    print("    FAIL: no tool_calls in the response — model did not call the tool.")
    return False


def main() -> int:
    """Resolve config, run the two probes, and recommend a tool-calling mode."""
    load_dotenv()
    config = Config.from_env()

    print("HCode v2 — model probe")
    print("-" * 40)
    print(f"model    : {config.model}")
    print(f"base_url : {config.base_url or '(provider default)'}")
    print(f"api_key  : {_mask(config.api_key)}")

    import os

    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
    if not config.api_key and not config.base_url and not has_anthropic:
        print(
            "\nNo endpoint configured — nothing to probe.\n"
            "Set HCODE_MODEL_NAME / HCODE_MODEL_BASE_URL / HCODE_MODEL_API_KEY in .env\n"
            "(see .env.example), then re-run:  uv run python scripts/probe_model.py"
        )
        return 0

    model = _build_model()
    chat_ok = _check_chat(model)
    tool_ok = _check_tool_call(model)

    print("\n" + "-" * 40)
    if chat_ok and tool_ok:
        print("RESULT: native tool-calling works -> set HCODE_TOOLCALL_MODE=native")
        return 0
    if chat_ok and not tool_ok:
        print("RESULT: chat works but no native tool-calling -> use the JSON "
              "fallback (HCODE_TOOLCALL_MODE=json, issue A2)")
        return 1
    print("RESULT: endpoint not reachable / chat failed — check the config above.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
