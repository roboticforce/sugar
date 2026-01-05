"""
Tests for SugarAgent - Claude Agent SDK integration

Tests the core agent implementation for Sugar 3.0, covering:
- Configuration and initialization
- SDK query execution with streaming
- Hook integration for quality gates
- Error handling and retry logic
"""

import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from typing import Any, Dict, List

from sugar.agent.base import (
    SugarAgent,
    SugarAgentConfig,
    AgentResponse,
    is_transient_error,
    retry_with_backoff,
    TRANSIENT_ERRORS,
)
from sugar.agent.hooks import QualityGateHooks, HookContext


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def agent_config():
    """Create a default agent configuration for testing."""
    return SugarAgentConfig(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        permission_mode="acceptEdits",
        allowed_tools=[],
        mcp_servers={},
        quality_gates_enabled=True,
        system_prompt_additions="",
        working_directory="/tmp/test",
        timeout=300,
        max_retries=3,
        retry_base_delay=0.1,  # Fast retries for tests
        retry_max_delay=1.0,
    )


@pytest.fixture
def agent(agent_config):
    """Create a SugarAgent instance for testing."""
    return SugarAgent(agent_config)


@pytest.fixture
def quality_gates_config():
    """Quality gates configuration for testing."""
    return {
        "enabled": True,
        "protected_paths": [".env", "secrets.yaml"],
        "dangerous_commands": ["rm -rf /"],
    }


@pytest.fixture
def agent_with_quality_gates(agent_config, quality_gates_config):
    """Create an agent with quality gates configured."""
    return SugarAgent(agent_config, quality_gates_config)


@pytest.fixture
def mock_sdk_response():
    """Mock SDK streaming response - dict format."""
    return [
        {
            "type": "assistant",
            "content": [
                {"type": "text", "text": "I'll help you with that task."},
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "/test/file.py"},
                },
            ],
        },
        {
            "type": "assistant",
            "content": [
                {"type": "text", "text": "Here's what I found:"},
                {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": "/test/output.py", "content": "# code"},
                },
            ],
        },
        {"type": "result", "content": "Task completed successfully."},
    ]


# ============================================================================
# Test SugarAgentConfig
# ============================================================================


