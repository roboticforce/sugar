"""Task Hooks Executor

Execute pre/post task hooks - shell commands that run before and after task execution.
This allows for automated linting, testing, cleanup, and other workflow automation.
"""

import subprocess
import logging
from typing import List, Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class HookExecutor:
    """Execute pre/post task hooks with variable substitution."""

    def __init__(self, project_dir: str):
        """
        Initialize hook executor.

        Args:
            project_dir: Project root directory where hooks will be executed
        """
        self.project_dir = Path(project_dir).resolve()
        logger.debug(f"HookExecutor initialized for project: {self.project_dir}")

    async def execute_hooks(
        self,
        hooks: List[str],
        hook_type: str,
        task: Dict[str, Any],
        timeout: int = 300
    ) -> Dict[str, Any]:
        """
        Execute a list of hook commands.

        Args:
            hooks: List of shell commands to execute
            hook_type: Type of hook (pre_hooks or post_hooks) for logging
            task: Task dictionary for variable substitution
            timeout: Timeout in seconds for each hook command (default: 300s/5min)

        Returns:
            Dictionary with execution results:
                - success: True if all hooks passed, False if any failed
                - failed_hook: Command that failed (if any)
                - outputs: List of outputs from each hook
                - errors: List of errors from failed hooks
        """
        if not hooks:
            logger.debug(f"No {hook_type} to execute")
            return {
                "success": True,
                "outputs": [],
                "errors": []
            }

        task_id = task.get("id", "unknown")
        logger.info(f"Executing {len(hooks)} {hook_type} for task {task_id}")

        outputs = []
        errors = []

        for i, hook in enumerate(hooks, 1):
            try:
                # Substitute task variables in the command
                cmd = self._substitute_variables(hook, task)
                logger.debug(f"Running {hook_type}[{i}/{len(hooks)}]: {cmd}")

                result = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=str(self.project_dir),
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )

                # Log output
                if result.stdout:
                    logger.debug(f"Hook stdout: {result.stdout[:200]}")
                if result.stderr:
                    logger.debug(f"Hook stderr: {result.stderr[:200]}")

                outputs.append({
                    "command": cmd,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode
                })

                # Check for failure
                if result.returncode != 0:
                    error_msg = f"{hook_type} failed: {cmd}\nExit code: {result.returncode}"
                    if result.stderr:
                        error_msg += f"\nError: {result.stderr}"

                    logger.error(error_msg)
                    errors.append({
                        "command": cmd,
                        "error": error_msg,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "returncode": result.returncode
                    })

                    return {
                        "success": False,
                        "failed_hook": cmd,
                        "failed_hook_index": i - 1,
                        "outputs": outputs,
                        "errors": errors
                    }

                logger.debug(f"{hook_type}[{i}/{len(hooks)}] passed: {cmd}")

            except subprocess.TimeoutExpired as e:
                error_msg = f"{hook_type} timed out after {timeout}s: {cmd}"
                logger.error(error_msg)
                errors.append({
                    "command": cmd,
                    "error": error_msg,
                    "timeout": timeout
                })

                return {
                    "success": False,
                    "failed_hook": cmd,
                    "failed_hook_index": i - 1,
                    "outputs": outputs,
                    "errors": errors,
                    "timeout": True
                }

            except Exception as e:
                error_msg = f"{hook_type} exception: {cmd}\n{str(e)}"
                logger.error(error_msg)
                errors.append({
                    "command": cmd,
                    "error": str(e)
                })

                return {
                    "success": False,
                    "failed_hook": cmd,
                    "failed_hook_index": i - 1,
                    "outputs": outputs,
                    "errors": errors
                }

        logger.info(f"All {len(hooks)} {hook_type} passed for task {task_id}")
        return {
            "success": True,
            "outputs": outputs,
            "errors": []
        }

    def _substitute_variables(self, cmd: str, task: Dict[str, Any]) -> str:
        """
        Replace variables in hook command with task data.

        Supported variables:
            {task_id} - Task ID
            {task_type} - Task type (bug_fix, feature, etc.)
            {task_title} - Task title
            {task_priority} - Task priority (1-5)

        Args:
            cmd: Command string with variable placeholders
            task: Task dictionary

        Returns:
            Command with variables substituted
        """
        # Extract task fields with safe defaults
        task_id = str(task.get("id", ""))
        task_type = str(task.get("type", ""))
        task_title = str(task.get("title", ""))
        task_priority = str(task.get("priority", ""))

        # Perform substitution
        substituted = cmd.format(
            task_id=task_id,
            task_type=task_type,
            task_title=task_title,
            task_priority=task_priority
        )

        return substituted
