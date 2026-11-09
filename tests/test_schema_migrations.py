"""Tests for the versioned SQLite schema ledger (``sugar.storage.migrations``).

The ledger is ``PRAGMA user_version``: fresh databases get the current version,
pre-ledger databases (``user_version == 0`` with tables already present) are
upgraded idempotently, and each version commits atomically with its DDL.
"""

import asyncio
import sqlite3

import aiosqlite
import pytest

import sugar.storage.migrations as migrations
from sugar.storage import SCHEMA_VERSION
from sugar.storage.migrations import (
    MIGRATIONS,
    SchemaVersionError,
    apply_migrations,
    get_schema_version,
)
from sugar.storage.work_queue import WorkQueue

#: The oldest schema Sugar shipped, before any ``_migrate_*`` step existed.
LEGACY_WORK_ITEMS_DDL = """
CREATE TABLE work_items (
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
    error_message TEXT
)
"""

#: Columns every version-1 database must have (newest groups named here).
V1_REQUIRED_COLUMNS = {
    # timing
    "total_execution_time",
    "started_at",
    "total_elapsed_time",
    "commit_sha",
    # orchestration
    "orchestrate",
    "parent_task_id",
    "stage",
    "blocked_by",
    "context_path",
    "assigned_agent",
    # acceptance / verification / thinking
    "acceptance_criteria",
    "verification_required",
    "verification_status",
    "verification_results",
    "thinking_log_path",
    "thinking_summary",
    "thinking_stats",
    # provenance + lease
    "created_by",
    "created_by_kind",
    "created_by_session",
    "claimed_by",
    "lease_expires_at",
}