class TestSugarAgentConfig:
    """Test SugarAgentConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = SugarAgentConfig()
        assert config.model == "claude-sonnet-4-20250514"
        assert config.max_tokens == 8192
        assert config.permission_mode == "acceptEdits"
        assert config.allowed_tools == []
        assert config.mcp_servers == {}
        assert config.quality_gates_enabled is True
        assert (
            config.timeout == 7200
        )  # 2 hours - increased to handle long-running tasks
        assert config.max_retries == 3

    def test_custom_values(self):
        """Test custom configuration values."""
        config = SugarAgentConfig(
            model="claude-opus-4",
            max_tokens=16384,
            permission_mode="bypassPermissions",
            allowed_tools=["Read", "Write"],
            quality_gates_enabled=False,
            timeout=600,
            max_retries=5,
        )
        assert config.model == "claude-opus-4"
        assert config.max_tokens == 16384
        assert config.permission_mode == "bypassPermissions"
        assert config.allowed_tools == ["Read", "Write"]
        assert config.quality_gates_enabled is False
        assert config.timeout == 600
        assert config.max_retries == 5


# ============================================================================
# Test AgentResponse
# ============================================================================


class TestAgentResponse:
    """Test AgentResponse dataclass."""

    def test_successful_response(self):
        """Test creating a successful response."""
        response = AgentResponse(
            success=True,
            content="Task completed",
            tool_uses=[{"tool": "Write", "input": {"file_path": "/test.py"}}],
            files_modified=["/test.py"],
            execution_time=1.5,
        )
        assert response.success is True
        assert response.content == "Task completed"
        assert len(response.tool_uses) == 1
        assert len(response.files_modified) == 1
        assert response.execution_time == 1.5
        assert response.error is None

    def test_failed_response(self):
        """Test creating a failed response."""
        response = AgentResponse(
            success=False,
            content="",
            execution_time=0.5,
            error="API connection failed",
        )
        assert response.success is False
        assert response.error == "API connection failed"

    def test_to_dict(self):
        """Test response serialization to dict."""
        response = AgentResponse(
            success=True,
            content="Done",
            tool_uses=[{"tool": "Read"}],
            files_modified=["/a.py"],
            execution_time=2.0,
            quality_gate_results={"blocked": 0},
        )
        d = response.to_dict()
        assert d["success"] is True
        assert d["content"] == "Done"
        assert d["tool_uses"] == [{"tool": "Read"}]
        assert d["files_modified"] == ["/a.py"]
        assert d["execution_time"] == 2.0
        assert d["quality_gate_results"] == {"blocked": 0}


# ============================================================================
# Test Retry Logic
# ============================================================================


class TestRetryLogic:
    """Test retry utilities."""

    def test_is_transient_error_rate_limit(self):
        """Test detection of rate limit errors."""
        error = Exception("rate_limit_exceeded")
        assert is_transient_error(error) is True

    def test_is_transient_error_timeout(self):
        """Test detection of timeout errors."""
        error = Exception("Connection timeout after 30s")
        assert is_transient_error(error) is True

    def test_is_transient_error_503(self):
        """Test detection of 503 errors."""
        error = Exception("HTTP 503 Service Unavailable")
        assert is_transient_error(error) is True

    def test_is_transient_error_429(self):
        """Test detection of 429 errors."""
        error = Exception("HTTP 429 Too Many Requests")
        assert is_transient_error(error) is True

    def test_is_not_transient_error(self):
        """Test non-transient errors."""
        error = Exception("Invalid API key")
        assert is_transient_error(error) is False

    @pytest.mark.asyncio
    async def test_retry_with_backoff_success(self):
        """Test successful execution without retry."""
        call_count = 0

        async def success_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await retry_with_backoff(success_func, max_retries=3, base_delay=0.01)
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_with_backoff_transient_then_success(self):
        """Test retry after transient error."""
        call_count = 0

        async def fail_then_success():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("rate_limit exceeded")
            return "success"

        result = await retry_with_backoff(
            fail_then_success, max_retries=3, base_delay=0.01
        )
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_with_backoff_non_transient_fails(self):
        """Test non-transient error is not retried."""
        call_count = 0

        async def non_transient_error():
            nonlocal call_count
            call_count += 1
            raise Exception("Invalid API key")

        with pytest.raises(Exception, match="Invalid API key"):
            await retry_with_backoff(
                non_transient_error, max_retries=3, base_delay=0.01
            )
        assert call_count == 1  # No retries

    @pytest.mark.asyncio
    async def test_retry_with_backoff_exhausted(self):
        """Test all retries exhausted."""
        call_count = 0

        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise Exception("rate_limit exceeded")

        with pytest.raises(Exception, match="rate_limit"):
            await retry_with_backoff(always_fail, max_retries=2, base_delay=0.01)
        assert call_count == 3  # Initial + 2 retries


# ============================================================================
# Test SugarAgent Initialization
# ============================================================================


class TestSugarAgentInit:
    """Test SugarAgent initialization."""

    def test_basic_init(self, agent_config):
        """Test basic agent initialization."""
        agent = SugarAgent(agent_config)
        assert agent.config == agent_config
        assert agent._session_active is False
        assert agent._execution_history == []
        assert agent._current_options is None
        assert isinstance(agent.hooks, QualityGateHooks)

    def test_init_with_quality_gates(self, agent_config, quality_gates_config):
        """Test initialization with quality gates config."""
        agent = SugarAgent(agent_config, quality_gates_config)
        assert agent.quality_gates_config == quality_gates_config
        assert agent.hooks.enabled is True

    def test_init_without_quality_gates(self, agent_config):
        """Test initialization without quality gates config."""
        config = SugarAgentConfig(quality_gates_enabled=False)
        agent = SugarAgent(config)
        # Hooks object exists but checks will be bypassed if disabled
        assert isinstance(agent.hooks, QualityGateHooks)


# ============================================================================
# Test System Prompt Building
# ============================================================================


class TestSystemPromptBuilding:
    """Test system prompt construction."""

    def test_build_system_prompt_default(self, agent):
        """Test default system prompt."""
        prompt = agent._build_system_prompt()
        assert "Sugar" in prompt
        assert "autonomous development assistant" in prompt
        assert "guidelines" in prompt.lower()

    def test_build_system_prompt_with_additions(self, agent_config):
        """Test system prompt with custom additions."""
        agent_config.system_prompt_additions = "Always use TypeScript."
        agent = SugarAgent(agent_config)
        prompt = agent._build_system_prompt()
        assert "Always use TypeScript" in prompt

    def test_build_system_prompt_with_context(self, agent):
        """Test system prompt with task context."""
        prompt = agent._build_system_prompt(task_context="Working on auth module")
        assert "Working on auth module" in prompt
        assert "Task Context" in prompt


# ============================================================================
# Test Options Building
# ============================================================================


class TestOptionsBuilding:
    """Test ClaudeAgentOptions construction."""

    def test_build_options_basic(self, agent):
        """Test basic options building."""
        options = agent._build_options()
        assert options is not None
        # Check that system_prompt is set
        assert options.system_prompt is not None

    def test_build_options_with_quality_gates(self, agent_with_quality_gates):
        """Test options include quality gate hooks."""
        options = agent_with_quality_gates._build_options()
        assert options.hooks is not None
        assert "PreToolUse" in options.hooks
        assert "PostToolUse" in options.hooks

    def test_build_options_without_quality_gates(self, agent_config):
        """Test options without quality gates."""
        agent_config.quality_gates_enabled = False
        agent = SugarAgent(agent_config)
        options = agent._build_options()
        # hooks should be None or empty when disabled
        assert options.hooks is None or options.hooks == {}

    def test_build_options_with_allowed_tools(self, agent_config):
        """Test options with specific allowed tools."""
        agent_config.allowed_tools = ["Read", "Write", "Bash"]
        agent = SugarAgent(agent_config)
        options = agent._build_options()
        assert options.allowed_tools == ["Read", "Write", "Bash"]

    def test_build_options_with_mcp_servers(self, agent_config):
        """Test options with MCP servers configured."""
        agent_config.mcp_servers = {"playwright": {"command": "npx playwright"}}
        agent = SugarAgent(agent_config)
        options = agent._build_options()
        assert options.mcp_servers == {"playwright": {"command": "npx playwright"}}


# ============================================================================
# Test Session Management
# ============================================================================


class TestSessionManagement:
    """Test session lifecycle."""

    @pytest.mark.asyncio
    async def test_start_session(self, agent):
        """Test starting a session."""
        await agent.start_session()
        assert agent._session_active is True
        assert agent._current_options is not None

    @pytest.mark.asyncio
    async def test_start_session_with_context(self, agent):
        """Test starting session with task context."""
        await agent.start_session(task_context="Working on feature X")
        assert agent._session_active is True
        assert "Working on feature X" in agent._current_options.system_prompt

    @pytest.mark.asyncio
    async def test_end_session(self, agent):
        """Test ending a session."""
        await agent.start_session()
        await agent.end_session()
        assert agent._session_active is False
        assert agent._current_options is None

    @pytest.mark.asyncio
    async def test_restart_session(self, agent):
        """Test restarting session ends previous one."""
        await agent.start_session(task_context="Context 1")
        options1 = agent._current_options

        await agent.start_session(task_context="Context 2")
        options2 = agent._current_options

        # Should have new options
        assert "Context 2" in options2.system_prompt
        assert "Context 1" not in options2.system_prompt

    @pytest.mark.asyncio
    async def test_context_manager(self, agent):
        """Test async context manager usage."""
        async with agent:
            assert agent._session_active is True
        assert agent._session_active is False


# ============================================================================
# Test Execute Method
# ============================================================================


class TestExecute:
    """Test the execute() method."""

    @pytest.mark.asyncio
    async def test_execute_basic(self, agent, mock_sdk_response):
        """Test basic execution with mocked SDK."""

        # Mock the query function to return our test data
        async def mock_query(**kwargs):
            for msg in mock_sdk_response:
                yield msg

        with patch("sugar.agent.base.query", mock_query):
            response = await agent.execute("Test prompt")

        assert response.success is True
        assert "help you with that task" in response.content
        assert len(response.tool_uses) == 2
        # Only Write should be in files_modified
        assert "/test/output.py" in response.files_modified
        assert "/test/file.py" not in response.files_modified  # Read doesn't modify

    @pytest.mark.asyncio
    async def test_execute_tracks_file_modifications(self, agent):
        """Test that file modifications are tracked correctly."""
        mock_messages = [
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": "/a.py"},
                    },
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "/b.py"},
                    },
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/c.py"},
                    },
                ],
            }
        ]

        async def mock_query(**kwargs):
            for msg in mock_messages:
                yield msg

        with patch("sugar.agent.base.query", mock_query):
            response = await agent.execute("Modify files")

        assert "/a.py" in response.files_modified
        assert "/b.py" in response.files_modified
        assert "/c.py" not in response.files_modified  # Read doesn't count

    @pytest.mark.asyncio
    async def test_execute_error_handling(self, agent):
        """Test error handling in execute."""

        async def mock_query(**kwargs):
            raise Exception("API connection failed")
            yield  # Make it a generator

        with patch("sugar.agent.base.query", mock_query):
            response = await agent.execute("Test prompt")

        assert response.success is False
        assert "API connection failed" in response.error

    @pytest.mark.asyncio
    async def test_execute_stores_history(self, agent):
        """Test that execution history is stored."""

        async def mock_query(**kwargs):
            yield {"type": "text", "text": "Done"}

        with patch("sugar.agent.base.query", mock_query):
            await agent.execute("First task")
            await agent.execute("Second task")

        history = agent.get_execution_history()
        assert len(history) == 2
        assert history[0]["prompt"] == "First task"
        assert history[1]["prompt"] == "Second task"

    @pytest.mark.asyncio
    async def test_execute_quality_gate_results(self, agent_with_quality_gates):
        """Test quality gate results are included."""

        async def mock_query(**kwargs):
            yield {"type": "text", "text": "Done"}

        with patch("sugar.agent.base.query", mock_query):
            response = await agent_with_quality_gates.execute("Test")

        assert response.quality_gate_results is not None
        assert "total_tool_executions" in response.quality_gate_results

    @pytest.mark.asyncio
    async def test_execute_with_retry(self, agent_config):
        """Test retry on transient errors."""
        agent_config.max_retries = 2
        agent_config.retry_base_delay = 0.01
        agent = SugarAgent(agent_config)

        call_count = 0

        async def mock_query(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("rate_limit exceeded")
            yield {"type": "text", "text": "Success after retry"}

        with patch("sugar.agent.base.query", mock_query):
            response = await agent.execute("Test with retry")

        assert response.success is True
        assert "Success after retry" in response.content

    @pytest.mark.asyncio
    async def test_execute_with_timeout(self, agent_config):
        """Test timeout handling when query hangs."""
        agent_config.timeout = 0.1  # 100ms for testing
        agent = SugarAgent(agent_config)

        async def mock_query_that_hangs(**kwargs):
            yield {"type": "text", "text": "Started work"}
            # Simulate a hanging generator
            await asyncio.sleep(10)  # Much longer than timeout
            yield {"type": "text", "text": "This should not appear"}

        with patch("sugar.agent.base.query", mock_query_that_hangs):
            response = await agent.execute("Test with timeout")

        # Should succeed with partial results (the first message before hang)
        assert response.success is True
        assert "Started work" in response.content
        # The second message should NOT appear since we timed out
        assert "This should not appear" not in response.content


# ============================================================================
# Test Execute Work Item
# ============================================================================


class TestExecuteWorkItem:
    """Test execute_work_item for legacy compatibility."""

    @pytest.mark.asyncio
    async def test_execute_work_item_basic(self, agent):
        """Test executing a work item."""
        work_item = {
            "id": "task-123",
            "title": "Fix bug in auth",
            "type": "bug_fix",
            "priority": 5,
            "description": "Authentication fails on edge cases",
            "source": "github",
        }

        async def mock_query(**kwargs):
            yield {"type": "text", "text": "Bug fixed successfully"}

        with patch("sugar.agent.base.query", mock_query):
            result = await agent.execute_work_item(work_item)

        assert result["success"] is True
        assert result["work_item_id"] == "task-123"
        assert result["agent_sdk"] is True
        assert "Bug fixed successfully" in result["output"]

    @pytest.mark.asyncio
    async def test_execute_work_item_failure(self, agent):
        """Test work item execution failure."""
        work_item = {"id": "task-fail", "title": "Will fail", "type": "feature"}

        async def mock_query(**kwargs):
            raise Exception("Execution failed")
            yield

        with patch("sugar.agent.base.query", mock_query):
            result = await agent.execute_work_item(work_item)

        assert result["success"] is False
        assert result["error"] == "Execution failed"


# ============================================================================
# Test Work Item Prompt Building
# ============================================================================


class TestWorkItemPromptBuilding:
    """Test prompt construction from work items."""

    def test_build_work_item_prompt(self, agent):
        """Test building prompt from work item."""
        work_item = {
            "title": "Add user login",
            "type": "feature",
            "priority": 3,
            "description": "Implement OAuth login flow",
        }
        prompt = agent._build_work_item_prompt(work_item)

        assert "Add user login" in prompt
        assert "feature" in prompt
        assert "3" in prompt  # Priority
        assert "OAuth login flow" in prompt

    def test_build_work_item_context(self, agent):
        """Test building context from work item."""
        work_item = {
            "id": "task-abc",
            "source": "github",
            "context": {"issue_number": 42},
        }
        context = agent._build_work_item_context(work_item)

        assert "task-abc" in context
        assert "github" in context


# ============================================================================
# Test Summary Extraction
# ============================================================================


class TestSummaryExtraction:
    """Test summary extraction from content."""

    def test_extract_summary_basic(self, agent):
        """Test basic summary extraction."""
        content = "Fixed the authentication bug.\nMore details here."
        summary = agent._extract_summary(content)
        assert summary == "Fixed the authentication bug."

    def test_extract_summary_skips_headers(self, agent):
        """Test that headers are skipped."""
        content = "# Header\nActual content here."
        summary = agent._extract_summary(content)
        assert summary == "Actual content here."

    def test_extract_summary_truncates(self, agent):
        """Test that long summaries are truncated."""
        content = "A" * 300
        summary = agent._extract_summary(content)
        assert len(summary) == 200

    def test_extract_summary_empty(self, agent):
        """Test empty content."""
        summary = agent._extract_summary("")
        assert summary == ""


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests for end-to-end flows."""

    @pytest.mark.asyncio
    async def test_full_execution_flow(self, agent_with_quality_gates):
        """Test a complete execution flow."""
        # Simulate a realistic response
        mock_messages = [
            {
                "type": "assistant",
                "content": [
                    {"type": "text", "text": "I'll analyze and fix the code."},
                ],
            },
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/src/main.py"},
                    },
                ],
            },
            {
                "type": "assistant",
                "content": [
                    {"type": "text", "text": "Found the bug. Fixing now."},
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {
                            "file_path": "/src/main.py",
                            "old_string": "broken",
                            "new_string": "fixed",
                        },
                    },
                ],
            },
            {"type": "result", "content": "Bug successfully fixed."},
        ]

        async def mock_query(**kwargs):
            for msg in mock_messages:
                yield msg

        with patch("sugar.agent.base.query", mock_query):
            # Start session
            await agent_with_quality_gates.start_session(
                task_context="Fixing authentication bugs"
            )

            # Execute task
            response = await agent_with_quality_gates.execute(
                "Fix the authentication bug in main.py"
            )

            # End session
            await agent_with_quality_gates.end_session()

        # Verify response
        assert response.success is True
        assert "analyze and fix" in response.content
        assert "/src/main.py" in response.files_modified
        assert len(response.tool_uses) == 2  # Read and Edit

        # Verify quality gate results
        assert response.quality_gate_results is not None

    @pytest.mark.asyncio
    async def test_multiple_executions_in_session(self, agent):
        """Test multiple executions within one session."""
        responses = [
            [{"type": "text", "text": "First task done"}],
            [{"type": "text", "text": "Second task done"}],
            [{"type": "text", "text": "Third task done"}],
        ]
        current_response = [0]

        async def mock_query(**kwargs):
            for msg in responses[current_response[0]]:
                yield msg
            current_response[0] += 1

        with patch("sugar.agent.base.query", mock_query):
            await agent.start_session()

            r1 = await agent.execute("Task 1")
            r2 = await agent.execute("Task 2")
            r3 = await agent.execute("Task 3")

            await agent.end_session()

        assert r1.success and r2.success and r3.success
        assert "First" in r1.content
        assert "Second" in r2.content
        assert "Third" in r3.content

        # Check history
        history = agent.get_execution_history()
        assert len(history) == 3
