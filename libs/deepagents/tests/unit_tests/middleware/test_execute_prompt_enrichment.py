"""Pin the ENRICHED execute-phase prompt body so it can't silently regress.

These tests assert against the EXECUTE phase system text only — the text PEV
injects via ``wrap_model_call`` when ``_pev_phase == "execute"``. They reuse the
same injection/capture style as ``test_pev_middleware.py`` (build a
``ModelRequest`` with ``system_message=None``, run ``wrap_model_call`` with a
capturing handler, then read the joined text blocks of the resulting system
message).

They say nothing about the plan or verify phases, and they re-check the FROZEN
execute invariants (header present, marker present, no other phase header leaks
in) alongside the new enrichment anchors.

Expected to FAIL until ``_EXECUTE_PROMPT`` is enriched — that is the point.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage

from deepagents.middleware.pev import PEVMiddleware


# ── Helpers (mirrors test_pev_middleware.py capture style) ────────────────────


def _make_mock_runtime() -> MagicMock:
    runtime = MagicMock()
    runtime.context = {}
    runtime.store = None
    del runtime.config
    return runtime


def _make_mock_model() -> MagicMock:
    model = MagicMock()
    model._llm_type = "test-model"
    model.profile = {"max_input_tokens": 100000}
    model._get_ls_params.return_value = {"ls_provider": "test"}
    return model


def _make_execute_state() -> dict[str, Any]:
    return {
        "messages": [],
        "_pev_phase": "execute",
        "_pev_iteration": 0,
        "_pev_error_count": 0,
        "_pev_plan": None,
        "_pev_recent_hashes": [],
        "_pev_task": None,
    }


def _execute_system_text() -> str:
    """Return the system text PEV injects for the execute phase."""
    request = ModelRequest(
        model=_make_mock_model(),
        messages=[],
        system_message=None,
        runtime=_make_mock_runtime(),
        state=_make_execute_state(),
    )
    captured: list[ModelRequest] = []

    def handler(req: ModelRequest) -> AIMessage:
        captured.append(req)
        return AIMessage(content="ok")

    PEVMiddleware().wrap_model_call(request, handler)
    assert captured, "wrap_model_call did not invoke the handler"

    sm = captured[0].system_message
    if sm is None:
        return ""
    return " ".join(
        b.get("text", "") for b in sm.content_blocks if b.get("type") == "text"
    )


# ── Enrichment anchors (case-sensitive, exactly as written) ───────────────────

_ENRICHMENT_ANCHORS = (
    "working directory",   # operate in the real working dir
    "Read",                # read before edit
    "before",              # (read ... before editing)
    "conventions",         # follow existing code conventions
    "already use",         # check a library is already used before assuming
    "root cause",          # on repeated failure, find the root cause
    "output text",         # communicate in output text, not tool calls/comments
    "EXECUTION COMPLETE",  # persistence ends only on the marker
)


def test_execute_prompt_contains_each_enrichment_anchor() -> None:
    text = _execute_system_text()
    missing = [a for a in _ENRICHMENT_ANCHORS if a not in text]
    assert not missing, f"execute prompt is missing enrichment anchors: {missing}"


# ── Frozen execute invariants ─────────────────────────────────────────────────


def test_execute_prompt_keeps_header_and_marker() -> None:
    text = _execute_system_text()
    assert "PEV Execution Phase" in text  # header (FROZEN)
    assert "EXECUTION COMPLETE" in text   # marker (FROZEN)


def test_execute_prompt_does_not_leak_other_phase_headers() -> None:
    text = _execute_system_text()
    assert "PEV Planning Phase" not in text
    assert "PEV Verification Phase" not in text
