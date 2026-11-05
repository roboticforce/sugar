"""Tests for actor provenance and claim leases (docs/local-dashboard.md §10).

Every test uses a throwaway SQLite database; the repository's ``.sugar/`` store
is never touched.
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

from click.testing import CliRunner

from sugar.identity import (
    Actor,
    agent_actor,
    lease_expiry,
    lease_seconds_from_env,
    normalize_actor_id,
    normalize_kind,
    resolve_actor,
    worker_actor,
)
from sugar.main import cli
from sugar.storage.work_queue import WorkQueue

# ---------------------------------------------------------------------------
# identity helpers
# ---------------------------------------------------------------------------


class TestResolveActor:
    def test_explicit_wins(self, monkeypatch):
        monkeypatch.setenv("SUGAR_ACTOR", "env-actor")
        actor = resolve_actor("dsh", kind="agent", session="0.4.2")
        assert actor == Actor("dsh", "agent", "0.4.2")

    def test_env_precedence(self, monkeypatch):
        monkeypatch.delenv("SUGAR_ACTOR", raising=False)
        monkeypatch.setenv("SUGAR_AGENT_ID", "from-agent-id")
        actor = resolve_actor()
        assert actor.id == "from-agent-id"
        assert actor.kind == "agent"  # agent env implies agent kind

    def test_sugar_actor_beats_agent_id(self, monkeypatch):
        monkeypatch.setenv("SUGAR_ACTOR", "explicit-env")
        monkeypatch.setenv("SUGAR_AGENT_ID", "agent-env")
        assert resolve_actor().id == "explicit-env"

    def test_default_is_user_at_host(self, monkeypatch):
        monkeypatch.delenv("SUGAR_ACTOR", raising=False)
        monkeypatch.delenv("SUGAR_AGENT_ID", raising=False)
        monkeypatch.setenv("USER", "steve")
        actor = resolve_actor()
        assert actor.id.startswith("steve@")
        assert actor.kind == "human"

    def test_kind_and_session_env(self, monkeypatch):
        monkeypatch.setenv("SUGAR_ACTOR_KIND", "system")
        monkeypatch.setenv("SUGAR_SESSION", "run-42")
        actor = resolve_actor("discovery")
        assert actor.kind == "system"
        assert actor.session == "run-42"

    def test_blank_values_fall_through(self, monkeypatch):
        monkeypatch.delenv("SUGAR_ACTOR", raising=False)
        monkeypatch.delenv("SUGAR_AGENT_ID", raising=False)
        monkeypatch.setenv("USER", "steve")
        assert resolve_actor("   ").id.startswith("steve@")

    def test_agent_actor(self):
        assert agent_actor("Claude Code", "1.2.3") == Actor(
            "Claude Code", "agent", "1.2.3"
        )
        assert agent_actor(None).id == "mcp-client"


class TestNormalizers:
    def test_actor_id(self):
        assert normalize_actor_id("  dsh  ") == "dsh"
        assert normalize_actor_id(None) is None
        assert normalize_actor_id("   ") is None
        assert len(normalize_actor_id("x" * 500)) == 128

    def test_kind(self):
        assert normalize_kind("AGENT") == "agent"
        assert normalize_kind("robot") is None
        assert normalize_kind(None) is None


class TestLease:
    def test_lease_expiry_is_sqlite_utc_format(self):
        value = lease_expiry(900)
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        expected = datetime.now(timezone.utc) + timedelta(seconds=900)
        assert abs((parsed - expected).total_seconds()) < 10

    def test_lease_seconds_env(self, monkeypatch):
        monkeypatch.delenv("SUGAR_LEASE_SECONDS", raising=False)
        assert lease_seconds_from_env() == 900
        monkeypatch.setenv("SUGAR_LEASE_SECONDS", "120")
        assert lease_seconds_from_env() == 120
        monkeypatch.setenv("SUGAR_LEASE_SECONDS", "nope")
        assert lease_seconds_from_env() == 900

    def test_worker_actor(self, monkeypatch):
        monkeypatch.delenv("SUGAR_WORKER_ID", raising=False)
        actor = worker_actor()
        assert actor.kind == "agent"
        assert actor.id.startswith("sugar-worker@")
        monkeypatch.setenv("SUGAR_WORKER_ID", "worker-7")
        assert worker_actor().id == "worker-7"


# ---------------------------------------------------------------------------
# storage: migration, claim lease, lifecycle
# ---------------------------------------------------------------------------


def _legacy_db(tmp_path) -> str:
    """A valid Sugar DB with the identity columns removed (an older install)."""
    path = str(tmp_path / "legacy.db")

    async def build():
        queue = WorkQueue(path)
        await queue.initialize()
        await queue.add_work(
            {"type": "feature", "title": "old task", "status": "pending"}
        )
        await queue.close()

    asyncio.run(build())
    conn = sqlite3.connect(path)
    for column in (
        "created_by",
        "created_by_kind",
        "created_by_session",
        "claimed_by",
        "lease_expires_at",
    ):
        conn.execute(f"ALTER TABLE work_items DROP COLUMN {column}")
    conn.commit()
    conn.close()
    return path


class TestMigration:
    def test_columns_added_and_legacy_rows_stay_null(self, tmp_path):
        db_path = _legacy_db(tmp_path)

        async def go():
            queue = WorkQueue(db_path)
            await queue.initialize()
            items = await queue.get_recent_work(limit=1)
            await queue.close()
            return items[0]

        item = asyncio.run(go())
        assert item["created_by"] is None
        assert item["created_by_kind"] is None
        assert item["claimed_by"] is None
        assert item["lease_expires_at"] is None

        conn = sqlite3.connect(db_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(work_items)")}
        conn.close()
        assert {
            "created_by",
            "created_by_kind",
            "created_by_session",
            "claimed_by",
            "lease_expires_at",
        } <= columns

    def test_migration_is_idempotent(self, tmp_path):
        db_path = _legacy_db(tmp_path)

        async def go():
            for _ in range(2):
                queue = WorkQueue(db_path)
                await queue.initialize()
                await queue.close()

        asyncio.run(go())  # must not raise


class TestClaimAttribution:
    def _queue(self, tmp_path):
        queue = WorkQueue(str(tmp_path / "queue.db"))
        asyncio.run(queue.initialize())
        return queue

    def test_created_by_roundtrip(self, tmp_path):
        queue = self._queue(tmp_path)

        async def go():
            work_id = await queue.add_work(
                {
                    "type": "feature",
                    "title": "with provenance",
                    "priority": 2,
                    "created_by": "dsh",
                    "created_by_kind": "agent",
                    "created_by_session": "0.4.2",
                }
            )
            item = await queue.get_work_item(work_id)
            await queue.close()
            return item

        item = asyncio.run(go())
        assert item["created_by"] == "dsh"
        assert item["created_by_kind"] == "agent"
        assert item["created_by_session"] == "0.4.2"

    def test_claim_sets_lease_and_complete_clears_it(self, tmp_path):
        queue = self._queue(tmp_path)

        async def go():
            work_id = await queue.add_work(
                {"type": "feature", "title": "claimable", "priority": 1}
            )
            claimed = await queue.get_next_work(
                worker_id="claude-code", lease_seconds=60
            )
            assert claimed["id"] == work_id
            assert claimed["claimed_by"] == "claude-code"
            assert claimed["lease_expires_at"] is not None

            stored = await queue.get_work_item(work_id)
            assert stored["claimed_by"] == "claude-code"
            assert stored["lease_expires_at"] == claimed["lease_expires_at"]

            await queue.complete_work(work_id, {"ok": True})
            done = await queue.get_work_item(work_id)
            await queue.close()
            return done

        done = asyncio.run(go())
        assert done["status"] == "completed"
        assert done["claimed_by"] is None
        assert done["lease_expires_at"] is None

    def test_claim_defaults_to_resolved_worker(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SUGAR_WORKER_ID", "worker-env")
        queue = self._queue(tmp_path)

        async def go():
            await queue.add_work({"type": "feature", "title": "claim me"})
            claimed = await queue.get_next_work()
            await queue.close()
            return claimed

        claimed = asyncio.run(go())
        assert claimed["claimed_by"] == "worker-env"

    def test_hold_and_release_clear_the_lease(self, tmp_path):
        queue = self._queue(tmp_path)

        async def go():
            work_id = await queue.add_work({"type": "feature", "title": "paused"})
            await queue.get_next_work(worker_id="w1")
            assert (await queue.get_work_item(work_id))["claimed_by"] == "w1"
            await queue.fail_work(work_id, "boom", max_retries=5)
            after_fail = await queue.get_work_item(work_id)
            assert after_fail["status"] == "pending"
            assert after_fail["claimed_by"] is None
            assert after_fail["lease_expires_at"] is None

            await queue.get_next_work(worker_id="w2")
            await queue.hold_work(work_id, "needs input")
            after_hold = await queue.get_work_item(work_id)
            assert after_hold["status"] == "hold"
            assert after_hold["claimed_by"] is None

            await queue.release_work(work_id)
            after_release = await queue.get_work_item(work_id)
            await queue.close()
            return after_release

        released = asyncio.run(go())
        assert released["status"] == "pending"
        assert released["claimed_by"] is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCliActor:
    def _add(self, runner, args):
        assert runner.invoke(cli, ["init"]).exit_code == 0
        result = runner.invoke(cli, ["add", *args])
        assert result.exit_code == 0, result.output

        async def read():
            queue = WorkQueue(".sugar/sugar.db")
            await queue.initialize()
            items = await queue.get_recent_work(limit=1)
            await queue.close()
            return items[0]

        return asyncio.run(read())

    def test_add_with_actor_flags(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            item = self._add(
                runner,
                ["actor provenance task", "--actor", "dsh", "--actor-kind", "agent"],
            )
        assert item["created_by"] == "dsh"
        assert item["created_by_kind"] == "agent"

    def test_add_defaults_to_human(self, monkeypatch):
        monkeypatch.delenv("SUGAR_ACTOR", raising=False)
        monkeypatch.delenv("SUGAR_AGENT_ID", raising=False)
        monkeypatch.setenv("USER", "steve")
        runner = CliRunner()
        with runner.isolated_filesystem():
            item = self._add(runner, ["default actor task"])
        assert item["created_by_kind"] == "human"
        assert item["created_by"].startswith("steve@")
