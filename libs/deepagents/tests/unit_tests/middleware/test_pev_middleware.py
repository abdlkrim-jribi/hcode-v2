"""Unit tests for PEV (Plan-Execute-Verify) middleware."""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import MagicMock

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage

from deepagents.middleware.pev import PEVMiddleware
from deepagents.middleware.task_classifier import TaskClassifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_runtime() -> MagicMock:
    runtime = MagicMock()
    runtime.context = {}
    runtime.store = None
    del runtime.config
    return runtime


def make_mock_model() -> MagicMock:
    model = MagicMock()
    model._llm_type = "test-model"
    model.profile = {"max_input_tokens": 100000}
    model._get_ls_params.return_value = {"ls_provider": "test"}
    return model


def make_mock_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    return tool


def make_model_request(
    state: dict[str, Any],
    runtime: Any,
    *,
    tools: list | None = None,
) -> ModelRequest:
    kwargs: dict[str, Any] = {
        "model": make_mock_model(),
        "messages": state.get("messages", []),
        "system_message": None,
        "runtime": runtime,
        "state": state,
    }
    if tools is not None:
        kwargs["tools"] = tools
    return ModelRequest(**kwargs)


def make_pev_state(
    messages: list | None = None,
    *,
    phase: str = "plan",
    iteration: int = 0,
    error_count: int = 0,
    plan: str | None = None,
    recent_hashes: list[str] | None = None,
    task: str | None = None,
) -> dict[str, Any]:
    return {
        "messages": messages or [],
        "_pev_phase": phase,
        "_pev_iteration": iteration,
        "_pev_error_count": error_count,
        "_pev_plan": plan,
        "_pev_recent_hashes": recent_hashes or [],
        "_pev_task": task,
    }


# ---------------------------------------------------------------------------
# TaskClassifier
# ---------------------------------------------------------------------------


class TestTaskClassifier:
    def setup_method(self) -> None:
        self.classifier = TaskClassifier()

    def test_trivial_short_question(self) -> None:
        assert self.classifier.classify("what is python?") == "trivial"

    def test_trivial_starts_with_list(self) -> None:
        assert self.classifier.classify("list all users in the database") == "trivial"

    def test_trivial_starts_with_get(self) -> None:
        assert self.classifier.classify("get the current version") == "trivial"

    def test_complex_implement(self) -> None:
        assert self.classifier.classify("implement a REST API for user management") == "complex"

    def test_complex_refactor(self) -> None:
        assert self.classifier.classify("refactor the authentication module") == "complex"

    def test_complex_debug(self) -> None:
        assert self.classifier.classify("debug the failing integration tests") == "complex"

    def test_simple_short_task(self) -> None:
        assert self.classifier.classify("write a note") == "simple"

    def test_moderate_long_task_no_complex_keywords(self) -> None:
        # >15 words, no complex keywords → moderate
        task = "review this approach and tell me if the logic is sound for the given context please"
        assert self.classifier.classify(task) == "moderate"

    def test_requires_pev_true_for_complex(self) -> None:
        assert self.classifier.requires_pev("implement a new feature") is True

    def test_requires_pev_false_for_trivial(self) -> None:
        assert self.classifier.requires_pev("what is the meaning of life?") is False

    def test_requires_pev_false_for_simple(self) -> None:
        assert self.classifier.requires_pev("write a note") is False

    def test_get_initial_phase_plan_for_complex(self) -> None:
        assert self.classifier.get_initial_phase("implement a parser") == "plan"

    def test_get_initial_phase_fast_for_simple(self) -> None:
        assert self.classifier.get_initial_phase("write a note") == "fast"

    def test_get_initial_phase_trivial_for_trivial(self) -> None:
        assert self.classifier.get_initial_phase("what is python?") == "trivial"


# ---------------------------------------------------------------------------
# PEVMiddleware.before_agent — phase initialisation
# ---------------------------------------------------------------------------


class TestPEVMiddlewarePhaseInit:
    def setup_method(self) -> None:
        self.middleware = PEVMiddleware()
        self.runtime = make_mock_runtime()

    def _call_before_agent(self, task: str) -> dict[str, Any]:
        state: dict[str, Any] = {"messages": [HumanMessage(content=task)]}
        result = self.middleware.before_agent(state, self.runtime)
        assert result is not None
        return result

    def test_complex_task_sets_plan_phase(self) -> None:
        result = self._call_before_agent("implement a data pipeline")
        assert result["_pev_phase"] == "plan"

    def test_simple_task_sets_fast_phase(self) -> None:
        result = self._call_before_agent("write a note")
        assert result["_pev_phase"] == "fast"

    def test_trivial_task_sets_trivial_phase(self) -> None:
        result = self._call_before_agent("what is python?")
        assert result["_pev_phase"] == "trivial"

    def test_counters_zeroed_on_init(self) -> None:
        result = self._call_before_agent("implement a feature")
        assert result["_pev_iteration"] == 0
        assert result["_pev_error_count"] == 0
        assert result["_pev_plan"] is None
        assert result["_pev_recent_hashes"] == []

    def test_task_text_captured(self) -> None:
        result = self._call_before_agent("implement a data pipeline")
        assert result["_pev_task"] == "implement a data pipeline"

    def test_empty_messages_defaults_to_fast(self) -> None:
        state: dict[str, Any] = {"messages": []}
        result = self.middleware.before_agent(state, self.runtime)
        assert result is not None
        assert result["_pev_phase"] == "fast"

    def test_classifies_latest_human_message_in_multi_turn_thread(self) -> None:
        # A chat thread accumulates checkpointed history: turn 1 was trivial
        # small talk, the CURRENT turn is a complex task. Classification must
        # follow the latest human message, not stick to the first-of-thread.
        state: dict[str, Any] = {"messages": [
            HumanMessage(content="hi"),
            AIMessage(content="Hello! How can I help?"),
            HumanMessage(content="create calc.py with add(a,b) and a test for it"),
        ]}
        result = self.middleware.before_agent(state, self.runtime)
        assert result is not None
        assert result["_pev_phase"] == "plan"
        assert result["_pev_task"].startswith("create calc.py")

    async def test_abefore_agent_delegates_to_sync(self) -> None:
        state: dict[str, Any] = {"messages": [HumanMessage(content="implement a feature")]}
        result = await self.middleware.abefore_agent(state, self.runtime)
        assert result is not None
        assert result["_pev_phase"] == "plan"


# ---------------------------------------------------------------------------
# PEVMiddleware.wrap_model_call — prompt injection and tool filtering
# ---------------------------------------------------------------------------


