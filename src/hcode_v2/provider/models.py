"""Live model discovery for the HCode v2 agent.

The agent can run against any OpenAI-compatible provider, but only a subset of a
provider's catalog is actually usable: the agent's plan/execute loop relies on
tool-calling, its prompts are large, and the enterprise demo wants *free*
models. This module fetches the provider's catalog and filters it down to the
models the agent can really drive.

Split into pure + impure halves so the filter is unit-testable without a network:

- ``filter_usable_models``  — pure: catalog list → ``[{id, name, context_length}]``
- ``fetch_models``          — impure: GET ``{base_url}/models`` with the bearer key
- ``fallback_models``       — pure: a never-empty list from ``HCODE_MODELS`` / the
                              configured ``HCODE_MODEL_NAME`` for when the fetch fails

SECURITY: the API key is sent ONLY in the request ``Authorization`` header. The
returned rows carry model **ids/names** and context length — never the key. The
key never appears in the result, so it cannot reach the ``list_models`` RPC
response, daemon-message events, or logs.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# The agent's prompts (skills + PEV phase prompts + env block) are large; a model
# with a tiny window truncates the plan and dead-ends. 32k is the floor that has
# held up for the plan→execute→verify loop.
_MIN_CONTEXT = 32_000


def _is_free(model: dict[str, Any]) -> bool:
    """True when the model's prompt price is zero.

    OpenRouter reports pricing as STRINGS ("0", "0.0000005"); other providers may
    use numbers or omit pricing entirely. Treat a missing/garbage price as
    NOT-free (conservative — never surface a model we can't prove is free).
    """
    pricing = model.get("pricing")
    if not isinstance(pricing, dict):
        return False
    prompt = pricing.get("prompt")
    try:
        return float(prompt) == 0.0
    except (TypeError, ValueError):
        return False


def _is_tool_capable(model: dict[str, Any]) -> bool:
    """True when the model advertises tool/function-calling support.

    CRITICAL: the agent's plan/execute uses tool-calling; a model without it
    breaks the loop entirely, so a model that does not list ``tools`` in its
    ``supported_parameters`` is excluded.
    """
    params = model.get("supported_parameters")
    return isinstance(params, list) and "tools" in params


def _context_length(model: dict[str, Any]) -> int:
    """Best-effort context window as an int (0 when unknown/garbage)."""
    raw = model.get("context_length") or model.get("context_window")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def filter_usable_models(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter a provider catalog to the agent-usable models.

    Keeps models that are simultaneously FREE, TOOL-CAPABLE, and have a context
    window >= 32k. Returns a clean, sorted, key-free list of
    ``{id, name, context_length}`` — ids/names ONLY, never any secret.
    """
    out: list[dict[str, Any]] = []
    for model in catalog:
        if not isinstance(model, dict):
            continue
        mid = model.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        ctx = _context_length(model)
        if not (_is_free(model) and _is_tool_capable(model) and ctx >= _MIN_CONTEXT):
            continue
        out.append({
            "id": mid,
            "name": model.get("name") or mid,
            "context_length": ctx,
        })
    # Stable, friendly ordering: widest context first, then id for determinism.
    out.sort(key=lambda m: (-m["context_length"], m["id"]))
    return out


async def fetch_models(base_url: str, api_key: str | None, timeout: float = 10.0) -> list[dict[str, Any]]:
    """GET ``{base_url}/models`` and return the raw catalog list.

    The bearer key (when present) goes ONLY in the Authorization header. Raises on
    any transport/HTTP/parse error so the caller can fall back — this function
    never swallows failures or returns a placeholder.
    """
    import httpx

    url = base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # OpenAI/OpenRouter wrap the catalog in {"data": [...]}; some endpoints return
    # a bare list. Accept both.
    if isinstance(data, dict):
        catalog = data.get("data") or data.get("models") or []
    elif isinstance(data, list):
        catalog = data
    else:
        catalog = []
    return [m for m in catalog if isinstance(m, dict)]


def fallback_models() -> list[dict[str, Any]]:
    """A never-empty model list for when the live fetch is unavailable.

    Sources, in order:
      1. ``HCODE_MODELS`` — comma-separated model ids (explicit operator override)
      2. ``HCODE_MODEL_NAME`` / ``HCODE_MODEL`` — the single configured model
      3. the built-in default (``gpt-4o-mini``)

    Context length is unknown offline, so it is reported as 0 (the UI shows the id
    without a window hint). The dropdown is therefore ALWAYS populated.
    """
    raw = os.getenv("HCODE_MODELS", "")
    ids = [s.strip() for s in raw.split(",") if s.strip()]
    if not ids:
        from hcode_v2.utils.config import Config
        ids = [Config.from_env().model]
    # De-dupe, preserve order.
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for mid in ids:
        if mid in seen:
            continue
        seen.add(mid)
        out.append({"id": mid, "name": mid, "context_length": 0})
    return out


__all__ = ["filter_usable_models", "fetch_models", "fallback_models"]
