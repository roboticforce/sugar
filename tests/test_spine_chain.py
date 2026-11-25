"""Tests for event hash-chain + Merkle checkpoints (task-44)."""

import asyncio
import json
import sqlite3

import pytest
from click.testing import CliRunner
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app
from sugar.dashboard.spine import (
    CHAIN_ALGORITHM,
    CHAIN_GENESIS,
    SCHEMA,
    EventSpine,
    backfill_event_chain,
    build_checkpoint,
    list_checkpoints,
    verify_checkpoint,
    verify_checkpoint_against_db,
    verify_event_chain,
)
from sugar.main import cli


def run(coro):
    return asyncio.run(coro)


async def make_spine(path, **kwargs):
    spine = EventSpine(
        str(path), retention_days=kwargs.pop("retention_days", 0), **kwargs
    )
    await spine.start()
    return spine


class TestAppendChain:
    def test_append_builds_chain_and_verifies(self, tmp_path):
        path = tmp_path / "events.db"

        async def go():
            spine = await make_spine(path)
            first = await spine.append({"kind": "a", "actor": "steve", "project": "p"})
            second = await spine.append({"kind": "b", "payload": {"x": 1}})
            rows, _ = await spine.list_events()
            await spine.close()
            return first, second, rows

        first, second, rows = run(go())
        assert first["id"] == 1 and second["id"] == 2
        with sqlite3.connect(path) as conn:
            chain = conn.execute(
                "SELECT event_id, algorithm, prev_hash, hash FROM event_chain "
                "ORDER BY event_id"
            ).fetchall()
        assert [row[0] for row in chain] == [1, 2]
        assert all(row[1] == CHAIN_ALGORITHM for row in chain)
        assert chain[0][2] == CHAIN_GENESIS
        assert chain[1][2] == chain[0][3]
        result = run(verify_event_chain(path))
        assert result["ok"] is True, result
        assert result["checked"] == 2
        assert result["head_hash"] == chain[-1][3]
        assert result["first_bad_id"] is None

    def test_append_batch_is_transactional_and_chained(self, tmp_path):
        path = tmp_path / "events.db"

        async def go():
            spine = await make_spine(path)
            events = await spine.append_batch(
                [{"kind": "a"}, {"kind": "b"}, {"kind": "c"}]
            )
            await spine.close()
            return events

        events = run(go())
        assert [event["id"] for event in events] == [1, 2, 3]
        result = run(verify_event_chain(path))
        assert result["ok"] is True
        assert result["checked"] == 3

    def test_append_batch_rejects_invalid_without_partial_rows(self, tmp_path):
        path = tmp_path / "events.db"

        async def go():
            spine = await make_spine(path)
            with pytest.raises(Exception):
                await spine.append_batch([{"kind": "ok"}, {"missing": True}])
            count = await spine.count()
            chain = await verify_event_chain(path)
            await spine.close()
            return count, chain

        count, chain = run(go())
        assert count == 0
        assert chain["ok"] is True
        assert chain["checked"] == 0

    def test_empty_spine_verifies(self, tmp_path):
        path = tmp_path / "events.db"

        async def go():
            spine = await make_spine(path)
            await spine.close()

        run(go())
        result = run(verify_event_chain(path))
        assert result["ok"] is True
        assert result["checked"] == 0
        assert result["head_hash"] is None
        assert result["checkpoint"] is None