IDENTITY_COLUMNS = (
    "created_by",
    "created_by_kind",
    "created_by_session",
    "claimed_by",
    "lease_expires_at",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


def make_db_version(path: str) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def table_names(path: str) -> set:
    with sqlite3.connect(path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }


def index_names(path: str) -> set:
    with sqlite3.connect(path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }


def columns(path: str, table: str = "work_items") -> set:
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def create_legacy_db(path: str, *, extra_columns: str = "") -> None:
    with sqlite3.connect(path) as conn:
        ddl = LEGACY_WORK_ITEMS_DDL.rstrip().rstrip(")")
        if extra_columns:
            ddl += ",\n    " + extra_columns + "\n)"
        else:
            ddl += "\n)"
        conn.execute(ddl)
        conn.execute(
            "INSERT INTO work_items (id, type, title, status) "
            "VALUES ('legacy-1', 'feature', 'old task', 'pending')"
        )
        conn.commit()


def initialize(path: str) -> WorkQueue:
    queue = WorkQueue(path)
    run(queue.initialize())
    return queue


async def _apply(path: str) -> int:
    async with aiosqlite.connect(path) as db:
        return await apply_migrations(db)


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_versions_are_ordered_and_current(self):
        versions = [version for version, _upgrade in MIGRATIONS]
        assert versions == sorted(versions)
        assert len(set(versions)) == len(versions), "duplicate migration versions"
        assert versions[0] == 1, "version 1 must be the baseline"
        assert versions[-1] == SCHEMA_VERSION
        assert all(callable(upgrade) for _version, upgrade in MIGRATIONS)

    def test_schema_version_is_exported(self):
        from sugar.storage import SCHEMA_VERSION as exported

        assert exported == migrations.SCHEMA_VERSION
        assert WorkQueue.SCHEMA_VERSION == SCHEMA_VERSION
        assert isinstance(WorkQueue.schema_version, property)

    def test_schema_version_property_is_zero_before_initialize(self, tmp_path):
        assert WorkQueue(str(tmp_path / "uninitialized.db")).schema_version == 0


# ---------------------------------------------------------------------------
# Fresh databases
# ---------------------------------------------------------------------------


class TestFreshDatabase:
    def test_initialize_creates_schema_and_records_version(self, tmp_path):
        db_path = str(tmp_path / "fresh.db")
        queue = initialize(db_path)

        assert queue.schema_version == SCHEMA_VERSION
        assert make_db_version(db_path) == SCHEMA_VERSION
        assert {"work_items", "task_types"} <= table_names(db_path)
        assert V1_REQUIRED_COLUMNS <= columns(db_path)
        assert {
            "idx_work_items_priority_status",
            "idx_work_items_status",
            "idx_work_items_parent_task_id",
        } <= index_names(db_path)

        with sqlite3.connect(db_path) as conn:
            seeded = conn.execute("SELECT COUNT(*) FROM task_types").fetchone()[0]
        assert seeded == 5

    def test_apply_migrations_directly_returns_version(self, tmp_path):
        db_path = str(tmp_path / "direct.db")
        assert run(_apply(db_path)) == SCHEMA_VERSION
        assert make_db_version(db_path) == SCHEMA_VERSION

        # A second run on the same file is a no-op that reports the same version.
        assert run(_apply(db_path)) == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Legacy (pre-ledger) databases
# ---------------------------------------------------------------------------


class TestLegacyDatabase:
    def test_legacy_db_is_upgraded_and_recorded(self, tmp_path):
        db_path = str(tmp_path / "legacy.db")
        create_legacy_db(db_path)
        assert make_db_version(db_path) == 0

        queue = initialize(db_path)

        assert queue.schema_version == SCHEMA_VERSION
        assert make_db_version(db_path) == SCHEMA_VERSION
        assert V1_REQUIRED_COLUMNS <= columns(db_path)
        assert {"work_items", "task_types"} <= table_names(db_path)

        # Existing rows survive; unknown provenance stays NULL (never guessed).
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT title, status, created_by, claimed_by, lease_expires_at "
                "FROM work_items WHERE id = 'legacy-1'"
            ).fetchone()
        assert row == ("old task", "pending", None, None, None)

    def test_partially_migrated_legacy_db_keeps_existing_columns(self, tmp_path):
        db_path = str(tmp_path / "partial.db")
        create_legacy_db(db_path, extra_columns="orchestrate BOOLEAN DEFAULT 0")
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE work_items SET orchestrate = 1")
            conn.commit()

        initialize(db_path)

        assert V1_REQUIRED_COLUMNS <= columns(db_path)
        with sqlite3.connect(db_path) as conn:
            assert (
                conn.execute(
                    "SELECT orchestrate FROM work_items WHERE id = 'legacy-1'"
                ).fetchone()[0]
                == 1
            )

    def test_stale_ledger_is_repaired(self, tmp_path):
        """user_version can be ahead of reality (e.g. an old tool altered it)."""
        db_path = str(tmp_path / "stale.db")
        initialize(db_path)

        # Simulate a legacy tool that dropped the newest columns but could not
        # update the ledger: version says current, schema does not.
        with sqlite3.connect(db_path) as conn:
            for column in IDENTITY_COLUMNS:
                conn.execute(f"ALTER TABLE work_items DROP COLUMN {column}")
            conn.commit()
        assert make_db_version(db_path) == SCHEMA_VERSION
        assert not set(IDENTITY_COLUMNS) & columns(db_path)

        initialize(db_path)

        assert set(IDENTITY_COLUMNS) <= columns(db_path)
        assert make_db_version(db_path) == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_double_initialize_is_a_noop(self, tmp_path):
        db_path = str(tmp_path / "twice.db")
        first = initialize(db_path)
        second = initialize(db_path)

        assert first.schema_version == second.schema_version == SCHEMA_VERSION
        assert make_db_version(db_path) == SCHEMA_VERSION
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM task_types").fetchone()[0] == 5

    def test_double_apply_migrations_is_a_noop(self, tmp_path):
        db_path = str(tmp_path / "apply-twice.db")
        initialize(db_path)
        before = columns(db_path)

        assert run(_apply(db_path)) == SCHEMA_VERSION
        assert run(_apply(db_path)) == SCHEMA_VERSION

        assert columns(db_path) == before
        assert make_db_version(db_path) == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Ledger safety
# ---------------------------------------------------------------------------


class TestLedgerSafety:
    def test_failed_migration_does_not_advance_version(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "failure.db")
        create_legacy_db(db_path)

        async def boom(_db):
            await _db.execute("CREATE TABLE half_applied (x INTEGER)")
            raise RuntimeError("migration exploded")

        monkeypatch.setattr(migrations, "MIGRATIONS", [(1, boom)])
        monkeypatch.setattr(migrations, "PROBES", {})

        with pytest.raises(RuntimeError, match="migration exploded"):
            run(_apply(db_path))

        # The version bump and the half-applied DDL were rolled back together.
        assert make_db_version(db_path) == 0
        assert "half_applied" not in table_names(db_path)

    def test_newer_database_is_refused(self, tmp_path):
        db_path = str(tmp_path / "future.db")
        initialize(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
            conn.commit()

        with pytest.raises(SchemaVersionError):
            initialize(db_path)
        with pytest.raises(SchemaVersionError):
            run(_apply(db_path))

        # Refusing must not touch the newer database.
        assert make_db_version(db_path) == SCHEMA_VERSION + 1


# ---------------------------------------------------------------------------
# Entry point (get_schema_version helper)
# ---------------------------------------------------------------------------


def test_get_schema_version_helper(tmp_path):
    db_path = str(tmp_path / "helper.db")

    async def go():
        async with aiosqlite.connect(db_path) as db:
            return await get_schema_version(db)

    assert run(go()) == 0
    initialize(db_path)
    assert run(go()) == SCHEMA_VERSION
