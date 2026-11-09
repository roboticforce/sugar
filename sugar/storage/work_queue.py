"""
Work Queue - Manage work items with priorities and persistence
"""

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

from ..identity import (
    DEFAULT_LEASE_SECONDS,
    format_lease_timestamp,
    lease_expiry,
    worker_actor,
)
from .migrations import SCHEMA_VERSION, apply_migrations

logger = logging.getLogger(__name__)

#: How many pending rows a claim scans per batch while skipping blocked tasks.
CLAIM_SCAN_BATCH = 100


class WorkQueue:
    """Persistent work queue with priority management.

    SQLite implementation of :class:`sugar.storage.ports.TaskStore`. The
    coordination invariants (atomic claim, lease reaper, dependency
    enforcement) live here for now; a future Postgres backend implements the
    same protocol against the conformance suite in
    ``tests/test_task_store_conformance.py``.
    """

    #: Schema version this implementation writes/expects (see migrations.py).
    SCHEMA_VERSION = SCHEMA_VERSION

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._initialized = False
        self._schema_version = 0
        self._init_lock = asyncio.Lock()

    @property
    def schema_version(self) -> int:
        """Schema version applied by the last :meth:`initialize` (0 before)."""
        return self._schema_version

    async def initialize(self):
        """Create or upgrade the database schema (idempotent).

        All SQLite DDL lives in :mod:`sugar.storage.migrations`; this method
        only opens the connection and asks the ledger to bring the database to
        :data:`~sugar.storage.migrations.SCHEMA_VERSION`. Legacy databases
        (``user_version == 0`` with tables already present) are upgraded by the
        same idempotent registry.
        """
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            async with aiosqlite.connect(self.db_path) as db:
                self._schema_version = await apply_migrations(db)

            logger.debug(
                "✅ Work queue initialized: %s (schema v%d)",
                self.db_path,
                self._schema_version,
            )
            self._initialized = True

    async def close(self):
        """Close the work queue (for testing)"""
        # SQLite connections are closed automatically, but this method
        # provides a consistent interface for tests
        pass

    async def work_exists(
        self, source_file: str, exclude_statuses: List[str] = None
    ) -> bool:
        """Check if work item with given source_file already exists"""
        if exclude_statuses is None:
            exclude_statuses = ["failed"]  # Don't prevent retrying failed items

        async with aiosqlite.connect(self.db_path) as db:
            query = "SELECT COUNT(*) FROM work_items WHERE source_file = ?"
            params = [source_file]

            if exclude_statuses:
                placeholders = ",".join("?" * len(exclude_statuses))
                query += f" AND status NOT IN ({placeholders})"
                params.extend(exclude_statuses)

            cursor = await db.execute(query, params)
            count = (await cursor.fetchone())[0]
            return count > 0

    async def add_work(self, work_item: Dict[str, Any]) -> str:
        """Add a new work item to the queue"""
        work_id = str(uuid.uuid4())

        # Set defaults
        work_item.setdefault("status", "pending")
        work_item.setdefault("priority", 3)
        work_item.setdefault("attempts", 0)
        work_item.setdefault("orchestrate", False)

        async with aiosqlite.connect(self.db_path) as db:
            # Prepare blocked_by as JSON if it's a list
            blocked_by = work_item.get("blocked_by", [])
            if isinstance(blocked_by, list):
                blocked_by_json = json.dumps(blocked_by)
            else:
                blocked_by_json = blocked_by

            # Prepare acceptance_criteria as JSON if it's a list
            acceptance_criteria = work_item.get("acceptance_criteria", [])
            if isinstance(acceptance_criteria, list):
                acceptance_criteria_json = (
                    json.dumps(acceptance_criteria) if acceptance_criteria else None
                )
            else:
                acceptance_criteria_json = acceptance_criteria

            await db.execute(
                """
                INSERT INTO work_items
                (id, type, title, description, priority, status, source, source_file, context,
                 orchestrate, parent_task_id, stage, blocked_by, context_path, assigned_agent,
                 acceptance_criteria, created_by, created_by_kind, created_by_session)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    work_id,
                    work_item["type"],
                    work_item["title"],
                    work_item.get("description", ""),
                    work_item["priority"],
                    work_item["status"],
                    work_item.get("source", ""),
                    work_item.get("source_file", ""),
                    json.dumps(work_item.get("context", {})),
                    work_item.get("orchestrate", False),
                    work_item.get("parent_task_id"),
                    work_item.get("stage"),
                    blocked_by_json if blocked_by else None,
                    work_item.get("context_path"),
                    work_item.get("assigned_agent"),
                    acceptance_criteria_json,
                    work_item.get("created_by"),
                    work_item.get("created_by_kind"),
                    work_item.get("created_by_session"),
                ),
            )
            await db.commit()

        logger.debug(
            f"➕ Added work item: {work_item['title']} (priority: {work_item['priority']})"
        )
        return work_id

    async def _first_incomplete_blocker(self, db, work_item: Dict[str, Any]):
        """Return a blocker id if *work_item* has an unmet dependency, else ``None``.

        ``blocked_by`` is stored as a JSON array. Semantics (matching
        ``get_ready_subtasks``): a missing/empty list means no dependency; a task
        is blocked unless **every** blocker exists and is ``completed``.
        Unreadable JSON fails closed — better a visibly blocked task than work
        running before prerequisites it cannot evaluate.
        """
        raw = work_item.get("blocked_by")
        if raw in (None, "", "[]"):
            return None
        try:
            blockers = json.loads(raw) if isinstance(raw, str) else list(raw)
        except (TypeError, ValueError):
            logger.warning(
                "Task %s has unreadable blocked_by=%r; treating as blocked",
                work_item.get("id"),
                raw,
            )
            return "<unreadable>"
        if not blockers:
            return None

        placeholders = ",".join("?" for _ in blockers)
        cursor = await db.execute(
            f"SELECT id, status FROM work_items WHERE id IN ({placeholders})",
            [str(blocker) for blocker in blockers],
        )
        statuses = {row[0]: row[1] async for row in cursor}
        for blocker in blockers:
            if statuses.get(str(blocker)) != "completed":
                return str(blocker)
        return None

    async def get_next_work(
        self,
        worker_id: Optional[str] = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> Optional[Dict[str, Any]]:
        """Atomically claim the highest-priority *claimable* task.

        A task is claimable when it is ``pending`` and every id in
        ``blocked_by`` exists and is ``completed`` (missing blockers block, as in
        ``get_ready_subtasks``). ``BEGIN IMMEDIATE`` serializes writers, so the
        scan-then-update below is one atomic step: nobody else can claim the
        chosen task or change its blockers mid-transaction.

        The same UPDATE records who claimed the task (``claimed_by``, from
        ``worker_id`` or the resolved local actor) and a ``lease_expires_at`` so a
        crashed worker can be reaped instead of stranding the task.
        """
        async with aiosqlite.connect(self.db_path, isolation_level=None) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")

            try:
                offset = 0
                while True:
                    cursor = await db.execute(
                        """
                        SELECT * FROM work_items
                        WHERE status = 'pending'
                        ORDER BY priority ASC, created_at ASC, id ASC
                        LIMIT ? OFFSET ?
                        """,
                        (CLAIM_SCAN_BATCH, offset),
                    )
                    rows = await cursor.fetchall()
                    if not rows:
                        await db.execute("ROLLBACK")
                        return None

                    for row in rows:
                        work_item = dict(row)
                        blocker = await self._first_incomplete_blocker(db, work_item)
                        if blocker is not None:
                            logger.debug(
                                "Task %s is blocked by %s; skipping",
                                work_item.get("id"),
                                blocker,
                            )
                            continue

                        # A claim is made by a worker, not by the human who queued it.
                        actor = worker_actor(worker_id)
                        lease_until = lease_expiry(lease_seconds)
                        await db.execute(
                            """
                            UPDATE work_items
                            SET status = 'active',
                                attempts = attempts + 1,
                                last_attempt_at = CURRENT_TIMESTAMP,
                                started_at = CASE WHEN started_at IS NULL THEN CURRENT_TIMESTAMP ELSE started_at END,
                                claimed_by = ?,
                                lease_expires_at = ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ? AND status = 'pending'
                            """,
                            (actor.id, lease_until, work_item["id"]),
                        )
                        await db.execute("COMMIT")

                        if work_item.get("context"):
                            try:
                                work_item["context"] = json.loads(work_item["context"])
                            except json.JSONDecodeError:
                                work_item["context"] = {}
                        else:
                            work_item["context"] = {}

                        work_item["attempts"] = (work_item.get("attempts") or 0) + 1
                        work_item["status"] = "active"
                        work_item["claimed_by"] = actor.id
                        work_item["lease_expires_at"] = lease_until
                        logger.debug(
                            "📋 Retrieved work item: %s (attempt #%s)",
                            work_item["title"],
                            work_item["attempts"],
                        )
                        return work_item

                    # Nothing claimable in this batch; keep scanning so tasks
                    # behind a long run of blocked rows are not starved.
                    offset += len(rows)
                    if len(rows) < CLAIM_SCAN_BATCH:
                        await db.execute("ROLLBACK")
                        return None

            except Exception:
                await db.execute("ROLLBACK")
                raise

    async def complete_work(self, work_id: str, result: Dict[str, Any]):
        """Mark a work item as completed with results and timing"""
        async with aiosqlite.connect(self.db_path) as db:
            # Extract execution time from result
            execution_time = 0.0
            try:
                if isinstance(result, dict):
                    # Try various ways to extract execution time
                    execution_time = (
                        result.get("execution_time", 0)
                        or result.get("result", {}).get("execution_time", 0)
                        or 0.0
                    )
            except (TypeError, AttributeError):
                execution_time = 0.0

            # Extract thinking data from result
            thinking_log_path = result.get("thinking_log_path")
            thinking_summary = result.get("thinking_summary")
            thinking_stats = result.get("thinking_stats")
            thinking_stats_json = json.dumps(thinking_stats) if thinking_stats else None

            await db.execute(
                """
                UPDATE work_items
                SET status = 'completed',
                    result = ?,
                    completed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP,
                    total_execution_time = total_execution_time + ?,
                    total_elapsed_time = (
                        CASE
                            WHEN started_at IS NOT NULL
                            THEN (julianday(CURRENT_TIMESTAMP) - julianday(started_at)) * 86400.0
                            ELSE (julianday(CURRENT_TIMESTAMP) - julianday(created_at)) * 86400.0
                        END
                    ),
                    thinking_log_path = ?,
                    thinking_summary = ?,
                    thinking_stats = ?,
                    claimed_by = NULL,
                    lease_expires_at = NULL
                WHERE id = ?
            """,
                (
                    json.dumps(result),
                    execution_time,
                    thinking_log_path,
                    thinking_summary,
                    thinking_stats_json,
                    work_id,
                ),
            )

            await db.commit()

        logger.debug(
            f"✅ Completed work item: {work_id} (+{execution_time:.1f}s execution)"
        )

    async def fail_work(
        self,
        work_id: str,
        error_message: str,
        max_retries: int = 3,
        execution_time: float = 0.0,
    ):
        """Mark a work item as failed, or retry if under retry limit"""
        async with aiosqlite.connect(self.db_path) as db:
            # Get current attempts
            cursor = await db.execute(
                """
                SELECT attempts, title FROM work_items WHERE id = ?
            """,
                (work_id,),
            )

            row = await cursor.fetchone()
            if not row:
                logger.error(f"Work item not found: {work_id}")
                return

            attempts, title = row

            if attempts >= max_retries:
                # Final failure - record total elapsed time
                await db.execute(
                    """
                    UPDATE work_items 
                    SET status = 'failed',
                        error_message = ?,
                        claimed_by = NULL,
                        lease_expires_at = NULL,
                        updated_at = CURRENT_TIMESTAMP,
                        total_execution_time = total_execution_time + ?,
                        total_elapsed_time = (
                            CASE 
                                WHEN started_at IS NOT NULL 
                                THEN (julianday(CURRENT_TIMESTAMP) - julianday(started_at)) * 86400.0
                                ELSE (julianday(CURRENT_TIMESTAMP) - julianday(created_at)) * 86400.0
                            END
                        )
                    WHERE id = ?
                """,
                    (error_message, execution_time, work_id),
                )

                logger.error(
                    f"❌ Work item failed permanently: {title} (after {attempts} attempts, +{execution_time:.1f}s)"
                )
            else:
                # Retry later - accumulate execution time but don't calculate elapsed time yet
                await db.execute(
                    """
                    UPDATE work_items 
                    SET status = 'pending',
                        error_message = ?,
                        claimed_by = NULL,
                        lease_expires_at = NULL,
                        updated_at = CURRENT_TIMESTAMP,
                        total_execution_time = total_execution_time + ?
                    WHERE id = ?
                """,
                    (error_message, execution_time, work_id),
                )

                logger.warning(
                    f"⚠️ Work item will be retried: {title} (attempt {attempts}/{max_retries}, +{execution_time:.1f}s)"
                )

            await db.commit()

    async def get_work_item(self, work_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific work item by ID"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute(
                """
                SELECT * FROM work_items WHERE id = ?
            """,
                (work_id,),
            )

            row = await cursor.fetchone()

            if not row:
                return None

            work_item = dict(row)

            # Parse JSON fields
            for field in ["context", "result"]:
                if work_item[field]:
                    try:
                        work_item[field] = json.loads(work_item[field])
                    except json.JSONDecodeError:
                        work_item[field] = {}
                else:
                    work_item[field] = {}

            # ``blocked_by`` is a JSON array or NULL in storage; the port exposes
            # a list. Unreadable values are returned unchanged for inspection
            # (the claim path fails closed on them).
            raw_blocked_by = work_item.get("blocked_by")
            if raw_blocked_by in (None, ""):
                work_item["blocked_by"] = []
            elif isinstance(raw_blocked_by, str):
                try:
                    parsed = json.loads(raw_blocked_by)
                except json.JSONDecodeError:
                    pass
                else:
                    if isinstance(parsed, list):
                        work_item["blocked_by"] = parsed

            return work_item

    async def get_recent_work(
        self, limit: int = 10, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get recent work items, optionally filtered by status"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            query = "SELECT * FROM work_items"
            params = []

            if status:
                query += " WHERE status = ?"
                params.append(status)

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            work_items = []
            for row in rows:
                work_item = dict(row)

                # Parse JSON fields
                for field in ["context", "result"]:
                    if work_item[field]:
                        try:
                            work_item[field] = json.loads(work_item[field])
                        except json.JSONDecodeError:
                            work_item[field] = {}
                    else:
                        work_item[field] = {}

                work_items.append(work_item)

            return work_items

    async def get_stats(self) -> Dict[str, int]:
        """Get queue statistics"""
        async with aiosqlite.connect(self.db_path) as db:
            stats = {}

            # Count by status
            cursor = await db.execute(
                """
                SELECT status, COUNT(*) as count 
                FROM work_items 
                GROUP BY status
            """
            )

            rows = await cursor.fetchall()
            for row in rows:
                stats[row[0]] = row[1]

            # Set defaults for missing statuses
            for status in ["pending", "hold", "active", "completed", "failed"]:
                stats.setdefault(status, 0)

            # Total items
            stats["total"] = sum(stats.values())

            # Recent activity (last 24 hours)
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM work_items 
                WHERE created_at > datetime('now', '-1 day')
            """
            )
            stats["recent_24h"] = (await cursor.fetchone())[0]

            return stats

    async def cleanup_old_items(self, days_old: int = 30):
        """Clean up old completed/failed items"""
        if not isinstance(days_old, int) or days_old < 0:
            raise ValueError("days_old must be a non-negative integer")

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM work_items
                WHERE status IN ('completed', 'failed')
                AND created_at < datetime('now', '-' || ? || ' days')
            """,
                (days_old,),
            )

            deleted_count = cursor.rowcount
            await db.commit()

            if deleted_count > 0:
                logger.info(f"🗑️ Cleaned up {deleted_count} old work items")

            return deleted_count

    async def get_work_by_id(self, work_id: str) -> Optional[Dict[str, Any]]:
        """Get specific work item by ID"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT id, type, title, description, priority, status, source,
                       context, created_at, updated_at, attempts, last_attempt_at,
                       completed_at, result, total_execution_time, started_at,
                       total_elapsed_time, commit_sha,
                       orchestrate, parent_task_id, stage, blocked_by, context_path, assigned_agent,
                       acceptance_criteria,
                       verification_required, verification_status, verification_results,
                       created_by, created_by_kind, created_by_session,
                       claimed_by, lease_expires_at
                FROM work_items
                WHERE id = ?
            """,
                (work_id,),
            ) as cursor:
                row = await cursor.fetchone()

                if row:
                    return {
                        "id": row[0],
                        "type": row[1],
                        "title": row[2],
                        "description": row[3],
                        "priority": row[4],
                        "status": row[5],
                        "source": row[6],
                        "context": json.loads(row[7]) if row[7] else {},
                        "created_at": row[8],
                        "updated_at": row[9],
                        "attempts": row[10],
                        "last_attempt_at": row[11],
                        "completed_at": row[12],
                        "result": json.loads(row[13]) if row[13] else None,
                        "total_execution_time": row[14],
                        "started_at": row[15],
                        "total_elapsed_time": row[16],
                        "commit_sha": row[17],
                        "orchestrate": bool(row[18]) if row[18] is not None else False,
                        "parent_task_id": row[19],
                        "stage": row[20],
                        "blocked_by": json.loads(row[21]) if row[21] else [],
                        "context_path": row[22],
                        "assigned_agent": row[23],
                        "acceptance_criteria": json.loads(row[24]) if row[24] else [],
                        "verification_required": (
                            bool(row[25]) if row[25] is not None else False
                        ),
                        "verification_status": row[26] if row[26] else "pending",
                        "verification_results": (
                            json.loads(row[27]) if row[27] else None
                        ),
                        "created_by": row[28],
                        "created_by_kind": row[29],
                        "created_by_session": row[30],
                        "claimed_by": row[31],
                        "lease_expires_at": row[32],
                    }
                return None

    async def remove_work(self, work_id: str) -> bool:
        """Remove work item by ID"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM work_items WHERE id = ?", (work_id,))
            await db.commit()
            return cursor.rowcount > 0

    ALLOWED_UPDATE_COLUMNS: frozenset = frozenset(
        {
            "title",
            "description",
            "type",
            "priority",
            "status",
            "context",
            "acceptance_criteria",
            "blocked_by",
            "verification_results",
            "assigned_agent",
            "claimed_by",
            "lease_expires_at",
            "stage",
            "context_path",
            "error_count",
            "last_error",
            "result",
            "started_at",
            "completed_at",
            "updated_at",
        }
    )

    async def update_work(self, work_id: str, updates: Dict[str, Any]) -> bool:
        """Update work item by ID"""
        if not updates:
            return False

        # Validate column names against allowlist to prevent SQL injection
        invalid_keys = set(updates.keys()) - self.ALLOWED_UPDATE_COLUMNS
        if invalid_keys:
            raise ValueError(
                f"Invalid update columns: {invalid_keys}. "
                f"Allowed columns: {sorted(self.ALLOWED_UPDATE_COLUMNS)}"
            )

        # Build dynamic UPDATE query
        set_clauses = []
        values = []

        for key, value in updates.items():
            if key in (
                "context",
                "acceptance_criteria",
                "blocked_by",
                "verification_results",
            ):
                set_clauses.append(f"{key} = ?")
                values.append(json.dumps(value) if value else None)
            else:
                set_clauses.append(f"{key} = ?")
                values.append(value)

        values.append(work_id)  # FOR WHERE clause

        query = f"UPDATE work_items SET {', '.join(set_clauses)} WHERE id = ?"

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, values)
            await db.commit()
            return cursor.rowcount > 0

    async def update_commit_sha(self, work_id: str, commit_sha: str) -> bool:
        """Update the commit SHA for a work item"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE work_items
                SET commit_sha = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (commit_sha, work_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def hold_work(self, work_id: str, reason: str = None) -> bool:
        """Put a work item on hold (atomic read-modify-write)"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT context, status FROM work_items WHERE id = ?", (work_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return False

            context = {}
            if row["context"]:
                try:
                    context = json.loads(row["context"])
                except json.JSONDecodeError:
                    context = {}

            if reason:
                context["hold_reason"] = reason
                context["held_at"] = datetime.now().isoformat()

            await db.execute(
                """UPDATE work_items
                   SET status = 'hold', context = ?, updated_at = CURRENT_TIMESTAMP,
                       claimed_by = NULL, lease_expires_at = NULL
                   WHERE id = ?""",
                (json.dumps(context), work_id),
            )
            await db.commit()

        logger.info(f"⏸️ Work item put on hold: {work_id}")
        return True

    async def release_work(self, work_id: str) -> bool:
        """Release a work item from hold to pending status (atomic read-modify-write)"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT context, status FROM work_items WHERE id = ?", (work_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return False

            if row["status"] != "hold":
                logger.warning(
                    f"Work item {work_id} is not on hold (status: {row['status']})"
                )
                return False

            context = {}
            if row["context"]:
                try:
                    context = json.loads(row["context"])
                except json.JSONDecodeError:
                    context = {}

            context.pop("hold_reason", None)
            context.pop("held_at", None)
            context["released_at"] = datetime.now().isoformat()

            await db.execute(
                """UPDATE work_items
                   SET status = 'pending', context = ?, updated_at = CURRENT_TIMESTAMP,
                       claimed_by = NULL, lease_expires_at = NULL
                   WHERE id = ?""",
                (json.dumps(context), work_id),
            )
            await db.commit()

        success = True
        if success:
            logger.info(f"▶️ Work item released from hold: {work_id}")
        return success

    async def heartbeat(
        self,
        task_id: str,
        worker_id: Optional[str] = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> bool:
        """Extend the lease on a task owned by *worker_id*.

        Returns ``False`` when the task is not active or owned by someone else,
        so a worker that lost its claim (reaped, or reclaimed by another worker)
        can stop instead of racing the new owner.
        """
        actor = worker_actor(worker_id)
        lease_until = lease_expiry(lease_seconds)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE work_items
                SET lease_expires_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'active' AND claimed_by = ?
                """,
                (lease_until, task_id, actor.id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def requeue_expired_leases(self, now: Optional[datetime] = None) -> List[str]:
        """Return active tasks with expired leases to ``pending``.

        This is the reaper half of the lease contract: a task whose worker
        crashed (no heartbeat) becomes claimable again instead of sitting
        ``active`` forever. Attempts are intentionally *not* reset here — each
        claim increments them, so a task that repeatedly dies is visible.

        ``now`` is injectable for tests; storage timestamps are UTC in
        :data:`sugar.identity.LEASE_TIMESTAMP_FORMAT` so string ordering is
        chronological.
        """
        cutoff = format_lease_timestamp(now or datetime.now(timezone.utc))
        async with aiosqlite.connect(self.db_path, isolation_level=None) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    """
                    SELECT id FROM work_items
                    WHERE status = 'active'
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at < ?
                    """,
                    (cutoff,),
                )
                expired = [row["id"] for row in await cursor.fetchall()]
                if expired:
                    placeholders = ",".join("?" for _ in expired)
                    await db.execute(
                        f"""
                        UPDATE work_items
                        SET status = 'pending',
                            claimed_by = NULL,
                            lease_expires_at = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id IN ({placeholders}) AND status = 'active'
                        """,
                        expired,
                    )
                await db.execute("COMMIT")
                if expired:
                    logger.warning(
                        "♻️ Requeued %d task(s) with expired leases: %s",
                        len(expired),
                        ", ".join(expired),
                    )
                return expired
            except Exception:
                await db.execute("ROLLBACK")
                raise

    async def health_check(self) -> dict:
        """Return health status of the work queue"""
        stats = await self.get_stats()

        return {
            "initialized": self._initialized,
            "database_path": self.db_path,
            "total_tasks": stats.get("total", 0),
            "status": "healthy",
        }

    async def get_pending_work(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get pending work items ordered by priority"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute(
                """
                SELECT * FROM work_items
                WHERE status = 'pending'
                ORDER BY priority ASC, created_at ASC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()

            work_items = []
            for row in rows:
                work_item = dict(row)
                # Parse JSON fields
                for field in ["context", "result"]:
                    if work_item[field]:
                        try:
                            work_item[field] = json.loads(work_item[field])
                        except json.JSONDecodeError:
                            work_item[field] = {}
                    else:
                        work_item[field] = {}
                work_items.append(work_item)

            return work_items

    async def mark_work_active(self, work_id: str):
        """Mark a work item as active"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE work_items 
                SET status = 'active', 
                    attempts = attempts + 1,
                    last_attempt_at = CURRENT_TIMESTAMP,
                    started_at = CASE WHEN started_at IS NULL THEN CURRENT_TIMESTAMP ELSE started_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (work_id,),
            )
            await db.commit()

    async def mark_work_completed(self, work_id: str, result: Dict[str, Any]):
        """Mark a work item as completed"""
        await self.complete_work(work_id, result)

    async def mark_work_failed(
        self, work_id: str, error_info: Dict[str, Any], max_retries: int = 3
    ):
        """Mark a work item as failed"""
        error_message = error_info.get("error", "Unknown error")
        if "details" in error_info:
            error_message += f": {error_info['details']}"
        await self.fail_work(work_id, error_message, max_retries)

    async def get_subtasks(self, parent_task_id: str) -> List[Dict[str, Any]]:
        """Get all subtasks for a parent task."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute(
                """
                SELECT * FROM work_items
                WHERE parent_task_id = ?
                ORDER BY created_at ASC
                """,
                (parent_task_id,),
            )
            rows = await cursor.fetchall()

            subtasks = []
            for row in rows:
                work_item = dict(row)
                # Parse JSON fields
                for field in ["context", "result", "blocked_by"]:
                    if work_item.get(field):
                        try:
                            work_item[field] = json.loads(work_item[field])
                        except json.JSONDecodeError:
                            work_item[field] = [] if field == "blocked_by" else {}
                    else:
                        work_item[field] = [] if field == "blocked_by" else {}
                subtasks.append(work_item)

            return subtasks

    async def get_ready_subtasks(self, parent_task_id: str) -> List[Dict[str, Any]]:
        """Get subtasks that are not blocked."""
        subtasks = await self.get_subtasks(parent_task_id)
        ready_tasks = []

        for task in subtasks:
            # Check if task is blocked
            blocked_by = task.get("blocked_by", [])
            if not blocked_by:
                ready_tasks.append(task)
            else:
                # Check if blocking tasks are complete
                all_blockers_complete = True
                async with aiosqlite.connect(self.db_path) as db:
                    for blocker_id in blocked_by:
                        cursor = await db.execute(
                            "SELECT status FROM work_items WHERE id = ?",
                            (blocker_id,),
                        )
                        row = await cursor.fetchone()
                        if not row or row[0] != "completed":
                            all_blockers_complete = False
                            break

                if all_blockers_complete:
                    ready_tasks.append(task)

        return ready_tasks

    async def update_orchestration_stage(self, task_id: str, stage: str) -> bool:
        """Update the current orchestration stage."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE work_items
                SET stage = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (stage, task_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def check_subtasks_complete(self, parent_task_id: str) -> bool:
        """Check if all subtasks for a parent are complete."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
                FROM work_items
                WHERE parent_task_id = ?
                """,
                (parent_task_id,),
            )
            row = await cursor.fetchone()

            if not row or row[0] == 0:
                # No subtasks found
                return False

            total, completed = row
            return total == completed
