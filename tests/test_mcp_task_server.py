"""
Tests for Sugar Task Queue MCP Server

Tests all tool functions for the Sugar Task Queue MCP server without requiring
full MCP SDK installation. Uses mocked WorkQueue for isolated unit tests.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Try to import FastMCP and task server components, skip tests if not available
try:
    from sugar.mcp.task_server import (
        create_task_mcp_server,
        get_work_queue,
        get_valid_task_types,
        FASTMCP_AVAILABLE,
    )

    MCP_AVAILABLE = FASTMCP_AVAILABLE
except ImportError:
    MCP_AVAILABLE = False
    create_task_mcp_server = None
    get_work_queue = None
    get_valid_task_types = None


# =============================================================================
# Test Helper Functions
# =============================================================================


def get_tool_function(mcp, tool_name: str):
    """Get a tool function from FastMCP server by name."""
    tool = mcp._tool_manager._tools.get(tool_name)
    if tool:
        return tool.fn
    return None


def get_resource_function(mcp, resource_uri: str):
    """Get a resource function from FastMCP server by URI."""
    resources = mcp._resource_manager._resources
    resource = resources.get(resource_uri)
    if resource:
        return resource.fn
    return None


# =============================================================================
# Helper Function Tests
# =============================================================================


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestGetWorkQueue:
    """Tests for get_work_queue helper function"""

    @patch("sugar.storage.work_queue.WorkQueue")
    @patch("sugar.mcp.task_server.Path")
    def test_get_work_queue_current_dir(self, mock_path_class, mock_queue_class):
        """Test get_work_queue when .sugar exists in current directory"""
        # Setup mocks
        mock_cwd = MagicMock()
        mock_sugar_dir = MagicMock()
        mock_sugar_dir.exists.return_value = True
        mock_cwd.__truediv__ = MagicMock(return_value=mock_sugar_dir)
        mock_path_class.cwd.return_value = mock_cwd

        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue

        result = get_work_queue()

        assert result == mock_queue
        mock_queue_class.assert_called_once()

    @patch("sugar.storage.work_queue.WorkQueue")
    @patch("sugar.mcp.task_server.Path")
    def test_get_work_queue_parent_dir(self, mock_path_class, mock_queue_class):
        """Test get_work_queue searches parent directories"""
        # Setup - .sugar not in current, but in parent
        mock_current_sugar = MagicMock()
        mock_current_sugar.exists.return_value = False

        mock_parent_sugar = MagicMock()
        mock_parent_sugar.exists.return_value = True

        mock_parent = MagicMock()
        mock_parent.__truediv__ = MagicMock(return_value=mock_parent_sugar)

        mock_cwd = MagicMock()
        mock_cwd.__truediv__ = MagicMock(return_value=mock_current_sugar)
        mock_cwd.parents = [mock_parent]
        mock_path_class.cwd.return_value = mock_cwd

        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue

        result = get_work_queue()

        assert result == mock_queue

    @patch("sugar.mcp.task_server.Path")
    def test_get_work_queue_not_found(self, mock_path_class):
        """Test get_work_queue raises RuntimeError when not in Sugar project"""
        # Setup - .sugar not found anywhere
        mock_sugar_dir = MagicMock()
        mock_sugar_dir.exists.return_value = False

        mock_cwd = MagicMock()
        mock_cwd.__truediv__ = MagicMock(return_value=mock_sugar_dir)
        mock_cwd.parents = []
        mock_path_class.cwd.return_value = mock_cwd

        with pytest.raises(RuntimeError, match="Not in a Sugar project"):
            get_work_queue()


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestGetValidTaskTypes:
    """Tests for get_valid_task_types helper"""

    def test_returns_valid_task_types(self):
        """Test that valid task types list is returned"""
        types = get_valid_task_types()

        assert isinstance(types, list)
        assert len(types) > 0
        assert "bug_fix" in types
        assert "feature" in types
        assert "test" in types
        assert "refactor" in types
        assert "documentation" in types


# =============================================================================
# sugar_add_task Tool Tests
# =============================================================================


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestSugarAddTask:
    """Tests for sugar_add_task MCP tool"""

    @pytest.fixture
    def mock_queue(self):
        """Create a mock WorkQueue"""
        queue = AsyncMock()
        queue.initialize = AsyncMock()
        queue.add_work = AsyncMock(return_value="task-uuid-123")
        return queue

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_add_task_success(self, mock_get_queue, mock_queue):
        """Test successful task addition"""
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_add_task")

        result = await tool_func(
            title="Fix login bug",
            type="bug_fix",
            description="Users can't login",
            priority=1,
        )

        assert result["success"] is True
        assert result["task_id"] == "task-uuid-123"
        assert result["title"] == "Fix login bug"
        assert result["type"] == "bug_fix"
        assert result["priority"] == 1
        mock_queue.add_work.assert_called_once()

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_add_task_invalid_type(self, mock_get_queue, mock_queue):
        """Test task addition with invalid type"""
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_add_task")

        result = await tool_func(
            title="Test task",
            type="invalid_type",
        )

        assert result["success"] is False
        assert "Invalid task type" in result["error"]
        mock_queue.add_work.assert_not_called()

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_add_task_invalid_priority(self, mock_get_queue, mock_queue):
        """Test task addition with invalid priority"""
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_add_task")

        result = await tool_func(
            title="Test task",
            priority=10,  # Invalid - must be 1-5
        )

        assert result["success"] is False
        assert "Priority must be between 1" in result["error"]
        mock_queue.add_work.assert_not_called()

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_add_task_invalid_status(self, mock_get_queue, mock_queue):
        """Test task addition with invalid status"""
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_add_task")

        result = await tool_func(
            title="Test task",
            status="completed",  # Invalid - must be pending or hold
        )

        assert result["success"] is False
        assert "must be 'pending' or 'hold'" in result["error"]
        mock_queue.add_work.assert_not_called()

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_add_task_with_acceptance_criteria_json(
        self, mock_get_queue, mock_queue
    ):
        """Test task addition with JSON acceptance criteria"""
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_add_task")

        criteria = json.dumps(
            [{"description": "Tests pass"}, {"description": "Code reviewed"}]
        )

        result = await tool_func(
            title="Feature task",
            acceptance_criteria=criteria,
        )

        assert result["success"] is True
        call_args = mock_queue.add_work.call_args[0][0]
        assert len(call_args["acceptance_criteria"]) == 2

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_add_task_with_acceptance_criteria_string(
        self, mock_get_queue, mock_queue
    ):
        """Test task addition with plain string acceptance criteria"""
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_add_task")

        result = await tool_func(
            title="Feature task",
            acceptance_criteria="All tests must pass",
        )

        assert result["success"] is True
        call_args = mock_queue.add_work.call_args[0][0]
        assert len(call_args["acceptance_criteria"]) == 1
        assert (
            call_args["acceptance_criteria"][0]["description"] == "All tests must pass"
        )

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_add_task_exception_handling(self, mock_get_queue):
        """Test task addition handles exceptions gracefully"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock(side_effect=Exception("Database error"))
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_add_task")

        result = await tool_func(title="Test task")

        assert result["success"] is False
        assert "Database error" in result["error"]


