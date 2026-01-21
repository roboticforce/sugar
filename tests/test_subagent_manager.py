"""
Tests for SubAgentManager

Tests the sub-agent spawning and management functionality, covering:
- SubAgentResult creation and serialization
- SubAgentManager initialization
- Single sub-agent spawning
- Parallel sub-agent execution
- Concurrency limits
- Timeout handling
- Error handling
"""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, Mock

from sugar.agent.base import SugarAgentConfig, AgentResponse
from sugar.agent.subagent_manager import SubAgentManager, SubAgentResult

# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def parent_config():
    """Create a parent agent configuration for testing."""
    return SugarAgentConfig(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        permission_mode="acceptEdits",
        quality_gates_enabled=True,
        timeout=300,
        max_retries=3,
        retry_base_delay=0.1,
        retry_max_delay=1.0,
    )


@pytest.fixture
def subagent_manager(parent_config):
    """Create a SubAgentManager instance for testing."""
    return SubAgentManager(
        parent_config=parent_config,
        max_concurrent=3,
        default_timeout=60,
    )


@pytest.fixture
def mock_agent_response():
    """Mock successful AgentResponse."""
    return AgentResponse(
        success=True,
        content="Task completed successfully. Made changes to the code.",
        tool_uses=[
            {"tool": "Write", "input": {"file_path": "/test/file.py"}},
            {"tool": "Edit", "input": {"file_path": "/test/other.py"}},
        ],
        files_modified=["/test/file.py", "/test/other.py"],
        execution_time=2.5,
    )


@pytest.fixture
def mock_agent_error_response():
    """Mock error AgentResponse."""
    return AgentResponse(
        success=False,
        content="",
        execution_time=1.0,
        error="API connection failed",
    )


# ============================================================================
# Test SubAgentResult
# ============================================================================


class TestSubAgentResult:
    """Test SubAgentResult dataclass."""

    def test_successful_result(self):
        """Test creating a successful result."""
        result = SubAgentResult(
            task_id="task-1",
            success=True,
            summary="Completed the authentication module",
            files_modified=["/src/auth.py", "/src/utils.py"],
            execution_time=5.2,
        )

        assert result.task_id == "task-1"
        assert result.success is True
        assert "authentication" in result.summary
        assert len(result.files_modified) == 2
        assert result.execution_time == 5.2
        assert result.error is None

    def test_failed_result(self):
        """Test creating a failed result."""
        result = SubAgentResult(
            task_id="task-2",
            success=False,
            summary="Task failed",
            execution_time=1.0,
            error="Timeout exceeded",
        )

        assert result.task_id == "task-2"
        assert result.success is False
        assert result.error == "Timeout exceeded"
        assert result.files_modified == []

    def test_to_dict(self):
        """Test result serialization to dict."""
        result = SubAgentResult(
            task_id="task-3",
            success=True,
            summary="Done",
            files_modified=["/a.py"],
            execution_time=3.0,
        )

        d = result.to_dict()
        assert d["task_id"] == "task-3"
        assert d["success"] is True
        assert d["summary"] == "Done"
        assert d["files_modified"] == ["/a.py"]
        assert d["execution_time"] == 3.0
        assert d["error"] is None


# ============================================================================
# Test SubAgentManager Initialization
# ============================================================================


