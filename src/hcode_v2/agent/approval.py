"""Pure helpers for HITL bash-approval — SLICE (a).

Slice (a) of per-action human approval: bash-only, approve/reject, TTY-gated,
single tool call. This module holds only the PURE pieces of that flow so they can
be unit-tested in isolation; the full interrupt/resume loop (astream_events parks
at the shell tool, the chat loop reads ``state.interrupts`` and resumes with
``Command(resume=...)``) is wired in ``factory.py`` + ``cli/main.py`` and verified
live.

Three helpers:

* :func:`_approval_interrupt_on` — the TTY-gated ``interrupt_on`` config builder.
* :func:`_command_from_interrupt` — extract the command string to show the user.
* :func:`_decision_for_choice` — map a user choice to a resume decision dict.
"""

from __future__ import annotations

from typing import Any

# Slice (a): bash-only approve/reject. The model's shell tool is the deepagents
# builtin "execute"; "bash" is hcode's own, gated too as belt-and-suspenders.
_SHELL_DECISIONS = ["approve", "reject"]


def _approval_interrupt_on(enabled: bool) -> dict[str, Any] | None:
    """interrupt_on config for create_deep_agent: gate execute+bash on approve/
    reject when enabled (interactive TTY), else None (no parking — run/daemon)."""
    if not enabled:
        return None
    return {
        "execute": {"allowed_decisions": list(_SHELL_DECISIONS)},
        "bash": {"allowed_decisions": list(_SHELL_DECISIONS)},
    }


def _command_from_interrupt(value: Any) -> str:
    """Pull the shell command string from an interrupt payload (HITLRequest):
    value["action_requests"][0]["args"]["command"]. Returns "" if the shape is
    missing/unexpected (never raises)."""
    try:
        reqs = value.get("action_requests") or []
        args = (reqs[0] or {}).get("args") or {}
        cmd = args.get("command")
        return cmd if isinstance(cmd, str) else ""
    except (AttributeError, IndexError, TypeError):
        return ""


def _decision_for_choice(choice: str, message: str = "") -> dict[str, Any]:
    """Map a user choice to a resume Command decision dict.
    "approve"/"accept"/"always" -> {"type":"approve"} (all RUN this command; the
    chat loop's prompt yes-token is "accept" and the always-auto path also sends
    "accept", while the "always" session effect lives in the chat loop, not the
    decision); anything else -> {"type":"reject"} (+ message if given)."""
    if choice in ("approve", "accept", "always"):
        return {"type": "approve"}
    decision: dict[str, Any] = {"type": "reject"}
    if message:
        decision["message"] = message
    return decision
