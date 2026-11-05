"""Observers that feed the event spine.

Kanban: tail ``task_events`` by ascending id with a watermark (the board's own
audit log is the source of truth; we do not write to the board).
Sugar queue: the dashboard already diffs snapshots, so the state machine emits
one activity event per observed transition (see ``events.DashboardState``).

Both are best-effort: failures are logged and never break the dashboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

from .spine import EventSpine

logger = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 0.5
MAX_BATCH = 200

# ``task_events.kind`` -> spine kind. Unknown kinds degrade to task.updated.
KANBAN_KIND_MAP = {
    "created": "task.created",
    "claimed": "task.claimed",
    "commented": "task.commented",
    "completed": "task.completed",
    "blocked": "task.blocked",
    "unblocked": "task.unblocked",
    "assigned": "task.assigned",
    "archived": "task.archived",
}


def _payload_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {"raw": raw[:200]}
        return parsed if isinstance(parsed, dict) else {"raw": raw[:200]}
    return {}


def _machine_from_lock(lock: Any) -> Optional[str]:
    if not isinstance(lock, str) or not lock:
        return None
    head = lock.split(":", 1)[0].strip()
    return head[:64] or None


def map_kanban_event(row: Dict[str, Any]) -> Dict[str, Any]:
    """Map one joined ``task_events`` row onto a spine event body."""
    native = str(row.get("kind") or "updated")
    payload = _payload_dict(row.get("payload"))
    actor = (
        payload.get("author")
        or payload.get("assignee")
        or row.get("assignee")
        or row.get("created_by")
    )
    body: Dict[str, Any] = {
        "kind": KANBAN_KIND_MAP.get(native, "task.updated"),
        "actor": actor,
        "actor_kind": "agent",
        "harness": row.get("created_by"),
        "machine": _machine_from_lock(payload.get("lock")),
        "session_id": None,
        "task_id": row.get("task_id"),
        "run_id": row.get("run_id"),
        "project": row.get("project_id"),
        "source": "observer:kanban",
        "payload": {
            "title": row.get("title"),
            "status": row.get("status"),
            "native_kind": native,
            **{
                key: payload[key]
                for key in ("author", "assignee", "lock", "summary")
                if key in payload
            },
        },
    }
    created_at = row.get("created_at")
    if created_at is not None:
        body["ts"] = _iso(int(created_at))
    return body


def _iso(unix_seconds: int) -> str:
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(int(unix_seconds), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError, OSError):
        return ""


class KanbanObserver:
    """Polls the board's ``task_events`` and appends unseen rows to the spine."""

    def __init__(
        self,
        store: Any,
        spine: EventSpine,
        broadcaster: Any = None,
        poll_interval: float = DEFAULT_POLL_SECONDS,
    ):
        self.store = store
        self.spine = spine
        self.broadcaster = broadcaster
        self.poll_interval = poll_interval
        self._watermark = 0
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        # Start from the newest row: history stays on the board, the spine is
        # for what happens from now on.
        rows = await self.store.fetch_task_events_after(0, limit=1, newest_first=True)
        self._watermark = int(rows[0]["id"]) if rows else 0
        self._task = asyncio.create_task(
            self._loop(), name="sugar-spine-kanban-observer"
        )

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.poll_interval)
                await self._drain()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - transient DB errors
                logger.debug("kanban observer tick failed", exc_info=True)

    async def _drain(self) -> None:
        rows = await self.store.fetch_task_events_after(
            self._watermark, limit=MAX_BATCH
        )
        for row in rows:
            try:
                event = await self.spine.append(
                    map_kanban_event(row), source="observer:kanban"
                )
            except Exception:  # pragma: no cover - spine must stay best effort
                logger.debug("kanban observer: append failed", exc_info=True)
                continue
            self._watermark = int(row["id"])
            if self.broadcaster is not None:
                self.broadcaster.publish("activity", event)
