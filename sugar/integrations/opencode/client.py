"""
OpenCode HTTP Client

Provides async HTTP client for communicating with OpenCode server.
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from .config import OpenCodeConfig
from .models import Event, Message, NotificationLevel, Session

logger = logging.getLogger(__name__)

# Check for aiohttp availability
try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    aiohttp = None  # type: ignore


class OpenCodeClient:
    """
    Async HTTP client for OpenCode server.

    Provides methods for:
    - Session management
    - Context injection
    - Notifications
    - Event subscription
    """

    def __init__(self, config: Optional[OpenCodeConfig] = None):
        """
        Initialize OpenCode client.

        Args:
            config: Configuration object (defaults to env-based config)
        """
        if not AIOHTTP_AVAILABLE:
            raise ImportError(
                "aiohttp not available. Install with: pip install aiohttp"
            )

        self.config = config or OpenCodeConfig.from_env()
        self._session: Optional[aiohttp.ClientSession] = None
        self._connected = False

    def _auth_headers(self) -> Dict[str, str]:
        """Get authentication headers."""
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    async def __aenter__(self) -> "OpenCodeClient":
        """Async context manager entry."""
        timeout = aiohttp.ClientTimeout(total=self.config.timeout)
        self._session = aiohttp.ClientSession(
            base_url=self.config.server_url,
            headers=self._auth_headers(),
            timeout=timeout,
        )
        return self

    async def __aexit__(self, *args) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the client session."""
        if self._session:
            await self._session.close()
            self._session = None
            self._connected = False

    async def health_check(self) -> bool:
        """Check if OpenCode server is reachable."""
        if not self._session:
            return False

        try:
            async with self._session.get("/health") as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            return False

    # =========================================================================
    # Session Management
    # =========================================================================

    async def list_sessions(self) -> List[Session]:
        """List all active OpenCode sessions."""
        if not self._session:
            raise RuntimeError("Client not connected. Use async with context.")

        try:
            async with self._session.get("/sessions") as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to list sessions: {resp.status}")
                    return []
                data = await resp.json()
                return [Session.from_dict(s) for s in data.get("sessions", [])]
        except Exception as e:
            logger.error(f"Error listing sessions: {e}")
            return []

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a specific session by ID."""
        if not self._session:
            raise RuntimeError("Client not connected. Use async with context.")

        try:
            async with self._session.get(f"/sessions/{session_id}") as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return Session.from_dict(data)
        except Exception as e:
            logger.error(f"Error getting session {session_id}: {e}")
            return None

    # =========================================================================
    # Context Injection
    # =========================================================================

    async def inject_context(
        self,
        session_id: str,
        context: str,
        role: str = "system",
    ) -> bool:
        """
        Inject context into an OpenCode session.

        Args:
            session_id: Target session ID
            context: Context content to inject
            role: Message role (system, user, assistant)

        Returns:
            True if injection succeeded
        """
        if not self._session:
            raise RuntimeError("Client not connected. Use async with context.")

        payload = {
            "content": context,
            "role": role,
            "noReply": True,  # Don't generate a response
        }

        try:
            async with self._session.post(
                f"/sessions/{session_id}/messages",
                json=payload,
            ) as resp:
                success = resp.status in (200, 201)
                if not success:
                    logger.warning(
                        f"Context injection failed: {resp.status} - {await resp.text()}"
                    )
                return success
        except Exception as e:
            logger.error(f"Error injecting context: {e}")
            return False

    async def inject_memories(
        self,
        session_id: str,
        memories: List[Dict[str, Any]],
    ) -> bool:
        """
        Inject Sugar memories into an OpenCode session.

        Args:
            session_id: Target session ID
            memories: List of memory dicts with content, type, etc.

        Returns:
            True if injection succeeded
        """
        if not memories:
            return True

        # Format memories for injection
        formatted = self._format_memories(memories)
        return await self.inject_context(session_id, formatted)

    def _format_memories(self, memories: List[Dict[str, Any]]) -> str:
        """Format memories for LLM consumption."""
        if not memories:
            return ""

        lines = ["## Sugar Context (from memory)", ""]

        # Group by type
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for m in memories:
            mem_type = m.get("type", "other")
            by_type.setdefault(mem_type, []).append(m)

        type_headers = {
            "decision": "Previous Decisions",
            "preference": "Coding Preferences",
            "file_context": "File Context",
            "error_pattern": "Known Error Patterns",
            "research": "Research Notes",
            "outcome": "Past Outcomes",
        }

        for mem_type, items in by_type.items():
            header = type_headers.get(mem_type, mem_type.title())
            lines.append(f"### {header}")
            lines.append("")
            for item in items:
                lines.append(f"- {item.get('content', '')}")
            lines.append("")

        return "\n".join(lines)

    # =========================================================================
    # Notifications
    # =========================================================================

    async def notify(
        self,
        title: str,
        message: str,
        level: NotificationLevel = NotificationLevel.INFO,
    ) -> bool:
        """
        Send a notification to OpenCode TUI.

        Args:
            title: Notification title
            message: Notification message
            level: Severity level

        Returns:
            True if notification was sent
        """
        if not self._session:
            raise RuntimeError("Client not connected. Use async with context.")

        payload = {
            "title": title,
            "message": message,
            "level": level.value,
        }

        try:
            async with self._session.post("/tui/notify", json=payload) as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug(f"Notification failed: {e}")
            return False

    async def notify_task_completed(
        self,
        task_id: str,
        title: str,
        status: str,
        execution_time: Optional[float] = None,
    ) -> bool:
        """Send task completion notification."""
        level = (
            NotificationLevel.SUCCESS
            if status == "completed"
            else NotificationLevel.WARNING
        )
        time_str = f" ({execution_time:.1f}s)" if execution_time else ""
        return await self.notify(
            title=f"Task {status.title()}: {task_id[:8]}",
            message=f"{title}{time_str}",
            level=level,
        )

    async def notify_task_failed(
        self,
        task_id: str,
        title: str,
        error: str,
    ) -> bool:
        """Send task failure notification."""
        return await self.notify(
            title=f"Task Failed: {task_id[:8]}",
            message=f"{title}\nError: {error}",
            level=NotificationLevel.ERROR,
        )

    # =========================================================================
    # Event Subscription
    # =========================================================================

    async def subscribe_events(
        self,
        event_types: Optional[List[str]] = None,
    ) -> AsyncIterator[Event]:
        """
        Subscribe to OpenCode events via SSE.

        Args:
            event_types: Optional list of event types to filter

        Yields:
            Event objects as they arrive
        """
        if not self._session:
            raise RuntimeError("Client not connected. Use async with context.")

        try:
            async with self._session.get(
                "/events",
                headers={"Accept": "text/event-stream"},
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"Event subscription failed: {resp.status}")
                    return

                async for line in resp.content:
                    line_str = line.decode("utf-8").strip()

                    if line_str.startswith("data: "):
                        data = line_str[6:]
                        try:
                            event = Event.from_sse("message", data)

                            # Filter by event type if specified
                            if event_types and event.type not in event_types:
                                continue

                            yield event
                        except Exception as e:
                            logger.debug(f"Failed to parse event: {e}")
                            continue

        except asyncio.CancelledError:
            logger.debug("Event subscription cancelled")
        except Exception as e:
            logger.error(f"Event subscription error: {e}")

    # =========================================================================
    # Utility Methods
    # =========================================================================

    async def is_available(self) -> bool:
        """Check if OpenCode server is available."""
        if not self.config.enabled:
            return False

        try:
            async with self as client:
                return await client.health_check()
        except Exception:
            return False


# Convenience function for one-off operations
async def inject_context_to_opencode(
    session_id: str,
    context: str,
    config: Optional[OpenCodeConfig] = None,
) -> bool:
    """
    Inject context into an OpenCode session (convenience function).

    Args:
        session_id: Target session ID
        context: Context to inject
        config: Optional configuration

    Returns:
        True if injection succeeded
    """
    async with OpenCodeClient(config) as client:
        return await client.inject_context(session_id, context)


async def notify_opencode(
    title: str,
    message: str,
    level: NotificationLevel = NotificationLevel.INFO,
    config: Optional[OpenCodeConfig] = None,
) -> bool:
    """
    Send notification to OpenCode (convenience function).

    Args:
        title: Notification title
        message: Notification message
        level: Severity level
        config: Optional configuration

    Returns:
        True if notification was sent
    """
    async with OpenCodeClient(config) as client:
        return await client.notify(title, message, level)
