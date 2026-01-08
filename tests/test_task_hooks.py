"""
Tests for task execution hooks

Tests the HookExecutor and integration with task type system.
"""

import sys
import pytest
import pytest_asyncio
from pathlib import Path
import tempfile
import shutil

from sugar.executor.hooks import HookExecutor
from sugar.storage.task_type_manager import TaskTypeManager


class TestHookExecutor:
    """Tests for HookExecutor"""

    @pytest.fixture
    def temp_project_dir(self):
        """Create a temporary project directory"""
        temp_dir = Path(tempfile.mkdtemp())
        yield temp_dir
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def hook_executor(self, temp_project_dir):
        """Create a HookExecutor instance"""
        return HookExecutor(str(temp_project_dir))

    @pytest.fixture
    def sample_task(self):
        """Sample task for variable substitution"""
        return {
            "id": "task-123",
            "type": "bug_fix",
            "title": "Fix critical bug",
            "priority": 1,
        }

    @pytest.mark.asyncio
    async def test_no_hooks_returns_success(self, hook_executor, sample_task):
        """Empty hook list should succeed immediately"""
        result = await hook_executor.execute_hooks([], "pre_hooks", sample_task)

        assert result["success"] is True
        assert result["outputs"] == []
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_simple_passing_hook(self, hook_executor, sample_task):
        """Simple shell command that succeeds"""
        hooks = ["echo 'test'"]
        result = await hook_executor.execute_hooks(hooks, "pre_hooks", sample_task)

        assert result["success"] is True
        assert len(result["outputs"]) == 1
        assert result["outputs"][0]["returncode"] == 0
        assert "test" in result["outputs"][0]["stdout"]

    @pytest.mark.asyncio
    async def test_failing_hook_stops_execution(self, hook_executor, sample_task):
        """Failing hook should stop execution and return failure"""
        hooks = ["exit 1"]
        result = await hook_executor.execute_hooks(hooks, "post_hooks", sample_task)

        assert result["success"] is False
        assert "failed_hook" in result
        assert result["failed_hook"] == "exit 1"
        assert len(result["errors"]) == 1

    @pytest.mark.asyncio
    async def test_multiple_hooks_stop_on_first_failure(
        self, hook_executor, sample_task
    ):
        """Multiple hooks should stop at first failure"""
        hooks = ["echo 'first'", "exit 1", "echo 'third'"]  # Should not execute
        result = await hook_executor.execute_hooks(hooks, "pre_hooks", sample_task)

        assert result["success"] is False
        assert len(result["outputs"]) == 2  # First passes, second fails
        assert result["failed_hook_index"] == 1

    @pytest.mark.asyncio
    async def test_all_hooks_pass(self, hook_executor, sample_task):
        """All hooks passing should succeed"""
        hooks = ["echo 'first'", "echo 'second'", "echo 'third'"]
        result = await hook_executor.execute_hooks(hooks, "post_hooks", sample_task)

        assert result["success"] is True
        assert len(result["outputs"]) == 3
        assert all(output["returncode"] == 0 for output in result["outputs"])

    @pytest.mark.asyncio
    async def test_variable_substitution(self, hook_executor, sample_task):
        """Hook commands should have variables substituted"""
        hooks = ["echo 'Task: {task_id} - {task_type} - {task_title}'"]
        result = await hook_executor.execute_hooks(hooks, "pre_hooks", sample_task)

        assert result["success"] is True
        output = result["outputs"][0]["stdout"]
        assert "task-123" in output
        assert "bug_fix" in output
        assert "Fix critical bug" in output

    @pytest.mark.asyncio
    async def test_hook_timeout(self, hook_executor, sample_task):
        """Hook should timeout if it runs too long"""
        hooks = ["sleep 10"]  # Will timeout
        result = await hook_executor.execute_hooks(
            hooks, "pre_hooks", sample_task, timeout=1  # 1 second timeout
        )

        assert result["success"] is False
        assert "timeout" in result
        assert result["timeout"] is True

    @pytest.mark.asyncio
    async def test_hook_working_directory(
        self, hook_executor, sample_task, temp_project_dir
    ):
        """Hooks should execute in project directory"""
        # Create a test file in project dir
        test_file = temp_project_dir / "test.txt"
        test_file.write_text("content")

        hooks = ["cat test.txt"]
        result = await hook_executor.execute_hooks(hooks, "pre_hooks", sample_task)

        assert result["success"] is True
        assert "content" in result["outputs"][0]["stdout"]

    @pytest.mark.asyncio
    async def test_hook_captures_stderr(self, hook_executor, sample_task):
        """Hook should capture both stdout and stderr"""
        hooks = ["echo 'error' >&2"]
        result = await hook_executor.execute_hooks(hooks, "pre_hooks", sample_task)

        assert result["success"] is True
        assert "error" in result["outputs"][0]["stderr"]


