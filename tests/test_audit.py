"""Tests for the audit export library and CLI (docs/audit.md).

Every test uses throwaway SQLite/export files; nothing touches the real
``~/.sugar/events.db``.
"""

import asyncio
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from sugar.audit.export import (
    FORMAT,
    MAX_EXPORT_LIMIT,
    AuditConfigError,
    AuditFileError,
    canonical_json,
    export_events,
    manifest_path_for,
    normalize_kinds,
    parse_timestamp,
    resolve_signing_key,
    verify_export,
)
from sugar.dashboard.spine import EventSpine
from sugar.main import cli

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

SEED_EVENTS = [
    {
        "kind": "task.created",
        "actor": "steve",
        "actor_kind": "human",
        "ts": "2026-11-01T09:00:00Z",
        "project": "sugar",
        "payload": {"title": "first task"},
    },
    {
        "kind": "task.completed",
        "actor": "steve",
        "actor_kind": "human",
        "ts": "2026-11-02T10:00:00Z",
        "payload": {"status": "completed"},
    },
    {
        "kind": "run.started",
        "actor": "dsh",
        "actor_kind": "agent",
        "harness": "dsh",
        "ts": "2026-11-03T11:00:00Z",
        "payload": {"run": "r1"},
    },
    {
        "kind": "tool.invoked",
        "actor": "dsh",
        "actor_kind": "agent",
        "harness": "dsh",
        "ts": "2026-11-04T12:00:00Z",
        "task_id": "task-1",
        "payload": {"tool": "bash"},
    },
]


def run(coro):
    return asyncio.run(coro)


def seed_spine(path: Path) -> Path:
    async def go():
        spine = EventSpine(str(path), retention_days=0)
        await spine.start()
        for event in SEED_EVENTS:
            await spine.append(dict(event))
        await spine.close()

    run(go())
    return path


@pytest.fixture
def events_db(tmp_path):
    return seed_spine(tmp_path / "events.db")


@pytest.fixture
def audit_out(tmp_path):
    return tmp_path / "audit.jsonl"


def do_export(events_db, out=..., **kwargs):
    if out is ...:
        out = Path(events_db).parent / "audit.jsonl"
    return run(export_events(str(events_db), out, **kwargs))


def read_manifest(out: Path) -> dict:
    return json.loads(manifest_path_for(out).read_text(encoding="utf-8"))


def write_manifest(out: Path, manifest: dict) -> None:
    manifest_path_for(out).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cli_config(tmp_path: Path) -> Path:
    """Minimal config so CLI tests never write into the repo's .sugar dir."""
    config = tmp_path / "config.yaml"
    config.write_text(
        "sugar:\n"
        "  storage:\n"
        f"    database: \"{tmp_path / 'sugar.db'}\"\n"
        "  logging:\n"
        f"    file: \"{tmp_path / 'sugar.log'}\"\n",
        encoding="utf-8",
    )
    return config


def invoke(runner, config, *args):
    return runner.invoke(cli, ["--config", str(config), "audit", *args])


# ---------------------------------------------------------------------------
# Export content / manifest
# ---------------------------------------------------------------------------


