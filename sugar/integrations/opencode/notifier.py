"""
OpenCode Task Notifier

Sends task lifecycle notifications to OpenCode when enabled.
This module bridges Sugar's execution flow with OpenCode's notification system.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from .config import OpenCodeConfig

logger = logging.getLogger(__name__)

# Check for aiohttp availability
try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


class OpenCodeNotifier:
    """
    Handles task notifications to OpenCode.

    This class is designed to be used by Sugar's executor to notify
    OpenCode about task lifecycle events (started, completed, failed).
    """

    def __init__(self, config: Optional[OpenCodeConfig] = None):
        """
        Initialize the OpenCode notifier.

        Args:
            config: OpenCode configuration. If None, will load from env.
        """
        self._config = config
        self._client = None
        self._enabled = False

        # Initialize if aiohttp is available
        if AIOHTTP_AVAILABLE:
            self._config = config or OpenCodeConfig.from_env()
            self._enabled = self._config.enabled
        else:
            logger.debug("aiohttp not available, OpenCode notifications disabled")

    @property
    def enabled(self) -> bool:
        """Check if OpenCode notifications are enabled."""
        return self._enabled and AIOHTTP_AVAILABLE

    async def _get_client(self):
        """Lazy-load the OpenCode client."""
        if self._client is None and self.enabled:
            from .client import OpenCodeClient

            self._client = OpenCodeClient(self._config)
        return self._client

    async def notify_task_started(
        self,
        task_id: str,
        title: str,
        task_type: str,
        priority: int,
    ) -> bool:
        """
        Notify OpenCode that a task has started.

        Args:
            task_id: Task ID
            title: Task title
            task_type: Task type (bug_fix, feature, etc.)
            priority: Task priority (1-5)

        Returns:
            True if notification was sent successfully
        """
        if not self.enabled:
            return False

        try:
            from .models import NotificationLevel

            client = await self._get_client()
            async with client:
                return await client.notify(
                    title=f"Task Started: {task_id[:8]}",
                    message=f"{title}\nType: {task_type} | Priority: {priority}",
                    level=NotificationLevel.INFO,
                )
        except Exception as e:
            logger.debug(f"Failed to notify task started: {e}")
            return False

    async def notify_task_completed(
        self,
        task_id: str,
        title: str,
        execution_time: Optional[float] = None,
        model_used: Optional[str] = None,
    ) -> bool:
        """
        Notify OpenCode that a task has completed.

        Args:
            task_id: Task ID
            title: Task title
            execution_time: Execution time in seconds
            model_used: Claude model used for execution

        Returns:
            True if notification was sent successfully
        """
        if not self.enabled or not self._config.notify_on_completion:
            return False

        try:
            from .models import NotificationLevel

            client = await self._get_client()
            time_str = f" ({execution_time:.1f}s)" if execution_time else ""
            model_str = f"\nModel: {model_used}" if model_used else ""

            async with client:
                return await client.notify(
                    title=f"Task Completed: {task_id[:8]}",
                    message=f"{title}{time_str}{model_str}",
                    level=NotificationLevel.SUCCESS,
                )
        except Exception as e:
            logger.debug(f"Failed to notify task completed: {e}")
            return False

    async def notify_task_failed(
        self,
        task_id: str,
        title: str,
        error: str,
        execution_time: Optional[float] = None,
    ) -> bool:
        """
        Notify OpenCode that a task has failed.

        Args:
            task_id: Task ID
            title: Task title
            error: Error message
            execution_time: Execution time in seconds

        Returns:
            True if notification was sent successfully
        """
        if not self.enabled or not self._config.notify_on_failure:
            return False

        try:
            from .models import NotificationLevel

            client = await self._get_client()
            time_str = f" ({execution_time:.1f}s)" if execution_time else ""

            # Truncate error for notification
            error_short = error[:200] + "..." if len(error) > 200 else error

            async with client:
                return await client.notify(
                    title=f"Task Failed: {task_id[:8]}",
                    message=f"{title}{time_str}\nError: {error_short}",
                    level=NotificationLevel.ERROR,
                )
        except Exception as e:
            logger.debug(f"Failed to notify task failed: {e}")
            return False

    async def inject_context_for_task(
        self,
        session_id: str,
        task: Dict[str, Any],
    ) -> bool:
        """
        Inject relevant memories into an OpenCode session for a task.

        Args:
            session_id: OpenCode session ID
            task: Task dictionary with title, description, etc.

        Returns:
            True if context was injected successfully
        """
        if not self.enabled or not self._config.auto_inject:
            return False

        try:
            from .injector import ContextInjector

            injector = ContextInjector(self._config)

            # Build query from task info
            query_parts = [task.get("title", "")]
            if task.get("description"):
                query_parts.append(task["description"])
            query = " ".join(query_parts)

            # Get relevant context
            context = await injector.get_context_for_prompt(query)

            if context:
                return await injector.inject_into_session(session_id, context)

            return True  # No context to inject is still success
        except Exception as e:
            logger.debug(f"Failed to inject context: {e}")
            return False


# Singleton instance for easy access
_notifier: Optional[OpenCodeNotifier] = None


def get_notifier() -> OpenCodeNotifier:
    """Get the global OpenCode notifier instance."""
    global _notifier
    if _notifier is None:
        _notifier = OpenCodeNotifier()
    return _notifier


async def notify_task_started(
    task_id: str, title: str, task_type: str, priority: int
) -> bool:
    """Convenience function to notify task started."""
    return await get_notifier().notify_task_started(task_id, title, task_type, priority)


async def notify_task_completed(
    task_id: str,
    title: str,
    execution_time: Optional[float] = None,
    model_used: Optional[str] = None,
) -> bool:
    """Convenience function to notify task completed."""
    return await get_notifier().notify_task_completed(
        task_id, title, execution_time, model_used
    )


async def notify_task_failed(
    task_id: str, title: str, error: str, execution_time: Optional[float] = None
) -> bool:
    """Convenience function to notify task failed."""
    return await get_notifier().notify_task_failed(
        task_id, title, error, execution_time
    )
