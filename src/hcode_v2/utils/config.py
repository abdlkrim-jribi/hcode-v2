"""Typed model configuration for HCode v2.

`Config` is the single source of truth for which model the agent talks to and how
to reach it. It resolves values from the environment with a documented precedence:

- **model** — the model name/string to request.
- **api_key** — the bearer token for the OpenAI-compatible endpoint.
- **base_url** — the endpoint base URL (``None`` means the provider default).
- **toolcall_mode** — how to bind tools: ``"native"`` (default, standard function-calling),
  ``"json"`` (inject JSON instruction + text-parse responses), or
  ``"auto"`` (inject instruction, prefer native tool_calls, fall back to JSON parsing).

Each value prefers the canonical ``HCODE_MODEL_*`` variable and falls back to the
older ``OPENAI_*`` / ``HCODE_MODEL`` variables that are already in active use.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

_DEFAULT_MODEL = "gpt-4o-mini"
ToolcallMode = Literal["native", "json", "auto"]


@dataclass(frozen=True)
class Config:
    """Resolved model configuration.

    Attributes:
        model: Model name/string to request (e.g. ``gpt-oss-120b``).
        api_key: API key for the OpenAI-compatible endpoint, or ``None`` if unset.
        base_url: Endpoint base URL, or ``None`` to use the provider default.
        toolcall_mode: Tool-calling strategy — ``"native"``, ``"json"``, or ``"auto"``.
    """

    model: str
    api_key: str | None
    base_url: str | None
    toolcall_mode: ToolcallMode

    @classmethod
    def from_env(cls) -> Config:
        """Build a `Config` from environment variables.

        Resolution order (first non-empty wins):

        - model: ``HCODE_MODEL_NAME`` → ``HCODE_MODEL`` → ``"gpt-4o-mini"``
        - api_key: ``HCODE_MODEL_API_KEY`` → ``OPENAI_API_KEY`` → ``None``
        - base_url: ``HCODE_MODEL_BASE_URL`` → ``OPENAI_BASE_URL`` → ``None``
        - toolcall_mode: ``HCODE_TOOLCALL_MODE`` → ``"native"``

        Returns:
            A frozen `Config` with resolved fields.
        """
        model = os.getenv("HCODE_MODEL_NAME") or os.getenv("HCODE_MODEL") or _DEFAULT_MODEL
        api_key = os.getenv("HCODE_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY") or None
        base_url = os.getenv("HCODE_MODEL_BASE_URL") or os.getenv("OPENAI_BASE_URL") or None
        raw_mode = os.getenv("HCODE_TOOLCALL_MODE", "native").lower().strip()
        toolcall_mode: ToolcallMode = raw_mode if raw_mode in ("native", "json", "auto") else "native"  # type: ignore[assignment]
        return cls(model=model, api_key=api_key, base_url=base_url, toolcall_mode=toolcall_mode)


__all__ = ["Config", "ToolcallMode"]