class TestExport:
    def test_jsonl_is_canonical_and_ordered(self, events_db, audit_out):
        result = do_export(events_db, audit_out)
        assert result.count == 4
        assert (result.first_id, result.last_id) == (1, 4)
        assert result.truncated is False
        assert result.signed is False

        raw_lines = audit_out.read_text(encoding="utf-8").splitlines()
        assert len(raw_lines) == 4
        records = [json.loads(line) for line in raw_lines]
        assert [record["id"] for record in records] == [1, 2, 3, 4]
        assert [record["kind"] for record in records] == [
            "task.created",
            "task.completed",
            "run.started",
            "tool.invoked",
        ]
        assert records[0]["payload"] == {"title": "first task"}
        assert records[0]["ts"] == "2026-11-01T09:00:00Z"
        assert records[2]["harness"] == "dsh"
        for line, record in zip(raw_lines, records):
            assert line == canonical_json(record)
            assert ISO_Z.match(record["ts"])
            assert ISO_Z.match(record["ingested_at"])

    def test_manifest_schema_and_hash(self, events_db, audit_out):
        result = do_export(events_db, audit_out)
        manifest = read_manifest(audit_out)
        assert manifest["format"] == FORMAT
        assert manifest["source"] == "sugar-event-spine"
        assert manifest["sugar_version"]
        assert ISO_Z.match(manifest["generated_at"])
        assert manifest["count"] == 4
        assert manifest["first_id"] == 1
        assert manifest["last_id"] == 4
        assert manifest["sha256"] == sha256_file(audit_out)
        assert manifest["sha256"] == result.sha256
        assert manifest["truncated"] is False
        assert manifest["filters"] == {
            "since": None,
            "until": None,
            "kinds": [],
            "limit": None,
        }
        assert "signed" not in manifest
        assert Path(manifest_path_for(audit_out)).name == "audit.manifest.json"

    def test_manifest_path_derivation(self):
        assert manifest_path_for("/tmp/a.jsonl") == Path("/tmp/a.manifest.json")
        assert manifest_path_for("/tmp/a") == Path("/tmp/a.manifest.json")
        assert manifest_path_for("/tmp/a.txt") == Path("/tmp/a.txt.manifest.json")

    def test_export_is_read_only_on_spine(self, events_db, audit_out):
        before = sha256_file(events_db)
        do_export(events_db, audit_out)
        assert sha256_file(events_db) == before

    def test_export_reads_wal_while_spine_is_open(self, tmp_path, audit_out):
        async def go():
            spine = EventSpine(str(tmp_path / "live.db"), retention_days=0)
            await spine.start()
            await spine.append({"kind": "task.created", "ts": "2026-11-01T00:00:00Z"})
            await spine.append({"kind": "task.completed", "ts": "2026-11-02T00:00:00Z"})
            result = await export_events(str(tmp_path / "live.db"), audit_out)
            await spine.close()
            return result

        result = run(go())
        assert result.count == 2
        assert result.first_id == 1 and result.last_id == 2
        assert verify_export(audit_out, manifest_path_for(audit_out)).ok

    def test_export_refuses_overwrite(self, events_db, audit_out):
        do_export(events_db, audit_out)
        with pytest.raises(AuditConfigError, match="already exists"):
            do_export(events_db, audit_out)
        # --force replaces it.
        result = do_export(events_db, audit_out, force=True)
        assert result.count == 4


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class TestVerify:
    def test_verify_ok(self, events_db, audit_out):
        exported = do_export(events_db, audit_out)
        result = verify_export(audit_out, manifest_path_for(audit_out))
        assert result.ok is True
        assert result.errors == []
        assert result.count == 4
        assert result.sha256 == exported.sha256
        assert result.signed is False
        assert result.manifest["format"] == FORMAT

    def test_tampered_jsonl_is_detected(self, events_db, audit_out):
        do_export(events_db, audit_out)
        text = audit_out.read_text(encoding="utf-8")
        audit_out.write_text(
            text.replace('"title":"first task"', '"title":"tampered task"'),
            encoding="utf-8",
        )
        result = verify_export(audit_out, manifest_path_for(audit_out))
        assert result.ok is False
        assert any("sha256 mismatch" in error for error in result.errors)

    def test_tampered_manifest_count_is_detected(self, events_db, audit_out):
        do_export(events_db, audit_out)
        manifest = read_manifest(audit_out)
        manifest["count"] = 99
        write_manifest(audit_out, manifest)
        result = verify_export(audit_out, manifest_path_for(audit_out))
        assert result.ok is False
        assert any("count mismatch" in error for error in result.errors)

    def test_non_canonical_line_detected_even_with_matching_hash(
        self, events_db, audit_out
    ):
        do_export(events_db, audit_out)
        lines = audit_out.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        # Same JSON value, non-canonical bytes (spaces after separators).
        lines[0] = json.dumps(record, sort_keys=True)
        audit_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifest = read_manifest(audit_out)
        manifest["sha256"] = sha256_file(audit_out)
        write_manifest(audit_out, manifest)
        result = verify_export(audit_out, manifest_path_for(audit_out))
        assert result.ok is False
        assert any("not canonical JSON" in error for error in result.errors)

    def test_out_of_order_ids_detected(self, events_db, audit_out):
        do_export(events_db, audit_out)
        lines = audit_out.read_text(encoding="utf-8").splitlines()
        lines[0], lines[1] = lines[1], lines[0]
        audit_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifest = read_manifest(audit_out)
        manifest["sha256"] = sha256_file(audit_out)
        manifest["first_id"] = 2
        write_manifest(audit_out, manifest)
        result = verify_export(audit_out, manifest_path_for(audit_out))
        assert result.ok is False
        assert any("strictly increasing" in error for error in result.errors)

    def test_removed_line_detected(self, events_db, audit_out):
        do_export(events_db, audit_out)
        lines = audit_out.read_text(encoding="utf-8").splitlines()
        audit_out.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        result = verify_export(audit_out, manifest_path_for(audit_out))
        assert result.ok is False
        assert any("count mismatch" in error for error in result.errors)

    def test_malformed_manifest_raises(self, events_db, audit_out):
        do_export(events_db, audit_out)
        manifest_path_for(audit_out).write_text("{not json", encoding="utf-8")
        with pytest.raises(AuditFileError, match="not valid JSON"):
            verify_export(audit_out, manifest_path_for(audit_out))

    def test_manifest_must_be_object(self, events_db, audit_out):
        do_export(events_db, audit_out)
        manifest_path_for(audit_out).write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(AuditFileError, match="JSON object"):
            verify_export(audit_out, manifest_path_for(audit_out))

    def test_missing_export_file_raises(self, events_db, audit_out):
        do_export(events_db, audit_out)
        manifest = read_manifest(audit_out)
        missing = audit_out.parent / "missing.jsonl"
        with pytest.raises(AuditFileError, match="not found"):
            verify_export(missing, manifest_path_for(audit_out))
        assert manifest["count"] == 4

    def test_missing_required_manifest_key_is_a_mismatch(self, events_db, audit_out):
        do_export(events_db, audit_out)
        manifest = read_manifest(audit_out)
        del manifest["count"]
        write_manifest(audit_out, manifest)
        result = verify_export(audit_out, manifest_path_for(audit_out))
        assert result.ok is False
        assert any("missing required key: count" in error for error in result.errors)