class TestSubAgentManagerInit:
    """Test SubAgentManager initialization."""

    def test_basic_init(self, parent_config):
        """Test basic manager initialization."""
        manager = SubAgentManager(
            parent_config=parent_config,
            max_concurrent=3,
            default_timeout=60,
        )

        assert manager.parent_config == parent_config
        assert manager.max_concurrent == 3
        assert manager.default_timeout == 60
        assert manager._semaphore._value == 3  # Semaphore initialized
        assert manager._active_subagents == {}

    def test_custom_concurrency(self, parent_config):
        """Test initialization with custom concurrency limit."""
        manager = SubAgentManager(
            parent_config=parent_config,
            max_concurrent=5,
            default_timeout=120,
        )

        assert manager.max_concurrent == 5
        assert manager._semaphore._value == 5
        assert manager.default_timeout == 120

    def test_inherits_parent_config(self, parent_config):
        """Test that manager inherits parent configuration."""
        manager = SubAgentManager(parent_config)

        assert manager.parent_config.model == parent_config.model
        assert manager.parent_config.permission_mode == parent_config.permission_mode
        assert (
            manager.parent_config.quality_gates_enabled
            == parent_config.quality_gates_enabled
        )


# ============================================================================
# Test SubAgent Config Creation
# ============================================================================


class TestSubAgentConfigCreation:
    """Test sub-agent configuration creation."""

    def test_create_subagent_config_basic(self, subagent_manager):
        """Test basic sub-agent config creation."""
        config = subagent_manager._create_subagent_config("task-1")

        assert config.model == subagent_manager.parent_config.model
        assert config.permission_mode == subagent_manager.parent_config.permission_mode
        assert config.timeout == subagent_manager.default_timeout
        assert "task-1" in config.system_prompt_additions

    def test_create_subagent_config_with_context(self, subagent_manager):
        """Test sub-agent config with custom context."""
        config = subagent_manager._create_subagent_config(
            "task-2", "Working on authentication"
        )

        assert "task-2" in config.system_prompt_additions
        assert "authentication" in config.system_prompt_additions
        assert "sub-agent" in config.system_prompt_additions.lower()

    def test_subagent_config_inherits_settings(self, subagent_manager):
        """Test that sub-agent config inherits parent settings."""
        config = subagent_manager._create_subagent_config("task-3")

        # Should inherit these from parent
        assert config.model == subagent_manager.parent_config.model
        assert config.max_tokens == subagent_manager.parent_config.max_tokens
        assert (
            config.quality_gates_enabled
            == subagent_manager.parent_config.quality_gates_enabled
        )
        assert config.max_retries == subagent_manager.parent_config.max_retries

    def test_subagent_config_copies_lists_and_dicts(self, parent_config):
        """Test that lists and dicts are copied, not shared."""
        parent_config.allowed_tools = ["Read", "Write"]
        parent_config.mcp_servers = {"server1": {"cmd": "test"}}

        manager = SubAgentManager(parent_config)
        config = manager._create_subagent_config("task-4")

        # Modify sub-agent config
        config.allowed_tools.append("Bash")
        config.mcp_servers["server2"] = {"cmd": "test2"}

        # Parent should not be affected
        assert "Bash" not in parent_config.allowed_tools
        assert "server2" not in parent_config.mcp_servers


# ============================================================================
# Test Summary Extraction
# ============================================================================


class TestSummaryExtraction:
    """Test summary extraction from agent responses."""

    def test_extract_summary_from_content(self, subagent_manager):
        """Test extracting summary from response content."""
        response = AgentResponse(
            success=True,
            content="Successfully implemented the feature.\nMore details here.",
            tool_uses=[],
            files_modified=[],
            execution_time=1.0,
        )

        summary = subagent_manager._extract_summary(response)
        assert summary == "Successfully implemented the feature."

    def test_extract_summary_skips_headers(self, subagent_manager):
        """Test that markdown headers are skipped."""
        response = AgentResponse(
            success=True,
            content="# Header\n## Subheader\nActual summary here.",
            tool_uses=[],
            files_modified=[],
            execution_time=1.0,
        )

        summary = subagent_manager._extract_summary(response)
        assert summary == "Actual summary here."

    def test_extract_summary_truncates_long_content(self, subagent_manager):
        """Test that long summaries are truncated."""
        long_content = "A" * 300
        response = AgentResponse(success=True, content=long_content, execution_time=1.0)

        summary = subagent_manager._extract_summary(response)
        assert len(summary) == 200
        assert summary.endswith("...")

    def test_extract_summary_from_error(self, subagent_manager):
        """Test extracting summary from error response."""
        response = AgentResponse(
            success=False, content="", execution_time=1.0, error="Connection failed"
        )

        summary = subagent_manager._extract_summary(response)
        assert summary == "Connection failed"

    def test_extract_summary_empty_content(self, subagent_manager):
        """Test extracting summary from empty content."""
        response = AgentResponse(success=True, content="", execution_time=1.0)

        summary = subagent_manager._extract_summary(response)
        assert "no output" in summary.lower()