# =============================================================================
# sugar_list_tasks Tool Tests
# =============================================================================


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestSugarListTasks:
    """Tests for sugar_list_tasks MCP tool"""

    @pytest.fixture
    def sample_tasks(self):
        """Sample task data"""
        return [
            {
                "id": "task-123-uuid",
                "title": "Fix bug",
                "type": "bug_fix",
                "priority": 1,
                "status": "pending",
                "created_at": "2025-01-01T00:00:00",
                "attempts": 0,
            },
            {
                "id": "task-456-uuid",
                "title": "Add feature",
                "type": "feature",
                "priority": 3,
                "status": "pending",
                "created_at": "2025-01-02T00:00:00",
                "attempts": 1,
            },
            {
                "id": "task-789-uuid",
                "title": "Write tests",
                "type": "test",
                "priority": 2,
                "status": "hold",
                "created_at": "2025-01-03T00:00:00",
                "attempts": 0,
            },
        ]

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_list_tasks_success(self, mock_get_queue, sample_tasks):
        """Test successful task listing"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_recent_work = AsyncMock(return_value=sample_tasks)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_list_tasks")

        result = await tool_func()

        assert result["success"] is True
        assert result["count"] == 3
        assert len(result["tasks"]) == 3
        assert result["tasks"][0]["id"] == "task-123"  # Short ID
        assert result["tasks"][0]["full_id"] == "task-123-uuid"

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_list_tasks_filter_by_status(self, mock_get_queue, sample_tasks):
        """Test filtering tasks by status"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_recent_work = AsyncMock(return_value=[sample_tasks[0]])
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_list_tasks")

        result = await tool_func(status="pending")

        assert result["success"] is True
        assert result["count"] == 1
        mock_queue.get_recent_work.assert_called_once_with(limit=20, status="pending")

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_list_tasks_filter_by_type(self, mock_get_queue, sample_tasks):
        """Test filtering tasks by type"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_recent_work = AsyncMock(return_value=sample_tasks)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_list_tasks")

        result = await tool_func(type="bug_fix")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["tasks"][0]["type"] == "bug_fix"

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_list_tasks_filter_by_priority(self, mock_get_queue, sample_tasks):
        """Test filtering tasks by priority"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_recent_work = AsyncMock(return_value=sample_tasks)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_list_tasks")

        result = await tool_func(priority=1)

        assert result["success"] is True
        assert result["count"] == 1
        assert result["tasks"][0]["priority"] == 1

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_list_tasks_limit_capped(self, mock_get_queue):
        """Test that limit is capped at 100"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_recent_work = AsyncMock(return_value=[])
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_list_tasks")

        result = await tool_func(limit=500)

        assert result["success"] is True
        # Verify limit was capped to 100
        mock_queue.get_recent_work.assert_called_once_with(limit=100, status=None)

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_list_tasks_empty_result(self, mock_get_queue):
        """Test listing when no tasks exist"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_recent_work = AsyncMock(return_value=[])
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_list_tasks")

        result = await tool_func()

        assert result["success"] is True
        assert result["count"] == 0
        assert result["tasks"] == []

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_list_tasks_exception_handling(self, mock_get_queue):
        """Test task listing handles exceptions gracefully"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock(side_effect=Exception("Database error"))
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_list_tasks")

        result = await tool_func()

        assert result["success"] is False
        assert "Database error" in result["error"]
        assert result["tasks"] == []
        assert result["count"] == 0


# =============================================================================
# sugar_view_task Tool Tests
# =============================================================================


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestSugarViewTask:
    """Tests for sugar_view_task MCP tool"""

    @pytest.fixture
    def full_task(self):
        """Complete task data"""
        return {
            "id": "task-uuid-123",
            "title": "Fix critical bug",
            "type": "bug_fix",
            "description": "Detailed description",
            "priority": 1,
            "status": "completed",
            "source": "github",
            "context": {"issue": 42},
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T01:00:00",
            "started_at": "2025-01-01T00:30:00",
            "completed_at": "2025-01-01T01:00:00",
            "attempts": 1,
            "total_execution_time": 1800.0,
            "total_elapsed_time": 1800.0,
            "result": "Bug fixed successfully",
            "error_message": None,
            "acceptance_criteria": [{"description": "Tests pass"}],
            "commit_sha": "abc123",
        }

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_view_task_found(self, mock_get_queue, full_task):
        """Test viewing task that exists"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_item = AsyncMock(return_value=full_task)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_view_task")

        result = await tool_func(task_id="task-uuid-123")

        assert result["success"] is True
        assert result["task"]["id"] == "task-uuid-123"
        assert result["task"]["title"] == "Fix critical bug"
        assert result["task"]["status"] == "completed"
        assert result["task"]["commit_sha"] == "abc123"

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_view_task_not_found(self, mock_get_queue):
        """Test viewing task that doesn't exist"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_item = AsyncMock(return_value=None)
        mock_queue.get_recent_work = AsyncMock(return_value=[])
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_view_task")

        result = await tool_func(task_id="nonexistent")

        assert result["success"] is False
        assert "Task not found" in result["error"]

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_view_task_prefix_match(self, mock_get_queue, full_task):
        """Test viewing task by short ID prefix"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        # First call returns None (exact match fails)
        mock_queue.get_work_item = AsyncMock(side_effect=[None, full_task])
        # Prefix search returns matching task
        mock_queue.get_recent_work = AsyncMock(return_value=[full_task])
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_view_task")

        result = await tool_func(task_id="task-uuid")

        assert result["success"] is True
        assert result["task"]["id"] == "task-uuid-123"

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_view_task_exception_handling(self, mock_get_queue):
        """Test view task handles exceptions gracefully"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock(side_effect=Exception("Database error"))
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_view_task")

        result = await tool_func(task_id="test")

        assert result["success"] is False
        assert "Database error" in result["error"]


# =============================================================================
# sugar_update_task Tool Tests
# =============================================================================


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestSugarUpdateTask:
    """Tests for sugar_update_task MCP tool"""

    @pytest.fixture
    def existing_task(self):
        """Existing task data"""
        return {
            "id": "task-uuid-456",
            "title": "Original title",
            "type": "feature",
            "priority": 3,
            "status": "pending",
        }

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_update_task_success(self, mock_get_queue, existing_task):
        """Test successful task update"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_item = AsyncMock(return_value=existing_task)
        mock_queue.update_work = AsyncMock(return_value=True)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_update_task")

        result = await tool_func(
            task_id="task-uuid-456",
            title="New title",
            priority=1,
        )

        assert result["success"] is True
        assert "title" in result["updated_fields"]
        assert "priority" in result["updated_fields"]
        mock_queue.update_work.assert_called_once()

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_update_task_not_found(self, mock_get_queue):
        """Test updating task that doesn't exist"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_item = AsyncMock(return_value=None)
        mock_queue.get_recent_work = AsyncMock(return_value=[])
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_update_task")

        result = await tool_func(task_id="nonexistent", title="New")

        assert result["success"] is False
        assert "Task not found" in result["error"]

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_update_task_invalid_type(self, mock_get_queue, existing_task):
        """Test updating with invalid task type"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_item = AsyncMock(return_value=existing_task)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_update_task")

        result = await tool_func(task_id="task-uuid-456", type="invalid_type")

        assert result["success"] is False
        assert "Invalid task type" in result["error"]

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_update_task_invalid_priority(self, mock_get_queue, existing_task):
        """Test updating with invalid priority"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_item = AsyncMock(return_value=existing_task)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_update_task")

        result = await tool_func(task_id="task-uuid-456", priority=10)

        assert result["success"] is False
        assert "Priority must be between 1 and 5" in result["error"]

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_update_task_invalid_status(self, mock_get_queue, existing_task):
        """Test updating with invalid status"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_item = AsyncMock(return_value=existing_task)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_update_task")

        result = await tool_func(task_id="task-uuid-456", status="completed")

        assert result["success"] is False
        assert "Can only change status to 'pending' or 'hold'" in result["error"]

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_update_task_no_fields(self, mock_get_queue, existing_task):
        """Test updating with no fields provided"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_item = AsyncMock(return_value=existing_task)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_update_task")

        result = await tool_func(task_id="task-uuid-456")

        assert result["success"] is False
        assert "No fields to update" in result["error"]

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_update_task_exception_handling(self, mock_get_queue):
        """Test update task handles exceptions gracefully"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock(side_effect=Exception("Database error"))
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_update_task")

        result = await tool_func(task_id="test", title="New")

        assert result["success"] is False
        assert "Database error" in result["error"]


