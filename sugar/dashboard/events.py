"""SSE broadcasting and SQLite change detection for the dashboard.

One long-lived read connection polls ``PRAGMA data_version`` every 400 ms.
When another connection (the CLI, the MCP server, ``sugar run``, another
dashboard tab) commits, the version changes and the watcher re-reads all rows,
diffs them against the in-memory snapshot and publishes per-id deltas.
"""

import asyncio
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from .serializer import iso_utc_z
from .store import TaskStore

logger = logging.getLogger(__name__)

KEEPALIVE_INTERVAL = 15.0
DEFAULT_POLL_INTERVAL = 0.4
DEFAULT_PRESENCE_INTERVAL = 2.0
DEFAULT_QUEUE_SIZE = 64


def format_sse(event: str, data: Any, event_id: Optional[int] = None) -> str:
    """Format one SSE event as a string chunk.

    ``event_id`` adds an SSE ``id:`` line (used by the ephemeral presence
    heartbeat); existing events keep their two-line format unchanged.
    """
    payload = json.dumps(data, separators=(",", ":"))
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {event}\ndata: {payload}\n\n"


def validate_presence_interval(value: Any) -> float:
    """Return *value* as a positive, finite float or raise ``ValueError``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("presence_interval must be a positive number")
    interval = float(value)
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("presence_interval must be a positive finite number")
    return interval


def presence_interval_from_env(
    default: float = DEFAULT_PRESENCE_INTERVAL,
) -> float:
    """Read ``$SUGAR_PRESENCE_INTERVAL``; invalid values fall back to *default*."""
    raw = os.environ.get("SUGAR_PRESENCE_INTERVAL")
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "dashboard: invalid SUGAR_PRESENCE_INTERVAL=%r; using %.1fs", raw, default
        )
        return default
    if not math.isfinite(value) or value <= 0:
        logger.warning(
            "dashboard: invalid SUGAR_PRESENCE_INTERVAL=%r; using %.1fs", raw, default
        )
        return default
    return value


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

    def publish(self, event: str, data: Any, event_id: Optional[int] = None) -> None:
        item = (event, data, event_id)
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
        presence_interval: float = DEFAULT_PRESENCE_INTERVAL,
    ):
        self.store = store
        self.broadcaster = broadcaster
        self.poll_interval = poll_interval
        self.presence_interval = validate_presence_interval(presence_interval)
        self.started_at: Optional[float] = None
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._stats: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._watcher: Optional[asyncio.Task] = None
        self._presence_task: Optional[asyncio.Task] = None
        self._presence_id = 0
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
        self._presence_task = asyncio.create_task(
            self._presence_loop(), name="sugar-dashboard-presence"
        )

    async def stop(self) -> None:
        tasks = [
            ("watcher", self._watcher),
            ("presence", self._presence_task),
        ]
        self._watcher = None
        self._presence_task = None
        for label, task in tasks:
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover - defensive
                logger.debug(
                    "dashboard %s loop exited with error", label, exc_info=True
                )
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

    # -- ephemeral presence heartbeat --------------------------------------

    def build_presence(self) -> Dict[str, Any]:
        """Build the ephemeral presence payload from the in-memory snapshot.

        Cheap by construction: no database access. ``agents`` are the currently
        active/claimed tasks ordered by lease expiry (nulls last); ``counts``
        mirror the last stats snapshot plus ``total``; ``uptime_s`` is time since
        state start. Timestamps are normalized to ISO-8601 UTC ``Z``.
        """
        agents = []
        for task in self._tasks.values():
            if task.get("status") != "active":
                continue
            agents.append(
                {
                    "actor": task.get("claimed_by") or task.get("created_by"),
                    "task_id": task.get("id"),
                    "title": task.get("title") or "",
                    "status": task.get("status") or "active",
                    "lease_expires_at": iso_utc_z(task.get("lease_expires_at")),
                    "started_at": iso_utc_z(task.get("started_at")),
                    "attempts": int(task.get("attempts") or 0),
                }
            )
        # Normalized timestamps sort correctly (ISO-8601 strings are ordered);
        # tasks without a lease go last, with task id as a stable tie-breaker.
        agents.sort(
            key=lambda agent: (
                agent["lease_expires_at"] is None,
                agent["lease_expires_at"] or "",
                str(agent["task_id"] or ""),
            )
        )

        stats = self._stats if isinstance(self._stats, dict) else {}
        raw_counts = stats.get("by_status")
        counts: Dict[str, int] = {}
        if isinstance(raw_counts, dict):
            for status, count in raw_counts.items():
                try:
                    counts[str(status)] = int(count)
                except (TypeError, ValueError):
                    continue
        try:
            total = int(stats.get("total"))
        except (TypeError, ValueError):
            total = sum(counts.values())
        counts["total"] = total

        uptime = 0.0
        if self.started_at is not None:
            uptime = round(max(0.0, time.monotonic() - self.started_at), 1)

        return {
            "ts": iso_utc_z(datetime.now(timezone.utc)),
            "agents": agents,
            "counts": counts,
            "uptime_s": uptime,
        }

    async def publish_presence(self) -> bool:
        """Publish one presence heartbeat; no-op (False) with no subscribers."""
        if self.broadcaster.client_count == 0:
            return False
        payload = self.build_presence()
        self._presence_id += 1
        self.broadcaster.publish("presence", payload, event_id=self._presence_id)
        return True

    async def _presence_loop(self) -> None:
        while True:
            await asyncio.sleep(self.presence_interval)
            try:
                await self.publish_presence()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - presence must never kill the server
                logger.exception("dashboard presence tick failed")

    async def refresh(
        self,
        publish: bool = True,
        suppress_activity_for: Optional[Set[str]] = None,
    ) -> None:
        """Re-read all rows, diff against the snapshot, publish deltas.

        A local write handler calls this directly so events go out immediately
        instead of waiting for the next poll; the watcher also calls it when
        ``data_version`` changes. ``suppress_activity_for`` lets a caller that
        already emitted a more specific activity event (e.g. the lease reaper)
        keep the generic status-diff event from duplicating it.
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
                await self._emit_activity(
                    previous, tasks, suppress_activity_for or set()
                )
            for task in upserts:
                self.broadcaster.publish("upsert", {"task": task})
            for task_id in deletes:
                self.broadcaster.publish("delete", {"id": task_id})
            if upserts or deletes or stats_changed:
                self.broadcaster.publish("stats", stats)

    async def _emit_activity(
        self,
        previous: Dict[str, Dict[str, Any]],
        current: Dict[str, Dict[str, Any]],
        suppress: Set[str],
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
            if task_id in suppress:
                continue
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

    async def record_activity(
        self,
        kind: str,
        task: Dict[str, Any],
        *,
        actor: Optional[str] = None,
        actor_kind: Optional[str] = None,
        previous_status: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Append one explicit activity event for a task and broadcast it.

        Used when the transition is not visible as a plain status diff — e.g. the
        lease reaper returning an expired task to ``pending``. Best effort: the
        spine is optional and a write failure must never break the dashboard.
        """
        if self.spine is None:
            return None
        payload: Dict[str, Any] = {
            "title": task.get("title"),
            "status": task.get("status"),
            "previous_status": previous_status,
            "source": task.get("source"),
        }
        if extra:
            payload.update(extra)
        body = {
            "kind": kind,
            "actor": actor or task.get("created_by"),
            "actor_kind": actor_kind or task.get("created_by_kind"),
            "harness": "sugar",
            "session_id": task.get("created_by_session"),
            "task_id": task.get("id"),
            "project": None,
            "source": "observer:sugar",
            "payload": payload,
        }
        try:
            event = await self.spine.append(body, source="observer:sugar")
        except Exception:  # pragma: no cover - spine must stay best effort
            logger.debug("spine: activity append failed", exc_info=True)
            return None
        self.broadcaster.publish("activity", event)
        return event

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