# ============================================================================
# Test Single Sub-Agent Spawning
# ============================================================================


class TestSpawnSubAgent:
    """Test spawning a single sub-agent."""

    @pytest.mark.asyncio
    async def test_spawn_basic(self, subagent_manager, mock_agent_response):
        """Test basic sub-agent spawning."""

        # Mock the SugarAgent execute method
        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = AsyncMock(return_value=mock_agent_response)

            result = await subagent_manager.spawn(
                task_id="test-1",
                prompt="Complete the authentication module",
                task_context="Working on auth feature",
            )

        assert result.task_id == "test-1"
        assert result.success is True
        assert result.summary  # Should have extracted summary
        assert len(result.files_modified) == 2
        assert result.execution_time >= 0  # May be 0 on fast systems (Windows)
        assert result.error is None

    @pytest.mark.asyncio
    async def test_spawn_with_timeout_override(self, subagent_manager):
        """Test spawning with custom timeout."""

        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = AsyncMock(
                return_value=AgentResponse(
                    success=True, content="Done", execution_time=1.0
                )
            )

            result = await subagent_manager.spawn(
                task_id="test-2", prompt="Test task", timeout=120
            )

        assert result.success is True

    @pytest.mark.asyncio
    async def test_spawn_timeout_exceeded(self, subagent_manager):
        """Test sub-agent timeout handling."""

        # Create a mock that simulates a long-running task
        async def slow_execute(*args, **kwargs):
            await asyncio.sleep(10)  # Longer than timeout
            return AgentResponse(success=True, content="Done", execution_time=10.0)

        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = slow_execute

            result = await subagent_manager.spawn(
                task_id="test-timeout", prompt="Slow task", timeout=0.1
            )

        assert result.success is False
        assert "timed out" in result.summary.lower()
        assert result.error == "Timeout exceeded"

    @pytest.mark.asyncio
    async def test_spawn_handles_execution_error(
        self, subagent_manager, mock_agent_error_response
    ):
        """Test sub-agent error handling."""

        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = AsyncMock(return_value=mock_agent_error_response)

            result = await subagent_manager.spawn(
                task_id="test-error", prompt="Task that fails"
            )

        assert result.success is False
        assert result.error == "API connection failed"

    @pytest.mark.asyncio
    async def test_spawn_handles_exception(self, subagent_manager):
        """Test exception handling during spawn."""

        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = AsyncMock(side_effect=Exception("Unexpected error"))

            result = await subagent_manager.spawn(
                task_id="test-exception", prompt="Task with exception"
            )

        assert result.success is False
        assert "Unexpected error" in result.error
        assert "failed" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_spawn_cleans_up_active_subagents(
        self, subagent_manager, mock_agent_response
    ):
        """Test that completed sub-agents are cleaned up."""

        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = AsyncMock(return_value=mock_agent_response)

            # Spawn and complete
            await subagent_manager.spawn(task_id="test-cleanup", prompt="Test")

            # Should be cleaned up
            assert "test-cleanup" not in subagent_manager._active_subagents


# ============================================================================
# Test Parallel Sub-Agent Spawning
# ============================================================================


