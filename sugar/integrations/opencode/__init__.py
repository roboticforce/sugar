"""
Sugar OpenCode Integration

Provides bidirectional communication between Sugar and OpenCode:
- Context injection into OpenCode sessions
- Event subscription from OpenCode
- Notifications to OpenCode TUI
- Automatic memory injection
- Learning capture from sessions
"""

from .client import OpenCodeClient
from .config import OpenCodeConfig
from .models import Session, Message, NotificationLevel
from .injector import ContextInjector, LearningCapture
from .notifier import (
    OpenCodeNotifier,
    get_notifier,
    notify_task_started,
    notify_task_completed,
    notify_task_failed,
)

__all__ = [
    "OpenCodeClient",
    "OpenCodeConfig",
    "Session",
    "Message",
    "NotificationLevel",
    "ContextInjector",
    "LearningCapture",
    "OpenCodeNotifier",
    "get_notifier",
    "notify_task_started",
    "notify_task_completed",
    "notify_task_failed",
]