class TestBackfillAndMigration:
    def _legacy_db(self, path):
        with sqlite3.connect(path) as conn:
            conn.executescript(SCHEMA)
            for index, kind in enumerate(("legacy.one", "legacy.two"), start=1):
                conn.execute(
                    "INSERT INTO events (ts, kind, actor, machine, source, ingested_at) "
                    "VALUES (?, ?, 'steve', 'host', 'ingest', ?)",
                    (
                        f"2026-01-0{index}T00:00:00Z",
                        kind,
                        f"2026-01-0{index}T00:00:01Z",
                    ),
                )
            conn.commit()

    def test_backfill_legacy_events(self, tmp_path):
        path = tmp_path / "legacy.db"
        self._legacy_db(path)
        # No chain rows yet.
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM event_chain").fetchone()[0] == 0
        result = run(verify_event_chain(path))
        assert result["ok"] is False  # chain table exists but empty; events unchained

        created = run(backfill_event_chain(path))
        assert created == 2
        assert run(backfill_event_chain(path)) == 0
        result = run(verify_event_chain(path))
        assert result["ok"] is True, result
        assert result["checked"] == 2

    def test_reopen_is_idempotent_and_repairs_tail(self, tmp_path):
        path = tmp_path / "events.db"

        async def go():
            spine = await make_spine(path)
            await spine.append_batch([{"kind": "a"}, {"kind": "b"}])
            await spine.close()

        run(go())
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM event_chain WHERE event_id = 2")
            conn.commit()
        assert run(verify_event_chain(path))["ok"] is False

        async def reopen():
            spine = await make_spine(path)
            await spine.append({"kind": "c"})
            await spine.close()

        run(reopen())
        result = run(verify_event_chain(path))
        assert result["ok"] is True, result
        assert result["checked"] == 3

    def test_prune_removes_chain_rows_and_still_verifies(self, tmp_path):
        path = tmp_path / "events.db"

        async def go():
            spine = await make_spine(path, retention_days=1)
            await spine.append({"kind": "old", "ts": "2020-01-01T00:00:00Z"})
            await spine.append({"kind": "new"})
            removed = await spine.prune()
            await spine.close()
            return removed

        assert run(go()) == 1
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM event_chain").fetchone()[0] == 1
        result = run(verify_event_chain(path))
        assert result["ok"] is True, result
        assert result["checked"] == 1


class TestTamperDetection:
    def _spine(self, path):
        async def go():
            spine = await make_spine(path)
            await spine.append_batch(
                [
                    {"kind": "a", "payload": {"value": 1}},
                    {"kind": "b", "payload": {"value": 2}},
                    {"kind": "c", "payload": {"value": 3}},
                ]
            )
            await spine.close()

        run(go())

    def test_rewritten_payload_fails(self, tmp_path):
        path = tmp_path / "events.db"
        self._spine(path)
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE events SET payload = '{\"value\":99}' WHERE id = 2")
            conn.commit()
        result = run(verify_event_chain(path))
        assert result["ok"] is False
        assert result["first_bad_id"] == 2
        assert any("hash mismatch" in error for error in result["errors"])

    def test_deleted_event_row_fails(self, tmp_path):
        path = tmp_path / "events.db"
        self._spine(path)
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM events WHERE id = 2")
            conn.commit()
        result = run(verify_event_chain(path))
        assert result["ok"] is False
        assert result["first_bad_id"] == 2
        assert any("chain row has no event" in error for error in result["errors"])

    def test_deleted_chain_row_fails(self, tmp_path):
        path = tmp_path / "events.db"
        self._spine(path)
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM event_chain WHERE event_id = 2")
            conn.commit()
        result = run(verify_event_chain(path))
        assert result["ok"] is False
        assert result["first_bad_id"] == 2
        assert any("has no chain row" in error for error in result["errors"])

    def test_deleted_middle_pair_is_a_gap(self, tmp_path):
        path = tmp_path / "events.db"
        self._spine(path)
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM events WHERE id = 2")
            conn.execute("DELETE FROM event_chain WHERE event_id = 2")
            conn.commit()
        result = run(verify_event_chain(path))
        assert result["ok"] is False
        assert any("chain gap" in error for error in result["errors"])

    def test_bad_link_fails(self, tmp_path):
        path = tmp_path / "events.db"
        self._spine(path)
        with sqlite3.connect(path) as conn:
            conn.execute(
                "UPDATE event_chain SET prev_hash = ? WHERE event_id = 2",
                (CHAIN_GENESIS,),
            )
            conn.commit()
        result = run(verify_event_chain(path))
        assert result["ok"] is False
        assert result["first_bad_id"] == 2