class TestSpawnParallelSubAgents:
    """Test spawning multiple sub-agents in parallel."""

    @pytest.mark.asyncio
    async def test_spawn_parallel_basic(self, subagent_manager):
        """Test basic parallel spawning."""

        tasks = [
            {"task_id": "task-1", "prompt": "Task 1"},
            {"task_id": "task-2", "prompt": "Task 2"},
            {"task_id": "task-3", "prompt": "Task 3"},
        ]

        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = AsyncMock(
                return_value=AgentResponse(
                    success=True,
                    content="Task completed",
                    files_modified=[],
                    execution_time=1.0,
                )
            )

            results = await subagent_manager.spawn_parallel(tasks)

        assert len(results) == 3
        assert all(r.success for r in results)
        assert [r.task_id for r in results] == ["task-1", "task-2", "task-3"]

    @pytest.mark.asyncio
    async def test_spawn_parallel_with_context_and_timeout(self, subagent_manager):
        """Test parallel spawning with task-specific context and timeouts."""

        tasks = [
            {
                "task_id": "task-1",
                "prompt": "Task 1",
                "context": "Context 1",
                "timeout": 30,
            },
            {"task_id": "task-2", "prompt": "Task 2", "context": "Context 2"},
        ]

        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = AsyncMock(
                return_value=AgentResponse(
                    success=True, content="Done", execution_time=1.0
                )
            )

            results = await subagent_manager.spawn_parallel(tasks)

        assert len(results) == 2
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_spawn_parallel_with_mixed_results(self, subagent_manager):
        """Test parallel spawning with some successes and some failures."""

        tasks = [
            {"task_id": "success-1", "prompt": "Will succeed"},
            {"task_id": "failure-1", "prompt": "Will fail"},
            {"task_id": "success-2", "prompt": "Will succeed"},
        ]

        call_count = [0]

        async def mock_execute(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:  # Second call fails
                return AgentResponse(
                    success=False, content="", execution_time=1.0, error="Failed"
                )
            return AgentResponse(success=True, content="Success", execution_time=1.0)

        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = mock_execute

            results = await subagent_manager.spawn_parallel(tasks)

        assert len(results) == 3
        assert results[0].success is True
        assert results[1].success is False
        assert results[2].success is True

    @pytest.mark.asyncio
    async def test_spawn_parallel_handles_exceptions(self, subagent_manager):
        """Test that exceptions in parallel tasks are caught."""

        tasks = [
            {"task_id": "task-1", "prompt": "Normal task"},
            {"task_id": "task-2", "prompt": "Exception task"},
        ]

        call_count = [0]

        async def mock_execute(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                raise Exception("Sub-agent crashed")
            return AgentResponse(success=True, content="Done", execution_time=1.0)

        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = mock_execute

            results = await subagent_manager.spawn_parallel(tasks)

        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False
        assert "crashed" in results[1].error

    @pytest.mark.asyncio
    async def test_spawn_parallel_respects_order(self, subagent_manager):
        """Test that results are returned in input order."""

        tasks = [{"task_id": f"task-{i}", "prompt": f"Task {i}"} for i in range(5)]

        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = AsyncMock(
                return_value=AgentResponse(
                    success=True, content="Done", execution_time=1.0
                )
            )

            results = await subagent_manager.spawn_parallel(tasks)

        # Results should match input order
        assert [r.task_id for r in results] == [f"task-{i}" for i in range(5)]


# ============================================================================
# Test Concurrency Control
# ============================================================================


class TestConcurrencyControl:
    """Test concurrency limits and semaphore behavior."""

    @pytest.mark.asyncio
    async def test_respects_max_concurrent_limit(self, parent_config):
        """Test that max_concurrent limit is enforced."""
        manager = SubAgentManager(parent_config, max_concurrent=2, default_timeout=60)

        # Track concurrent executions
        concurrent_count = [0]
        max_concurrent_seen = [0]
        lock = asyncio.Lock()

        async def mock_execute(*args, **kwargs):
            async with lock:
                concurrent_count[0] += 1
                if concurrent_count[0] > max_concurrent_seen[0]:
                    max_concurrent_seen[0] = concurrent_count[0]

            # Simulate work
            await asyncio.sleep(0.1)

            async with lock:
                concurrent_count[0] -= 1

            return AgentResponse(success=True, content="Done", execution_time=0.1)

        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = mock_execute

            tasks = [{"task_id": f"task-{i}", "prompt": f"Task {i}"} for i in range(5)]

            await manager.spawn_parallel(tasks)

        # Should never exceed max_concurrent=2
        assert max_concurrent_seen[0] <= 2

    @pytest.mark.asyncio
    async def test_semaphore_initialization(self, parent_config):
        """Test that semaphore is initialized with correct value."""
        manager = SubAgentManager(parent_config, max_concurrent=4)
        assert manager._semaphore._value == 4


# ============================================================================
# Test Manager State Tracking
# ============================================================================


class TestManagerStateTracking:
    """Test active sub-agent tracking and state management."""

    @pytest.mark.asyncio
    async def test_get_active_count_empty(self, subagent_manager):
        """Test active count when no sub-agents running."""
        assert subagent_manager.get_active_count() == 0

    @pytest.mark.asyncio
    async def test_get_active_task_ids_empty(self, subagent_manager):
        """Test active task IDs when empty."""
        assert subagent_manager.get_active_task_ids() == []

    @pytest.mark.asyncio
    async def test_cancel_all_no_active(self, subagent_manager):
        """Test cancel_all with no active sub-agents."""
        # Should not raise
        await subagent_manager.cancel_all()
        assert subagent_manager.get_active_count() == 0


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests for sub-agent manager."""

    @pytest.mark.asyncio
    async def test_full_workflow_single_subagent(self, subagent_manager):
        """Test complete workflow for spawning a single sub-agent."""

        async def mock_execute(*args, **kwargs):
            # Add small delay to ensure measurable execution time on Windows
            # (Windows datetime has ~15ms resolution)
            await asyncio.sleep(0.02)
            return AgentResponse(
                success=True,
                content="Successfully refactored the authentication module.",
                tool_uses=[{"tool": "Edit", "input": {"file_path": "/auth.py"}}],
                files_modified=["/auth.py"],
                execution_time=5.0,
            )

        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = mock_execute

            result = await subagent_manager.spawn(
                task_id="refactor-auth",
                prompt="Refactor the authentication module for better security",
                task_context="Part of security audit sprint",
                timeout=120,
            )

        assert result.task_id == "refactor-auth"
        assert result.success is True
        assert "authentication" in result.summary.lower()
        assert "/auth.py" in result.files_modified
        assert result.execution_time > 0

    @pytest.mark.asyncio
    async def test_full_workflow_parallel_subagents(self, subagent_manager):
        """Test complete workflow for parallel sub-agents."""

        tasks = [
            {
                "task_id": "implement-login",
                "prompt": "Implement login endpoint",
                "context": "Auth feature",
            },
            {
                "task_id": "implement-logout",
                "prompt": "Implement logout endpoint",
                "context": "Auth feature",
            },
            {
                "task_id": "implement-refresh",
                "prompt": "Implement token refresh endpoint",
                "context": "Auth feature",
            },
        ]

        with patch("sugar.agent.subagent_manager.SugarAgent") as MockAgent:
            mock_instance = MockAgent.return_value
            mock_instance.execute = AsyncMock(
                return_value=AgentResponse(
                    success=True,
                    content="Endpoint implemented successfully",
                    files_modified=["/routes/auth.py"],
                    execution_time=3.0,
                )
            )

            results = await subagent_manager.spawn_parallel(tasks, timeout=180)

        assert len(results) == 3
        assert all(r.success for r in results)
        assert results[0].task_id == "implement-login"
        assert results[1].task_id == "implement-logout"
        assert results[2].task_id == "implement-refresh"
