"""Read-only SQLite access for the local dashboard.

The dashboard only ever *reads* through this module; writes go through
``sugar.storage.work_queue.WorkQueue`` (see ``service.py``).  A single
long-lived ``aiosqlite`` connection is kept open so ``PRAGMA data_version``
can be used to detect commits made by other processes.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from .serializer import serialize_task

logger = logging.getLogger(__name__)

STATUS_DEFAULTS = ("pending", "active", "hold", "completed", "failed")

_SORT_ORDERS = {
    "created_at_desc": "created_at DESC, id ASC",
    "priority": "priority ASC, created_at ASC, id ASC",
}


class TaskStore:
    """Read-only access to ``work_items`` and ``task_types``."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def start(self) -> None:
        if self._db is not None:
            return
        # isolation_level=None keeps the connection in autocommit mode: short
        # read transactions only, so a long-lived reader never blocks writers
        # (``sugar run`` / MCP / CLI) for more than one statement.
        self._db = await aiosqlite.connect(self.db_path, isolation_level=None)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA busy_timeout = 5000")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("TaskStore is not started")
        return self._db

    async def data_version(self) -> int:
        """Return ``PRAGMA data_version`` for the long-lived read connection."""
        cursor = await self._conn().execute("PRAGMA data_version")
        try:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0
        finally:
            await cursor.close()

    async def fetch_all(self) -> List[Dict[str, Any]]:
        """Return every task, newest first, serialized."""
        cursor = await self._conn().execute(
            "SELECT * FROM work_items ORDER BY created_at DESC, id ASC"
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        return [serialize_task(row) for row in rows]

    async def fetch_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        cursor = await self._conn().execute(
            "SELECT * FROM work_items WHERE id = ?", (task_id,)
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return serialize_task(row) if row is not None else None

    async def fetch_stats(self) -> Dict[str, Any]:
        """Return queue stats in the shape of ``GET /api/stats``."""
        conn = self._conn()

        cursor = await conn.execute(
            "SELECT status, COUNT(*) FROM work_items GROUP BY status"
        )
        try:
            status_rows = await cursor.fetchall()
        finally:
            await cursor.close()

        by_status: Dict[str, int] = {name: 0 for name in STATUS_DEFAULTS}
        for status, count in status_rows:
            key = status or "pending"
            by_status[key] = by_status.get(key, 0) + int(count)

        cursor = await conn.execute(
            "SELECT type, COUNT(*) FROM work_items GROUP BY type ORDER BY type ASC"
        )
        try:
            type_rows = await cursor.fetchall()
        finally:
            await cursor.close()

        by_type: Dict[str, int] = {}
        for task_type, count in type_rows:
            by_type[task_type or "unknown"] = int(count)

        cursor = await conn.execute("SELECT COUNT(*) FROM work_items")
        try:
            row = await cursor.fetchone()
            total = int(row[0]) if row else 0
        finally:
            await cursor.close()

        cursor = await conn.execute(
            "SELECT COUNT(*) FROM work_items "
            "WHERE created_at > datetime('now', '-1 day')"
        )
        try:
            row = await cursor.fetchone()
            recent_24h = int(row[0]) if row else 0
        finally:
            await cursor.close()

        return {
            "total": total,
            "by_status": by_status,
            "by_type": by_type,
            "recent_24h": recent_24h,
        }

    async def fetch_task_types(self) -> List[Dict[str, Any]]:
        """Return ``/api/task-types`` payload rows.

        The ``task_types`` table is the source of truth, but databases created
        through ``WorkQueue`` may only hold the legacy 5-type seed while rows
        reference other built-in types (``chore``, ``perf``, ...).  Those
        observed types are appended so clients can always edit every row they
        can list; validation uses this same union.
        """
        types: List[Dict[str, Any]] = []
        seen = set()
        try:
            cursor = await self._conn().execute(
                "SELECT id, name, description FROM task_types "
                "ORDER BY is_default DESC, name ASC"
            )
        except aiosqlite.OperationalError:
            cursor = None
        if cursor is not None:
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
            for row in rows:
                type_id = row["id"]
                if type_id in seen:
                    continue
                seen.add(type_id)
                types.append(
                    {
                        "id": type_id,
                        "name": row["name"] if row["name"] is not None else type_id,
                        "description": row["description"] or "",
                    }
                )

        try:
            cursor = await self._conn().execute(
                "SELECT DISTINCT type FROM work_items "
                "WHERE type IS NOT NULL AND type != '' ORDER BY type ASC"
            )
        except aiosqlite.OperationalError:
            cursor = None
        if cursor is not None:
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
            for row in rows:
                type_id = row[0]
                if type_id not in seen:
                    seen.add(type_id)
                    types.append({"id": type_id, "name": type_id, "description": ""})

        return types

    async def fetch_task_type_ids(self) -> List[str]:
        return [task_type["id"] for task_type in await self.fetch_task_types()]

    async def fetch_tasks(
        self,
        *,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        priority: Optional[int] = None,
        query: Optional[str] = None,
        created_by: Optional[str] = None,
        order: str = "created_at_desc",
        limit: int = 200,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Return a filtered page of tasks plus the total matching count."""
        where: List[str] = []
        params: List[Any] = []

        if status:
            where.append("status = ?")
            params.append(status)
        if task_type:
            where.append("type = ?")
            params.append(task_type)
        if priority is not None:
            where.append("priority = ?")
            params.append(priority)
        if created_by:
            # Provenance filter (§10.4): exact match on the creator id.
            where.append("created_by = ?")
            params.append(created_by)
        if query:
            pattern = (
                "%"
                + (query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))
                + "%"
            )
            where.append(
                "(title LIKE ? ESCAPE '\\' OR COALESCE(description, '') "
                "LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern, pattern])

        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        order_sql = _SORT_ORDERS.get(order, _SORT_ORDERS["created_at_desc"])
        conn = self._conn()

        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM work_items{where_sql}", params
        )
        try:
            row = await cursor.fetchone()
            total = int(row[0]) if row else 0
        finally:
            await cursor.close()

        cursor = await conn.execute(
            f"SELECT * FROM work_items{where_sql} "
            f"ORDER BY {order_sql} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()

        return [serialize_task(row) for row in rows], total
