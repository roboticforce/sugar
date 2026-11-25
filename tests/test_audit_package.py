"""Tests for the scoped audit package + verify CLI (task-41)."""

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from sugar.audit import package as package_module
from sugar.audit.package import (
    PACKAGE_FORMAT,
    REQUIRED_FILES,
    build_audit_package,
    verify_audit_package,
)
from sugar.dashboard.spine import EventSpine
from sugar.main import cli
from sugar.storage.work_queue import WorkQueue

SECRET = "ghp_" + "q" * 30


def run(coro):
    return asyncio.run(coro)


async def seed_spine(path):
    spine = EventSpine(str(path), retention_days=0)
    await spine.start()
    events = [
        {
            "kind": "session.started",
            "actor": "dsh",
            "actor_kind": "agent",
            "harness": "dsh",
            "session_id": "s-alpha",
            "project": "alpha",
            "ts": "2026-11-01T10:00:00Z",
            "payload": {"payload": {"title": "alpha session"}},
        },
        {
            "kind": "tool.started",
            "actor": "dsh",
            "actor_kind": "agent",
            "harness": "dsh",
            "session_id": "s-alpha",
            "project": "alpha",
            "task_id": "t-alpha",
            "ts": "2026-11-01T10:01:00Z",
            "payload": {
                "tool_use_id": "call-1",
                "tool_name": "Bash",
                "payload": {"arguments": {"command": f"export TOKEN={SECRET}"}},
            },
        },
        {
            "kind": "tool.completed",
            "actor": "dsh",
            "actor_kind": "agent",
            "harness": "dsh",
            "session_id": "s-alpha",
            "project": "alpha",
            "task_id": "t-alpha",
            "ts": "2026-11-01T10:02:00Z",
            "payload": {
                "tool_use_id": "call-1",
                "tool_name": "Bash",
                "payload": {"result": "ok"},
            },
        },
        {
            "kind": "artifact.created",
            "actor": "dsh",
            "actor_kind": "agent",
            "harness": "dsh",
            "session_id": "s-alpha",
            "project": "alpha",
            "task_id": "t-alpha",
            "ts": "2026-11-01T10:03:00Z",
            "payload": {"payload": {"path": "/repo/report.md"}},
        },
        {
            "kind": "verification.check_result",
            "actor": "dsh",
            "actor_kind": "agent",
            "harness": "dsh",
            "session_id": "s-alpha",
            "project": "alpha",
            "task_id": "t-alpha",
            "ts": "2026-11-01T10:04:00Z",
            "payload": {"status": "passed", "check": "pytest"},
        },
        {
            "kind": "usage.reported",
            "actor": "dsh",
            "actor_kind": "agent",
            "harness": "dsh",
            "session_id": "s-alpha",
            "project": "alpha",
            "task_id": "t-alpha",
            "ts": "2026-11-01T10:05:00Z",
            "payload": {"tokens_in": 100, "tokens_out": 50, "model": "dsh-model"},
        },
        {
            "kind": "session.ended",
            "actor": "dsh",
            "actor_kind": "agent",
            "harness": "dsh",
            "session_id": "s-alpha",
            "project": "alpha",
            "ts": "2026-11-01T10:06:00Z",
            "payload": {},
        },
        {
            "kind": "session.started",
            "actor": "dsh",
            "actor_kind": "agent",
            "harness": "dsh",
            "session_id": "s-beta",
            "project": "beta",
            "ts": "2026-11-02T10:00:00Z",
            "payload": {"payload": {"title": "beta session"}},
        },
    ]
    for event in events:
        await spine.append(event)
    await spine.close()