# ---------------------------------------------------------------------------
# Filters / limits / empty range
# ---------------------------------------------------------------------------


class TestFiltersAndLimits:
    def test_kinds_filter(self, events_db, audit_out):
        result = do_export(events_db, audit_out, kinds=["run.started", "task.created"])
        assert result.count == 2
        assert (result.first_id, result.last_id) == (1, 3)
        assert result.filters["kinds"] == ["run.started", "task.created"]
        records = [
            json.loads(line)
            for line in audit_out.read_text(encoding="utf-8").splitlines()
        ]
        assert [record["kind"] for record in records] == [
            "task.created",
            "run.started",
        ]
        assert verify_export(audit_out, manifest_path_for(audit_out)).ok

    def test_since_until_are_inclusive(self, events_db, audit_out):
        result = do_export(
            events_db,
            audit_out,
            since="2026-11-02T10:00:00Z",
            until="2026-11-03T11:00:00Z",
        )
        assert result.count == 2
        assert (result.first_id, result.last_id) == (2, 3)
        assert result.filters["since"] == "2026-11-02T10:00:00Z"
        assert result.filters["until"] == "2026-11-03T11:00:00Z"

    def test_combined_filters(self, events_db, audit_out):
        result = do_export(
            events_db,
            audit_out,
            kinds=["run.started", "tool.invoked"],
            since="2026-11-04T00:00:00Z",
        )
        assert result.count == 1
        assert result.first_id == 4

    def test_empty_range(self, events_db, audit_out):
        result = do_export(events_db, audit_out, since="2030-01-01T00:00:00Z")
        assert result.count == 0
        assert result.first_id is None
        assert result.last_id is None
        assert audit_out.read_bytes() == b""
        manifest = read_manifest(audit_out)
        assert manifest["first_id"] is None
        assert manifest["last_id"] is None
        assert manifest["count"] == 0
        assert manifest["sha256"] == hashlib.sha256(b"").hexdigest()
        assert verify_export(audit_out, manifest_path_for(audit_out)).ok

    def test_limit_sets_truncated(self, events_db, audit_out):
        result = do_export(events_db, audit_out, limit=2)
        assert result.count == 2
        assert (result.first_id, result.last_id) == (1, 2)
        assert result.truncated is True
        manifest = read_manifest(audit_out)
        assert manifest["truncated"] is True
        assert manifest["filters"]["limit"] == 2
        assert verify_export(audit_out, manifest_path_for(audit_out)).ok

    def test_limit_above_total_is_not_truncated(self, events_db, audit_out):
        result = do_export(events_db, audit_out, limit=100)
        assert result.count == 4
        assert result.truncated is False

    @pytest.mark.parametrize("limit", [0, -1, MAX_EXPORT_LIMIT + 1])
    def test_invalid_limit(self, events_db, audit_out, limit):
        with pytest.raises(AuditConfigError):
            do_export(events_db, audit_out, limit=limit)


