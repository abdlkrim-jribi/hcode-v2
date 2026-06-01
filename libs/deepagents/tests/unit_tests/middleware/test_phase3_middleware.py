"""Unit tests for Phase 3 middleware: SafetyGuard, HCodeSkills, Workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deepagents.middleware.hcode_skills import HCodeSkillsMiddleware
from deepagents.middleware.safety_guard import SafetyGuardMiddleware
from deepagents.middleware.workflows import WorkflowLoader, WorkflowMiddleware


# ---------------------------------------------------------------------------
# Shared helpers
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


def make_model_request(state: dict[str, Any], runtime: Any) -> ModelRequest:
    return ModelRequest(
        model=make_mock_model(),
        messages=state.get("messages", []),
        system_message=None,
        runtime=runtime,
        state=state,
    )


def make_tool_request(tool_name: str, args: dict[str, Any]) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": tool_name, "args": args, "id": "test-id"},
        tool=None,
        state={},
        runtime=MagicMock(),
    )


def system_text(request: ModelRequest) -> str:
    sm = request.system_message
    if sm is None:
        return ""
    return " ".join(b.get("text", "") for b in sm.content_blocks if b.get("type") == "text")


# ---------------------------------------------------------------------------
# SafetyGuardMiddleware
# ---------------------------------------------------------------------------


class TestSafetyGuardMiddleware:
    def setup_method(self) -> None:
        self._reads: dict[str, str] = {}
        self._writes: dict[str, str] = {}
        self.runtime = make_mock_runtime()
        self.middleware = SafetyGuardMiddleware(
            file_reader=lambda p: self._reads.get(p),
            file_writer=lambda p, c: self._writes.update({p: c}),
        )

    def test_before_agent_sets_transaction_active(self) -> None:
        result = self.middleware.before_agent({}, self.runtime)
        assert result is not None
        assert result["_safety_transaction_active"] is True

    def test_before_agent_clears_previous_state(self) -> None:
        self.middleware._in_flight_modified = ["/old.txt"]
        self.middleware._in_flight_backups = {"/old.txt": "old"}
        self.middleware.before_agent({}, self.runtime)
        assert self.middleware.get_modified_files() == []
        assert self.middleware.get_backup("/old.txt") is None

    def test_wrap_tool_call_backs_up_file_before_write(self) -> None:
        self._reads["/test.txt"] = "original content"
        request = make_tool_request("write", {"path": "/test.txt", "content": "new"})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="test-id"))

        self.middleware.wrap_tool_call(request, handler)

        assert self.middleware.get_backup("/test.txt") == "original content"
        handler.assert_called_once()

    def test_wrap_tool_call_restores_backup_on_exception(self) -> None:
        self._reads["/test.txt"] = "original content"
        request = make_tool_request("write", {"path": "/test.txt", "content": "new"})

        def failing_handler(req: ToolCallRequest) -> ToolMessage:
            msg = "write failed"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError):
            self.middleware.wrap_tool_call(request, failing_handler)

        assert self._writes.get("/test.txt") == "original content"

    def test_wrap_tool_call_does_not_restore_when_no_backup(self) -> None:
        request = make_tool_request("write", {"path": "/new.txt", "content": "x"})

        def failing_handler(req: ToolCallRequest) -> ToolMessage:
            msg = "fail"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError):
            self.middleware.wrap_tool_call(request, failing_handler)

        assert "/new.txt" not in self._writes

    def test_wrap_tool_call_skips_backup_for_non_destructive_tools(self) -> None:
        self._reads["/test.txt"] = "data"
        request = make_tool_request("read", {"path": "/test.txt"})
        handler = MagicMock(return_value=ToolMessage(content="data", tool_call_id="test-id"))

        self.middleware.wrap_tool_call(request, handler)

        assert self.middleware.get_backup("/test.txt") is None
        handler.assert_called_once()

    def test_wrap_tool_call_handles_bash_without_backup(self) -> None:
        # bash is destructive but not a file tool, so it is never backed up.
        request = make_tool_request("bash", {"command": "rm -rf /tmp/test"})
        handler = MagicMock(return_value=ToolMessage(content="done", tool_call_id="test-id"))

        self.middleware.wrap_tool_call(request, handler)

        assert self.middleware.get_modified_files() == []
        handler.assert_called_once()

    def test_after_agent_sets_transaction_inactive(self) -> None:
        result = self.middleware.after_agent({}, self.runtime)
        assert result is not None
        assert result["_safety_transaction_active"] is False

    def test_after_agent_snapshots_modified_files_to_state(self) -> None:
        self._reads["/a.txt"] = "old"
        request = make_tool_request("write", {"path": "/a.txt", "content": "new"})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="test-id"))

        self.middleware.wrap_tool_call(request, handler)
        result = self.middleware.after_agent({}, self.runtime)

        assert result is not None
        assert "/a.txt" in result["_safety_modified_files"]

    def test_modified_files_tracked_correctly(self) -> None:
        self.middleware.before_agent({}, self.runtime)
        for path in ("/a.txt", "/b.txt"):
            req = make_tool_request("write", {"path": path, "content": "x"})
            self.middleware.wrap_tool_call(req, MagicMock(return_value=ToolMessage(content="ok", tool_call_id="t")))

        modified = self.middleware.get_modified_files()
        assert "/a.txt" in modified
        assert "/b.txt" in modified

    async def test_abefore_agent_delegates(self) -> None:
        result = await self.middleware.abefore_agent({}, self.runtime)
        assert result is not None
        assert result["_safety_transaction_active"] is True

    async def test_aafter_agent_delegates(self) -> None:
        result = await self.middleware.aafter_agent({}, self.runtime)
        assert result is not None
        assert result["_safety_transaction_active"] is False


# ---------------------------------------------------------------------------
# WorkflowLoader
# ---------------------------------------------------------------------------


class TestWorkflowLoader:
    def test_load_basic_workflow(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("# Workflow\n## Steps\n1. First step\n2. Second step\n3. Third step\n")

        steps = WorkflowLoader().load(str(f))

        assert len(steps) == 3
        assert steps[0] == {"text": "First step", "turbo": False}
        assert steps[1] == {"text": "Second step", "turbo": False}
        assert steps[2] == {"text": "Third step", "turbo": False}

    def test_turbo_annotation_detected(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("## Steps\n1. Clean files // turbo\n2. Run tests\n")

        steps = WorkflowLoader().load(str(f))

        assert steps[0]["turbo"] is True
        assert steps[0]["text"] == "Clean files"
        assert steps[1]["turbo"] is False

    def test_turbo_all_annotation_makes_current_and_following_turbo(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("## Steps\n1. First step\n2. Turbo start // turbo-all\n3. Third step\n4. Fourth step\n")

        steps = WorkflowLoader().load(str(f))

        assert steps[0]["turbo"] is False
        assert steps[1]["turbo"] is True  # inclusive: step with // turbo-all is also turbo
        assert steps[2]["turbo"] is True
        assert steps[3]["turbo"] is True

    def test_turbo_all_text_stripped(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("## Steps\n1. Deploy // turbo-all\n")

        steps = WorkflowLoader().load(str(f))

        assert steps[0]["text"] == "Deploy"
        assert "turbo" not in steps[0]["text"]

    def test_missing_file_raises_error(self) -> None:
        with pytest.raises(FileNotFoundError):
            WorkflowLoader().load("/nonexistent/workflow.md")

    def test_non_step_lines_are_ignored(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("# Title\n\nSome description.\n\n## Steps\n1. Real step\n\n> Note: skip this\n")

        steps = WorkflowLoader().load(str(f))

        assert len(steps) == 1
        assert steps[0]["text"] == "Real step"


# ---------------------------------------------------------------------------
# WorkflowMiddleware
# ---------------------------------------------------------------------------


class TestWorkflowMiddleware:
    def setup_method(self) -> None:
        self.runtime = make_mock_runtime()

    def _state(
        self,
        *,
        active: bool = False,
        steps: list[dict[str, Any]] | None = None,
        current_step: int = 0,
    ) -> dict[str, Any]:
        return {
            "messages": [],
            "_workflow_active": active,
            "_workflow_steps": steps or [],
            "_workflow_current_step": current_step,
            "_workflow_name": None,
            "_workflow_results": [],
        }

    def test_workflow_not_active_for_normal_task(self) -> None:
        mw = WorkflowMiddleware()
        state: dict[str, Any] = {"messages": [HumanMessage(content="implement a feature")]}
        result = mw.before_agent(state, self.runtime)
        assert result is not None
        assert result["_workflow_active"] is False

    def test_workflow_activated_by_run_workflow_prefix(self, tmp_path: Path) -> None:
        wd = tmp_path / "workflows"
        wd.mkdir()
        (wd / "my-test.md").write_text("## Steps\n1. Do something\n2. Finish\n")

        mw = WorkflowMiddleware(workflows_dir=str(wd))
        state: dict[str, Any] = {"messages": [HumanMessage(content="run workflow my-test")]}
        result = mw.before_agent(state, self.runtime)

        assert result is not None
        assert result["_workflow_active"] is True
        assert len(result["_workflow_steps"]) == 2
        assert result["_workflow_name"] == "my-test"

    def test_workflow_activated_by_slash_prefix(self, tmp_path: Path) -> None:
        wd = tmp_path / "workflows"
        wd.mkdir()
        (wd / "deploy.md").write_text("## Steps\n1. Build\n")

        mw = WorkflowMiddleware(workflows_dir=str(wd))
        state: dict[str, Any] = {"messages": [HumanMessage(content="/workflow deploy")]}
        result = mw.before_agent(state, self.runtime)

        assert result is not None
        assert result["_workflow_active"] is True

    def test_missing_workflow_file_sets_inactive(self, tmp_path: Path) -> None:
        mw = WorkflowMiddleware(workflows_dir=str(tmp_path))
        state: dict[str, Any] = {"messages": [HumanMessage(content="run workflow ghost")]}
        result = mw.before_agent(state, self.runtime)
        assert result is not None
        assert result["_workflow_active"] is False

    def test_workflow_step_injected_into_system_prompt(self) -> None:
        state = self._state(active=True, steps=[{"text": "Do step 1", "turbo": False}], current_step=0)
        mw = WorkflowMiddleware()
        captured: list[ModelRequest] = []

        request = make_model_request(state, self.runtime)

        def handler(req: ModelRequest) -> AIMessage:
            captured.append(req)
            return AIMessage(content="ok")

        mw.wrap_model_call(request, handler)

        assert len(captured) == 1
        assert "Do step 1" in system_text(captured[0])

    def test_step_number_shown_in_prompt(self) -> None:
        steps = [{"text": "Step A", "turbo": False}, {"text": "Step B", "turbo": False}]
        state = self._state(active=True, steps=steps, current_step=1)
        mw = WorkflowMiddleware()
        captured: list[ModelRequest] = []

        def handler(req: ModelRequest) -> AIMessage:
            captured.append(req)
            return AIMessage(content="ok")

        mw.wrap_model_call(make_model_request(state, self.runtime), handler)

        text = system_text(captured[0])
        assert "Step 2/2" in text

    def test_turbo_step_adds_no_confirmation_instruction(self) -> None:
        state = self._state(active=True, steps=[{"text": "Run deploy", "turbo": True}], current_step=0)
        mw = WorkflowMiddleware()
        captured: list[ModelRequest] = []

        def handler(req: ModelRequest) -> AIMessage:
            captured.append(req)
            return AIMessage(content="ok")

        mw.wrap_model_call(make_model_request(state, self.runtime), handler)

        assert "without asking for confirmation" in system_text(captured[0])

    def test_non_turbo_step_omits_no_confirmation_instruction(self) -> None:
        state = self._state(active=True, steps=[{"text": "Review code", "turbo": False}], current_step=0)
        mw = WorkflowMiddleware()
        captured: list[ModelRequest] = []

        def handler(req: ModelRequest) -> AIMessage:
            captured.append(req)
            return AIMessage(content="ok")

        mw.wrap_model_call(make_model_request(state, self.runtime), handler)

        assert "without asking for confirmation" not in system_text(captured[0])

    def test_all_steps_done_jumps_to_end(self) -> None:
        state = self._state(active=True, steps=[{"text": "Only step", "turbo": False}], current_step=0)
        mw = WorkflowMiddleware()
        result = mw.after_model(state, self.runtime)

        assert result is not None
        assert result["jump_to"] == "end"
        assert result["_workflow_active"] is False

    def test_step_counter_increments_after_model(self) -> None:
        steps = [{"text": "Step 1", "turbo": False}, {"text": "Step 2", "turbo": False}]
        state = self._state(active=True, steps=steps, current_step=0)
        mw = WorkflowMiddleware()
        result = mw.after_model(state, self.runtime)

        assert result is not None
        assert result["_workflow_current_step"] == 1
        assert result["jump_to"] == "model"

    def test_after_model_returns_none_when_inactive(self) -> None:
        state = self._state(active=False)
        assert WorkflowMiddleware().after_model(state, self.runtime) is None

    async def test_aafter_model_delegates(self) -> None:
        steps = [{"text": "Step 1", "turbo": False}]
        state = self._state(active=True, steps=steps, current_step=0)
        result = await WorkflowMiddleware().aafter_model(state, self.runtime)
        assert result is not None
        assert result["jump_to"] == "end"


# ---------------------------------------------------------------------------
# HCodeSkillsMiddleware
# ---------------------------------------------------------------------------


class TestHCodeSkillsMiddleware:
    def setup_method(self) -> None:
        self.runtime = make_mock_runtime()

    def test_skill_loaded_from_hcode_format(self, tmp_path: Path) -> None:
        sd = tmp_path / "skills" / "code-review"
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text("## Code Review\nReview the code carefully.")

        mw = HCodeSkillsMiddleware(skills_dir=str(tmp_path / "skills"))
        result = mw.before_agent({}, self.runtime)

        assert result is not None
        skills = result["_hcode_skills"]
        assert len(skills) == 1
        assert skills[0]["name"] == "code-review"

    def test_skill_dir_variable_replaced_with_actual_path(self, tmp_path: Path) -> None:
        sd = tmp_path / "skills" / "my-skill"
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text("Content located in {skill_dir}")

        mw = HCodeSkillsMiddleware(skills_dir=str(tmp_path / "skills"))
        result = mw.before_agent({}, self.runtime)

        assert result is not None
        content = result["_hcode_skills"][0]["content"]
        assert "{skill_dir}" not in content
        assert str(sd) in content

    def test_missing_skills_dir_does_not_raise(self) -> None:
        mw = HCodeSkillsMiddleware(skills_dir="/nonexistent/path/skills")
        result = mw.before_agent({}, self.runtime)

        assert result is not None
        assert result["_hcode_skills"] == []

    def test_subdir_without_skill_md_is_skipped(self, tmp_path: Path) -> None:
        sd = tmp_path / "skills"
        (sd / "no-skill").mkdir(parents=True)
        real = sd / "real-skill"
        real.mkdir()
        (real / "SKILL.md").write_text("A real skill")

        mw = HCodeSkillsMiddleware(skills_dir=str(sd))
        result = mw.before_agent({}, self.runtime)

        assert result is not None
        assert len(result["_hcode_skills"]) == 1
        assert result["_hcode_skills"][0]["name"] == "real-skill"

    def test_skills_injected_into_system_prompt(self, tmp_path: Path) -> None:
        sd = tmp_path / "skills" / "testing"
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text("Run pytest for all tests.")

        mw = HCodeSkillsMiddleware(skills_dir=str(tmp_path / "skills"))
        state: dict[str, Any] = {"messages": [], "_hcode_skills": [{"name": "testing", "path": str(sd / "SKILL.md"), "content": "Run pytest for all tests."}], "_hcode_skills_loaded": True}

        captured: list[ModelRequest] = []

        def handler(req: ModelRequest) -> AIMessage:
            captured.append(req)
            return AIMessage(content="ok")

        mw.wrap_model_call(make_model_request(state, self.runtime), handler)

        assert len(captured) == 1
        assert "testing" in system_text(captured[0])

    def test_before_agent_skipped_when_already_loaded(self, tmp_path: Path) -> None:
        sd = tmp_path / "skills" / "skill-a"
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text("Skill A")

        mw = HCodeSkillsMiddleware(skills_dir=str(tmp_path / "skills"))
        state: dict[str, Any] = {"_hcode_skills_loaded": True, "_hcode_skills": []}
        result = mw.before_agent(state, self.runtime)

        assert result is None

    async def test_abefore_agent_delegates(self, tmp_path: Path) -> None:
        sd = tmp_path / "skills" / "my-skill"
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text("Content")

        mw = HCodeSkillsMiddleware(skills_dir=str(tmp_path / "skills"))
        result = await mw.abefore_agent({}, self.runtime)

        assert result is not None
        assert len(result["_hcode_skills"]) == 1
