"""Prompt slimming — phase-aware trimming of the assembled system prompt.

Measured problem (M1, tiktoken o200k on the real assembled requests): HCode's
model calls carried ~4.4k (plan) / ~10.3k (execute) / ~5.0k (verify) input
tokens, and Groq's free tier counts input + ``max_tokens`` against an 8,000
tokens-per-request cap — so gpt-oss-120b (the enterprise-aligned target) 413'd
on every real call. The bulk was NOT the project map: it was 2.4k of skills
injected into EVERY phase (including plan, where no tools exist, and verify,
which is read-only), ~1.1k of vendored middleware prompts mostly describing
tools HCode EXCLUDES (write_todos / read_file / write_file / edit_file /
execute), and a 1.7k-schema `task` subagent tool that has never been part of
HCode's product surface.

``PromptSlimMiddleware`` runs LAST in the middleware chain (same ordering
guarantee HarnessNotesMiddleware documents: later-registered wrap_model_call
sees the fully-assembled system message) and, per PEV phase:

  level "1" (default — strictly quality-neutral):
    - phase-scopes the skills section: plan keeps only planning guidance,
      verify keeps none (its vendored prompt is prescriptive and the phase is
      read-only), execute/fast keep the working set;
    - excises the vendored sections that describe excluded tools — text the
      HarnessNotesMiddleware note already tells the model to ignore (we stop
      paying twice);
    - (the factory also drops the unused `task` subagent tool, whose prompt
      sections are excised here).

  level "max" (the free-tier lane, e.g. Groq's 8k tokens-per-request cap):
    - additionally caps ``max_tokens`` per phase via ``model_settings`` —
      Groq counts the completion budget toward the per-request limit, so the
      global HCODE_MAX_TOKENS=8000 alone made even a 1.2k-input call 413;
    - (the factory also drops the notebook/web tool trios from the menu).

  level "0": middleware absent — byte-identical pre-slim behaviour.

Never edits vendored files: all trimming happens on the in-flight request via
``request.override``, the established HCode-side pattern (HarnessNotes /
PlanReview / ForcePlan).
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse

# ── configuration ─────────────────────────────────────────────────────────────

_ON = ("1", "true", "yes", "on")


def slim_level() -> str:
    """Resolve HCODE_SLIM_PROMPT to '0' | '1' | 'max' (default '1')."""
    raw = os.getenv("HCODE_SLIM_PROMPT", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return "0"
    if raw == "max":
        return "max"
    return "1"


def slim_excluded_tools(level: str) -> frozenset[str]:
    """Extra tool-menu exclusions per slim level (factory feeds these into the
    existing _ToolExclusionMiddleware — the same mechanism that already strips
    read_file/execute/write_todos).

    - level >= 1: the `task` subagent spawner — 1,664 tokens of schema plus 529
      tokens of prompt for a deepagents rider capability that HCode's product
      surface (35 first-party tools), demos, and probes have never used; a
      mid-daemon subagent would also bypass the event bridge (opaque UX).
    - level max: the notebook and web trios — real but non-core capabilities,
      traded for fitting the free-tier per-request cap. Restore with
      HCODE_SLIM_PROMPT=1 (keeps them) or 0.
    """
    if level == "0":
        return frozenset()
    excluded = {"task"}
    if level == "max":
        excluded |= {
            "notebook_read", "notebook_edit", "notebook_execute",
            "web_search", "web_fetch", "web_scrape",
        }
    return frozenset(excluded)


# Skills kept per phase (names = skill directory names). Everything else in the
# "## HCode Skills" section is dropped for that phase.
#   plan:   tools are stripped (pev.py binds none) — guidance about running
#           tests / writing code / debugging is unusable; keep planning craft.
#   verify: read-only, and the vendored verify prompt fully prescribes the
#           verdict protocol — skills add nothing actionable.
#   execute/fast: the working set — everything except planning craft (already
#           consumed) and code-review (a review-lane skill, not an authoring one).
_PLAN_KEEP: frozenset[str] = frozenset({"concise-planning"})
_VERIFY_KEEP: frozenset[str] = frozenset()
_EXECUTE_DROP: frozenset[str] = frozenset({"concise-planning", "code-review"})

# Vendored prompt sections to excise — each describes tools HCode excludes from
# the menu (see factory._EXCLUDED_BUILTIN_TOOLS + slim_excluded_tools) or a
# mechanism keyed to those tools. Exact top-level headers, matched at
# line-start; a section runs to the next top-level "## " header. The vendored
# tree is pinned (CLAUDE.md Rule 14), so these anchors are stable.
# "## Following Conventions" is deliberately NOT here (alive, 30 tokens).
_DEAD_SECTION_HEADERS: tuple[str, ...] = (
    "## `write_todos`",
    "## Important To-Do List Usage Notes to Remember",
    "## Filesystem Tools `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`",
    "## Large Tool Results",
    "## Execute Tool `execute`",
    "## `task` (subagent spawner)",
    "## Important Task Tool Usage Notes to Remember",
)

_SKILLS_HEADER = "## HCode Skills"

# The skills section can't end at just any "## " line — skill CONTENT contains
# its own "## " markdown headers (e.g. clean-code's "## Names"). It ends where
# the next middleware's appended text begins. In factory order, the appenders
# after the skills middleware are the harness note and PEV's phase prompt
# (WorkflowMiddleware only injects "## Workflow Execution" mid-workflow-run).
_SKILLS_END_ANCHORS: tuple[str, ...] = (
    "\n## Path & Tool Convention",
    "\n## PEV ",
    "\n## Workflow Execution",
)

# A skill delimiter line is exactly "### <dir-name>" where dir names are kebab
# slugs (hcode_skills.py builds f"### {skill['name']}"). Skill-internal ###
# headers ("### Example usage") contain spaces/uppercase and don't match.
_SKILL_DELIM = re.compile(r"(?m)^### (?=[a-z0-9][a-z0-9-]*$)")


def _skills_region(text: str) -> tuple[int, int] | None:
    """(start, end) of the skills section incl. header, or None if absent."""
    idx = text.find(f"\n{_SKILLS_HEADER}")
    if idx >= 0:
        start = idx + 1
    elif text.startswith(_SKILLS_HEADER):
        start = 0
    else:
        return None
    search_from = start + len(_SKILLS_HEADER)
    end = len(text)
    for anchor in _SKILLS_END_ANCHORS:
        pos = text.find(anchor, search_from)
        if pos >= 0:
            end = min(end, pos + 1)  # keep the leading newline with the successor
    return (start, end)

# Phase-aware completion caps (level "max" only). Groq's free tier counts
# max_tokens toward the 8,000 tokens-per-request cap, so the global
# HCODE_MAX_TOKENS (default 8000) must shrink per call. Reasoning models spend
# budget on reasoning tokens before visible text — plan gets the most headroom.
# Env-overridable for tuning without a code change.
def _cap(env: str, default: int) -> int:
    try:
        return int(os.getenv(env, str(default)))
    except ValueError:
        return default


def _phase_caps() -> dict[str, int]:
    return {
        "plan": _cap("HCODE_SLIM_MAXTOK_PLAN", 2048),
        "execute": _cap("HCODE_SLIM_MAXTOK_EXECUTE", 1280),
        "fast": _cap("HCODE_SLIM_MAXTOK_EXECUTE", 1280),
        "trivial": _cap("HCODE_SLIM_MAXTOK_EXECUTE", 1280),
        "verify": _cap("HCODE_SLIM_MAXTOK_VERIFY", 768),
    }


# ── text surgery helpers (pure, no-op on missing anchors) ─────────────────────

def _drop_section(text: str, header: str) -> str:
    """Remove a top-level section (header line through the char before the next
    top-level '## ' header, or end of text). No-op if the header is absent."""
    idx = text.find(f"\n{header}")
    if idx < 0:
        # also handle a section at the very start of the text
        if text.startswith(header):
            idx = 0
        else:
            return text
    else:
        idx += 1  # point at the '#' of the header line
    nxt = re.compile(r"(?m)^## ").search(text, idx + len(header))
    end = nxt.start() if nxt else len(text)
    return text[:idx] + text[end:]


def _filter_skills(text: str, keep: frozenset[str]) -> str:
    """Within the '## HCode Skills' section, keep only the '### <name>' blocks
    whose name is in ``keep``. An empty ``keep`` drops the whole section."""
    region = _skills_region(text)
    if region is None:
        return text
    start, end = region
    section = text[start:end]

    if not keep:
        return text[:start] + text[end:]

    # Split the section into the header preamble + '### <slug>' blocks (skill
    # delimiters only — skill-internal ### headers don't match _SKILL_DELIM).
    blocks = _SKILL_DELIM.split(section)
    preamble = blocks[0]
    kept = [b for b in blocks[1:] if b.split("\n", 1)[0].strip() in keep]
    if not kept:
        return text[:start] + text[end:]
    rebuilt = preamble + "".join(f"### {b}" for b in kept)
    return text[:start] + rebuilt + text[end:]


def slim_system_text(text: str, phase: str) -> str:
    """Apply the level-1 text cuts for ``phase`` to an assembled system prompt."""
    for header in _DEAD_SECTION_HEADERS:
        text = _drop_section(text, header)
    if phase == "plan":
        keep = _PLAN_KEEP
    elif phase == "verify":
        keep = _VERIFY_KEEP
    else:  # execute / fast / trivial / unknown → the working set
        return _filter_skills_drop(text, _EXECUTE_DROP)
    return _filter_skills(text, keep)


def _filter_skills_drop(text: str, drop: frozenset[str]) -> str:
    """Complement of _filter_skills: remove the named blocks, keep the rest."""
    region = _skills_region(text)
    if region is None:
        return text
    start, end = region
    section = text[start:end]
    blocks = _SKILL_DELIM.split(section)
    preamble = blocks[0]
    kept = [b for b in blocks[1:] if b.split("\n", 1)[0].strip() not in drop]
    if not kept:
        return text[:start] + text[end:]
    rebuilt = preamble + "".join(f"### {b}" for b in kept)
    return text[:start] + rebuilt + text[end:]


# ── the middleware ────────────────────────────────────────────────────────────

class PromptSlimMiddleware(AgentMiddleware[Any, Any, Any]):
    """Slim the assembled system prompt (and, at level 'max', the completion
    budget) per PEV phase. Register LAST so it sees the final prompt text."""

    def __init__(self, level: str = "1") -> None:
        self._level = level

    def _slim(self, request: "ModelRequest[Any]") -> "ModelRequest[Any]":
        try:
            phase: str = request.state.get("_pev_phase", "fast")
            overrides: dict[str, Any] = {}

            sm = request.system_message
            if sm is not None:
                content = sm.content
                text = content if isinstance(content, str) else "".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in content
                )
                slimmed = slim_system_text(text, phase)
                if slimmed != text:
                    overrides["system_message"] = sm.model_copy(update={"content": slimmed})

            if self._level == "max":
                cap = _phase_caps().get(phase, _phase_caps()["fast"])
                settings = dict(request.model_settings or {})
                settings["max_tokens"] = cap
                overrides["model_settings"] = settings

            return request.override(**overrides) if overrides else request
        except Exception:
            # Slimming must never break a model call — degrade to the full prompt.
            return request

    def wrap_model_call(
        self,
        request: "ModelRequest[Any]",
        handler: "Callable[[ModelRequest[Any]], ModelResponse[Any]]",
    ) -> "ModelResponse[Any]":
        return handler(self._slim(request))

    async def awrap_model_call(
        self,
        request: "ModelRequest[Any]",
        handler: "Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]]",
    ) -> "ModelResponse[Any]":
        return await handler(self._slim(request))


__all__ = ["PromptSlimMiddleware", "slim_level", "slim_excluded_tools", "slim_system_text"]