class TestPEVMiddlewarePromptInjection:
    def setup_method(self) -> None:
        self.middleware = PEVMiddleware()
        self.runtime = make_mock_runtime()

    def _call_wrap(self, state: dict[str, Any], tools: list | None = None) -> ModelRequest | None:
        request = make_model_request(state, self.runtime, tools=tools)
        captured: list[ModelRequest] = []

        def handler(req: ModelRequest) -> AIMessage:
            captured.append(req)
            return AIMessage(content="ok")

        self.middleware.wrap_model_call(request, handler)
        return captured[0] if captured else None

    def _system_text(self, request: ModelRequest) -> str:
        sm = request.system_message
        if sm is None:
            return ""
        return " ".join(b.get("text", "") for b in sm.content_blocks if b.get("type") == "text")

    def test_plan_prompt_contains_marker(self) -> None:
        captured = self._call_wrap(make_pev_state(phase="plan"))
        assert captured is not None
        assert "PLAN COMPLETE" in self._system_text(captured)

    def test_execute_prompt_contains_marker(self) -> None:
        captured = self._call_wrap(make_pev_state(phase="execute"))
        assert captured is not None
        assert "EXECUTION COMPLETE" in self._system_text(captured)

    def test_verify_prompt_contains_markers(self) -> None:
        captured = self._call_wrap(make_pev_state(phase="verify"))
        assert captured is not None
        text = self._system_text(captured)
        assert "VERIFIED OK" in text
        assert "ISSUES FOUND" in text

    def test_verify_prompt_names_readonly_tools_and_forbids_execute(self) -> None:
        # Probe-proven failure: in verify the model kept calling the unbound
        # `execute` tool until the per-phase breaker ended the run, so VERIFIED
        # OK was never emitted. The prompt must name exactly what IS available
        # and explicitly rule out everything else.
        captured = self._call_wrap(make_pev_state(phase="verify"))
        assert captured is not None
        text = self._system_text(captured)
        # names the available read-only tools (must match _VERIFY_READONLY_TOOLS)
        assert "read_file, read, ls, glob, grep" in text
        # explicitly calls out execute (and other tools) as unavailable
        assert "execute" in text
        assert "Do NOT" in text
        # demands the verdict line
        assert "MUST end" in text
        assert "VERIFIED OK" in text
        assert "ISSUES FOUND" in text

    def test_trivial_phase_no_prompt_injected(self) -> None:
        captured = self._call_wrap(make_pev_state(phase="trivial"))
        assert captured is not None
        assert captured.system_message is None

    def test_verify_keeps_only_readonly_tools(self) -> None:
        # read_file is the deepagents filesystem-middleware reader; read is the
        # hcode registry reader. Verify must keep BOTH (probe-proven: the model
        # called read_file, which wasn't whitelisted, and wasted verify turns).
        tools = [
            make_mock_tool("read"),
            make_mock_tool("read_file"),
            make_mock_tool("write"),
            make_mock_tool("write_file"),
            make_mock_tool("execute"),
            make_mock_tool("ls"),
            make_mock_tool("bash"),
            make_mock_tool("grep"),
        ]
        captured = self._call_wrap(make_pev_state(phase="verify"), tools=tools)
        assert captured is not None
        remaining = {t.name for t in captured.tools}
        assert remaining == {"read", "read_file", "ls", "grep"}

    def test_non_verify_phase_does_not_filter_tools(self) -> None:
        tools = [make_mock_tool("read"), make_mock_tool("write"), make_mock_tool("bash")]
        captured = self._call_wrap(make_pev_state(phase="execute"), tools=tools)
        assert captured is not None
        assert len(captured.tools) == 3

    def test_plan_phase_binds_no_tools(self) -> None:
        # Plan must produce a textual plan + PLAN COMPLETE before acting: with
        # tools bound the model skips planning, so plan binds none.
        tools = [make_mock_tool("read"), make_mock_tool("write"), make_mock_tool("bash")]
        captured = self._call_wrap(make_pev_state(phase="plan"), tools=tools)
        assert captured is not None
        assert captured.tools == []
        # the plan instruction is still injected alongside
        assert "PLAN COMPLETE" in self._system_text(captured)

    def test_execute_phase_rebinds_tools_after_plan(self) -> None:
        tools = [make_mock_tool("read"), make_mock_tool("write"), make_mock_tool("bash")]
        plan_captured = self._call_wrap(make_pev_state(phase="plan"), tools=tools)
        execute_captured = self._call_wrap(make_pev_state(phase="execute"), tools=tools)
        assert plan_captured is not None and execute_captured is not None
        assert plan_captured.tools == []
        assert {t.name for t in execute_captured.tools} == {"read", "write", "bash"}

    def test_verify_with_no_tools_passthrough(self) -> None:
        captured = self._call_wrap(make_pev_state(phase="verify"))
        assert captured is not None
        assert captured.tools == []

    async def test_awrap_model_call_injects_prompt(self) -> None:
        state = make_pev_state(phase="plan")
        request = make_model_request(state, self.runtime)
        captured: list[ModelRequest] = []

        async def handler(req: ModelRequest) -> AIMessage:
            captured.append(req)
            return AIMessage(content="ok")

        await self.middleware.awrap_model_call(request, handler)
        assert len(captured) == 1
        text = " ".join(b.get("text", "") for b in captured[0].system_message.content_blocks if b.get("type") == "text")
        assert "PLAN COMPLETE" in text


