"""
Tests for Task Orchestration - real TaskOrchestrator + AgentRouter.

These tests exercise the real implementation against an in-process WorkQueue
(temp sqlite) and a fake agent executor. Mocks are only used where a real
Claude SDK call cannot run in CI (the executor).
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from sugar.orchestration.task_orchestrator import (
    OrchestrationStage,
    TaskOrchestrator,
)
from sugar.orchestration.agent_router import AgentRouter

# ----------------------------------------------------------------------------
# Test fixtures / helpers
# ----------------------------------------------------------------------------

# A plan output the planning agent might produce. Subtask 2 depends on 1, and
# the Dependencies line precedes the Agent line so the regex parser captures it.
PLAN_OUTPUT = """## Sub-tasks

1. **Backend API** - Build the API endpoint for OAuth
   Agent: backend-developer

2. **Frontend UI** - Build the login UI
   Dependencies: 1
   Agent: frontend-designer
"""


class FakeExecutor:
    """Stand-in for AgentSDKExecutor that returns canned stage/subtask output.

    Records every execute_work call so tests can assert execution order.
    """

    def __init__(self):
        self.calls = []

    async def execute_work(self, work_item, task_type_info=None):
        self.calls.append(
            {
                "title": work_item.get("title"),
                "description": work_item.get("description", ""),
                "type": work_item.get("type"),
            }
        )
        desc = work_item.get("description", "")

        if "Orchestration Stage: PLANNING" in desc:
            return {
                "success": True,
                "output": PLAN_OUTPUT,
                "files_changed": [],
                "summary": "plan created",
            }
        if "Orchestration Stage: REVIEW" in desc:
            return {
                "success": True,
                "output": "Review passed - code looks good",
                "files_changed": [],
                "summary": "reviewed",
            }
        if "# Subtask:" in desc:
            title = work_item.get("title", "subtask")
            safe = title.replace(" ", "_")
            return {
                "success": True,
                "output": f"Implemented {title}",
                "files_changed": [f"{safe}.py"],
                "summary": f"did {title}",
            }
        # research / default
        return {
            "success": True,
            "output": "Research findings: relevant patterns identified.",
            "files_changed": [],
            "summary": "researched",
        }


@pytest.fixture
def orchestration_config():
    """Full Sugar-style config with a top-level orchestration: block.

    TaskOrchestrator reads config.get('orchestration'), so the block lives at
    the top level (sibling to sugar:), matching what `sugar init` emits.
    """
    return {
        "orchestration": {
            "enabled": True,
            "auto_decompose": "auto",
            "detection": {
                "task_types": ["feature", "epic"],
                "keywords": [
                    "implement",
                    "build",
                    "create full",
                    "add complete",
                    "redesign",
                    "refactor entire",
                ],
                "min_complexity": "high",
            },
            "stages": {
                "research": {
                    "enabled": True,
                    "agent": "Explore",
                    "timeout": 600,
                    "output_path": ".sugar/orchestration/{task_id}/research.md",
                },
                "planning": {
                    "enabled": True,
                    "agent": "Plan",
                    "timeout": 300,
                    "output_path": ".sugar/orchestration/{task_id}/plan.md",
                },
                "implementation": {
                    "enabled": True,
                    "parallel": True,
                    "max_concurrent": 3,
                    "timeout_per_task": 1800,
                    "agent_routing": {
                        "*ui*|*frontend*|*component*|*design*": "frontend-designer",
                        "*api*|*backend*|*endpoint*|*service*": "backend-developer",
                        "*test*|*spec*|*coverage*": "qa-engineer",
                        "*security*|*auth*|*permission*": "security-engineer",
                        "*devops*|*deploy*|*ci*|*docker*": "devops-engineer",
                        "*doc*|*readme*|*guide*": "general-purpose",
                        "default": "general-purpose",
                    },
                },
                "review": {
                    "enabled": True,
                    "run_tests": False,  # don't run real pytest by default
                    "require_passing": True,
                },
            },
        }
    }


@pytest_asyncio.fixture
async def real_orchestrator(orchestration_config, mock_work_queue):
    """A TaskOrchestrator wired to a real temp WorkQueue and a FakeExecutor."""
    executor = FakeExecutor()
    orchestrator = TaskOrchestrator(
        config=orchestration_config,
        work_queue=mock_work_queue,
        agent_executor=executor,
    )
    # Expose the executor for call-order assertions.
    orchestrator._test_executor = executor
    return orchestrator


async def _add_parent(queue, **overrides):
    """Add an orchestratable parent task and return its work-item dict."""
    defaults = {
        "type": "feature",
        "title": "Build OAuth authentication with UI and API",
        "description": "implement full OAuth authentication system end-to-end",
        "priority": 3,
        "status": "pending",
        "orchestrate": True,
        "context": {"added_via": "test"},
    }
    defaults.update(overrides)
    parent_id = await queue.add_work(defaults)
    return await queue.get_work_by_id(parent_id)


# ----------------------------------------------------------------------------
# should_orchestrate
# ----------------------------------------------------------------------------


class TestShouldOrchestrate:
    @pytest.mark.asyncio
    async def test_feature_type_triggers(self, real_orchestrator):
        task = {"id": "t1", "type": "feature", "title": "x", "description": "x"}
        assert await real_orchestrator.should_orchestrate(task) is True

    @pytest.mark.asyncio
    async def test_epic_type_triggers(self, real_orchestrator):
        task = {"id": "t2", "type": "epic", "title": "x", "description": "x"}
        assert await real_orchestrator.should_orchestrate(task) is True

    @pytest.mark.asyncio
    async def test_keyword_triggers(self, real_orchestrator):
        task = {
            "id": "t3",
            "type": "bug_fix",
            "title": "redesign the auth flow",
            "description": "small change",
        }
        assert await real_orchestrator.should_orchestrate(task) is True

    @pytest.mark.asyncio
    async def test_bug_fix_does_not_trigger(self, real_orchestrator):
        task = {
            "id": "t4",
            "type": "bug_fix",
            "title": "Fix typo in readme",
            "description": "just a typo",
        }
        assert await real_orchestrator.should_orchestrate(task) is False

    @pytest.mark.asyncio
    async def test_explicit_flag_triggers(self, real_orchestrator):
        task = {
            "id": "t5",
            "type": "bug_fix",
            "title": "small",
            "description": "small",
            "orchestrate": True,
        }
        assert await real_orchestrator.should_orchestrate(task) is True

    @pytest.mark.asyncio
    async def test_disabled_config_never_triggers(
        self, orchestration_config, mock_work_queue
    ):
        orchestration_config["orchestration"]["enabled"] = False
        orch = TaskOrchestrator(
            config=orchestration_config,
            work_queue=mock_work_queue,
            agent_executor=FakeExecutor(),
        )
        task = {"id": "t6", "type": "feature", "title": "build x", "orchestrate": True}
        assert await orch.should_orchestrate(task) is False


# ----------------------------------------------------------------------------
# generate_subtasks
# ----------------------------------------------------------------------------


class TestGenerateSubtasks:
    @pytest.mark.asyncio
    async def test_parse_subtasks_with_dependencies(self, real_orchestrator):
        subtasks = await real_orchestrator.generate_subtasks(
            PLAN_OUTPUT, {"id": "parent-1", "type": "feature", "priority": 3}
        )
        assert len(subtasks) == 2
        assert subtasks[0]["title"] == "Backend API"
        assert subtasks[0]["assigned_agent"] == "backend-developer"
        assert subtasks[0]["blocked_by"] == []

        assert subtasks[1]["title"] == "Frontend UI"
        assert subtasks[1]["assigned_agent"] == "frontend-designer"
        # Dependency "1" normalized to the placeholder id of subtask 1
        assert subtasks[1]["blocked_by"] == ["parent-1-sub-1"]

    @pytest.mark.asyncio
    async def test_fallback_single_subtask(self, real_orchestrator):
        subtasks = await real_orchestrator.generate_subtasks(
            "no structured plan here", {"id": "p2", "type": "feature"}
        )
        assert len(subtasks) == 1
        assert subtasks[0]["id"] == "p2-sub-1"


# ----------------------------------------------------------------------------
# AgentRouter
# ----------------------------------------------------------------------------


class TestAgentRouter:
    def test_route_backend_pattern(self, orchestration_config):
        router = AgentRouter(orchestration_config)
        assert (
            router.route({"title": "Build API endpoint", "type": "feature"})
            == "backend-developer"
        )

    def test_route_frontend_pattern(self, orchestration_config):
        router = AgentRouter(orchestration_config)
        assert (
            router.route({"title": "Add login UI component", "type": "feature"})
            == "frontend-designer"
        )

    def test_route_explicit_assignment_wins(self, orchestration_config):
        router = AgentRouter(orchestration_config)
        assert (
            router.route(
                {
                    "title": "Build API endpoint",
                    "type": "feature",
                    "assigned_agent": "qa-engineer",
                }
            )
            == "qa-engineer"
        )

    def test_route_default_fallback(self, orchestration_config):
        router = AgentRouter(orchestration_config)
        assert router.route({"title": "do stuff", "type": "feature"}) == (
            "general-purpose"
        )

    def test_get_stage_agent(self, orchestration_config):
        router = AgentRouter(orchestration_config)
        assert router.get_stage_agent(OrchestrationStage.RESEARCH) == "Explore"
        assert router.get_stage_agent(OrchestrationStage.PLANNING) == "Plan"
        assert router.get_stage_agent(OrchestrationStage.REVIEW) == "code-reviewer"

    def test_get_available_agents(self, orchestration_config):
        router = AgentRouter(orchestration_config)
        agents = router.get_available_agents()
        assert "backend-developer" in agents
        assert "frontend-designer" in agents


# ----------------------------------------------------------------------------
# Full orchestrate() flow
# ----------------------------------------------------------------------------


class TestOrchestrate:
    @pytest.mark.asyncio
    async def test_runs_all_stages_and_persists_subtasks(
        self, real_orchestrator, mock_work_queue
    ):
        parent = await _add_parent(mock_work_queue)
        result = await real_orchestrator.orchestrate(parent)

        assert result.success is True
        assert [s.value for s in result.stages_completed] == [
            "research",
            "planning",
            "implementation",
            "review",
        ]
        assert len(result.subtasks) == 2

        # Subtasks persisted to the queue, completed, with parent + agent set
        subtasks = await mock_work_queue.get_subtasks(parent["id"])
        assert len(subtasks) == 2
        for st in subtasks:
            assert st["parent_task_id"] == parent["id"]
            assert st["status"] == "completed"
            assert st["assigned_agent"] in (
                "backend-developer",
                "frontend-designer",
            )

        # Parent stage advanced to review and context_path is a context.md file
        refreshed = await mock_work_queue.get_work_by_id(parent["id"])
        assert refreshed["stage"] == "review"
        assert refreshed["context_path"].endswith("context.md")

    @pytest.mark.asyncio
    async def test_dependency_ordering(self, real_orchestrator, mock_work_queue):
        parent = await _add_parent(mock_work_queue)
        await real_orchestrator.orchestrate(parent)

        executor = real_orchestrator._test_executor
        subtask_calls = [c for c in executor.calls if "# Subtask:" in c["description"]]
        assert len(subtask_calls) == 2
        # Backend API (no deps) must execute before Frontend UI (depends on 1)
        assert subtask_calls[0]["title"] == "Backend API"
        assert subtask_calls[1]["title"] == "Frontend UI"

    @pytest.mark.asyncio
    async def test_context_file_written(
        self, real_orchestrator, mock_work_queue, tmp_path, monkeypatch
    ):
        # Run from a tmp dir so .sugar/orchestration is written somewhere clean
        monkeypatch.chdir(tmp_path)
        parent = await _add_parent(mock_work_queue)
        result = await real_orchestrator.orchestrate(parent)

        ctx_file = tmp_path / result.context_path
        assert ctx_file.exists()
        content = ctx_file.read_text()
        assert "## Stage: research" in content
        assert "## Stage: planning" in content
        assert "## Stage: implementation" in content
        assert "## Stage: review" in content

    @pytest.mark.asyncio
    async def test_skip_stages_honored(self, real_orchestrator, mock_work_queue):
        parent = await _add_parent(
            mock_work_queue,
            context={"added_via": "test", "skip_stages": ["research", "review"]},
        )
        result = await real_orchestrator.orchestrate(parent)
        stage_values = [s.value for s in result.stages_completed]
        assert "research" not in stage_values
        assert "review" not in stage_values
        assert "planning" in stage_values
        assert "implementation" in stage_values

    @pytest.mark.asyncio
    async def test_planning_failure_fails_orchestration(
        self, orchestration_config, mock_work_queue
    ):
        class FailingPlanner(FakeExecutor):
            async def execute_work(self, work_item, task_type_info=None):
                desc = work_item.get("description", "")
                if "Orchestration Stage: PLANNING" in desc:
                    return {"success": False, "error": "plan failed", "output": ""}
                return await super().execute_work(work_item, task_type_info)

        orch = TaskOrchestrator(
            config=orchestration_config,
            work_queue=mock_work_queue,
            agent_executor=FailingPlanner(),
        )
        parent = await _add_parent(mock_work_queue)
        result = await orch.orchestrate(parent)
        assert result.success is False
        assert "Planning stage failed" in (result.error or "")


# ----------------------------------------------------------------------------
# Review run_tests / require_passing
# ----------------------------------------------------------------------------


class TestReviewRunTests:
    @pytest.mark.asyncio
    async def test_failing_tests_with_require_passing_fails_review(
        self, orchestration_config, mock_work_queue
    ):
        orchestration_config["orchestration"]["stages"]["review"]["run_tests"] = True
        orchestration_config["orchestration"]["stages"]["review"][
            "require_passing"
        ] = True
        orch = TaskOrchestrator(
            config=orchestration_config,
            work_queue=mock_work_queue,
            agent_executor=FakeExecutor(),
        )
        # Avoid actually invoking pytest in the repo
        with patch.object(
            orch, "_run_project_tests", new=AsyncMock(return_value=(False, "2 failed"))
        ):
            parent = await _add_parent(mock_work_queue)
            result = await orch.orchestrate(parent)

        assert result.success is False
        assert "tests" in (result.error or "").lower() or "Review" in (
            result.error or ""
        )

    @pytest.mark.asyncio
    async def test_failing_tests_without_require_passing_still_succeeds(
        self, orchestration_config, mock_work_queue
    ):
        cfg = orchestration_config
        cfg["orchestration"]["stages"]["review"]["run_tests"] = True
        cfg["orchestration"]["stages"]["review"]["require_passing"] = False
        orch = TaskOrchestrator(
            config=cfg,
            work_queue=mock_work_queue,
            agent_executor=FakeExecutor(),
        )
        with patch.object(
            orch, "_run_project_tests", new=AsyncMock(return_value=(False, "1 failed"))
        ):
            parent = await _add_parent(mock_work_queue)
            result = await orch.orchestrate(parent)

        assert result.success is True


# ----------------------------------------------------------------------------
# Orchestration config loading
# ----------------------------------------------------------------------------


class TestOrchestrationConfiguration:
    def test_defaults_applied_when_no_config(self):
        orch = TaskOrchestrator(config={}, work_queue=None, agent_executor=None)
        assert orch.orchestration_config["enabled"] is True
        assert orch.orchestration_config["auto_decompose"] == "auto"
        assert "research" in orch.orchestration_config["stages"]

    def test_user_config_overrides_defaults(self):
        cfg = {"orchestration": {"enabled": False, "auto_decompose": "explicit"}}
        orch = TaskOrchestrator(config=cfg, work_queue=None, agent_executor=None)
        assert orch.orchestration_config["enabled"] is False
        assert orch.orchestration_config["auto_decompose"] == "explicit"
        # Nested defaults still present
        assert "stages" in orch.orchestration_config

    def test_stage_merge(self):
        cfg = {
            "orchestration": {
                "stages": {"research": {"enabled": False, "timeout": 999}}
            }
        }
        orch = TaskOrchestrator(config=cfg, work_queue=None, agent_executor=None)
        research = orch.orchestration_config["stages"]["research"]
        assert research["enabled"] is False
        assert research["timeout"] == 999
        # Default keys retained
        assert research["agent"] == "Explore"
