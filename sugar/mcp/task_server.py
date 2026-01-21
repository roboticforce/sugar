"""
Sugar Task Queue MCP Server

Provides MCP (Model Context Protocol) server for Sugar task queue management,
allowing OpenCode, Claude Code, and other MCP clients to manage autonomous tasks.

Uses FastMCP for simplified server implementation.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Check for FastMCP availability
try:
    from mcp.server.fastmcp import FastMCP

    FASTMCP_AVAILABLE = True
except ImportError:
    try:
        from fastmcp import FastMCP

        FASTMCP_AVAILABLE = True
    except ImportError:
        FASTMCP_AVAILABLE = False
        FastMCP = None


def get_work_queue():
    """Get WorkQueue instance from Sugar project context."""
    from sugar.storage.work_queue import WorkQueue

    # Try to find .sugar directory
    cwd = Path.cwd()
    sugar_dir = cwd / ".sugar"

    if not sugar_dir.exists():
        # Check parent directories
        for parent in cwd.parents:
            potential = parent / ".sugar"
            if potential.exists():
                sugar_dir = potential
                break

    if not sugar_dir.exists():
        raise RuntimeError("Not in a Sugar project. Run 'sugar init' first.")

    db_path = sugar_dir / "sugar.db"
    return WorkQueue(str(db_path))


def get_valid_task_types() -> List[str]:
    """Get list of valid task types."""
    return [
        "bug_fix",
        "feature",
        "test",
        "refactor",
        "documentation",
        "chore",
        "style",
        "perf",
        "ci",
        "security",
    ]


def create_task_mcp_server() -> "FastMCP":
    """Create and configure the Sugar Task Queue MCP server."""
    if not FASTMCP_AVAILABLE:
        raise ImportError(
            "FastMCP not available. Install with: pip install 'sugarai[mcp]'"
        )

    mcp = FastMCP("Sugar Tasks")

    @mcp.tool()
    async def sugar_add_task(
        title: str,
        type: str = "feature",
        description: Optional[str] = None,
        priority: int = 3,
        status: str = "pending",
        acceptance_criteria: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Add a new task to the Sugar work queue for autonomous execution.

        Use this to queue work that Sugar will pick up and execute autonomously.
        Tasks are executed in priority order (1=urgent, 5=minimal).

        Args:
            title: Task title - clear, actionable description (required)
            type: Task type (bug_fix, feature, test, refactor, documentation, chore)
            description: Detailed task description with context
            priority: Priority 1-5 where 1=urgent, 5=minimal (default: 3)
            status: Initial status - pending or hold (default: pending)
            acceptance_criteria: JSON string of acceptance criteria list

        Returns:
            Task creation result with task_id
        """
        try:
            queue = get_work_queue()
            await queue.initialize()

            # Validate task type
            valid_types = get_valid_task_types()
            if type not in valid_types:
                return {
                    "success": False,
                    "error": f"Invalid task type '{type}'. Valid: {', '.join(valid_types)}",
                }

            # Validate priority
            if not 1 <= priority <= 5:
                return {
                    "success": False,
                    "error": "Priority must be between 1 (urgent) and 5 (minimal)",
                }

            # Validate status
            if status not in ("pending", "hold"):
                return {
                    "success": False,
                    "error": "Initial status must be 'pending' or 'hold'",
                }

            # Parse acceptance criteria if provided
            criteria_list = []
            if acceptance_criteria:
                try:
                    criteria_list = json.loads(acceptance_criteria)
                except json.JSONDecodeError:
                    # Treat as single criterion
                    criteria_list = [{"description": acceptance_criteria}]

            task_data = {
                "type": type,
                "title": title.strip(),
                "description": description or f"Task: {title}",
                "priority": priority,
                "status": status,
                "source": "mcp",
                "context": {"added_via": "sugar_mcp_tasks"},
                "acceptance_criteria": criteria_list,
            }

            task_id = await queue.add_work(task_data)

            return {
                "success": True,
                "task_id": task_id,
                "title": title,
                "type": type,
                "priority": priority,
                "status": status,
            }
        except Exception as e:
            logger.error(f"sugar_add_task failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def sugar_list_tasks(
        status: Optional[str] = None,
        type: Optional[str] = None,
        priority: Optional[int] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        List tasks in the Sugar work queue with optional filtering.

        Args:
            status: Filter by status (pending, hold, active, completed, failed, or all)
            type: Filter by task type
            priority: Filter by exact priority (1-5)
            limit: Maximum tasks to return (default: 20, max: 100)

        Returns:
            List of tasks matching criteria
        """
        try:
            queue = get_work_queue()
            await queue.initialize()

            # Validate and cap limit
            limit = min(max(1, limit), 100)

            # Get tasks
            status_filter = None if status == "all" else status
            tasks = await queue.get_recent_work(limit=limit, status=status_filter)

            # Apply additional filters
            if type:
                tasks = [t for t in tasks if t.get("type") == type]
            if priority is not None:
                tasks = [t for t in tasks if t.get("priority") == priority]

            # Format output
            formatted_tasks = []
            for task in tasks:
                formatted_tasks.append(
                    {
                        "id": task.get("id", "")[:8],
                        "full_id": task.get("id", ""),
                        "title": task.get("title", ""),
                        "type": task.get("type", ""),
                        "priority": task.get("priority", 3),
                        "status": task.get("status", ""),
                        "created_at": task.get("created_at", ""),
                        "attempts": task.get("attempts", 0),
                    }
                )

            return {
                "success": True,
                "tasks": formatted_tasks,
                "count": len(formatted_tasks),
            }
        except Exception as e:
            logger.error(f"sugar_list_tasks failed: {e}")
            return {"success": False, "error": str(e), "tasks": [], "count": 0}

    @mcp.tool()
    async def sugar_view_task(task_id: str) -> Dict[str, Any]:
        """
        Get detailed information about a specific task.

        Args:
            task_id: Task ID (full UUID or short 8-char prefix)

        Returns:
            Full task details including description, context, and history
        """
        try:
            queue = get_work_queue()
            await queue.initialize()

            # Try to find task by ID or prefix
            task = await queue.get_work_item(task_id)

            if not task:
                # Try prefix match
                all_tasks = await queue.get_recent_work(limit=100)
                for t in all_tasks:
                    if t.get("id", "").startswith(task_id):
                        task = await queue.get_work_item(t["id"])
                        break

            if not task:
                return {
                    "success": False,
                    "error": f"Task not found: {task_id}",
                }

            return {
                "success": True,
                "task": {
                    "id": task.get("id", ""),
                    "title": task.get("title", ""),
                    "type": task.get("type", ""),
                    "description": task.get("description", ""),
                    "priority": task.get("priority", 3),
                    "status": task.get("status", ""),
                    "source": task.get("source", ""),
                    "context": task.get("context", {}),
                    "created_at": task.get("created_at", ""),
                    "updated_at": task.get("updated_at", ""),
                    "started_at": task.get("started_at"),
                    "completed_at": task.get("completed_at"),
                    "attempts": task.get("attempts", 0),
                    "total_execution_time": task.get("total_execution_time", 0),
                    "total_elapsed_time": task.get("total_elapsed_time", 0),
                    "result": task.get("result"),
                    "error_message": task.get("error_message"),
                    "acceptance_criteria": task.get("acceptance_criteria", []),
                    "commit_sha": task.get("commit_sha"),
                },
            }
        except Exception as e:
            logger.error(f"sugar_view_task failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def sugar_update_task(
        task_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        type: Optional[str] = None,
        priority: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update properties of an existing task.

        Only provide the fields you want to change.

        Args:
            task_id: Task ID to update (required)
            title: New task title
            description: New description
            type: New task type
            priority: New priority (1-5)
            status: New status (pending or hold only)

        Returns:
            Update confirmation with changed fields
        """
        try:
            queue = get_work_queue()
            await queue.initialize()

            # Verify task exists
            task = await queue.get_work_item(task_id)
            if not task:
                # Try prefix match
                all_tasks = await queue.get_recent_work(limit=100)
                for t in all_tasks:
                    if t.get("id", "").startswith(task_id):
                        task_id = t["id"]
                        task = await queue.get_work_item(task_id)
                        break

            if not task:
                return {
                    "success": False,
                    "error": f"Task not found: {task_id}",
                }

            # Build updates
            updates = {}
            updated_fields = []

            if title is not None:
                updates["title"] = title.strip()
                updated_fields.append("title")

            if description is not None:
                updates["description"] = description
                updated_fields.append("description")

            if type is not None:
                valid_types = get_valid_task_types()
                if type not in valid_types:
                    return {
                        "success": False,
                        "error": f"Invalid task type '{type}'. Valid: {', '.join(valid_types)}",
                    }
                updates["type"] = type
                updated_fields.append("type")

            if priority is not None:
                if not 1 <= priority <= 5:
                    return {
                        "success": False,
                        "error": "Priority must be between 1 and 5",
                    }
                updates["priority"] = priority
                updated_fields.append("priority")

            if status is not None:
                if status not in ("pending", "hold"):
                    return {
                        "success": False,
                        "error": "Can only change status to 'pending' or 'hold'",
                    }
                updates["status"] = status
                updated_fields.append("status")

            if not updates:
                return {
                    "success": False,
                    "error": "No fields to update",
                }

            success = await queue.update_work(task_id, updates)

            return {
                "success": success,
                "task_id": task_id[:8],
                "updated_fields": updated_fields,
            }
        except Exception as e:
            logger.error(f"sugar_update_task failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def sugar_task_status() -> Dict[str, Any]:
        """
        Get queue statistics and system health information.

        Returns summary of tasks by status and overall queue health.
        """
        try:
            queue = get_work_queue()
            await queue.initialize()

            stats = await queue.get_stats()
            health = await queue.health_check()

            return {
                "success": True,
                "stats": {
                    "pending": stats.get("pending", 0),
                    "hold": stats.get("hold", 0),
                    "active": stats.get("active", 0),
                    "completed": stats.get("completed", 0),
                    "failed": stats.get("failed", 0),
                    "total": stats.get("total", 0),
                    "recent_24h": stats.get("recent_24h", 0),
                },
                "health": health,
            }
        except Exception as e:
            logger.error(f"sugar_task_status failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    async def sugar_remove_task(
        task_id: str,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Remove a task from the work queue.

        Active tasks cannot be removed unless force=True.
        This action cannot be undone.

        Args:
            task_id: Task ID to remove
            force: Force removal even if task is active (default: False)

        Returns:
            Removal confirmation
        """
        try:
            queue = get_work_queue()
            await queue.initialize()

            # Get task first to check status
            task = await queue.get_work_item(task_id)
            if not task:
                # Try prefix match
                all_tasks = await queue.get_recent_work(limit=100)
                for t in all_tasks:
                    if t.get("id", "").startswith(task_id):
                        task_id = t["id"]
                        task = await queue.get_work_item(task_id)
                        break

            if not task:
                return {
                    "success": False,
                    "error": f"Task not found: {task_id}",
                }

            # Check if task is active
            if task.get("status") == "active" and not force:
                return {
                    "success": False,
                    "error": "Cannot remove active task. Use force=True to override.",
                }

            was_status = task.get("status")
            was_title = task.get("title")
            success = await queue.remove_work(task_id)

            return {
                "success": success,
                "task_id": task_id[:8],
                "title": was_title,
                "was_status": was_status,
            }
        except Exception as e:
            logger.error(f"sugar_remove_task failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.resource("sugar://tasks/pending")
    async def pending_tasks_resource() -> str:
        """
        Current pending tasks in the Sugar queue.

        This resource provides a markdown list of pending tasks
        ordered by priority.
        """
        try:
            queue = get_work_queue()
            await queue.initialize()

            tasks = await queue.get_recent_work(limit=20, status="pending")

            if not tasks:
                return "# No Pending Tasks\n\nThe queue is empty."

            lines = ["# Pending Tasks", ""]
            for task in tasks:
                priority = task.get("priority", 3)
                priority_label = {
                    1: "Urgent",
                    2: "High",
                    3: "Normal",
                    4: "Low",
                    5: "Minimal",
                }.get(priority, "Normal")
                lines.append(
                    f"- **[{priority_label}]** {task.get('title')} ({task.get('id', '')[:8]})"
                )

            return "\n".join(lines)
        except Exception as e:
            return f"# Error loading tasks\n\n{e}"

    @mcp.resource("sugar://tasks/stats")
    async def stats_resource() -> str:
        """
        Queue statistics as markdown.
        """
        try:
            queue = get_work_queue()
            await queue.initialize()

            stats = await queue.get_stats()

            lines = [
                "# Sugar Queue Statistics",
                "",
                f"- **Pending:** {stats.get('pending', 0)}",
                f"- **On Hold:** {stats.get('hold', 0)}",
                f"- **Active:** {stats.get('active', 0)}",
                f"- **Completed:** {stats.get('completed', 0)}",
                f"- **Failed:** {stats.get('failed', 0)}",
                "",
                f"**Total:** {stats.get('total', 0)}",
                f"**Last 24h:** {stats.get('recent_24h', 0)}",
            ]

            return "\n".join(lines)
        except Exception as e:
            return f"# Error loading stats\n\n{e}"

    return mcp


def run_task_server(transport: str = "stdio"):
    """Run the Sugar Task Queue MCP server."""
    if not FASTMCP_AVAILABLE:
        raise ImportError(
            "FastMCP not available. Install with: pip install 'sugarai[mcp]'"
        )

    mcp = create_task_mcp_server()

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        raise ValueError(f"Unsupported transport: {transport}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_task_server()