class TestCheckpoints:
    def test_checkpoint_root_is_deterministic_and_persisted(self, tmp_path):
        path = tmp_path / "events.db"

        async def go():
            spine = await make_spine(path)
            await spine.append_batch([{"kind": "a"}, {"kind": "b"}, {"kind": "c"}])
            first = await build_checkpoint(path, from_id=1, to_id=3)
            second = await build_checkpoint(path, from_id=1, to_id=3)
            smaller = await build_checkpoint(path, from_id=1, to_id=2)
            listed = await list_checkpoints(path)
            await spine.close()
            return first, second, smaller, listed

        first, second, smaller, listed = run(go())
        assert first["root_hash"] == second["root_hash"]
        assert first["count"] == 3 and second["count"] == 3
        assert smaller["root_hash"] != first["root_hash"]
        assert smaller["count"] == 2
        assert len(listed) == 3
        assert listed[0]["id"] == 3
        assert listed[0]["root_hash"] == smaller["root_hash"]

    def test_checkpoint_range_validation(self, tmp_path):
        path = tmp_path / "events.db"

        async def go():
            spine = await make_spine(path)
            await spine.close()

        run(go())
        with pytest.raises(Exception):
            run(build_checkpoint(path))

    def test_signed_checkpoint_and_wrong_key(self, tmp_path, monkeypatch):
        path = tmp_path / "events.db"

        async def go():
            spine = await make_spine(path)
            await spine.append({"kind": "a"})
            await spine.close()

        run(go())
        monkeypatch.setenv("SUGAR_AUDIT_SIGN_KEY", "chain-key")
        checkpoint = run(build_checkpoint(path))
        assert checkpoint.get("signature")
        assert verify_checkpoint(checkpoint)["ok"] is True
        against = run(verify_checkpoint_against_db(path, checkpoint))
        assert against["ok"] is True

        monkeypatch.setenv("SUGAR_AUDIT_SIGN_KEY", "wrong-key")
        wrong = verify_checkpoint(checkpoint)
        assert wrong["ok"] is False
        assert any("signature mismatch" in error for error in wrong["errors"])

    def test_explicit_signing_env_missing(self, tmp_path):
        path = tmp_path / "events.db"

        async def go():
            spine = await make_spine(path)
            await spine.append({"kind": "a"})
            await spine.close()

        run(go())
        with pytest.raises(Exception):
            run(build_checkpoint(path, sign_key_env="SUGAR_DOES_NOT_EXIST_CHAIN"))

    def test_checkpoint_detects_covered_payload_change(self, tmp_path):
        path = tmp_path / "events.db"

        async def go():
            spine = await make_spine(path)
            await spine.append_batch([{"kind": "a"}, {"kind": "b"}])
            checkpoint = await build_checkpoint(path, from_id=1, to_id=2)
            await spine.close()
            return checkpoint

        checkpoint = run(go())
        with sqlite3.connect(path) as conn:
            conn.execute(
                "UPDATE events SET payload = '{\"tampered\":true}' WHERE id = 1"
            )
            conn.commit()
        against = run(verify_checkpoint_against_db(path, checkpoint))
        assert against["ok"] is False


class TestChainAPI:
    def test_spine_chain_endpoint(self, tmp_path):
        app = create_app(
            str(tmp_path / "sugar.db"),
            events_db=str(tmp_path / "events.db"),
            ingest_socket="",
        )
        with TestClient(app) as client:
            assert client.post("/api/events", json={"kind": "a"}).status_code == 201
            assert client.post("/api/events", json={"kind": "b"}).status_code == 201
            result = client.get("/api/spine/chain")
            assert result.status_code == 200
            payload = result.json()
            assert payload["ok"] is True
            assert payload["checked"] == 2

    def test_spine_chain_endpoint_503_without_spine(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), ingest_socket="")
        with TestClient(app) as client:
            assert client.get("/api/spine/chain").status_code == 503


class TestChainCLI:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    def _spine(self, path):
        async def go():
            spine = await make_spine(path)
            await spine.append_batch([{"kind": "a"}, {"kind": "b"}])
            await spine.close()

        run(go())

    def test_verify_chain_json(self, runner, tmp_path):
        path = tmp_path / "events.db"
        self._spine(path)
        result = runner.invoke(
            cli, ["audit", "verify-chain", "--db", str(path), "--json"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["checked"] == 2

    def test_verify_chain_failure_exit(self, runner, tmp_path):
        path = tmp_path / "events.db"
        self._spine(path)
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE events SET payload='{\"x\":1}' WHERE id=1")
            conn.commit()
        result = runner.invoke(cli, ["audit", "verify-chain", "--db", str(path)])
        assert result.exit_code == 1
        assert "FAILED" in result.output

    def test_checkpoint_and_checkpoints_cli(self, runner, tmp_path, monkeypatch):
        path = tmp_path / "events.db"
        self._spine(path)
        monkeypatch.setenv("SUGAR_AUDIT_SIGN_KEY", "cli-chain-key")
        result = runner.invoke(
            cli,
            [
                "audit",
                "checkpoint",
                "--db",
                str(path),
                "--from-id",
                "1",
                "--to-id",
                "2",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        checkpoint = json.loads(result.output)
        assert checkpoint["signature"]
        listed = runner.invoke(
            cli, ["audit", "checkpoints", "--db", str(path), "--json"]
        )
        assert listed.exit_code == 0, listed.output
        rows = json.loads(listed.output)
        assert len(rows) == 1
        assert rows[0]["root_hash"] == checkpoint["root_hash"]
