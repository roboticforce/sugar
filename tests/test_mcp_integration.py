"""
Integration tests for MCP server configuration and execution success handling.

These tests verify:
1. MCP server configuration is properly logged
2. Execution success/failure is properly detected and handled
3. Tasks are correctly marked as complete or failed based on result
"""

import pytest
import asyncio
import yaml
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call
from io import StringIO

from sugar.executor.agent_sdk_executor import AgentSDKExecutor
from sugar.core.loop import SugarLoop


class TestMCPServerLogging:
    """Test MCP server configuration logging."""

    def test_mcp_servers_logged_when_configured(self, caplog):
        """Verify MCP servers are logged at INFO level when configured."""
        config = {
            "model": "claude-sonnet-4-20250514",
            "timeout": 300,
            "dry_run": True,
            "mcp_servers": {
                "playwright": {
                    "command": "npx",
                    "args": ["-y", "@anthropic/mcp-server-playwright"],
                },
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@anthropic/mcp-server-filesystem"],
                },
            },
        }

        with caplog.at_level(logging.INFO):
            executor = AgentSDKExecutor(config)

        # Verify INFO log about MCP servers
        assert "MCP servers configured: ['playwright', 'filesystem']" in caplog.text
        # Verify the servers are stored
        assert executor.mcp_servers == config["mcp_servers"]

    def test_mcp_servers_debug_logged_when_configured(self, caplog):
        """Verify individual MCP server details are logged at DEBUG level."""
        config = {
            "model": "claude-sonnet-4-20250514",
            "timeout": 300,
            "dry_run": True,
            "mcp_servers": {
                "playwright": {
                    "command": "npx",
                    "args": ["-y", "@anthropic/mcp-server-playwright"],
                },
            },
        }

        with caplog.at_level(logging.DEBUG):
            executor = AgentSDKExecutor(config)

        # Verify DEBUG log with command details
        assert "playwright: npx" in caplog.text

    def test_no_mcp_servers_debug_logged(self, caplog):
        """Verify debug message when no MCP servers configured."""
        config = {
            "model": "claude-sonnet-4-20250514",
            "timeout": 300,
            "dry_run": True,
        }

        with caplog.at_level(logging.DEBUG):
            executor = AgentSDKExecutor(config)

        # Verify DEBUG log indicates no MCP servers
        assert "No MCP servers configured" in caplog.text
        assert executor.mcp_servers == {}

    def test_empty_mcp_servers_dict(self, caplog):
        """Verify empty MCP servers dict is handled correctly."""
        config = {
            "model": "claude-sonnet-4-20250514",
            "timeout": 300,
            "dry_run": True,
            "mcp_servers": {},
        }

        with caplog.at_level(logging.DEBUG):
            executor = AgentSDKExecutor(config)

        assert "No MCP servers configured" in caplog.text
        assert executor.mcp_servers == {}

    def test_mcp_servers_passed_to_agent_config(self):
        """Verify MCP servers are passed to agent config."""
        config = {
            "model": "claude-sonnet-4-20250514",
            "timeout": 300,
            "dry_run": True,
            "mcp_servers": {
                "playwright": {"command": "npx"},
            },
        }

        executor = AgentSDKExecutor(config)
        agent_config = executor._create_agent_config()

        assert agent_config.mcp_servers == {"playwright": {"command": "npx"}}


