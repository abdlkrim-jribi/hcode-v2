"""Typed model configuration for HCode v2.

`Config` is the single source of truth for which model the agent talks to and how
to reach it. It resolves three values from the environment:

- **model** — the model name/string to request.
- **api_key** — the bearer token for the OpenAI-compatible endpoint.
- **base_url** — the endpoint base URL (``None`` means the provider default).

Each value prefers the canonical ``HCODE_MODEL_*`` variable and falls back to the
older ``OPENAI_*`` / ``HCODE_MODEL`` variables that are already in active use, so
existing setups keep working unchanged.

The Anthropic path (selected on ``ANTHROPIC_API_KEY``) is handled in the model
factory, not here — this config describes the OpenAI-compatible endpoint only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_MODEL = "gpt-4o-mini"


@dataclass(frozen=True)
class Config:
    """Resolved model configuration.

    Attributes:
        model: Model name/string to request (e.g. ``gpt-oss-120b``).
        api_key: API key for the OpenAI-compatible endpoint, or ``None`` if unset.
        base_url: Endpoint base URL, or ``None`` to use the provider default.
    """

    model: str
    api_key: str | None
    base_url: str | None

    @classmethod
    def from_env(cls) -> Config:
        """Build a `Config` from environment variables.

        Resolution order (first non-empty wins):

        - model: ``HCODE_MODEL_NAME`` → ``HCODE_MODEL`` → ``"gpt-4o-mini"``
        - api_key: ``HCODE_MODEL_API_KEY`` → ``OPENAI_API_KEY`` → ``None``
        - base_url: ``HCODE_MODEL_BASE_URL`` → ``OPENAI_BASE_URL`` → ``None``

        Returns:
            A frozen `Config` with the resolved model, api_key, and base_url.
        """
        model = os.getenv("HCODE_MODEL_NAME") or os.getenv("HCODE_MODEL") or _DEFAULT_MODEL
        api_key = os.getenv("HCODE_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY") or None
        base_url = os.getenv("HCODE_MODEL_BASE_URL") or os.getenv("OPENAI_BASE_URL") or None
        return cls(model=model, api_key=api_key, base_url=base_url)


__all__ = ["Config"]