class TestTaskTypeHooks:
    """Tests for task type hooks integration"""

    @pytest_asyncio.fixture
    async def task_type_manager(self, temp_dir):
        """Create a TaskTypeManager with test database"""
        db_path = temp_dir / "test_hooks.db"
        manager = TaskTypeManager(str(db_path))
        await manager.initialize()
        return manager

    @pytest.mark.asyncio
    async def test_default_hooks_for_bug_fix(self, task_type_manager):
        """Bug fix task type should have default hooks"""
        pre_hooks = await task_type_manager.get_pre_hooks_for_type("bug_fix")
        post_hooks = await task_type_manager.get_post_hooks_for_type("bug_fix")

        assert isinstance(pre_hooks, list)
        assert isinstance(post_hooks, list)
        # Bug fixes should have test hook
        assert any("pytest" in hook for hook in post_hooks)

    @pytest.mark.asyncio
    async def test_default_hooks_for_feature(self, task_type_manager):
        """Feature task type should have default hooks"""
        pre_hooks = await task_type_manager.get_pre_hooks_for_type("feature")
        post_hooks = await task_type_manager.get_post_hooks_for_type("feature")

        assert isinstance(pre_hooks, list)
        assert isinstance(post_hooks, list)
        # Features should have test and formatting hooks
        assert any("pytest" in hook for hook in post_hooks)
        assert any("black" in hook for hook in post_hooks)

    @pytest.mark.asyncio
    async def test_set_custom_hooks(self, task_type_manager):
        """Should be able to set custom hooks for a task type"""
        custom_pre = ["echo 'starting'"]
        custom_post = ["echo 'done'", "pytest tests/"]

        success = await task_type_manager.set_hooks_for_type(
            "bug_fix", pre_hooks=custom_pre, post_hooks=custom_post
        )

        assert success is True

        # Verify hooks were set
        pre_hooks = await task_type_manager.get_pre_hooks_for_type("bug_fix")
        post_hooks = await task_type_manager.get_post_hooks_for_type("bug_fix")

        assert pre_hooks == custom_pre
        assert post_hooks == custom_post

    @pytest.mark.asyncio
    async def test_set_only_pre_hooks(self, task_type_manager):
        """Should be able to set only pre hooks"""
        custom_pre = ["echo 'pre check'"]

        success = await task_type_manager.set_hooks_for_type(
            "test", pre_hooks=custom_pre
        )

        assert success is True
        pre_hooks = await task_type_manager.get_pre_hooks_for_type("test")
        assert pre_hooks == custom_pre

    @pytest.mark.asyncio
    async def test_set_only_post_hooks(self, task_type_manager):
        """Should be able to set only post hooks"""
        custom_post = ["pytest -v"]

        success = await task_type_manager.set_hooks_for_type(
            "refactor", post_hooks=custom_post
        )

        assert success is True
        post_hooks = await task_type_manager.get_post_hooks_for_type("refactor")
        assert post_hooks == custom_post

    @pytest.mark.asyncio
    async def test_empty_hooks_for_nonexistent_type(self, task_type_manager):
        """Should return empty lists for nonexistent task type"""
        pre_hooks = await task_type_manager.get_pre_hooks_for_type("nonexistent")
        post_hooks = await task_type_manager.get_post_hooks_for_type("nonexistent")

        assert pre_hooks == []
        assert post_hooks == []

    @pytest.mark.asyncio
    async def test_get_task_type_includes_hooks(self, task_type_manager):
        """get_task_type should include hooks in response"""
        task_type = await task_type_manager.get_task_type("feature")

        assert "pre_hooks" in task_type
        assert "post_hooks" in task_type
        assert isinstance(task_type["pre_hooks"], list)
        assert isinstance(task_type["post_hooks"], list)


class TestHookErrorHandling:
    """Tests for hook error handling"""

    @pytest.fixture
    def temp_project_dir(self):
        """Create a temporary project directory"""
        temp_dir = Path(tempfile.mkdtemp())
        yield temp_dir
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def hook_executor(self, temp_project_dir):
        """Create a HookExecutor instance"""
        return HookExecutor(str(temp_project_dir))

    @pytest.fixture
    def sample_task(self):
        """Sample task for testing"""
        return {
            "id": "task-error-test",
            "type": "test",
            "title": "Error handling test",
            "priority": 3,
        }

    @pytest.mark.asyncio
    async def test_nonexistent_command(self, hook_executor, sample_task):
        """Hook with nonexistent command should fail gracefully"""
        hooks = ["nonexistent_command_12345"]
        result = await hook_executor.execute_hooks(hooks, "pre_hooks", sample_task)

        assert result["success"] is False
        assert "failed_hook" in result

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows shell handles quotes differently - not a syntax error",
    )
    async def test_syntax_error_in_command(self, hook_executor, sample_task):
        """Shell syntax errors should be caught"""
        hooks = ["echo 'unclosed quote"]
        result = await hook_executor.execute_hooks(hooks, "pre_hooks", sample_task)

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_hook_with_stderr_but_success(self, hook_executor, sample_task):
        """Hook can write to stderr and still succeed"""
        hooks = ["(echo 'warning' >&2) && exit 0"]
        result = await hook_executor.execute_hooks(hooks, "pre_hooks", sample_task)

        assert result["success"] is True
        assert "warning" in result["outputs"][0]["stderr"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
