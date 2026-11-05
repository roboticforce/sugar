"""Read-only SQLite access to a Hermes Kanban board.

One long-lived connection (``PRAGMA query_only = ON``) is kept open so
``PRAGMA data_version`` can detect commits made by Hermes, the dispatcher, the
MCP shim or another dashboard tab. Writes never pass through this module.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from . import mapping

logger = logging.getLogger(__name__)

_TASK_COLUMNS = (
    "id",
    "title",
    "body",
    "assignee",
    "status",
    "priority",
    "created_by",
    "created_at",
    "started_at",
    "completed_at",
    "workspace_kind",
    "workspace_path",
    "branch_name",
    "project_id",
    "claim_lock",
    "claim_expires",
    "tenant",
    "result",
    "consecutive_failures",
    "worker_pid",
    "last_failure_error",
    "last_heartbeat_at",
    "current_run_id",
    "current_step_key",
    "block_kind",
    "block_recurrences",
)

_COLUMNS_SQL = ", ".join(f"t.{column}" for column in _TASK_COLUMNS)
_ATTEMPTS_SQL = "(SELECT COUNT(*) FROM task_runs r WHERE r.task_id = t.id) AS attempts"
_SELECT = f"SELECT {_COLUMNS_SQL}, {_ATTEMPTS_SQL} FROM tasks t"

_SORT_ORDERS = {
    "created_at_desc": "t.created_at DESC, t.id ASC",
    "priority": "t.priority ASC, t.created_at ASC, t.id ASC",
}


class KanbanStore:
    """Read-only access to ``tasks``, ``task_links`` and the activity tables."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def start(self) -> None:
        if self._db is not None:
            return
        self._db = await aiosqlite.connect(self.db_path, isolation_level=None)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA busy_timeout = 5000")
        # Belt and braces: this connection must never be able to mutate the board.
        await self._db.execute("PRAGMA query_only = ON")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("KanbanStore is not started")
        return self._db

    async def _fetchall(self, sql: str, params: List[Any] = ()) -> List[aiosqlite.Row]:
        cursor = await self._conn().execute(sql, params)
        try:
            return await cursor.fetchall()
        finally:
            await cursor.close()

    async def _fetchone(
        self, sql: str, params: List[Any] = ()
    ) -> Optional[aiosqlite.Row]:
        cursor = await self._conn().execute(sql, params)
        try:
            return await cursor.fetchone()
        finally:
            await cursor.close()

    # -- change detection --------------------------------------------------

    async def data_version(self) -> int:
        row = await self._fetchone("PRAGMA data_version")
        return int(row[0]) if row else 0

    # -- reads -------------------------------------------------------------

    async def fetch_all(self) -> List[Dict[str, Any]]:
        rows = await self._fetchall(
            _SELECT + " WHERE t.status != ? ORDER BY t.created_at DESC",
            [mapping.ARCHIVED],
        )
        return await self._with_parents(rows)

    async def fetch_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        row = await self._fetchone(_SELECT + " WHERE t.id = ?", [task_id])
        if row is None and task_id:
            matches = await self._fetchall(
                _SELECT + " WHERE t.id LIKE ? ORDER BY t.created_at DESC LIMIT 2",
                [f"{task_id}%"],
            )
            if len(matches) == 1:
                row = matches[0]
        if row is None:
            return None
        parents = await self._parents_for(row["id"])
        activity = await self._activity(row["id"])
        return mapping.serialize_task(dict(row), parents=parents, activity=activity)

    async def fetch_tasks(
        self,
        *,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        priority: Optional[int] = None,
        query: Optional[str] = None,
        order: str = "created_at_desc",
        limit: int = 200,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        where: List[str] = []
        params: List[Any] = []

        if status and status == mapping.ARCHIVED:
            where.append("t.status = ?")
            params.append(mapping.ARCHIVED)
        else:
            where.append("t.status != ?")
            params.append(mapping.ARCHIVED)
            if status:
                where.append("t.status = ?")
                params.append(status)

        if task_type:
            if task_type == mapping.DEFAULT_PROJECT:
                where.append("(t.project_id IS NULL OR t.project_id = '')")
            else:
                where.append("t.project_id = ?")
                params.append(task_type)

        if priority is not None:
            where.append("t.priority = ?")
            params.append(priority)

        if query:
            like = f"%{query}%"
            where.append("(t.title LIKE ? OR t.body LIKE ? OR t.id LIKE ?)")
            params.extend([like, like, like])

        clause = " WHERE " + " AND ".join(where)
        total_row = await self._fetchone(
            "SELECT COUNT(*) FROM tasks t" + clause, list(params)
        )
        total = int(total_row[0]) if total_row else 0

        sql = (
            _SELECT
            + clause
            + f" ORDER BY {_SORT_ORDERS.get(order, _SORT_ORDERS['created_at_desc'])}"
            + " LIMIT ? OFFSET ?"
        )
        rows = await self._fetchall(sql, [*params, limit, offset])
        tasks = await self._with_parents(rows)
        return tasks, total

    async def fetch_task_events_after(
        self, after_id: int, *, limit: int = 200, newest_first: bool = False
    ) -> List[Dict[str, Any]]:
        """Board ``task_events`` rows newer than *after_id* (joined with tasks)."""
        order = "DESC" if newest_first else "ASC"
        rows = await self._fetchall(
            f"""
            SELECT e.id, e.task_id, e.run_id, e.kind, e.payload, e.created_at,
                   t.title, t.project_id, t.created_by, t.assignee, t.status
            FROM task_events e
            LEFT JOIN tasks t ON t.id = e.task_id
            WHERE e.id > ?
            ORDER BY e.id {order}
            LIMIT ?
            """,
            [max(0, int(after_id)), min(int(limit), 500)],
        )
        return [dict(row) for row in rows]

    async def fetch_stats(self) -> Dict[str, Any]:
        rows = await self._fetchall(
            "SELECT status, COUNT(*) FROM tasks t WHERE t.status != ? GROUP BY status",
            [mapping.ARCHIVED],
        )
        by_status = {status: 0 for status in mapping.KANBAN_STATUSES}
        for status, count in rows:
            by_status[str(status)] = int(count)

        type_rows = await self._fetchall(
            "SELECT t.project_id, COUNT(*) FROM tasks t"
            " WHERE t.status != ? GROUP BY t.project_id",
            [mapping.ARCHIVED],
        )
        by_type = {
            (str(project) if project else mapping.DEFAULT_PROJECT): int(count)
            for project, count in type_rows
        }

        cutoff = int(time.time()) - 86400
        recent_row = await self._fetchone(
            "SELECT COUNT(*) FROM tasks t WHERE t.status != ? AND t.created_at > ?",
            [mapping.ARCHIVED, cutoff],
        )
        total = sum(by_status.values())
        return {
            "total": total,
            "by_status": by_status,
            "by_type": by_type,
            "recent_24h": int(recent_row[0]) if recent_row else 0,
        }

    async def fetch_task_types(self) -> List[Dict[str, Any]]:
        rows = await self._fetchall(
            "SELECT t.project_id FROM tasks t"
            " WHERE t.project_id IS NOT NULL AND t.project_id != ''"
            " GROUP BY t.project_id ORDER BY t.project_id"
        )
        return [
            {"id": str(row[0]), "name": str(row[0]), "description": None}
            for row in rows
        ]

    async def fetch_task_type_ids(self) -> List[str]:
        return [item["id"] for item in await self.fetch_task_types()]

    # -- internals ---------------------------------------------------------

    async def _with_parents(self, rows: List[aiosqlite.Row]) -> List[Dict[str, Any]]:
        links = await self._fetchall("SELECT parent_id, child_id FROM task_links")
        parents: Dict[str, List[str]] = {}
        for parent_id, child_id in links:
            parents.setdefault(str(child_id), []).append(str(parent_id))
        return [
            mapping.serialize_task(dict(row), parents=parents.get(str(row["id"]), ()))
            for row in rows
        ]

    async def _parents_for(self, task_id: str) -> List[str]:
        rows = await self._fetchall(
            "SELECT parent_id FROM task_links WHERE child_id = ? ORDER BY parent_id",
            [task_id],
        )
        return [str(row[0]) for row in rows]

    async def _activity(self, task_id: str) -> Dict[str, Any]:
        comments = await self._fetchall(
            "SELECT id, author, body, created_at FROM task_comments"
            " WHERE task_id = ? ORDER BY created_at DESC LIMIT 50",
            [task_id],
        )
        runs = await self._fetchall(
            "SELECT id, profile, status, outcome, summary, error, started_at, ended_at"
            " FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 20",
            [task_id],
        )
        events = await self._fetchall(
            "SELECT id, kind, payload, created_at FROM task_events"
            " WHERE task_id = ? ORDER BY id DESC LIMIT 50",
            [task_id],
        )
        return {
            "comments": [
                {
                    "id": row["id"],
                    "author": row["author"],
                    "body": row["body"],
                    "at": mapping.iso_timestamp(row["created_at"]),
                }
                for row in comments
            ],
            "runs": [
                {
                    "id": row["id"],
                    "profile": row["profile"],
                    "status": row["status"],
                    "outcome": row["outcome"],
                    "summary": row["summary"],
                    "error": row["error"],
                    "started_at": mapping.iso_timestamp(row["started_at"]),
                    "ended_at": mapping.iso_timestamp(row["ended_at"]),
                }
                for row in runs
            ],
            "events": [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "payload": _maybe_json(row["payload"]),
                    "at": mapping.iso_timestamp(row["created_at"]),
                }
                for row in events
            ],
        }


def _maybe_json(raw: Any) -> Any:
    if raw in (None, ""):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw
