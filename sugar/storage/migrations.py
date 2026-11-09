"""Versioned SQLite schema migrations for the task store.

The storage layer used to apply a fixed chain of ad-hoc ``_migrate_*`` steps on
every ``WorkQueue.initialize()``. That made the schema state implicit: there was
no way to tell whether a database had been upgraded already, and a future
migration could not be applied once and then skipped.

This module makes the state explicit with a migration ledger:

- :data:`SCHEMA_VERSION` is the schema version this code writes;
- :data:`MIGRATIONS` is the ordered, append-only registry of
  ``(version, callable)`` upgrades. Every callable is *idempotent*, so a legacy
  database (``PRAGMA user_version == 0`` but ``work_items`` already exists) can
  be upgraded by simply running the registry;
- :func:`apply_migrations` reads the ledger, applies only the versions the
  database is missing, and records each version **atomically** with the DDL it
  describes (one transaction per version);
- :data:`PROBES` lets :func:`apply_migrations` detect a ledger that is ahead of
  reality (e.g. an old tool dropped/recreated a column while ``user_version``
  stayed put) and re-run the idempotent migrations instead of silently trusting
  a stale version.

The ledger is SQLite's ``PRAGMA user_version`` (an integer in the database
header): no extra table, no bookkeeping rows, and it travels with the file. It
is backend-internal — the :class:`sugar.storage.ports.TaskStore` port says
nothing about migrations, and a future Postgres backend keeps its own ledger.

Adding a migration (append-only, never edit a released version)::

    SCHEMA_VERSION = 2
    MIGRATIONS.append((2, _migrate_v2_add_foo_column))
    PROBES[2] = _probe_v2
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Dict, List, Set, Tuple

import aiosqlite

logger = logging.getLogger(__name__)

#: Version of the schema written by this code. Bump when adding a migration.
SCHEMA_VERSION = 1

#: ``(version, upgrade)`` pairs, ordered and append-only.
MigrationFn = Callable[[aiosqlite.Connection], Awaitable[None]]
ProbeFn = Callable[[aiosqlite.Connection], Awaitable[bool]]


class SchemaVersionError(RuntimeError):
    """The database schema is newer than this code understands.

    Refusing to touch it is safer than reading/writing a schema whose columns
    we do not know. Upgrade Sugar instead; the ledger stays untouched.
    """


# ---------------------------------------------------------------------------
# Ledger helpers
# ---------------------------------------------------------------------------


async def get_schema_version(db: aiosqlite.Connection) -> int:
    """Read the applied schema version (``PRAGMA user_version``, 0 if unset)."""
    cursor = await db.execute("PRAGMA user_version")
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    return int(row[0]) if row is not None and row[0] is not None else 0


async def _set_schema_version(db: aiosqlite.Connection, version: int) -> None:
    # PRAGMA values cannot be bound; ``version`` only ever comes from the
    # integer registry above, never from user input.
    await db.execute(f"PRAGMA user_version = {int(version)}")


async def _table_exists(db: aiosqlite.Connection, name: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    )
    try:
        return await cursor.fetchone() is not None
    finally:
        await cursor.close()


async def _table_columns(db: aiosqlite.Connection, table: str) -> Set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return {row[1] for row in rows}


async def _index_exists(db: aiosqlite.Connection, name: str) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
    )
    try:
        return await cursor.fetchone() is not None
    finally:
        await cursor.close()


# ---------------------------------------------------------------------------
# Migration v1: the original schema, idempotent step by step
# ---------------------------------------------------------------------------

_V1_TABLES = frozenset({"work_items", "task_types"})

_V1_WORK_ITEM_COLUMNS = frozenset(
    {
        "id",
        "type",
        "title",
        "description",
        "priority",
        "status",
        "source",
        "source_file",
        "context",
        "created_at",
        "updated_at",
        "attempts",
        "last_attempt_at",
        "completed_at",
        "result",
        "error_message",
        "total_execution_time",
        "started_at",
        "total_elapsed_time",
        "commit_sha",
        "orchestrate",
        "parent_task_id",
        "stage",
        "blocked_by",
        "context_path",
        "assigned_agent",
        "acceptance_criteria",
        "verification_required",
        "verification_status",
        "verification_results",
        "thinking_log_path",
        "thinking_summary",
        "thinking_stats",
        "created_by",
        "created_by_kind",
        "created_by_session",
        "claimed_by",
        "lease_expires_at",
    }
)

_V1_INDEXES = frozenset(
    {
        "idx_work_items_priority_status",
        "idx_work_items_status",
        "idx_work_items_parent_task_id",
    }
)

_TASK_TYPE_DEFAULTS = (
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
)


async def _create_work_items_table(db: aiosqlite.Connection) -> None:
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


async def _migrate_timing_columns(db: aiosqlite.Connection) -> None:
    """Add timing columns (total_execution_time, started_at, ...) if missing."""
    columns = await _table_columns(db, "work_items")
    additions = (
        ("total_execution_time", "REAL DEFAULT 0.0"),
        ("started_at", "TIMESTAMP"),
        ("total_elapsed_time", "REAL DEFAULT 0.0"),
        ("commit_sha", "TEXT"),
    )
    for name, column_type in additions:
        if name not in columns:
            await db.execute(f"ALTER TABLE work_items ADD COLUMN {name} {column_type}")
            logger.info("Added %s column to existing database", name)


async def _migrate_task_types_table(db: aiosqlite.Connection) -> None:
    """Create ``task_types`` and seed the built-in types when absent."""
    if await _table_exists(db, "task_types"):
        return

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

    for task_type in _TASK_TYPE_DEFAULTS:
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


async def _migrate_orchestration_columns(db: aiosqlite.Connection) -> None:
    """Add orchestration columns and the parent-task index."""
    columns = await _table_columns(db, "work_items")
    additions = (
        ("orchestrate", "BOOLEAN DEFAULT 0"),
        ("parent_task_id", "TEXT"),
        ("stage", "TEXT"),
        ("blocked_by", "TEXT"),
        ("context_path", "TEXT"),
        ("assigned_agent", "TEXT"),
    )
    for name, column_type in additions:
        if name not in columns:
            await db.execute(f"ALTER TABLE work_items ADD COLUMN {name} {column_type}")
            logger.info("Added %s column to existing database", name)

    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_work_items_parent_task_id
        ON work_items (parent_task_id)
    """
    )


async def _migrate_acceptance_criteria_column(db: aiosqlite.Connection) -> None:
    """Add the ``acceptance_criteria`` JSON column."""
    if "acceptance_criteria" not in await _table_columns(db, "work_items"):
        await db.execute("ALTER TABLE work_items ADD COLUMN acceptance_criteria TEXT")
        logger.info("Added acceptance_criteria column to existing database")


async def _migrate_verification_columns(db: aiosqlite.Connection) -> None:
    """Add the verification columns (AUTO-005)."""
    columns = await _table_columns(db, "work_items")
    additions = (
        ("verification_required", "BOOLEAN DEFAULT 0"),
        ("verification_status", "TEXT DEFAULT 'pending'"),
        ("verification_results", "TEXT"),
    )
    for name, column_type in additions:
        if name not in columns:
            await db.execute(f"ALTER TABLE work_items ADD COLUMN {name} {column_type}")
            logger.info("Added %s column to existing database", name)


async def _migrate_thinking_columns(db: aiosqlite.Connection) -> None:
    """Add the thinking-capture columns."""
    columns = await _table_columns(db, "work_items")
    additions = (
        ("thinking_log_path", "TEXT"),
        ("thinking_summary", "TEXT"),
        ("thinking_stats", "TEXT"),
    )
    for name, column_type in additions:
        if name not in columns:
            await db.execute(f"ALTER TABLE work_items ADD COLUMN {name} {column_type}")
            logger.info("Added %s column to existing database", name)