class TestExecutionSuccessHandling:
    """Test execution success/failure detection in SugarLoop."""

    @pytest.fixture
    def loop_with_mocks(self, sugar_config_file):
        """Create a SugarLoop with mocked dependencies."""
        with (
            patch("sugar.core.loop.WorkQueue"),
            patch("sugar.core.loop.ClaudeWrapper"),
            patch("sugar.core.loop.AgentSDKExecutor"),
            patch("sugar.core.loop.ErrorLogMonitor"),
            patch("sugar.core.loop.WorkflowOrchestrator"),
        ):
            loop = SugarLoop(str(sugar_config_file))

            # Set up common mocks
            loop.work_queue = AsyncMock()
            loop.work_queue.fail_work = AsyncMock()
            loop.work_queue.complete_work = AsyncMock()
            loop.workflow_orchestrator = AsyncMock()
            loop.workflow_orchestrator.prepare_work_execution = AsyncMock(
                return_value={}
            )
            loop.workflow_orchestrator.complete_work_execution = AsyncMock(
                return_value=True
            )
            loop.executor = AsyncMock()
            loop._handle_failed_workflow = AsyncMock()

            yield loop

    @pytest.mark.asyncio
    async def test_success_true_completes_work(self, loop_with_mocks):
        """Verify task with success=True is marked complete."""
        loop = loop_with_mocks

        mock_task = {
            "id": "task-success",
            "type": "bug_fix",
            "title": "Fix bug",
            "priority": 5,
        }

        loop.work_queue.get_next_work = AsyncMock(side_effect=[mock_task, None])
        loop.executor.execute_work = AsyncMock(
            return_value={
                "success": True,
                "result": "Task completed successfully",
                "files_changed": ["src/fix.py"],
            }
        )

        await loop._execute_work()

        # Verify complete_work was called, not fail_work
        loop.work_queue.complete_work.assert_called_once()
        loop.work_queue.fail_work.assert_not_called()
        loop._handle_failed_workflow.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_false_fails_work(self, loop_with_mocks):
        """Verify task with success=False is marked failed."""
        loop = loop_with_mocks

        mock_task = {
            "id": "task-failure",
            "type": "bug_fix",
            "title": "Fix bug",
            "priority": 5,
        }

        loop.work_queue.get_next_work = AsyncMock(side_effect=[mock_task, None])
        loop.executor.execute_work = AsyncMock(
            return_value={
                "success": False,
                "error": "Could not complete the task",
            }
        )

        await loop._execute_work()

        # Verify fail_work was called, not complete_work
        loop.work_queue.fail_work.assert_called_once()
        loop.work_queue.complete_work.assert_not_called()
        loop._handle_failed_workflow.assert_called_once()

        # Verify error message was passed
        call_args = loop.work_queue.fail_work.call_args
        assert call_args[0][0] == "task-failure"
        assert "Could not complete the task" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_success_false_uses_summary_if_no_error(self, loop_with_mocks):
        """Verify summary is used as error message if error field missing."""
        loop = loop_with_mocks

        mock_task = {
            "id": "task-no-error",
            "type": "bug_fix",
            "title": "Fix bug",
            "priority": 5,
        }

        loop.work_queue.get_next_work = AsyncMock(side_effect=[mock_task, None])
        loop.executor.execute_work = AsyncMock(
            return_value={
                "success": False,
                "summary": "Task failed due to permissions",
            }
        )

        await loop._execute_work()

        loop.work_queue.fail_work.assert_called_once()
        call_args = loop.work_queue.fail_work.call_args
        assert "Task failed due to permissions" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_missing_success_field_logs_warning(self, loop_with_mocks, caplog):
        """Verify warning is logged when success field is missing."""
        loop = loop_with_mocks

        mock_task = {
            "id": "task-no-success",
            "type": "bug_fix",
            "title": "Fix bug",
            "priority": 5,
        }

        loop.work_queue.get_next_work = AsyncMock(side_effect=[mock_task, None])
        # Return result without 'success' field
        loop.executor.execute_work = AsyncMock(
            return_value={
                "result": "Something happened",
            }
        )

        with caplog.at_level(logging.WARNING):
            await loop._execute_work()

        # Verify warning was logged about missing success field
        assert "Missing 'success' field" in caplog.text
        assert "task-no-success" in caplog.text

        # With default=False, task should be marked as failed
        loop.work_queue.fail_work.assert_called_once()
        loop.work_queue.complete_work.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_during_execution_fails_work(self, loop_with_mocks):
        """Verify exception during execution marks work as failed."""
        loop = loop_with_mocks

        mock_task = {
            "id": "task-exception",
            "type": "bug_fix",
            "title": "Fix bug",
            "priority": 5,
        }

        loop.work_queue.get_next_work = AsyncMock(side_effect=[mock_task, None])
        loop.executor.execute_work = AsyncMock(
            side_effect=Exception("Claude API error")
        )

        await loop._execute_work()

        loop.work_queue.fail_work.assert_called_once()
        loop.work_queue.complete_work.assert_not_called()

        # Verify error message contains exception
        call_args = loop.work_queue.fail_work.call_args
        assert "Claude API error" in call_args[0][1]


class TestMCPConfigInSugarLoop:
    """Test MCP config flows through SugarLoop to executor."""

    def test_mcp_servers_in_config_passed_to_executor(self, temp_dir):
        """Verify MCP servers from config are passed to executor."""
        config_path = temp_dir / ".sugar" / "config.yaml"
        config_path.parent.mkdir()

        config_data = {
            "sugar": {
                "dry_run": True,
                "loop_interval": 300,
                "max_concurrent_work": 3,
                "claude": {
                    "command": "claude",
                    "mcp_servers": {
                        "playwright": {
                            "command": "npx",
                            "args": ["-y", "@anthropic/mcp-server-playwright"],
                        }
                    },
                },
                "storage": {"database": "sugar.db"},
                "discovery": {
                    "error_logs": {"enabled": False},
                    "github": {"enabled": False},
                    "code_quality": {"enabled": False, "root_path": "."},
                    "test_coverage": {"enabled": False, "root_path": "."},
                },
            }
        }

        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        with (
            patch("sugar.core.loop.WorkQueue"),
            patch("sugar.core.loop.ClaudeWrapper"),
            patch("sugar.core.loop.AgentSDKExecutor") as mock_executor,
            patch("sugar.core.loop.ErrorLogMonitor"),
        ):
            SugarLoop(str(config_path))

            # Verify executor was called with config containing mcp_servers
            call_args = mock_executor.call_args
            config_passed = call_args[0][0]
            assert "mcp_servers" in config_passed
            assert "playwright" in config_passed["mcp_servers"]


