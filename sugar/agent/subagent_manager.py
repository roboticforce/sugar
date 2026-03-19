"""
SubAgent Manager for Sugar

Provides functionality for spawning and managing sub-agents with:
- Isolated execution contexts
- Parallel execution with concurrency control
- Timeout handling
- Result summarization and file tracking
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .base import AgentResponse, SugarAgent, SugarAgentConfig

logger = logging.getLogger(__name__)


@dataclass
class SubAgentResult:
    """Result from a sub-agent execution"""

    task_id: str
    success: bool
    summary: str
    files_modified: List[str] = field(default_factory=list)
    execution_time: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "task_id": self.task_id,
            "success": self.success,
            "summary": self.summary,
            "files_modified": self.files_modified,
            "execution_time": self.execution_time,
            "error": self.error,
        }


class SubAgentManager:
    """
    Manages spawning and execution of sub-agents.

    Sub-agents are isolated agent instances that can execute tasks
    independently or in parallel, with results summarized back to
    the parent agent.
    """

    def __init__(
        self,
        parent_config: SugarAgentConfig,
        max_concurrent: int = 3,
        default_timeout: int = 300,
    ):
        """
        Initialize the SubAgent manager.

        Args:
            parent_config: Configuration from parent agent to inherit
            max_concurrent: Maximum number of concurrent sub-agents
            default_timeout: Default timeout for sub-agent tasks (seconds)
        """
        self.parent_config = parent_config
        self.max_concurrent = max_concurrent
        self.default_timeout = default_timeout
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_subagents: Dict[str, SugarAgent] = {}

        logger.debug(
            f"SubAgentManager initialized with max_concurrent={max_concurrent}"
        )

    def _create_subagent_config(
        self, task_id: str, task_context: Optional[str] = None
    ) -> SugarAgentConfig:
        """
        Create a config for a sub-agent based on parent config.

        Args:
            task_id: Unique identifier for this sub-agent task
            task_context: Optional context to add to system prompt

        Returns:
            SugarAgentConfig for the sub-agent
        """
        # Inherit most settings from parent, but customize for sub-agent
        config = SugarAgentConfig(
            model=self.parent_config.model,
            max_tokens=self.parent_config.max_tokens,
            permission_mode=self.parent_config.permission_mode,
            allowed_tools=self.parent_config.allowed_tools.copy(),
            mcp_servers=self.parent_config.mcp_servers.copy(),
            quality_gates_enabled=self.parent_config.quality_gates_enabled,
            working_directory=self.parent_config.working_directory,
            timeout=self.default_timeout,
            max_retries=self.parent_config.max_retries,
            retry_base_delay=self.parent_config.retry_base_delay,
            retry_max_delay=self.parent_config.retry_max_delay,
        )

        # Add sub-agent context to system prompt
        subagent_context = f"\n\nYou are a sub-agent working on task: {task_id}"
        if task_context:
            subagent_context += f"\n{task_context}"
        subagent_context += (
            "\n\nFocus on completing your specific task and providing a clear summary."
        )

        config.system_prompt_additions = (
            self.parent_config.system_prompt_additions + subagent_context
        )

        return config

    async def spawn(
        self,
        task_id: str,
        prompt: str,
        task_context: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> SubAgentResult:
        """
        Spawn a single sub-agent to execute a task.

        Args:
            task_id: Unique identifier for this task
            prompt: Task prompt for the sub-agent
            task_context: Optional context for the task
            timeout: Optional timeout override (seconds)

        Returns:
            SubAgentResult with execution results
        """
        start_time = datetime.now(timezone.utc)
        timeout_seconds = timeout or self.default_timeout

        logger.info(f"Spawning sub-agent for task: {task_id}")

        try:
            # Acquire semaphore to respect concurrency limit
            async with self._semaphore:
                # Create sub-agent config and instance
                config = self._create_subagent_config(task_id, task_context)
                subagent = SugarAgent(config)
                self._active_subagents[task_id] = subagent

                # Execute with timeout
                try:
                    response = await asyncio.wait_for(
                        subagent.execute(prompt, task_context),
                        timeout=timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    execution_time = (
                        datetime.now(timezone.utc) - start_time
                    ).total_seconds()
                    logger.warning(
                        f"Sub-agent task {task_id} timed out after {timeout_seconds}s"
                    )
                    return SubAgentResult(
                        task_id=task_id,
                        success=False,
                        summary=f"Task timed out after {timeout_seconds} seconds",
                        execution_time=execution_time,
                        error="Timeout exceeded",
                    )

                # Extract summary from response
                summary = self._extract_summary(response)
                execution_time = (
                    datetime.now(timezone.utc) - start_time
                ).total_seconds()

                result = SubAgentResult(
                    task_id=task_id,
                    success=response.success,
                    summary=summary,
                    files_modified=response.files_modified.copy(),
                    execution_time=execution_time,
                    error=response.error,
                )

                logger.info(
                    f"Sub-agent task {task_id} completed in {execution_time:.2f}s, "
                    f"modified {len(result.files_modified)} files"
                )

                return result

        except Exception as e:
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.error(f"Sub-agent task {task_id} failed: {e}")
            return SubAgentResult(
                task_id=task_id,
                success=False,
                summary=f"Sub-agent execution failed: {str(e)}",
                execution_time=execution_time,
                error=str(e),
            )

        finally:
            # Clean up
            if task_id in self._active_subagents:
                del self._active_subagents[task_id]

    async def spawn_parallel(
        self,
        tasks: List[Dict[str, Any]],
        timeout: Optional[int] = None,
    ) -> List[SubAgentResult]:
        """
        Spawn multiple sub-agents to execute tasks in parallel.

        Tasks will be executed concurrently up to max_concurrent limit.

        Args:
            tasks: List of task dicts, each containing:
                - task_id: Unique identifier
                - prompt: Task prompt
                - context: Optional task context
            timeout: Optional timeout override for all tasks (seconds)

        Returns:
            List of SubAgentResults in the same order as input tasks
        """
        logger.info(f"Spawning {len(tasks)} sub-agents in parallel")
        start_time = datetime.now(timezone.utc)

        # Create coroutines for all tasks
        coroutines = []
        for task in tasks:
            task_id = task.get("task_id", f"task-{len(coroutines)}")
            prompt = task.get("prompt", "")
            context = task.get("context")
            task_timeout = task.get("timeout", timeout)

            coro = self.spawn(task_id, prompt, context, task_timeout)
            coroutines.append(coro)

        # Execute all tasks concurrently
        results = await asyncio.gather(*coroutines, return_exceptions=True)

        # Convert any exceptions to error results
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                task_id = tasks[i].get("task_id", f"task-{i}")
                processed_results.append(
                    SubAgentResult(
                        task_id=task_id,
                        success=False,
                        summary=f"Task failed with exception: {str(result)}",
                        error=str(result),
                    )
                )
            else:
                processed_results.append(result)

        total_time = (datetime.now(timezone.utc) - start_time).total_seconds()
        successful = sum(1 for r in processed_results if r.success)
        logger.info(
            f"Parallel execution completed in {total_time:.2f}s: "
            f"{successful}/{len(tasks)} successful"
        )

        return processed_results

    def _extract_summary(self, response: AgentResponse) -> str:
        """
        Extract a concise summary from agent response.

        Args:
            response: AgentResponse from sub-agent

        Returns:
            Summary string
        """
        if not response.success:
            return response.error or "Task failed"

        content = response.content.strip()
        if not content:
            return "Task completed (no output)"

        # Take first meaningful paragraph or first 200 chars
        lines = content.split("\n")
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                # Found first content line
                if len(line) > 200:
                    return line[:197] + "..."
                return line

        # Fallback to truncated content
        if len(content) > 200:
            return content[:197] + "..."
        return content

    def get_active_count(self) -> int:
        """Get number of currently active sub-agents"""
        return len(self._active_subagents)

    def get_active_task_ids(self) -> List[str]:
        """Get list of currently active task IDs"""
        return list(self._active_subagents.keys())

    async def cancel_all(self) -> None:
        """
        Cancel all active sub-agent tasks.

        Note: This will attempt graceful shutdown but may not
        interrupt tasks that are already executing.

        Takes a snapshot of the dict before iterating to avoid
        RuntimeError if spawn()'s finally block mutates the dict
        concurrently (e.g., during shutdown while tasks are in-flight).
        """
        logger.warning(f"Cancelling {len(self._active_subagents)} active sub-agents")

        # Snapshot to avoid RuntimeError: dictionary changed size during iteration
        active_snapshot = dict(self._active_subagents)

        for task_id, subagent in active_snapshot.items():
            try:
                await subagent.end_session()
                logger.debug(f"Cancelled sub-agent task: {task_id}")
            except Exception as e:
                logger.error(f"Error cancelling sub-agent {task_id}: {e}")

        self._active_subagents.clear()