# ---------------------------------------------------------------------------
# Signed exports
# ---------------------------------------------------------------------------


class TestSignedExports:
    @pytest.fixture
    def key(self, monkeypatch):
        secret = "correct-horse-battery-staple-0123456789"
        monkeypatch.setenv("SUGAR_AUDIT_HMAC_KEY", secret)
        return secret

    def test_sign_and_verify(self, events_db, audit_out, key):
        result = do_export(events_db, audit_out, sign_key_env="SUGAR_AUDIT_HMAC_KEY")
        assert result.signed is True
        assert re.fullmatch(r"[0-9a-f]{16}", result.key_id)
        manifest = read_manifest(audit_out)
        assert manifest["signed"]["alg"] == "hmac-sha256"
        assert manifest["signed"]["key_id"] == result.key_id
        assert re.fullmatch(r"[0-9a-f]{64}", manifest["signed"]["signature"])
        assert key not in audit_out.read_text(encoding="utf-8")
        assert key not in manifest_path_for(audit_out).read_text(encoding="utf-8")
        assert key not in json.dumps(result.as_dict())

        verified = verify_export(
            audit_out, manifest_path_for(audit_out), key_env="SUGAR_AUDIT_HMAC_KEY"
        )
        assert verified.ok is True, verified.errors
        assert verified.signed is True
        assert verified.key_id == result.key_id

    def test_wrong_key_fails(self, events_db, audit_out, key, monkeypatch):
        do_export(events_db, audit_out, sign_key_env="SUGAR_AUDIT_HMAC_KEY")
        monkeypatch.setenv("SUGAR_AUDIT_WRONG_KEY", "wrong-key-0123456789")
        result = verify_export(
            audit_out,
            manifest_path_for(audit_out),
            key_env="SUGAR_AUDIT_WRONG_KEY",
        )
        assert result.ok is False
        assert any("HMAC signature mismatch" in error for error in result.errors)
        assert any("key_id mismatch" in error for error in result.errors)
        assert "wrong-key-0123456789" not in json.dumps(result.as_dict())

    def test_signed_manifest_tamper_detected(self, events_db, audit_out, key):
        do_export(events_db, audit_out, sign_key_env="SUGAR_AUDIT_HMAC_KEY")
        manifest = read_manifest(audit_out)
        manifest["count"] = 99
        write_manifest(audit_out, manifest)
        result = verify_export(
            audit_out, manifest_path_for(audit_out), key_env="SUGAR_AUDIT_HMAC_KEY"
        )
        assert result.ok is False
        assert any("HMAC signature mismatch" in error for error in result.errors)

    def test_stripped_signature_block_fails(self, events_db, audit_out, key):
        do_export(events_db, audit_out, sign_key_env="SUGAR_AUDIT_HMAC_KEY")
        manifest = read_manifest(audit_out)
        manifest["signed"] = None
        write_manifest(audit_out, manifest)
        result = verify_export(
            audit_out, manifest_path_for(audit_out), key_env="SUGAR_AUDIT_HMAC_KEY"
        )
        assert result.ok is False
        assert any("signed block must be an object" in error for error in result.errors)
        # Even without a key, a present-but-null signature must not pass silently.
        stripped = verify_export(audit_out, manifest_path_for(audit_out))
        assert stripped.ok is False

    def test_signed_manifest_requires_key(self, events_db, audit_out, key):
        do_export(events_db, audit_out, sign_key_env="SUGAR_AUDIT_HMAC_KEY")
        result = verify_export(audit_out, manifest_path_for(audit_out))
        assert result.ok is False
        assert any("no signing key" in error for error in result.errors)

    def test_unsigned_export_rejects_key_expectation(self, events_db, audit_out, key):
        do_export(events_db, audit_out)
        result = verify_export(
            audit_out, manifest_path_for(audit_out), key_env="SUGAR_AUDIT_HMAC_KEY"
        )
        assert result.ok is False
        assert any("not signed" in error for error in result.errors)

    def test_missing_key_env_fails_verify(self, events_db, audit_out, key, monkeypatch):
        do_export(events_db, audit_out, sign_key_env="SUGAR_AUDIT_HMAC_KEY")
        monkeypatch.delenv("SUGAR_AUDIT_HMAC_KEY")
        result = verify_export(
            audit_out, manifest_path_for(audit_out), key_env="SUGAR_AUDIT_HMAC_KEY"
        )
        assert result.ok is False
        assert any("is not set" in error for error in result.errors)
        assert "SUGAR_AUDIT_HMAC_KEY" in " ".join(result.errors)

    def test_export_requires_set_key_env(self, events_db, audit_out, monkeypatch):
        monkeypatch.delenv("SUGAR_AUDIT_MISSING_KEY", raising=False)
        with pytest.raises(AuditConfigError, match="is not set"):
            do_export(events_db, audit_out, sign_key_env="SUGAR_AUDIT_MISSING_KEY")