class TestEndToEndExecution:
    """End-to-end tests for the full execution flow."""

    @pytest.fixture
    def full_config(self, temp_dir):
        """Create a complete config for end-to-end testing."""
        config_path = temp_dir / ".sugar" / "config.yaml"
        config_path.parent.mkdir()

        config_data = {
            "sugar": {
                "dry_run": True,
                "loop_interval": 60,
                "max_concurrent_work": 1,
                "claude": {
                    "command": "claude",
                    "timeout": 60,
                    "executor": "sdk",
                    "mcp_servers": {
                        "playwright": {
                            "command": "npx",
                            "args": ["-y", "@anthropic/mcp-server-playwright"],
                        }
                    },
                },
                "storage": {"database": str(temp_dir / "sugar.db")},
                "discovery": {
                    "error_logs": {"enabled": False},
                    "github": {"enabled": False},
                    "code_quality": {"enabled": False, "root_path": "."},
                    "test_coverage": {"enabled": False, "root_path": "."},
                },
            }
        }

        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        return config_path

    @pytest.mark.asyncio
    async def test_full_success_flow(self, full_config, caplog):
        """Test complete flow from task pickup to completion."""
        with (
            patch("sugar.core.loop.WorkQueue") as mock_queue_class,
            patch("sugar.core.loop.AgentSDKExecutor") as mock_executor_class,
            patch("sugar.core.loop.ErrorLogMonitor"),
            patch("sugar.core.loop.WorkflowOrchestrator") as mock_workflow_class,
        ):
            # Set up mocks
            mock_queue = AsyncMock()
            mock_queue_class.return_value = mock_queue

            mock_executor = AsyncMock()
            mock_executor_class.return_value = mock_executor

            mock_workflow = AsyncMock()
            mock_workflow_class.return_value = mock_workflow

            # Configure task and result
            test_task = {
                "id": "e2e-task-1",
                "type": "test",
                "title": "Run browser test",
                "priority": 5,
            }

            mock_queue.get_next_work = AsyncMock(side_effect=[test_task, None])
            mock_queue.complete_work = AsyncMock()
            mock_queue.fail_work = AsyncMock()

            mock_executor.execute_work = AsyncMock(
                return_value={
                    "success": True,
                    "result": "Browser test completed",
                    "files_changed": [],
                }
            )

            mock_workflow.prepare_work_execution = AsyncMock(return_value={})
            mock_workflow.complete_work_execution = AsyncMock(return_value=True)

            with caplog.at_level(logging.INFO):
                loop = SugarLoop(str(full_config))
                loop.work_queue = mock_queue
                loop.executor = mock_executor
                loop.workflow_orchestrator = mock_workflow

                await loop._execute_work()

            # Verify success flow
            mock_executor.execute_work.assert_called_once_with(test_task)
            mock_queue.complete_work.assert_called_once()
            mock_queue.fail_work.assert_not_called()

            # Verify completion was logged
            assert "Work completed" in caplog.text

    @pytest.mark.asyncio
    async def test_full_failure_flow(self, full_config, caplog):
        """Test complete flow from task pickup to failure."""
        with (
            patch("sugar.core.loop.WorkQueue") as mock_queue_class,
            patch("sugar.core.loop.AgentSDKExecutor") as mock_executor_class,
            patch("sugar.core.loop.ErrorLogMonitor"),
            patch("sugar.core.loop.WorkflowOrchestrator") as mock_workflow_class,
        ):
            # Set up mocks
            mock_queue = AsyncMock()
            mock_queue_class.return_value = mock_queue

            mock_executor = AsyncMock()
            mock_executor_class.return_value = mock_executor

            mock_workflow = AsyncMock()
            mock_workflow_class.return_value = mock_workflow

            # Configure task and failed result
            test_task = {
                "id": "e2e-task-fail",
                "type": "test",
                "title": "Run browser test",
                "priority": 5,
            }

            mock_queue.get_next_work = AsyncMock(side_effect=[test_task, None])
            mock_queue.complete_work = AsyncMock()
            mock_queue.fail_work = AsyncMock()

            mock_executor.execute_work = AsyncMock(
                return_value={
                    "success": False,
                    "error": "Browser automation failed: element not found",
                }
            )

            mock_workflow.prepare_work_execution = AsyncMock(return_value={})

            with caplog.at_level(logging.WARNING):
                loop = SugarLoop(str(full_config))
                loop.work_queue = mock_queue
                loop.executor = mock_executor
                loop.workflow_orchestrator = mock_workflow
                loop._handle_failed_workflow = AsyncMock()

                await loop._execute_work()

            # Verify failure flow
            mock_executor.execute_work.assert_called_once_with(test_task)
            mock_queue.fail_work.assert_called_once()
            mock_queue.complete_work.assert_not_called()

            # Verify failure was logged
            assert (
                "Task execution failed" in caplog.text or "e2e-task-fail" in caplog.text
            )
