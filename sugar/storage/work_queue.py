"""
Work Queue - Manage work items with priorities and persistence
"""

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)


class WorkQueue:
    """Persistent work queue with priority management"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._initialized = False

    async def initialize(self):
        """Initialize the database and create tables"""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS work_items (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    priority INTEGER DEFAULT 3,
                    status TEXT DEFAULT 'pending',
                    source TEXT,
                    source_file TEXT,
                    context TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    attempts INTEGER DEFAULT 0,
                    last_attempt_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    result TEXT,
                    error_message TEXT,
                    total_execution_time REAL DEFAULT 0.0,
                    started_at TIMESTAMP,
                    total_elapsed_time REAL DEFAULT 0.0,
                    commit_sha TEXT
                )
            """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_work_items_priority_status
                ON work_items (priority ASC, status, created_at)
            """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_work_items_status 
                ON work_items (status)
            """
            )

            # Migrate existing databases to add timing columns and task types table
            await self._migrate_timing_columns(db)
            await self._migrate_task_types_table(db)
            await self._migrate_orchestration_columns(db)
            await self._migrate_acceptance_criteria_column(db)
            await self._migrate_verification_columns(db)
            await self._migrate_thinking_columns(db)

            await db.commit()

        self._initialized = True

    async def _migrate_timing_columns(self, db):
        """Add timing columns to existing databases if they don't exist"""
        try:
            # Check if timing columns exist
            cursor = await db.execute("PRAGMA table_info(work_items)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            # Add missing timing columns
            if "total_execution_time" not in column_names:
                await db.execute(
                    "ALTER TABLE work_items ADD COLUMN total_execution_time REAL DEFAULT 0.0"
                )
                logger.info("Added total_execution_time column to existing database")

            if "started_at" not in column_names:
                await db.execute(
                    "ALTER TABLE work_items ADD COLUMN started_at TIMESTAMP"
                )
                logger.info("Added started_at column to existing database")

            if "total_elapsed_time" not in column_names:
                await db.execute(
                    "ALTER TABLE work_items ADD COLUMN total_elapsed_time REAL DEFAULT 0.0"
                )
                logger.info("Added total_elapsed_time column to existing database")

            if "commit_sha" not in column_names:
                await db.execute("ALTER TABLE work_items ADD COLUMN commit_sha TEXT")
                logger.info("Added commit_sha column to existing database")

        except Exception as e:
            logger.warning(f"Migration warning (non-critical): {e}")

    async def _migrate_task_types_table(self, db):
        """Create task_types table and populate with defaults if it doesn't exist"""
        try:
            # Check if task_types table exists
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='task_types'"
            )
            table_exists = await cursor.fetchone()

            if not table_exists:
                # Create task_types table
                await db.execute(
                    """
                    CREATE TABLE task_types (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        agent TEXT,
                        commit_template TEXT,
                        emoji TEXT,
                        file_patterns TEXT,
                        is_default BOOLEAN DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """
                )

                # Insert default task types
                default_types = [
                    {
                        "id": "bug_fix",
                        "name": "Bug Fix",
                        "description": "Fix existing issues or bugs",
                        "agent": "tech-lead",
                        "commit_template": "fix: {title}",
                        "emoji": "🐛",
                        "file_patterns": '["src/components/buggy_component.py", "tests/test_fix.py"]',
                        "is_default": 1,
                    },
                    {
                        "id": "feature",
                        "name": "Feature",
                        "description": "Add new functionality",
                        "agent": "general-purpose",
                        "commit_template": "feat: {title}",
                        "emoji": "✨",
                        "file_patterns": '["src/features/new_feature.py", "src/api/feature_endpoint.py"]',
                        "is_default": 1,
                    },
                    {
                        "id": "test",
                        "name": "Test",
                        "description": "Add or update tests",
                        "agent": "general-purpose",
                        "commit_template": "test: {title}",
                        "emoji": "🧪",
                        "file_patterns": '["tests/test_*.py", "spec/*.spec.js"]',
                        "is_default": 1,
                    },
                    {
                        "id": "refactor",
                        "name": "Refactor",
                        "description": "Code refactoring without changing functionality",
                        "agent": "code-reviewer",
                        "commit_template": "refactor: {title}",
                        "emoji": "♻️",
                        "file_patterns": '["src/legacy_code.py", "src/improved_code.py"]',
                        "is_default": 1,
                    },
                    {
                        "id": "documentation",
                        "name": "Documentation",
                        "description": "Documentation updates and improvements",
                        "agent": "general-purpose",
                        "commit_template": "docs: {title}",
                        "emoji": "📝",
                        "file_patterns": '["README.md", "docs/api_documentation.md"]',
                        "is_default": 1,
                    },
                ]

                for task_type in default_types:
                    await db.execute(
                        """
                        INSERT INTO task_types
                        (id, name, description, agent, commit_template, emoji, file_patterns, is_default)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            task_type["id"],
                            task_type["name"],
                            task_type["description"],
                            task_type["agent"],
                            task_type["commit_template"],
                            task_type["emoji"],
                            task_type["file_patterns"],
                            task_type["is_default"],
                        ),
                    )

                logger.info("Created task_types table and populated with default types")

        except Exception as e:
            logger.error(f"Error migrating task_types table: {e}")
            # Continue without task_types table

        logger.debug(f"✅ Work queue initialized: {self.db_path}")

    async def _migrate_orchestration_columns(self, db):
        """Add orchestration columns to work_items table"""
        try:
            # Check if orchestration columns exist
            cursor = await db.execute("PRAGMA table_info(work_items)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            # Add orchestrate column (boolean flag)
            if "orchestrate" not in column_names:
                await db.execute(
                    "ALTER TABLE work_items ADD COLUMN orchestrate BOOLEAN DEFAULT 0"
                )
                logger.info("Added orchestrate column to existing database")

            # Add parent_task_id column (foreign key to work_items.id)
            if "parent_task_id" not in column_names:
                await db.execute(
                    "ALTER TABLE work_items ADD COLUMN parent_task_id TEXT"
                )
                logger.info("Added parent_task_id column to existing database")

            # Add stage column (current orchestration stage)
            if "stage" not in column_names:
                await db.execute("ALTER TABLE work_items ADD COLUMN stage TEXT")
                logger.info("Added stage column to existing database")

            # Add blocked_by column (JSON array of task IDs)
            if "blocked_by" not in column_names:
                await db.execute("ALTER TABLE work_items ADD COLUMN blocked_by TEXT")
                logger.info("Added blocked_by column to existing database")

            # Add context_path column (path to orchestration context)
            if "context_path" not in column_names:
                await db.execute("ALTER TABLE work_items ADD COLUMN context_path TEXT")
                logger.info("Added context_path column to existing database")

            # Add assigned_agent column (specialist agent)
            if "assigned_agent" not in column_names:
                await db.execute(
                    "ALTER TABLE work_items ADD COLUMN assigned_agent TEXT"
                )
                logger.info("Added assigned_agent column to existing database")

            # Create index for parent_task_id queries
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_work_items_parent_task_id
                ON work_items (parent_task_id)
            """
            )

        except Exception as e:
            logger.warning(f"Orchestration migration warning (non-critical): {e}")

    async def _migrate_acceptance_criteria_column(self, db):
        """Add acceptance_criteria column to work_items table"""
        try:
            # Check if acceptance_criteria column exists
            cursor = await db.execute("PRAGMA table_info(work_items)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            # Add acceptance_criteria column (JSON field)
            if "acceptance_criteria" not in column_names:
                await db.execute(
                    "ALTER TABLE work_items ADD COLUMN acceptance_criteria TEXT"
                )
                logger.info("Added acceptance_criteria column to existing database")

        except Exception as e:
            logger.warning(f"Acceptance criteria migration warning (non-critical): {e}")

    async def _migrate_verification_columns(self, db):
        """Add verification columns to work_items table (AUTO-005)"""
        try:
            # Check existing columns
            cursor = await db.execute("PRAGMA table_info(work_items)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            # Add verification_required column (boolean flag)
            if "verification_required" not in column_names:
                await db.execute(
                    "ALTER TABLE work_items ADD COLUMN verification_required BOOLEAN DEFAULT 0"
                )
                logger.info("Added verification_required column to existing database")

            # Add verification_status column (pending, verified, failed)
            if "verification_status" not in column_names:
                await db.execute(
                    "ALTER TABLE work_items ADD COLUMN verification_status TEXT DEFAULT 'pending'"
                )
                logger.info("Added verification_status column to existing database")

            # Add verification_results column (JSON field storing detailed results)
            if "verification_results" not in column_names:
                await db.execute(
                    "ALTER TABLE work_items ADD COLUMN verification_results TEXT"
                )
                logger.info("Added verification_results column to existing database")

        except Exception as e:
            logger.warning(
                f"Verification columns migration warning (non-critical): {e}"
            )

    async def _migrate_thinking_columns(self, db):
        """Add thinking columns to work_items table for capturing Claude's reasoning"""
        try:
            # Check existing columns
            cursor = await db.execute("PRAGMA table_info(work_items)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            # Add thinking_log_path column (path to thinking markdown file)
            if "thinking_log_path" not in column_names:
                await db.execute(
                    "ALTER TABLE work_items ADD COLUMN thinking_log_path TEXT"
                )
                logger.info("Added thinking_log_path column to existing database")

            # Add thinking_summary column (summary of thinking captured)
            if "thinking_summary" not in column_names:
                await db.execute(
                    "ALTER TABLE work_items ADD COLUMN thinking_summary TEXT"
                )
                logger.info("Added thinking_summary column to existing database")

            # Add thinking_stats column (JSON field with thinking statistics)
            if "thinking_stats" not in column_names:
                await db.execute(
                    "ALTER TABLE work_items ADD COLUMN thinking_stats TEXT"
                )
                logger.info("Added thinking_stats column to existing database")

        except Exception as e:
            logger.warning(f"Thinking columns migration warning (non-critical): {e}")

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
                 acceptance_criteria)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            await db.commit()

        logger.debug(
            f"➕ Added work item: {work_item['title']} (priority: {work_item['priority']})"
        )
        return work_id

    async def get_next_work(self) -> Optional[Dict[str, Any]]:
        """Get the highest priority pending work item"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Get highest priority pending work item (exclude hold status)
            cursor = await db.execute(
                """
                SELECT * FROM work_items
                WHERE status = 'pending'
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
            """
            )

            row = await cursor.fetchone()

            if not row:
                return None

            work_item = dict(row)

            # Parse JSON context
            if work_item["context"]:
                try:
                    work_item["context"] = json.loads(work_item["context"])
                except json.JSONDecodeError:
                    work_item["context"] = {}
            else:
                work_item["context"] = {}

            # Mark as active and increment attempts
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
                (work_item["id"],),
            )

            await db.commit()

            work_item["attempts"] += 1
            work_item["status"] = "active"
            logger.debug(
                f"📋 Retrieved work item: {work_item['title']} (attempt #{work_item['attempts']})"
            )

            return work_item

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
                    thinking_stats = ?
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
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM work_items 
                WHERE status IN ('completed', 'failed') 
                AND created_at < datetime('now', '-{} days')
            """.format(
                    days_old
                )
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
                       verification_required, verification_status, verification_results
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
                    }
                return None

    async def remove_work(self, work_id: str) -> bool:
        """Remove work item by ID"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM work_items WHERE id = ?", (work_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def update_work(self, work_id: str, updates: Dict[str, Any]) -> bool:
        """Update work item by ID"""
        if not updates:
            return False

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
        """Put a work item on hold"""
        updates = {"status": "hold", "updated_at": "CURRENT_TIMESTAMP"}
        if reason:
            # Store hold reason in context
            work_item = await self.get_work_item(work_id)
            if work_item:
                context = work_item.get("context", {})
                context["hold_reason"] = reason
                context["held_at"] = datetime.now().isoformat()
                updates["context"] = context

        success = await self.update_work(work_id, updates)
        if success:
            logger.info(f"⏸️ Work item put on hold: {work_id}")
        return success

    async def release_work(self, work_id: str) -> bool:
        """Release a work item from hold to pending status"""
        work_item = await self.get_work_item(work_id)
        if not work_item:
            return False

        if work_item["status"] != "hold":
            logger.warning(
                f"Work item {work_id} is not on hold (status: {work_item['status']})"
            )
            return False

        # Clear hold-related context data
        context = work_item.get("context", {})
        context.pop("hold_reason", None)
        context.pop("held_at", None)
        context["released_at"] = datetime.now().isoformat()

        updates = {
            "status": "pending",
            "context": context,
            "updated_at": "CURRENT_TIMESTAMP",
        }

        success = await self.update_work(work_id, updates)
        if success:
            logger.info(f"▶️ Work item released from hold: {work_id}")
        return success

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
