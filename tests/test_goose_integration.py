"""
Tests for Goose MCP Integration - Sugar v3 Compatibility

Tests ensure the Sugar CLI commands work correctly with the MCP server
interface that Goose uses. This validates backwards compatibility between
Sugar v2 and v3 for the Goose integration.

MCP Tools tested:
- createTask -> sugar add
- listTasks -> sugar list
- viewTask -> sugar view
- updateTask -> sugar update
- removeTask -> sugar remove
- getStatus -> sugar status
- runOnce -> sugar run --once
- initSugar -> sugar init
"""

import pytest
import asyncio
import json
import subprocess
import tempfile
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture(scope="module")
def sugar_cmd():
    """Get the path to the sugar command."""
    # Try to find sugar in the virtual environment
    venv_sugar = Path(__file__).parent.parent / "venv" / "bin" / "sugar"
    if venv_sugar.exists():
        return str(venv_sugar)

    # Try to find it in PATH
    import shutil

    sugar_path = shutil.which("sugar")
    if sugar_path:
        return sugar_path

    pytest.skip("Sugar CLI not found in venv or PATH")


@pytest.fixture
def temp_project_dir():
    """Create a temporary project directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def initialized_project(temp_project_dir, sugar_cmd):
    """Create an initialized Sugar project."""
    result = subprocess.run(
        [sugar_cmd, "init"],
        cwd=temp_project_dir,
        capture_output=True,
        encoding="utf-8",
        text=True,
    )
    assert result.returncode == 0, f"Init failed: {result.stderr}"
    return temp_project_dir


# ============================================================================
# CLI Command Compatibility Tests
# ============================================================================


class TestCreateTaskCompatibility:
    """Tests for createTask MCP tool -> sugar add CLI compatibility."""

    def test_add_basic_task(self, initialized_project, sugar_cmd):
        """Test basic task creation."""
        result = subprocess.run(
            [sugar_cmd, "add", "Test task title"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0
        assert "Added" in result.stdout or "created" in result.stdout.lower()

    def test_add_with_type(self, initialized_project, sugar_cmd):
        """Test task creation with --type flag."""
        for task_type in ["bug_fix", "feature", "test", "refactor", "documentation"]:
            result = subprocess.run(
                [sugar_cmd, "add", f"Test {task_type}", "--type", task_type],
                cwd=initialized_project,
                capture_output=True,
                encoding="utf-8",
                text=True,
            )
            assert (
                result.returncode == 0
            ), f"Failed for type {task_type}: {result.stderr}"

    def test_add_with_priority(self, initialized_project, sugar_cmd):
        """Test task creation with --priority flag."""
        for priority in range(1, 6):
            result = subprocess.run(
                [
                    sugar_cmd,
                    "add",
                    f"Priority {priority} task",
                    "--priority",
                    str(priority),
                ],
                cwd=initialized_project,
                capture_output=True,
                encoding="utf-8",
                text=True,
            )
            assert (
                result.returncode == 0
            ), f"Failed for priority {priority}: {result.stderr}"

    def test_add_with_urgent_flag(self, initialized_project, sugar_cmd):
        """Test task creation with --urgent flag."""
        result = subprocess.run(
            [sugar_cmd, "add", "Urgent task", "--urgent"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0
        # Urgent should set priority to 5
        assert "URGENT" in result.stdout or "5" in result.stdout

    def test_add_with_description(self, initialized_project, sugar_cmd):
        """Test task creation with --description flag."""
        result = subprocess.run(
            [
                sugar_cmd,
                "add",
                "Task with description",
                "--description",
                "Detailed description here",
            ],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0


class TestListTasksCompatibility:
    """Tests for listTasks MCP tool -> sugar list CLI compatibility."""

    def test_list_basic(self, initialized_project, sugar_cmd):
        """Test basic task listing."""
        # Add a task first
        subprocess.run(
            [sugar_cmd, "add", "Test task"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
        )

        result = subprocess.run(
            [sugar_cmd, "list"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0

    def test_list_with_status_filter(self, initialized_project, sugar_cmd):
        """Test listing with --status filter."""
        for status in ["pending", "hold", "active", "completed", "failed", "all"]:
            result = subprocess.run(
                [sugar_cmd, "list", "--status", status],
                cwd=initialized_project,
                capture_output=True,
                encoding="utf-8",
                text=True,
            )
            assert (
                result.returncode == 0
            ), f"Failed for status {status}: {result.stderr}"

    def test_list_with_type_filter(self, initialized_project, sugar_cmd):
        """Test listing with --type filter."""
        result = subprocess.run(
            [sugar_cmd, "list", "--type", "feature"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0

    def test_list_with_priority_filter(self, initialized_project, sugar_cmd):
        """Test listing with --priority filter (v3 addition for Goose compatibility)."""
        # Add tasks with different priorities
        subprocess.run(
            [sugar_cmd, "add", "Priority 3 task", "--priority", "3"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
        )
        subprocess.run(
            [sugar_cmd, "add", "Priority 5 task", "--priority", "5"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
        )

        # Filter by priority 5
        result = subprocess.run(
            [sugar_cmd, "list", "--priority", "5", "--format", "json"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0

        # Parse JSON and verify only priority 5 tasks returned
        tasks = json.loads(result.stdout)
        for task in tasks:
            assert task.get("priority") == 5, f"Task has wrong priority: {task}"

    def test_list_with_limit(self, initialized_project, sugar_cmd):
        """Test listing with --limit flag."""
        result = subprocess.run(
            [sugar_cmd, "list", "--limit", "10"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0

    def test_list_json_format(self, initialized_project, sugar_cmd):
        """Test JSON output format for structured parsing."""
        subprocess.run(
            [sugar_cmd, "add", "Test task"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
        )

        result = subprocess.run(
            [sugar_cmd, "list", "--format", "json"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0

        # Should be valid JSON
        tasks = json.loads(result.stdout)
        assert isinstance(tasks, list)


class TestViewTaskCompatibility:
    """Tests for viewTask MCP tool -> sugar view CLI compatibility."""

    def test_view_task_by_id(self, initialized_project, sugar_cmd):
        """Test viewing a task by ID."""
        # Add a task and get its ID
        add_result = subprocess.run(
            [sugar_cmd, "add", "Task to view"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert add_result.returncode == 0

        # Get the task ID from the list
        list_result = subprocess.run(
            [sugar_cmd, "list", "--format", "json"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        tasks = json.loads(list_result.stdout)
        task_id = tasks[0]["id"]

        # View the task
        result = subprocess.run(
            [sugar_cmd, "view", task_id],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0


class TestUpdateTaskCompatibility:
    """Tests for updateTask MCP tool -> sugar update CLI compatibility."""

    def test_update_task_title(self, initialized_project, sugar_cmd):
        """Test updating task title."""
        # Add a task
        subprocess.run(
            [sugar_cmd, "add", "Original title"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
        )

        # Get task ID
        list_result = subprocess.run(
            [sugar_cmd, "list", "--format", "json"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        tasks = json.loads(list_result.stdout)
        task_id = tasks[0]["id"]

        # Update the title
        result = subprocess.run(
            [sugar_cmd, "update", task_id, "--title", "Updated title"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0

    def test_update_task_priority(self, initialized_project, sugar_cmd):
        """Test updating task priority."""
        subprocess.run(
            [sugar_cmd, "add", "Test task", "--priority", "3"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
        )

        list_result = subprocess.run(
            [sugar_cmd, "list", "--format", "json"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        tasks = json.loads(list_result.stdout)
        task_id = tasks[0]["id"]

        result = subprocess.run(
            [sugar_cmd, "update", task_id, "--priority", "5"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0

    def test_update_task_status(self, initialized_project, sugar_cmd):
        """Test updating task status including 'hold' (v3 feature)."""
        subprocess.run(
            [sugar_cmd, "add", "Test task"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
        )

        list_result = subprocess.run(
            [sugar_cmd, "list", "--format", "json"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        tasks = json.loads(list_result.stdout)
        task_id = tasks[0]["id"]

        # Test updating to 'hold' status (v3 addition)
        for status in ["hold", "pending", "completed"]:
            result = subprocess.run(
                [sugar_cmd, "update", task_id, "--status", status],
                cwd=initialized_project,
                capture_output=True,
                encoding="utf-8",
                text=True,
            )
            assert (
                result.returncode == 0
            ), f"Failed for status {status}: {result.stderr}"


class TestRemoveTaskCompatibility:
    """Tests for removeTask MCP tool -> sugar remove CLI compatibility."""

    def test_remove_task(self, initialized_project, sugar_cmd):
        """Test removing a task by ID."""
        subprocess.run(
            [sugar_cmd, "add", "Task to remove"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
        )

        list_result = subprocess.run(
            [sugar_cmd, "list", "--format", "json"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        tasks = json.loads(list_result.stdout)
        task_id = tasks[0]["id"]

        result = subprocess.run(
            [sugar_cmd, "remove", task_id],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0


class TestGetStatusCompatibility:
    """Tests for getStatus MCP tool -> sugar status CLI compatibility."""

    def test_status_command(self, initialized_project, sugar_cmd):
        """Test status command returns expected format."""
        result = subprocess.run(
            [sugar_cmd, "status"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0
        # Should contain status information
        assert "Tasks" in result.stdout or "Status" in result.stdout


class TestRunOnceCompatibility:
    """Tests for runOnce MCP tool -> sugar run --once CLI compatibility."""

    def test_run_once_dry_run(self, initialized_project, sugar_cmd):
        """Test run --once with --dry-run flag."""
        subprocess.run(
            [sugar_cmd, "add", "Test task"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
        )

        result = subprocess.run(
            [sugar_cmd, "run", "--once", "--dry-run"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
            timeout=60,
        )
        assert result.returncode == 0

    def test_run_once_validate(self, initialized_project, sugar_cmd):
        """Test run --once with --validate flag."""
        result = subprocess.run(
            [sugar_cmd, "run", "--once", "--validate"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
            timeout=60,
        )
        # Should at least not crash
        assert result.returncode in [0, 1]  # May fail if no tasks


class TestInitSugarCompatibility:
    """Tests for initSugar MCP tool -> sugar init CLI compatibility."""

    def test_init_creates_sugar_directory(self, temp_project_dir, sugar_cmd):
        """Test init creates .sugar directory."""
        result = subprocess.run(
            [sugar_cmd, "init"],
            cwd=temp_project_dir,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0
        assert (Path(temp_project_dir) / ".sugar").exists()

    def test_init_creates_config(self, temp_project_dir, sugar_cmd):
        """Test init creates config.yaml."""
        subprocess.run(
            [sugar_cmd, "init"],
            cwd=temp_project_dir,
            capture_output=True,
            encoding="utf-8",
        )
        assert (Path(temp_project_dir) / ".sugar" / "config.yaml").exists()


# ============================================================================
# V3 Executor Integration Tests
# ============================================================================


class TestV3ExecutorIntegration:
    """Tests for v3 Agent SDK executor integration."""

    def test_executor_selection_sdk(self, initialized_project, sugar_cmd):
        """Test that v3 uses Agent SDK executor by default."""
        result = subprocess.run(
            [sugar_cmd, "run", "--once", "--dry-run"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
            timeout=60,
        )
        # Should show Agent SDK executor in output or succeed
        assert "Agent SDK" in (result.stderr or "") or result.returncode == 0

    def test_quality_gates_enabled(self, initialized_project, sugar_cmd):
        """Test that quality gates are enabled in v3."""
        # Quality gates should be enabled by default in v3 config
        config_path = Path(initialized_project) / ".sugar" / "config.yaml"
        with open(config_path) as f:
            config_content = f.read()
        # Should have quality gates enabled (v3 feature)
        assert "quality_gates:" in config_content
        assert "enabled: true" in config_content


# ============================================================================
# Output Parsing Compatibility Tests
# ============================================================================


class TestOutputParsingCompatibility:
    """Tests that CLI output can be parsed by MCP server patterns."""

    def test_add_output_contains_task_id_pattern(self, initialized_project, sugar_cmd):
        """Test add command output matches MCP server parsing patterns.

        MCP server uses these patterns:
        - /Task (?:created|added).*?:\\s*(.+)/i
        - /ID:\\s*(.+)/i
        """
        result = subprocess.run(
            [sugar_cmd, "add", "Test task"],
            cwd=initialized_project,
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        assert result.returncode == 0

        import re

        # At least one of the MCP patterns should match
        pattern1 = re.search(
            r"Task (?:created|added).*?:\s*(.+)", result.stdout, re.IGNORECASE
        )
        pattern2 = re.search(r"ID:\s*(.+)", result.stdout, re.IGNORECASE)

        assert (
            pattern1 or pattern2 or "Added" in result.stdout
        ), f"Output doesn't match MCP parsing patterns: {result.stdout}"


# ============================================================================
# CLI Help Compatibility Tests
# ============================================================================


class TestCLIHelpCompatibility:
    """Verify all expected CLI options exist."""

    def test_add_has_required_options(self, sugar_cmd):
        """Verify add command has all options MCP expects."""
        result = subprocess.run(
            [sugar_cmd, "add", "--help"],
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        help_text = result.stdout

        required_options = ["--type", "--priority", "--urgent", "--description"]
        for opt in required_options:
            assert opt in help_text, f"Missing option {opt} in add command"

    def test_list_has_required_options(self, sugar_cmd):
        """Verify list command has all options MCP expects."""
        result = subprocess.run(
            [sugar_cmd, "list", "--help"],
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        help_text = result.stdout

        required_options = ["--status", "--type", "--priority", "--limit"]
        for opt in required_options:
            assert opt in help_text, f"Missing option {opt} in list command"

    def test_update_has_required_options(self, sugar_cmd):
        """Verify update command has all options MCP expects."""
        result = subprocess.run(
            [sugar_cmd, "update", "--help"],
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        help_text = result.stdout

        required_options = [
            "--title",
            "--type",
            "--priority",
            "--status",
            "--description",
        ]
        for opt in required_options:
            assert opt in help_text, f"Missing option {opt} in update command"

    def test_run_has_required_options(self, sugar_cmd):
        """Verify run command has all options MCP expects."""
        result = subprocess.run(
            [sugar_cmd, "run", "--help"],
            capture_output=True,
            encoding="utf-8",
            text=True,
        )
        help_text = result.stdout

        required_options = ["--once", "--dry-run", "--validate"]
        for opt in required_options:
            assert opt in help_text, f"Missing option {opt} in run command"