# ---------------------------------------------------------------------------
# Validation / bad files
# ---------------------------------------------------------------------------


class TestValidation:
    def test_missing_spine(self, tmp_path, audit_out):
        with pytest.raises(AuditFileError, match="not found"):
            do_export(tmp_path / "nope.db", audit_out)

    def test_non_spine_database(self, tmp_path, audit_out):
        bad_db = tmp_path / "not-spine.db"
        with sqlite3.connect(bad_db) as connection:
            connection.execute("CREATE TABLE other (id INTEGER)")
        with pytest.raises(AuditFileError, match="missing the 'events' table"):
            do_export(bad_db, audit_out)

    def test_bad_timestamps(self, events_db, audit_out):
        with pytest.raises(AuditConfigError, match="ISO-8601"):
            do_export(events_db, audit_out, since="yesterday")
        with pytest.raises(AuditConfigError, match="since"):
            do_export(
                events_db,
                audit_out,
                since="2026-11-05T00:00:00Z",
                until="2026-11-01T00:00:00Z",
            )

    def test_timestamp_normalization(self):
        assert parse_timestamp("2026-11-09") == "2026-11-09T00:00:00Z"
        assert parse_timestamp("2026-11-09T14:00:00-05:00") == "2026-11-09T19:00:00Z"
        with pytest.raises(AuditConfigError):
            parse_timestamp("not-a-date")

    def test_kinds_validation(self):
        assert normalize_kinds(None) == []
        assert normalize_kinds(["b", "a", "a", " a "]) == ["a", "b"]
        with pytest.raises(AuditConfigError):
            normalize_kinds("task.created")
        with pytest.raises(AuditConfigError):
            normalize_kinds([""])

    def test_key_env_validation(self, monkeypatch):
        monkeypatch.setenv("AUDIT_KEY", "some-secret-key")
        assert resolve_signing_key(None) is None
        assert resolve_signing_key("AUDIT_KEY") == b"some-secret-key"
        with pytest.raises(AuditConfigError, match="never the key itself"):
            resolve_signing_key("not a valid env name")
        with pytest.raises(AuditConfigError, match="is not set"):
            resolve_signing_key("AUDIT_KEY_DOES_NOT_EXIST")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestAuditCLI:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def config(self, tmp_path):
        return cli_config(tmp_path)

    def test_export_and_verify_human_output(self, runner, config, events_db, tmp_path):
        out = tmp_path / "cli-audit.jsonl"
        exported = invoke(
            runner, config, "export", "--db", str(events_db), "--out", str(out)
        )
        assert exported.exit_code == 0, exported.output
        assert "Audit export written" in exported.output
        assert "first_id=1" in exported.output
        assert out.exists()
        assert manifest_path_for(out).exists()

        verified = invoke(
            runner, config, "verify", str(out), str(manifest_path_for(out))
        )
        assert verified.exit_code == 0, verified.output
        assert "verified" in verified.output

    def test_export_json_summary(self, runner, config, events_db, tmp_path):
        out = tmp_path / "cli-audit.jsonl"
        result = invoke(
            runner,
            config,
            "export",
            "--db",
            str(events_db),
            "--out",
            str(out),
            "--json",
        )
        assert result.exit_code == 0, result.output
        summary = json.loads(result.output)
        assert summary["count"] == 4
        assert summary["format"] == FORMAT
        assert summary["signed"] is False
        assert summary["jsonl"] == str(out)

    def test_verify_failure_exit_code(self, runner, config, events_db, tmp_path):
        out = tmp_path / "cli-audit.jsonl"
        assert (
            invoke(
                runner, config, "export", "--db", str(events_db), "--out", str(out)
            ).exit_code
            == 0
        )
        out.write_text(
            out.read_text(encoding="utf-8").replace("first task", "tampered"),
            encoding="utf-8",
        )
        failed = invoke(runner, config, "verify", str(out), str(manifest_path_for(out)))
        assert failed.exit_code == 1
        assert "FAILED" in failed.output
        assert "sha256 mismatch" in failed.output

    def test_verify_json_failure(self, runner, config, events_db, tmp_path):
        out = tmp_path / "cli-audit.jsonl"
        invoke(runner, config, "export", "--db", str(events_db), "--out", str(out))
        out.write_text("{}", encoding="utf-8")
        failed = invoke(
            runner,
            config,
            "verify",
            str(out),
            str(manifest_path_for(out)),
            "--json",
        )
        assert failed.exit_code == 1
        payload = json.loads(failed.output)
        assert payload["ok"] is False
        assert payload["errors"]

    def test_signed_cli_round_trip_and_key_never_printed(
        self, runner, config, events_db, tmp_path, monkeypatch
    ):
        secret = "cli-signing-secret-0123456789abcdef"
        monkeypatch.setenv("SUGAR_AUDIT_HMAC_KEY", secret)
        out = tmp_path / "signed-cli.jsonl"
        exported = invoke(
            runner,
            config,
            "export",
            "--db",
            str(events_db),
            "--out",
            str(out),
            "--sign-key-env",
            "SUGAR_AUDIT_HMAC_KEY",
            "--json",
        )
        assert exported.exit_code == 0, exported.output
        assert secret not in exported.output
        summary = json.loads(exported.output)
        assert summary["signed"] is True
        assert summary["key_id"]

        verified = invoke(
            runner,
            config,
            "verify",
            str(out),
            str(manifest_path_for(out)),
            "--sign-key-env",
            "SUGAR_AUDIT_HMAC_KEY",
        )
        assert verified.exit_code == 0, verified.output
        assert secret not in verified.output

        monkeypatch.setenv("SUGAR_AUDIT_HMAC_KEY", "a-wrong-key-0123456789")
        failed = invoke(
            runner,
            config,
            "verify",
            str(out),
            str(manifest_path_for(out)),
            "--sign-key-env",
            "SUGAR_AUDIT_HMAC_KEY",
        )
        assert failed.exit_code == 1
        assert "HMAC signature mismatch" in failed.output
        assert "a-wrong-key-0123456789" not in failed.output

    def test_filters_and_limit_via_cli(self, runner, config, events_db, tmp_path):
        out = tmp_path / "filtered.jsonl"
        result = invoke(
            runner,
            config,
            "export",
            "--db",
            str(events_db),
            "--out",
            str(out),
            "--kinds",
            "task.created,run.started",
            "--since",
            "2026-11-02T00:00:00Z",
            "--limit",
            "1",
            "--json",
        )
        assert result.exit_code == 0, result.output
        summary = json.loads(result.output)
        assert summary["count"] == 1
        assert summary["first_id"] == 3
        assert summary["truncated"] is False
        assert summary["filters"]["kinds"] == ["run.started", "task.created"]

    def test_overwrite_refused_then_forced(self, runner, config, events_db, tmp_path):
        out = tmp_path / "overwrite.jsonl"
        assert (
            invoke(
                runner, config, "export", "--db", str(events_db), "--out", str(out)
            ).exit_code
            == 0
        )
        refused = invoke(
            runner, config, "export", "--db", str(events_db), "--out", str(out)
        )
        assert refused.exit_code == 1
        assert "already exists" in refused.output
        forced = invoke(
            runner,
            config,
            "export",
            "--db",
            str(events_db),
            "--out",
            str(out),
            "--force",
        )
        assert forced.exit_code == 0, forced.output

    def test_cli_errors(self, runner, config, events_db, tmp_path, monkeypatch):
        missing = invoke(
            runner,
            config,
            "export",
            "--db",
            str(tmp_path / "nope.db"),
            "--out",
            str(tmp_path / "x.jsonl"),
        )
        assert missing.exit_code == 1
        assert "not found" in missing.output

        bad_since = invoke(
            runner,
            config,
            "export",
            "--db",
            str(events_db),
            "--out",
            str(tmp_path / "y.jsonl"),
            "--since",
            "yesterday",
        )
        assert bad_since.exit_code == 1
        assert "ISO-8601" in bad_since.output

        empty_kinds = invoke(
            runner,
            config,
            "export",
            "--db",
            str(events_db),
            "--out",
            str(tmp_path / "z.jsonl"),
            "--kinds",
            "",
        )
        assert empty_kinds.exit_code == 1
        assert "--kinds" in empty_kinds.output

        monkeypatch.delenv("SUGAR_AUDIT_MISSING", raising=False)
        missing_key = invoke(
            runner,
            config,
            "export",
            "--db",
            str(events_db),
            "--out",
            str(tmp_path / "k.jsonl"),
            "--sign-key-env",
            "SUGAR_AUDIT_MISSING",
        )
        assert missing_key.exit_code == 1
        assert "is not set" in missing_key.output

    def test_help_lists_subcommands(self, runner):
        result = runner.invoke(cli, ["audit", "--help"])
        assert result.exit_code == 0
        assert "export" in result.output
        assert "verify" in result.output