async def seed_board(path):
    queue = WorkQueue(str(path))
    await queue.initialize()
    await queue.add_work(
        {
            "type": "feature",
            "title": "Alpha task",
            "description": "x" * 5000,
            "priority": 3,
            "status": "completed",
            "source": "test",
            "context": {"project": "alpha", "cwd": "/repo"},
            "acceptance_criteria": [],
            "verification_status": "passed",
        }
    )
    await queue.add_work(
        {
            "type": "bug_fix",
            "title": "Beta task",
            "description": "beta",
            "priority": 3,
            "status": "hold",
            "source": "test",
            "context": {"project": "beta", "cwd": "/repo/beta"},
            "acceptance_criteria": [],
        }
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    events = tmp_path / "events.db"
    board = tmp_path / "board.db"
    adapters = tmp_path / "adapters.json"
    adapters.write_text(
        json.dumps(
            {
                "version": 1,
                "adapters": {
                    "dsh": {
                        "display_name": "DeepSeek Harness",
                        "installed": True,
                        "fidelity": "L3",
                        "transport": "http-hook",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SUGAR_ADAPTERS_FILE", str(adapters))
    monkeypatch.setenv("SUGAR_MODEL_PRICES", str(tmp_path / "prices.json"))
    run(seed_spine(events))
    run(seed_board(board))
    return {"events": events, "board": board, "tmp": tmp_path}


def build(fixture, out, **kwargs):
    return run(
        build_audit_package(
            out,
            events_db=str(fixture["events"]),
            board_db=str(fixture["board"]),
            **kwargs,
        )
    )


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class TestPackageBuild:
    def test_structure_and_verify(self, project):
        out = project["tmp"] / "pkg"
        result = build(project, out)
        assert result.out_dir == str(out)
        assert result.events_count >= 1
        for name in REQUIRED_FILES:
            assert (out / name).exists(), name
        manifest = read_json(out / "package.manifest.json")
        assert manifest["format"] == PACKAGE_FORMAT
        assert manifest["scope"]["project"] is None
        assert set(manifest["files"]) == set(REQUIRED_FILES)
        verify = verify_audit_package(out)
        assert verify.ok, verify.errors
        assert verify.files_checked == len(REQUIRED_FILES)
        assert verify.signed is False

    def test_project_scope(self, project):
        out = project["tmp"] / "pkg-alpha"
        result = build(project, out, project="alpha")
        assert result.events_count >= 1
        events = [
            json.loads(line)
            for line in (out / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert events and all(event.get("project") == "alpha" for event in events)
        tasks = read_json(out / "tasks.json")["tasks"]
        assert tasks and all(task["context"]["project"] == "alpha" for task in tasks)
        evidence = read_json(out / "evidence.json")
        assert evidence["events"]
        assert all(event.get("project") == "alpha" for event in evidence["events"])

    def test_since_until_scope(self, project):
        out = project["tmp"] / "pkg-window"
        result = build(
            project,
            out,
            since="2026-11-01T10:02:00Z",
            until="2026-11-01T10:05:00Z",
        )
        assert result.events_count >= 1
        events = [
            json.loads(line)
            for line in (out / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert all(
            "2026-11-01T10:02:00Z" <= event["ts"] <= "2026-11-01T10:05:00Z"
            for event in events
        )
        assert read_json(out / "package.manifest.json")["scope"]["since"] == (
            "2026-11-01T10:02:00Z"
        )
        verify = verify_audit_package(out)
        assert verify.ok, verify.errors

    def test_empty_spine_and_missing_board(self, tmp_path):
        events = tmp_path / "empty.db"
        run(seed_empty_spine(events))
        out = tmp_path / "pkg-empty"
        result = run(
            build_audit_package(
                out,
                events_db=str(events),
                board_db=str(tmp_path / "missing-board.db"),
            )
        )
        assert result.events_count == 0
        assert result.tasks_count == 0
        assert result.limitations
        verify = verify_audit_package(out)
        assert verify.ok, verify.errors
        claims = read_json(out / "claims.json")
        statuses = {claim["status"] for claim in claims["claims"]}
        assert "not_proven" in statuses

    def test_tamper_detection(self, project):
        out = project["tmp"] / "pkg-tamper"
        build(project, out)
        target = out / "tasks.json"
        target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
        verify = verify_audit_package(out)
        assert verify.ok is False
        assert any(
            "sha256 mismatch" in error or "size mismatch" in error
            for error in verify.errors
        )

    def test_missing_required_file(self, project):
        out = project["tmp"] / "pkg-missing"
        build(project, out)
        (out / "coverage.json").unlink()
        verify = verify_audit_package(out)
        assert verify.ok is False
        assert any("coverage.json" in error for error in verify.errors)

    def test_signed_package_and_wrong_key(self, project, monkeypatch):
        monkeypatch.setenv("AUDIT_KEY", "correct-key")
        out = project["tmp"] / "pkg-signed"
        result = build(project, out, sign_key_env="AUDIT_KEY")
        assert result.signed is True
        verify = verify_audit_package(out, sign_key_env="AUDIT_KEY")
        assert verify.ok, verify.errors
        monkeypatch.setenv("AUDIT_KEY", "wrong-key")
        wrong = verify_audit_package(out, sign_key_env="AUDIT_KEY")
        assert wrong.ok is False
        assert any(
            "signature mismatch" in error or "key_id mismatch" in error
            for error in wrong.errors
        )
        missing = verify_audit_package(out)
        assert missing.ok is False

    def test_refuses_existing_without_force(self, project):
        out = project["tmp"] / "pkg-existing"
        build(project, out)
        with pytest.raises(Exception):
            build(project, out)
        forced = build(project, out, force=True)
        assert forced.out_dir == str(out)
        assert verify_audit_package(out).ok

    def test_atomic_failure_leaves_no_partial_package(self, project, monkeypatch):
        out = project["tmp"] / "pkg-atomic"
        build(project, out)
        before = verify_audit_package(out)
        assert before.ok

        def fail(*args, **kwargs):
            raise RuntimeError("simulated build failure")

        monkeypatch.setattr(package_module, "_traces_payload", fail)
        with pytest.raises(RuntimeError):
            build(project, out, force=True)
        after = verify_audit_package(out)
        assert after.ok, "previous package must be preserved after a failed rebuild"
        assert len(list(project["tmp"].glob(".pkg-atomic.tmp-*"))) == 0

    def test_no_partial_package_when_new_build_fails(self, project, monkeypatch):
        out = project["tmp"] / "pkg-new-fail"
        monkeypatch.setattr(
            package_module,
            "_traces_payload",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(RuntimeError):
            build(project, out)
        assert not out.exists()

    def test_no_secrets_in_event_payloads(self, project):
        out = project["tmp"] / "pkg-secret"
        build(project, out)
        events_text = (out / "events.jsonl").read_text(encoding="utf-8")
        assert SECRET not in events_text
        assert "ghp_" not in events_text

    def test_bounded_task_metadata(self, project):
        out = project["tmp"] / "pkg-bounded"
        build(project, out)
        tasks = read_json(out / "tasks.json")["tasks"]
        alpha = next(task for task in tasks if task["title"] == "Alpha task")
        assert len(alpha["description"]) <= 2000
        assert "thinking_summary" not in alpha
        assert (out / "events.jsonl").stat().st_size < 100_000

    def test_manifest_has_no_absolute_home_paths(self, project):
        out = project["tmp"] / "pkg-paths"
        build(project, out)
        home = str(Path.home())
        for name in ("package.manifest.json", "claims.json", "README.md"):
            assert home not in (out / name).read_text(encoding="utf-8")


class TestPackageCLI:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    def invoke(self, runner, project, *args):
        return runner.invoke(
            cli,
            [
                "audit",
                "package",
                "--db",
                str(project["events"]),
                "--board-db",
                str(project["board"]),
                *args,
            ],
        )

    def test_cli_build_json_and_verify(self, runner, project):
        out = project["tmp"] / "pkg-cli"
        result = self.invoke(runner, project, "--out", str(out), "--json")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["events"] >= 1
        assert payload["tasks"] >= 1
        assert (out / "package.manifest.json").exists()
        verify = runner.invoke(
            cli,
            ["audit", "verify-package", str(out), "--json"],
        )
        assert verify.exit_code == 0, verify.output
        data = json.loads(verify.output)
        assert data["ok"] is True
        assert data["files_checked"] == len(REQUIRED_FILES)

    def test_cli_verify_failure_exit_code(self, runner, project):
        out = project["tmp"] / "pkg-cli-bad"
        assert self.invoke(runner, project, "--out", str(out)).exit_code == 0
        (out / "tasks.json").write_text("{}", encoding="utf-8")
        verify = runner.invoke(cli, ["audit", "verify-package", str(out)])
        assert verify.exit_code == 1
        assert "verification FAILED" in verify.output

    def test_cli_existing_out_without_force(self, runner, project):
        out = project["tmp"] / "pkg-cli-exists"
        assert self.invoke(runner, project, "--out", str(out)).exit_code == 0
        second = self.invoke(runner, project, "--out", str(out))
        assert second.exit_code == 1
        forced = self.invoke(runner, project, "--out", str(out), "--force", "--json")
        assert forced.exit_code == 0, forced.output

    def test_cli_signed_roundtrip(self, runner, project, monkeypatch):
        monkeypatch.setenv("AUDIT_KEY", "cli-key")
        out = project["tmp"] / "pkg-cli-signed"
        result = self.invoke(
            runner,
            project,
            "--out",
            str(out),
            "--sign-key-env",
            "AUDIT_KEY",
            "--json",
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["signed"] is True
        verify = runner.invoke(
            cli,
            [
                "audit",
                "verify-package",
                str(out),
                "--sign-key-env",
                "AUDIT_KEY",
                "--json",
            ],
        )
        assert verify.exit_code == 0, verify.output


async def seed_empty_spine(path):
    spine = EventSpine(str(path), retention_days=0)
    await spine.start()
    await spine.close()