# ---------------------------------------------------------------------------
# PEVMiddleware.after_model — phase transitions and safety guards
# ---------------------------------------------------------------------------


class TestPEVMiddlewareTransitions:
    def setup_method(self) -> None:
        self.middleware = PEVMiddleware()
        self.runtime = make_mock_runtime()

    def _after_model(self, state: dict[str, Any]) -> dict[str, Any] | None:
        return self.middleware.after_model(state, self.runtime)

    def test_plan_complete_advances_to_execute(self) -> None:
        state = make_pev_state(
            messages=[AIMessage(content="Here is my plan.\nPLAN COMPLETE")],
            phase="plan",
        )
        result = self._after_model(state)
        assert result is not None
        assert result["_pev_phase"] == "execute"
        assert result["jump_to"] == "model"

    def test_plan_complete_stores_plan_text(self) -> None:
        content = "Step 1: do X.\nStep 2: do Y.\nPLAN COMPLETE"
        state = make_pev_state(messages=[AIMessage(content=content)], phase="plan")
        result = self._after_model(state)
        assert result is not None
        assert result["_pev_plan"] == content

    def test_execution_complete_advances_to_verify(self) -> None:
        state = make_pev_state(
            messages=[AIMessage(content="All steps done.\nEXECUTION COMPLETE")],
            phase="execute",
        )
        result = self._after_model(state)
        assert result is not None
        assert result["_pev_phase"] == "verify"
        assert result["jump_to"] == "model"

    def test_verified_ok_ends_agent(self) -> None:
        state = make_pev_state(
            messages=[AIMessage(content="Everything looks correct.\nVERIFIED OK")],
            phase="verify",
        )
        result = self._after_model(state)
        assert result is not None
        assert result["jump_to"] == "end"

    def test_issues_found_returns_to_execute(self) -> None:
        state = make_pev_state(
            messages=[AIMessage(content="ISSUES FOUND: step 2 failed")],
            phase="verify",
        )
        result = self._after_model(state)
        assert result is not None
        assert result["_pev_phase"] == "execute"
        assert result["_pev_error_count"] == 1
        assert result["jump_to"] == "model"

    def test_issues_found_increments_error_count(self) -> None:
        state = make_pev_state(
            messages=[AIMessage(content="ISSUES FOUND: more problems")],
            phase="verify",
            error_count=1,
        )
        result = self._after_model(state)
        assert result is not None
        assert result["_pev_error_count"] == 2

    def test_circuit_breaker_fires_at_max_errors(self) -> None:
        state = make_pev_state(
            messages=[AIMessage(content="still working")],
            phase="plan",
            error_count=3,
        )
        result = self._after_model(state)
        assert result is not None
        assert result["jump_to"] == "end"

    def test_circuit_breaker_fires_at_max_iterations(self) -> None:
        # new_iteration = iteration + 1 = 6 > _MAX_ITERATIONS (5)
        state = make_pev_state(
            messages=[AIMessage(content="still working")],
            phase="plan",
            iteration=5,
        )
        result = self._after_model(state)
        assert result is not None
        assert result["jump_to"] == "end"

    def test_loop_detection_terminates_on_repeated_response(self) -> None:
        same_response = "I am stuck in a loop, doing nothing"
        h = hashlib.sha256(same_response.encode()).hexdigest()
        state = make_pev_state(
            messages=[AIMessage(content=same_response)],
            phase="plan",
            recent_hashes=[h, h, h],  # 3 prior identical hashes → after adding one more, count ≥ 3
        )
        result = self._after_model(state)
        assert result is not None
        assert result["jump_to"] == "end"

    def test_fast_phase_returns_none(self) -> None:
        state = make_pev_state(messages=[AIMessage(content="Done!")], phase="fast")
        assert self._after_model(state) is None

    def test_trivial_phase_returns_none(self) -> None:
        state = make_pev_state(
            messages=[AIMessage(content="Python is a high-level language")],
            phase="trivial",
        )
        assert self._after_model(state) is None

    def test_markerless_plan_without_tools_retries_model(self) -> None:
        # A markerless, tool-less plan response used to fall through with no
        # jump_to, silently ending the run (PR-PEV-2, item 1). It must retry
        # the model instead; the per-phase iteration cap bounds the retries.
        state = make_pev_state(
            messages=[AIMessage(content="I am still working on the plan...")],
            phase="plan",
        )
        result = self._after_model(state)
        assert result is not None
        assert result["jump_to"] == "model"
        assert result["_pev_iteration"] == 1

    def test_markerless_execute_without_tools_retries_model(self) -> None:
        # Same silent-death gap as the plan phase (PR-PEV-3): an execute-phase
        # turn with no EXECUTION COMPLETE and no tool calls (e.g. an empty
        # reasoning-only finish_reason='stop' turn) used to fall through with
        # no jump_to and end the run silently. It must retry the model.
        state = make_pev_state(
            messages=[AIMessage(content="")],
            phase="execute",
        )
        result = self._after_model(state)
        assert result is not None
        assert result["jump_to"] == "model"
        assert result["_pev_iteration"] == 1

    def test_markerless_verify_without_tools_retries_model(self) -> None:
        # Verify-phase variant: no VERIFIED OK / ISSUES FOUND and no tool
        # calls must re-prompt for a verdict, not end the run silently.
        state = make_pev_state(
            messages=[AIMessage(content="Let me look at the files.")],
            phase="verify",
        )
        result = self._after_model(state)
        assert result is not None
        assert result["jump_to"] == "model"
        assert result["_pev_iteration"] == 1

    def test_markerless_retry_is_bounded_by_phase_caps(self) -> None:
        # The new retries stay bounded by the EXISTING per-phase caps: at the
        # cap the breaker (checked before any retry) ends the run.
        execute_at_cap = make_pev_state(
            messages=[AIMessage(content="")],
            phase="execute",
            iteration=15,  # _MAX_EXECUTE_ITERATIONS
        )
        result = self._after_model(execute_at_cap)
        assert result is not None
        assert result["jump_to"] == "end"

        verify_at_cap = make_pev_state(
            messages=[AIMessage(content="Still looking around.")],
            phase="verify",
            iteration=5,  # _MAX_ITERATIONS
        )
        result = self._after_model(verify_at_cap)
        assert result is not None
        assert result["jump_to"] == "end"

    def test_execute_turn_with_tool_calls_does_not_retry(self) -> None:
        # A normal tool-call turn must keep flowing to the tools node — the
        # retry only fires when there is neither a marker nor a tool call.
        state = make_pev_state(
            messages=[AIMessage(
                content="",
                tool_calls=[{"name": "write", "args": {"path": "f.py"}, "id": "c1", "type": "tool_call"}],
            )],
            phase="execute",
        )
        result = self._after_model(state)
        assert result is not None
        assert "jump_to" not in result

    def test_iteration_counter_incremented(self) -> None:
        state = make_pev_state(
            messages=[AIMessage(content="progress")],
            phase="execute",
            iteration=2,
        )
        result = self._after_model(state)
        assert result is not None
        assert result["_pev_iteration"] == 3

    async def test_aafter_model_delegates_to_sync(self) -> None:
        state = make_pev_state(
            messages=[AIMessage(content="PLAN COMPLETE")],
            phase="plan",
        )
        result = await self.middleware.aafter_model(state, self.runtime)
        assert result is not None
        assert result["_pev_phase"] == "execute"
        assert result["jump_to"] == "model"