# =============================================================================
# sugar_task_status Tool Tests
# =============================================================================


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestSugarTaskStatus:
    """Tests for sugar_task_status MCP tool"""

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_task_status_success(self, mock_get_queue):
        """Test successful status retrieval"""
        mock_stats = {
            "pending": 5,
            "hold": 2,
            "active": 1,
            "completed": 10,
            "failed": 3,
            "total": 21,
            "recent_24h": 8,
        }
        mock_health = {"status": "healthy", "message": "All systems operational"}

        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_stats = AsyncMock(return_value=mock_stats)
        mock_queue.health_check = AsyncMock(return_value=mock_health)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_task_status")

        result = await tool_func()

        assert result["success"] is True
        assert result["stats"]["pending"] == 5
        assert result["stats"]["completed"] == 10
        assert result["stats"]["total"] == 21
        assert result["health"]["status"] == "healthy"

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_task_status_exception_handling(self, mock_get_queue):
        """Test task status handles exceptions gracefully"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock(side_effect=Exception("Database error"))
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_task_status")

        result = await tool_func()

        assert result["success"] is False
        assert "Database error" in result["error"]


# =============================================================================
# sugar_remove_task Tool Tests
# =============================================================================


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestSugarRemoveTask:
    """Tests for sugar_remove_task MCP tool"""

    @pytest.fixture
    def pending_task(self):
        """Pending task data"""
        return {
            "id": "task-uuid-789",
            "title": "Task to remove",
            "status": "pending",
        }

    @pytest.fixture
    def active_task(self):
        """Active task data"""
        return {
            "id": "task-uuid-999",
            "title": "Active task",
            "status": "active",
        }

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_remove_task_success(self, mock_get_queue, pending_task):
        """Test successful task removal"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_item = AsyncMock(return_value=pending_task)
        mock_queue.remove_work = AsyncMock(return_value=True)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_remove_task")

        result = await tool_func(task_id="task-uuid-789")

        assert result["success"] is True
        assert result["task_id"] == "task-uui"  # Short ID (first 8 chars)
        assert result["was_status"] == "pending"
        mock_queue.remove_work.assert_called_once()

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_remove_active_task_without_force(self, mock_get_queue, active_task):
        """Test removing active task fails without force flag"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_item = AsyncMock(return_value=active_task)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_remove_task")

        result = await tool_func(task_id="task-uuid-999", force=False)

        assert result["success"] is False
        assert "Cannot remove active task" in result["error"]
        assert "force=True" in result["error"]

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_remove_active_task_with_force(self, mock_get_queue, active_task):
        """Test removing active task succeeds with force flag"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_item = AsyncMock(return_value=active_task)
        mock_queue.remove_work = AsyncMock(return_value=True)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_remove_task")

        result = await tool_func(task_id="task-uuid-999", force=True)

        assert result["success"] is True
        assert result["was_status"] == "active"
        mock_queue.remove_work.assert_called_once()

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_remove_task_not_found(self, mock_get_queue):
        """Test removing task that doesn't exist"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_item = AsyncMock(return_value=None)
        mock_queue.get_recent_work = AsyncMock(return_value=[])
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_remove_task")

        result = await tool_func(task_id="nonexistent")

        assert result["success"] is False
        assert "Task not found" in result["error"]

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_remove_task_exception_handling(self, mock_get_queue):
        """Test remove task handles exceptions gracefully"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock(side_effect=Exception("Database error"))
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_remove_task")

        result = await tool_func(task_id="test")

        assert result["success"] is False
        assert "Database error" in result["error"]


