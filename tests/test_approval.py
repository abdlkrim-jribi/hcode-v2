"""Unit tests for the HITL bash-approval pure helpers — SLICE (a).

Slice (a) of per-action human approval: bash-only, approve/reject, TTY-gated,
single tool call. The full interrupt/resume loop (astream_events parks at the
``execute``/``bash`` tool, the chat loop reads ``state.interrupts`` and resumes
with ``Command(resume=...)``) needs a live agent + checkpointer and is verified
live — NOT here.

What IS unit-testable are the three PURE helpers the loop is built from. They
live in ``hcode_v2.agent.approval`` (a small new module, sibling to
``path_containment.py``; imported by both ``factory.create_hcode_agent`` for the
``interrupt_on`` config and by ``cli/main.py`` for the prompt/resume wiring):

1. ``_approval_interrupt_on(enabled)`` — the TTY-gated config builder. Returns the
   ``interrupt_on`` dict keyed on BOTH the deepagents builtin ``execute`` tool
   (what the model actually calls for shell, per EXECUTION_SYSTEM_PROMPT) AND
   hcode's own ``bash`` tool, or ``None`` when approval is disabled (non-TTY:
   run/analyze/daemon must never park-and-hang).
2. ``_command_from_interrupt(value)`` — pulls the command string to SHOW the user
   out of a ``HITLRequest``-shaped interrupt value
   (``value["action_requests"][0]["args"]["command"]``), or ``""`` on any
   unexpected shape.
3. ``_decision_for_choice(choice, message="")`` — maps a user choice to a single
   resume decision dict: ``{"type": "approve"}`` or ``{"type": "reject"}`` (with
   an optional ``"message"`` key for reject-with-feedback).

These FAIL now: ``hcode_v2.agent.approval`` does not exist yet.
"""

from __future__ import annotations

from hcode_v2.agent.approval import (
    _approval_interrupt_on,
    _command_from_interrupt,
    _decision_for_choice,
)


# ── UNIT 1 — interrupt_on config builder (TTY gate) ─────────────────────────


def test_interrupt_on_enabled_has_execute_and_bash() -> None:
    """enabled=True → both ``execute`` and ``bash`` keys, each approve/reject."""
    result = _approval_interrupt_on(True)

    assert result is not None
    assert set(result) == {"execute", "bash"}
    for key in ("execute", "bash"):
        assert result[key]["allowed_decisions"] == ["approve", "reject"]


def test_interrupt_on_disabled_is_none() -> None:
    """enabled=False → None, so run/analyze/daemon never set interrupt_on."""
    assert _approval_interrupt_on(False) is None


# ── UNIT 2 — extract the command to show the user ───────────────────────────


def test_command_from_interrupt_reads_execute_command() -> None:
    """A HITLRequest-shaped value yields its first action's command string."""
    value = {
        "action_requests": [
            {"name": "execute", "args": {"command": "pytest x"}},
        ],
        "review_configs": [
            {"action_name": "execute", "allowed_decisions": ["approve", "reject"]},
        ],
    }

    assert _command_from_interrupt(value) == "pytest x"


def test_command_from_interrupt_missing_returns_empty() -> None:
    """Empty or malformed values degrade to "" rather than raising."""
    assert _command_from_interrupt({}) == ""
    assert _command_from_interrupt({"action_requests": []}) == ""
    assert _command_from_interrupt({"action_requests": [{"name": "execute"}]}) == ""
    assert _command_from_interrupt({"action_requests": [{"args": {}}]}) == ""


# ── UNIT 3 — map a user choice to a resume decision ─────────────────────────


def test_decision_approve() -> None:
    """approve → bare approve decision."""
    assert _decision_for_choice("approve") == {"type": "approve"}


def test_decision_reject() -> None:
    """reject without feedback → bare reject decision (no message key)."""
    assert _decision_for_choice("reject") == {"type": "reject"}


def test_decision_reject_with_message() -> None:
    """reject with feedback → reject decision carrying the message."""
    assert _decision_for_choice("reject", "no") == {"type": "reject", "message": "no"}
