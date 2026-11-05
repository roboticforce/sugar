"""SSE broadcasting and SQLite change detection for the dashboard.

One long-lived read connection polls ``PRAGMA data_version`` every 400 ms.
When another connection (the CLI, the MCP server, ``sugar run``, another
dashboard tab) commits, the version changes and the watcher re-reads all rows,
diffs them against the in-memory snapshot and publishes per-id deltas.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from .store import TaskStore

logger = logging.getLogger(__name__)

KEEPALIVE_INTERVAL = 15.0
DEFAULT_POLL_INTERVAL = 0.4
DEFAULT_QUEUE_SIZE = 64


def format_sse(event: str, data: Any) -> str:
    """Format one SSE event as a string chunk."""
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


class EventBroadcaster:
    """Fan-out of SSE events to every connected client.

    Each subscriber owns a bounded queue.  A slow client never blocks the
    watcher: when its queue is full the oldest pending event is dropped, and
    the client recovers on its next reconnect (the snapshot is authoritative).
    """

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE):
        self._queue_size = queue_size
        self._clients: set = set()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._clients.discard(queue)

    def publish(self, event: str, data: Any) -> None:
        item = (event, data)
        for queue in list(self._clients):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(item)
                except asyncio.QueueFull:
                    pass


class DashboardState:
    """In-memory snapshot of the queue, kept fresh by a polling watcher."""

    def __init__(
        self,
        store: TaskStore,
        broadcaster: EventBroadcaster,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ):
        self.store = store
        self.broadcaster = broadcaster
        self.poll_interval = poll_interval
        self.started_at: Optional[float] = None
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._stats: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._watcher: Optional[asyncio.Task] = None
        self._data_version = 0
        # Optional event spine (docs §11); the Sugar source diffs snapshots and
        # records transitions here. None for the Kanban source, which tails the
        # board's own task_events instead.
        self.spine = None

    async def start(self) -> None:
        await self.store.start()
        # Initial snapshot: also fails fast if the DB is unreadable.
        await self.refresh(publish=False)
        self._data_version = await self.store.data_version()
        self.started_at = time.monotonic()
        self._watcher = asyncio.create_task(
            self._watch_loop(), name="sugar-dashboard-watcher"
        )

    async def stop(self) -> None:
        watcher = self._watcher
        self._watcher = None
        if watcher is not None:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover - defensive
                logger.debug("dashboard watcher exited with error", exc_info=True)
        await self.store.close()

    async def snapshot(self) -> Dict[str, Any]:
        """Return the current authoritative snapshot for a new SSE client."""
        async with self._lock:
            return {
                "tasks": list(self._tasks.values()),
                "stats": dict(self._stats),
            }

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._tasks.get(task_id)

    async def refresh(self, publish: bool = True) -> None:
        """Re-read all rows, diff against the snapshot, publish deltas.

        A local write handler calls this directly so events go out immediately
        instead of waiting for the next poll; the watcher also calls it when
        ``data_version`` changes.
        """
        async with self._lock:
            tasks_list = await self.store.fetch_all()
            stats = await self.store.fetch_stats()
            tasks = {task["id"]: task for task in tasks_list}

            upserts = [
                task
                for task_id, task in tasks.items()
                if self._tasks.get(task_id) != task
            ]
            deletes: List[str] = [
                task_id for task_id in self._tasks if task_id not in tasks
            ]
            stats_changed = stats != self._stats

            previous = self._tasks
            self._tasks = tasks
            self._stats = stats

            if not publish:
                return
            if self.spine is not None:
                await self._emit_activity(previous, tasks)
            for task in upserts:
                self.broadcaster.publish("upsert", {"task": task})
            for task_id in deletes:
                self.broadcaster.publish("delete", {"id": task_id})
            if upserts or deletes or stats_changed:
                self.broadcaster.publish("stats", stats)

    async def _emit_activity(
        self, previous: Dict[str, Dict[str, Any]], current: Dict[str, Dict[str, Any]]
    ) -> None:
        """Best-effort: record status transitions on the Sugar queue."""
        status_kinds = {
            "active": "task.claimed",
            "completed": "task.completed",
            "failed": "task.failed",
            "hold": "task.held",
            "pending": "task.released",
        }
        for task_id, task in current.items():
            old = previous.get(task_id)
            if old is None:
                kind = "task.created"
            elif old.get("status") != task.get("status"):
                kind = status_kinds.get(task.get("status"), "task.updated")
                if kind == "task.released" and old.get("status") not in ("hold", None):
                    kind = "task.updated"
            else:
                continue

            actor = task.get("claimed_by") if kind == "task.claimed" else None
            if actor is None:
                actor = (old or {}).get("claimed_by") or task.get("created_by")
            body = {
                "kind": kind,
                "actor": actor,
                "actor_kind": (
                    "agent" if kind == "task.claimed" else task.get("created_by_kind")
                ),
                "harness": "sugar",
                "session_id": task.get("created_by_session"),
                "task_id": task_id,
                "project": None,
                "source": "observer:sugar",
                "payload": {
                    "title": task.get("title"),
                    "status": task.get("status"),
                    "previous_status": (old or {}).get("status"),
                    "source": task.get("source"),
                },
            }
            try:
                event = await self.spine.append(body, source="observer:sugar")
            except Exception:  # pragma: no cover - spine must stay best effort
                logger.debug("spine: sugar activity append failed", exc_info=True)
                continue
            self.broadcaster.publish("activity", event)

    async def _watch_loop(self) -> None:
        while True:
            await asyncio.sleep(self.poll_interval)
            try:
                version = await self.store.data_version()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - transient DB errors
                logger.debug("dashboard watcher: data_version failed: %s", exc)
                continue
            if version == self._data_version:
                continue
            self._data_version = version
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - transient DB errors
                logger.exception("dashboard watcher refresh failed")
