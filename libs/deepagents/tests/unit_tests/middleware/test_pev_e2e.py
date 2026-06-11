"""E2E happy-path baseline for the PEV cycle (PR-PEV-2, Step 0).

Drives the REAL ``create_deep_agent`` stack (PEV + the full factory middleware
list) with a scripted fake model through a complete plan -> execute -> verify
cycle. No network, no real model.

This is the tripwire for the PEV hardening steps: it must stay green while
``_compute_next_state`` / the breakers are modified. The script is sized to
pass under the CURRENT guards (5 model calls fits ``_MAX_ITERATIONS = 5``;
tool-call turns carry distinct non-empty content so the empty-content loop
detector stays quiet — the hardening steps add variants that stress both).
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from deepagents import create_deep_agent
from deepagents.middleware.hcode_skills import HCodeSkillsMiddleware
from deepagents.middleware.pev import PEVMiddleware
from deepagents.middleware.safety_guard import SafetyGuardMiddleware
from deepagents.middleware.workflows import WorkflowMiddleware

# "create" keyword -> TaskClassifier says complex -> initial phase "plan".
TASK = "create calc.py with add(a,b) and a test for it in test_calc.py"

STUB_TOOL_NAMES = {"write", "read", "ls", "glob", "grep"}
VERIFY_READONLY_NAMES = {"read", "ls", "glob", "grep"}


# ── Stub tools (names match the real hcode registry / PEV verify filter) ─────


@tool
def write(path: str, content: str = "") -> str:
    """Write a file (stub — performs no I/O)."""
    return f"wrote {path}"


@tool
def read(path: str) -> str:
    """Read a file (stub)."""
    return f"contents of {path}"


@tool
def ls(path: str = ".") -> str:
    """List a directory (stub)."""
    return "calc.py"


@tool
def glob(pattern: str) -> str:
    """Find files by pattern (stub)."""
    return "calc.py"


@tool
def grep(pattern: str) -> str:
    """Search file contents (stub)."""
    return "no matches"


# ── Scripted model ────────────────────────────────────────────────────────────


def _happy_path_script() -> list[AIMessage]:
    return [
        # (1) plan phase — numbered plan + marker, no tool calls
        AIMessage(
            content="1. Create calc.py with add(a, b) using write.\n"
            "2. Create test_calc.py with a test using write.\nPLAN COMPLETE"
        ),
        # (2)-(3) execute phase — one write per planned step
        AIMessage(
            content="Creating calc.py.",
            tool_calls=[{
                "name": "write",
                "args": {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"},
                "id": "call-1",
                "type": "tool_call",
            }],
        ),
        AIMessage(
            content="Creating test_calc.py.",
            tool_calls=[{
                "name": "write",
                "args": {"path": "test_calc.py", "content": "from calc import add\n"},
                "id": "call-2",
                "type": "tool_call",
            }],
        ),
        # (4) execute phase — done marker
        AIMessage(content="Both files are in place.\nEXECUTION COMPLETE"),
        # (5) verify phase — verdict
        AIMessage(content="Both planned steps completed correctly.\nVERIFIED OK"),
    ]


def _empty_tool_turns_script() -> list[AIMessage]:
    """Execute phase done entirely via tool-call-only turns with EMPTY content.

    Realistic for tool-heavy models: no prose between tool calls. The three
    identical empty contents must NOT register as a loop.
    """

    def _write(idx: int, path: str) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[{
                "name": "write",
                "args": {"path": path, "content": f"# file {idx}\n"},
                "id": f"call-{idx}",
                "type": "tool_call",
            }],
        )

    return [
        AIMessage(
            content="1. Create calc.py.\n2. Create test_calc.py.\n"
            "3. Create README.md.\nPLAN COMPLETE"
        ),
        _write(1, "calc.py"),
        _write(2, "test_calc.py"),
        _write(3, "README.md"),
        AIMessage(content="All three files are in place.\nEXECUTION COMPLETE"),
        AIMessage(content="All planned steps completed correctly.\nVERIFIED OK"),
    ]


def _long_execute_script(rounds: int = 8) -> list[AIMessage]:
    """A realistic multi-step task: plan, then ``rounds`` execute tool turns.

    Total model calls = rounds + 3, well past the old total-of-5 cap. Each
    turn carries distinct prose so only the iteration guard is exercised,
    not the loop detector.
    """
    script = [
        AIMessage(content="1. Create the files.\n2. Wire them together.\nPLAN COMPLETE"),
    ]
    for idx in range(1, rounds + 1):
        script.append(
            AIMessage(
                content=f"Step {idx}: writing file {idx}.",
                tool_calls=[{
                    "name": "write",
                    "args": {"path": f"file_{idx}.py", "content": f"# step {idx}\n"},
                    "id": f"call-{idx}",
                    "type": "tool_call",
                }],
            )
        )
    script.append(AIMessage(content="All steps are done.\nEXECUTION COMPLETE"))
    script.append(AIMessage(content="Everything checks out.\nVERIFIED OK"))
    return script


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


class ScriptedModel(BaseChatModel):
    """Pops scripted responses; records bound tools + system prompt per call."""

    script: list[AIMessage] = Field(default_factory=_happy_path_script)
    records: list[dict] = Field(default_factory=list)
    pending_tools: list[str] | None = None

    @property
    def _llm_type(self) -> str:
        return "pev-e2e-scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> BaseChatModel:
        self.pending_tools = [getattr(t, "name", str(t)) for t in tools]
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        idx = len(self.records)
        system_text = ""
        if messages and getattr(messages[0], "type", "") == "system":
            system_text = _text(messages[0].content)
        self.records.append({"tools": self.pending_tools, "system": system_text})
        self.pending_tools = None
        message = self.script[min(idx, len(self.script) - 1)]
        return ChatResult(generations=[ChatGeneration(message=message)])


# ── Harness ───────────────────────────────────────────────────────────────────


async def _run_happy_path(
    script: list[AIMessage] | None = None,
    thread_id: str = "pev-e2e-happy",
) -> tuple[dict, list[dict]]:
    """Run a scripted cycle through the real agent; return (result, records)."""
    model = ScriptedModel(script=script) if script is not None else ScriptedModel()
    agent = create_deep_agent(
        model=model,
        tools=[write, read, ls, glob, grep],
        # Same middleware list/order as hcode_v2's factory (factory.py:102-110);
        # skills/workflows dirs point nowhere so the run is hermetic.
        middleware=[
            PEVMiddleware(),
            SafetyGuardMiddleware(),
            HCodeSkillsMiddleware(skills_dir="nonexistent-skills"),
            WorkflowMiddleware(workflows_dir="nonexistent-workflows"),
        ],
        checkpointer=MemorySaver(),
    )
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=TASK)]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result, model.records


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestPEVHappyPathE2E:
    async def test_cycle_completes_with_all_phases(self) -> None:
        result, records = await _run_happy_path()

        # All five scripted calls were consumed — no premature jump_to "end",
        # no recursion error, no extra calls.
        assert len(records) == 5

        ai_messages = [m for m in result["messages"] if getattr(m, "type", "") == "ai"]
        assert len(ai_messages) == 5
        assert "VERIFIED OK" in _text(ai_messages[-1].content)
        # The two execute tool calls actually ran.
        tool_messages = [m for m in result["messages"] if getattr(m, "type", "") == "tool"]
        assert len(tool_messages) == 2

    async def test_phase_prompt_sequence(self) -> None:
        _, records = await _run_happy_path()
        assert len(records) == 5

        phases = []
        for record in records:
            system = record["system"]
            present = [
                name
                for marker, name in (
                    ("PEV Planning Phase", "plan"),
                    ("PEV Execution Phase", "execute"),
                    ("PEV Verification Phase", "verify"),
                )
                if marker in system
            ]
            # exactly one phase prompt per model call
            assert len(present) == 1, f"expected one phase prompt, got {present}"
            phases.append(present[0])

        assert phases == ["plan", "execute", "execute", "execute", "verify"]

    async def test_tool_binding_per_phase(self) -> None:
        _, records = await _run_happy_path()
        assert len(records) == 5

        # (1) plan: no tools bound at all (bind_tools never called)
        assert records[0]["tools"] is None

        # (2)-(4) execute: the full toolset is back — all stubs present plus
        # middleware-injected tools (write_todos etc.)
        for record in records[1:4]:
            bound = set(record["tools"] or [])
            assert STUB_TOOL_NAMES <= bound
            assert "write_todos" in bound

        # (5) verify: only the read-only set survives the PEV filter
        assert set(records[4]["tools"] or []) == VERIFY_READONLY_NAMES


class TestPEVEmptyContentToolTurnsE2E:
    async def test_consecutive_empty_tool_turns_do_not_trip_loop_detector(self) -> None:
        # Regression (PR-PEV-2, item 2b): three consecutive tool-call-only AI
        # messages with empty content hash identically; the loop detector must
        # not count them as a repeated-response loop and kill the run.
        result, records = await _run_happy_path(
            script=_empty_tool_turns_script(), thread_id="pev-e2e-empty-tools"
        )

        # All six scripted calls consumed — no premature "loop detected" end.
        assert len(records) == 6
        ai_messages = [m for m in result["messages"] if getattr(m, "type", "") == "ai"]
        assert "VERIFIED OK" in _text(ai_messages[-1].content)
        # All three tool calls actually ran.
        tool_messages = [m for m in result["messages"] if getattr(m, "type", "") == "tool"]
        assert len(tool_messages) == 3


class TestPEVLongExecuteE2E:
    async def test_multi_step_execute_phase_is_not_truncated(self) -> None:
        # Regression (PR-PEV-2, item 2a): _pev_iteration counted TOTAL model
        # calls across all phases against _MAX_ITERATIONS = 5, so a real
        # multi-step execute phase (8 tool rounds here, 11 model calls total)
        # was silently cut off mid-execute. The cap must apply per phase.
        script = _long_execute_script(rounds=8)
        result, records = await _run_happy_path(script=script, thread_id="pev-e2e-long-execute")

        # Every scripted call was consumed — the run reached the verify verdict.
        assert len(records) == len(script)
        ai_messages = [m for m in result["messages"] if getattr(m, "type", "") == "ai"]
        assert "VERIFIED OK" in _text(ai_messages[-1].content)
        # All eight execute tool calls actually ran.
        tool_messages = [m for m in result["messages"] if getattr(m, "type", "") == "tool"]
        assert len(tool_messages) == 8
