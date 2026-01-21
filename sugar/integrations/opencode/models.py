"""
OpenCode Integration Data Models
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class NotificationLevel(str, Enum):
    """Notification severity levels."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class SessionStatus(str, Enum):
    """Session status values."""

    ACTIVE = "active"
    IDLE = "idle"
    COMPACTING = "compacting"
    CLOSED = "closed"


@dataclass
class Message:
    """OpenCode session message."""

    id: str
    role: str  # user, assistant, system
    content: str
    created_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    """OpenCode session."""

    id: str
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    messages: List[Message] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        """Create session from API response dict."""
        return cls(
            id=data.get("id", ""),
            status=SessionStatus(data.get("status", "active")),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if data.get("created_at")
                else None
            ),
            updated_at=(
                datetime.fromisoformat(data["updated_at"])
                if data.get("updated_at")
                else None
            ),
            messages=[
                Message(
                    id=m.get("id", ""),
                    role=m.get("role", ""),
                    content=m.get("content", ""),
                )
                for m in data.get("messages", [])
            ],
            metadata=data.get("metadata", {}),
        )


@dataclass
class Event:
    """OpenCode SSE event."""

    type: str
    data: Dict[str, Any]
    timestamp: Optional[datetime] = None

    @classmethod
    def from_sse(cls, event_type: str, data: str) -> "Event":
        """Parse SSE event."""
        import json

        try:
            parsed_data = json.loads(data)
        except json.JSONDecodeError:
            parsed_data = {"raw": data}

        return cls(
            type=event_type,
            data=parsed_data,
            timestamp=datetime.now(),
        )


@dataclass
class TaskNotification:
    """Task completion notification."""

    task_id: str
    title: str
    status: str
    level: NotificationLevel
    message: Optional[str] = None
    execution_time: Optional[float] = None