# =============================================================================
# MCP Resource Tests
# =============================================================================


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestMCPResources:
    """Tests for MCP resources (pending_tasks, stats)"""

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_pending_tasks_resource(self, mock_get_queue):
        """Test pending tasks resource returns markdown"""
        mock_tasks = [
            {
                "id": "task-123-uuid",
                "title": "Urgent fix",
                "priority": 1,
            },
            {
                "id": "task-456-uuid",
                "title": "Normal task",
                "priority": 3,
            },
        ]

        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_recent_work = AsyncMock(return_value=mock_tasks)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        resource_func = get_resource_function(mcp, "sugar://tasks/pending")

        result = await resource_func()

        assert "# Pending Tasks" in result
        assert "Urgent fix" in result
        assert "Normal task" in result

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_pending_tasks_resource_empty(self, mock_get_queue):
        """Test pending tasks resource when queue is empty"""
        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_recent_work = AsyncMock(return_value=[])
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        resource_func = get_resource_function(mcp, "sugar://tasks/pending")

        result = await resource_func()

        assert "# No Pending Tasks" in result
        assert "queue is empty" in result

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_stats_resource(self, mock_get_queue):
        """Test stats resource returns markdown"""
        mock_stats = {
            "pending": 5,
            "hold": 2,
            "active": 1,
            "completed": 10,
            "failed": 3,
            "total": 21,
            "recent_24h": 8,
        }

        mock_queue = AsyncMock()
        mock_queue.initialize = AsyncMock()
        mock_queue.get_stats = AsyncMock(return_value=mock_stats)
        mock_get_queue.return_value = mock_queue

        mcp = create_task_mcp_server()
        resource_func = get_resource_function(mcp, "sugar://tasks/stats")

        result = await resource_func()

        assert "# Sugar Queue Statistics" in result
        assert "**Pending:** 5" in result
        assert "**Completed:** 10" in result
        assert "**Total:** 21" in result


# =============================================================================
# Error Handling Tests
# =============================================================================


@pytest.mark.skipif(not MCP_AVAILABLE, reason="MCP dependencies not installed")
class TestNotInSugarProject:
    """Tests for error handling when not in a Sugar project"""

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_add_task_not_in_project(self, mock_get_queue):
        """Test add_task fails gracefully when not in Sugar project"""
        mock_get_queue.side_effect = RuntimeError("Not in a Sugar project")

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_add_task")

        result = await tool_func(title="Test")

        assert result["success"] is False
        assert "Not in a Sugar project" in result["error"]

    @pytest.mark.asyncio
    @patch("sugar.mcp.task_server.get_work_queue")
    async def test_list_tasks_not_in_project(self, mock_get_queue):
        """Test list_tasks fails gracefully when not in Sugar project"""
        mock_get_queue.side_effect = RuntimeError("Not in a Sugar project")

        mcp = create_task_mcp_server()
        tool_func = get_tool_function(mcp, "sugar_list_tasks")

        result = await tool_func()

        assert result["success"] is False
        assert "Not in a Sugar project" in result["error"]