async def _migrate_identity_columns(db: aiosqlite.Connection) -> None:
    """Add provenance/lease columns (created_by, claimed_by, lease_expires_at).

    Legacy rows keep NULL: "unknown" is a truthful value and is not backfilled
    to a guess. See :mod:`sugar.identity` for the model.
    """
    columns = await _table_columns(db, "work_items")
    additions = (
        ("created_by", "TEXT"),
        ("created_by_kind", "TEXT"),
        ("created_by_session", "TEXT"),
        ("claimed_by", "TEXT"),
        ("lease_expires_at", "TIMESTAMP"),
    )
    for name, column_type in additions:
        if name not in columns:
            await db.execute(f"ALTER TABLE work_items ADD COLUMN {name} {column_type}")
            logger.info("Added %s column to existing database", name)


async def _migrate_v1_baseline(db: aiosqlite.Connection) -> None:
    """v1: create the task store schema and bring pre-ledger databases forward.

    Every step is idempotent (``IF NOT EXISTS`` / column probes), which is what
    makes the legacy path safe: a database created by an older Sugar (version 0,
    tables already present) simply runs every step and gains what it lacks.
    """
    await _create_work_items_table(db)

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

    await _migrate_timing_columns(db)
    await _migrate_task_types_table(db)
    await _migrate_orchestration_columns(db)
    await _migrate_acceptance_criteria_column(db)
    await _migrate_verification_columns(db)
    await _migrate_thinking_columns(db)
    await _migrate_identity_columns(db)
    logger.debug("Applied schema migration v1 (baseline)")


async def _probe_v1_baseline(db: aiosqlite.Connection) -> bool:
    """True when every table/column/index that v1 promises is present."""
    for table in _V1_TABLES:
        if not await _table_exists(db, table):
            return False
    missing_columns = _V1_WORK_ITEM_COLUMNS - await _table_columns(db, "work_items")
    if missing_columns:
        logger.warning(
            "Schema ledger v1 is stale; missing work_items columns: %s",
            ", ".join(sorted(missing_columns)),
        )
        return False
    for index in _V1_INDEXES:
        if not await _index_exists(db, index):
            logger.warning("Schema ledger v1 is stale; missing index: %s", index)
            return False
    return True


#: Ordered, append-only ``(version, upgrade)`` registry.
MIGRATIONS: List[Tuple[int, MigrationFn]] = [
    (1, _migrate_v1_baseline),
]

#: Per-version "is this really applied?" probes, used to repair stale ledgers.
PROBES: Dict[int, ProbeFn] = {
    1: _probe_v1_baseline,
}


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


async def apply_migrations(db: aiosqlite.Connection) -> int:
    """Bring ``db`` to :data:`SCHEMA_VERSION` and return the applied version.

    Each version is applied in its own ``BEGIN IMMEDIATE`` transaction together
    with its ``PRAGMA user_version`` bump, so a crash/failure can never record a
    version whose DDL did not commit. Applying twice is a no-op.

    Raises :class:`SchemaVersionError` if the database is *newer* than this code
    (a downgraded binary must not write to a schema it does not understand).
    """
    applied = await get_schema_version(db)
    if applied > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"database schema is v{applied}, but this Sugar only understands "
            f"v{SCHEMA_VERSION}; upgrade Sugar to open it"
        )

    # A ledger can be ahead of reality (e.g. a legacy tool altered the schema,
    # or a migration was interrupted between DDL and the version bump). Probes
    # are idempotent, so re-running is safe; without this a stale version would
    # silently skip needed columns.
    ledger_trusted = True
    if applied:
        for version in sorted(PROBES):
            if version > applied:
                break
            if not await PROBES[version](db):
                logger.warning(
                    "Schema ledger says v%d, but v%d is incomplete; "
                    "re-applying migrations",
                    applied,
                    version,
                )
                applied = version - 1
                ledger_trusted = False
                break

    for version, migration in MIGRATIONS:
        if version <= applied:
            continue

        await db.execute("BEGIN IMMEDIATE")
        try:
            # Re-read under the writer lock (unless the ledger is known stale):
            # another process may have finished this migration between our
            # initial read and here.
            if ledger_trusted:
                current = await get_schema_version(db)
                if current >= version:
                    applied = current
                    await db.rollback()
                    continue
            await migration(db)
            await _set_schema_version(db, version)
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        # From here on the ledger reflects reality again.
        ledger_trusted = True
        applied = version

    return applied
