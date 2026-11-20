"""Tests for the DSH session-tail adapter (``sugar/dsh``).

Fixtures are generated zstd-compressed JSONL files shaped like real
``~/.dsh/sessions/.../session.v4.jsonl.zstd`` logs. Tests that need a
decompressor are skipped when neither the system ``zstd`` CLI nor the optional
``zstandard`` package is available (no DSH app is required). launchd is never
loaded.
"""

import asyncio
import json
import os
import plistlib
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner

from sugar.connect import detect, installers, registry
from sugar.connect.doctor import run_doctor
from sugar.dashboard.spine import EventSpine
from sugar.dsh.discover import decode_workspace, discover_sessions
from sugar.dsh.parse import (
    DshConfigError,
    DshParseError,
    normalize_since,
    parse_session_file,
    zstd_available,
)
from sugar.dsh.tail import (
    DEFAULT_STATE,
    TailResult,
    default_state_path,
    load_state,
    save_state,
    tail_once,
    watch,
)
from sugar.main import cli
from sugar.telemetry.coverage import coverage_report

REPO_ROOT = Path(__file__).resolve().parents[1]
ZSTD_BIN = shutil.which("zstd")
HAS_DECOMPRESSOR = zstd_available()
requires_zstd = pytest.mark.skipif(
    not HAS_DECOMPRESSOR, reason="no zstd decompressor available"
)

SESSION_ID = "11111111-2222-3333-4444-555555555555"
WORKSPACE = "--Users-me-Dev-app--"
START_MS = 1_793_904_936_618


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


def compress(text: str) -> bytes:
    if ZSTD_BIN:
        completed = subprocess.run(
            [ZSTD_BIN, "-q", "-c"], input=text.encode("utf-8"), capture_output=True
        )
        assert completed.returncode == 0, completed.stderr
        return completed.stdout
    import zstandard  # type: ignore[import-not-found]  # pragma: no cover

    return zstandard.ZstdCompressor().compress(text.encode("utf-8"))  # pragma: no cover


def header(**overrides) -> dict:
    data = {
        "type": "session",
        "version": 4,
        "id": SESSION_ID,
        "createdAt": START_MS,
        "cwd": "/Users/me/Dev/app",
        "origin": "local",
        "agentPreset": "standard",
    }
    data.update(overrides)
    return data


def event(kind: str, seq: int, data: dict, *, time: int | None = None) -> dict:
    return {
        "type": kind,
        "seq": seq,
        "time": START_MS + (seq + 1) * 100 if time is None else time,
        "data": data,
    }


def default_events() -> list:
    return [
        header(),
        event(
            "request/header",
            0,
            {
                "header": "{'config': {'provider': 'deepseek-account', "
                "'model': 'deepseek-flash'}}",
                "reason": "initial",
            },
        ),
        event("approval/policy", 1, {"policy": "never", "source": "delegation"}),
        event("turn/start", 2, {"turn": 1}),
        event("step/start", 3, {"turn": 1, "step": 1}),
        event(
            "tool/call",
            4,
            {
                "turn": 1,
                "step": 1,
                "callId": "call_abc",
                "name": "bash",
                "arguments": json.dumps(
                    {"command": "pytest -q", "description": "run tests"}
                ),
            },
        ),
        event(
            "tool/result",
            5,
            {
                "turn": 1,
                "step": 1,
                "toolCallId": "call_abc",
                "content": [{"type": "text", "text": "passed 12 tests"}],
                "isError": False,
            },
        ),
        event(
            "tool/call",
            6,
            {
                "turn": 1,
                "step": 1,
                "callId": "call_def",
                "name": "write",
                "arguments": json.dumps({"file_path": "/tmp/x.py", "content": "y"}),
            },
        ),
        event(
            "tool/result",
            7,
            {
                "turn": 1,
                "step": 1,
                "message": {
                    "role": "tool",
                    "toolCallId": "call_def",
                    "isError": True,
                    "content": [{"type": "text", "text": "Error: permission denied"}],
                },
            },
        ),
        event("assistant/message", 8, {"turn": 1}),
        event("turn/end", 9, {"turn": 1, "reason": {"kind": "completed"}}),
    ]


def write_session(
    base: Path,
    events: list,
    *,
    workspace: str = WORKSPACE,
    session_id: str = SESSION_ID,
    raw: bytes | None = None,
) -> Path:
    session_dir = base / workspace / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "session.v4.jsonl.zstd"
    if raw is None:
        text = "\n".join(json.dumps(item) for item in events) + "\n"
        raw = compress(text)
    path.write_bytes(raw)
    return path


def closed_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def cli_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        "sugar:\n"
        "  storage:\n"
        f"    database: \"{tmp_path / 'sugar.db'}\"\n"
        "  logging:\n"
        f"    file: \"{tmp_path / 'sugar.log'}\"\n",
        encoding="utf-8",
    )
    return path


def invoke_cli(tmp_path: Path, *args):
    return CliRunner().invoke(cli, ["--config", str(cli_config(tmp_path)), *args])


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    # Keep the real PATH: the tail needs the system `zstd`, and detection is
    # scoped to the temp HOME below so host binaries cannot add rows.
    for variable in (
        "SUGAR_DSH_SESSIONS",
        "SUGAR_DSH_STATE",
        "SUGAR_ADAPTERS_PATH",
        "SUGAR_ADAPTERS_FILE",
        "SUGAR_BOARD_URL",
        "SUGAR_BOARD_TOKEN",
    ):
        monkeypatch.delenv(variable, raising=False)

    original = detect._candidate_paths

    def scoped_candidates(harness, home_path):
        return [
            path
            for path in original(harness, home_path)
            if str(path).startswith(str(home_path))
        ]

    monkeypatch.setattr(detect, "_candidate_paths", scoped_candidates)
    return home_dir


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscover:
    def test_missing_directory(self, tmp_path):
        assert discover_sessions(tmp_path / "nope") == []

    def test_finds_workspace_session(self, tmp_path):
        path = write_session(tmp_path / "sessions", default_events())
        sessions = discover_sessions(tmp_path / "sessions")
        assert len(sessions) == 1
        found = sessions[0]
        assert found.path == path
        assert found.session_id == SESSION_ID
        assert found.workspace_dir == WORKSPACE
        assert found.workspace == "/Users/me/Dev/app"

    def test_env_override(self, tmp_path, monkeypatch):
        base = tmp_path / "custom"
        write_session(base, default_events())
        monkeypatch.setenv("SUGAR_DSH_SESSIONS", str(base))
        sessions = discover_sessions()
        assert len(sessions) == 1

    def test_deepseek_fallback(self, tmp_path, monkeypatch):
        fallback = tmp_path / "home" / ".deepseek" / "sessions"
        write_session(fallback, default_events())
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.delenv("SUGAR_DSH_SESSIONS", raising=False)
        sessions = discover_sessions()
        assert len(sessions) == 1

    def test_workspace_decoding(self):
        assert decode_workspace("--Users-me-Dev-app--") == "/Users/me/Dev/app"
        assert decode_workspace("weird") == "weird"
        assert decode_workspace("") == ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@requires_zstd
class TestParse:
    def test_session_header(self, tmp_path):
        path = write_session(tmp_path, default_events())
        parsed = parse_session_file(path)
        assert parsed.session_id == SESSION_ID
        assert parsed.header_event is not None
        header = parsed.header_event
        assert header["kind"] == "session.started"
        assert header["harness"] == "dsh"
        assert header["actor"] == "dsh"
        assert header["actor_kind"] == "agent"
        assert header["session_id"] == SESSION_ID
        assert header["cwd"] == "/Users/me/Dev/app"
        assert header["ts"].endswith("Z")
        assert header["payload"]["fidelity"] == "L1"
        payload = header["payload"]["payload"]
        assert payload["origin"] == "local"
        assert payload["agent_preset"] == "standard"
        assert payload["version"] == 4

    def test_turn_mapping(self, tmp_path):
        path = write_session(tmp_path, default_events())
        parsed = parse_session_file(path)
        by_kind = {}
        for item in parsed.events:
            by_kind.setdefault(item.kind, []).append(item)
        assert [e.envelope["payload"]["turn_id"] for e in by_kind["turn.started"]] == [
            "1"
        ]
        ended = by_kind["turn.ended"][0]
        assert ended.envelope["payload"]["turn_id"] == "1"
        assert ended.envelope["payload"]["payload"]["reason"] == {"kind": "completed"}

    def test_tool_started(self, tmp_path):
        path = write_session(tmp_path, default_events())
        parsed = parse_session_file(path)
        started = [e for e in parsed.events if e.kind == "tool.started"]
        assert len(started) == 2
        first = started[0].envelope
        assert first["payload"]["fidelity"] == "L3"
        assert first["payload"]["tool_use_id"] == "call_abc"
        assert first["payload"]["tool_name"] == "bash"
        assert first["payload"]["cwd"] == "/Users/me/Dev/app"
        assert first["payload"]["payload"]["arguments"] == {
            "command": "pytest -q",
            "description": "run tests",
        }
        assert first["payload"]["payload"]["command"] == "pytest -q"

    def test_tool_completed_with_duration(self, tmp_path):
        path = write_session(tmp_path, default_events())
        parsed = parse_session_file(path)
        completed = [
            item.envelope
            for item in parsed.events
            if item.kind == "tool.completed"
            and item.envelope["payload"]["tool_use_id"] == "call_abc"
        ]
        assert len(completed) == 1
        event = completed[0]
        assert event["payload"]["status"] == "completed"
        assert event["payload"]["payload"]["result"] == "passed 12 tests"
        assert event["payload"]["payload"]["is_error"] is False
        assert event["payload"]["duration_ms"] == 100

    def test_tool_failed_from_nested_message(self, tmp_path):
        path = write_session(tmp_path, default_events())
        parsed = parse_session_file(path)
        failed = [item for item in parsed.events if item.kind == "tool.failed"]
        assert len(failed) == 1
        envelope = failed[0].envelope
        assert envelope["payload"]["tool_use_id"] == "call_def"
        assert envelope["payload"]["status"] == "failed"
        assert "permission denied" in envelope["payload"]["payload"]["result"]

    @pytest.mark.parametrize(
        "result_data",
        [
            {"toolCallId": "call_abc", "isError": True, "content": "boom"},
            {"toolCallId": "call_abc", "error": "boom"},
            {
                "message": {
                    "toolCallId": "call_abc",
                    "isError": True,
                    "content": [{"type": "text", "text": "boom"}],
                }
            },
        ],
    )
    def test_error_variants_fail(self, tmp_path, result_data):
        events = [
            header(),
            event(
                "tool/call",
                0,
                {"callId": "call_abc", "name": "bash", "arguments": "{}"},
            ),
            event("tool/result", 1, result_data),
        ]
        parsed = parse_session_file(write_session(tmp_path, events))
        kinds = [item.kind for item in parsed.events]
        assert "tool.failed" in kinds

    def test_approval_policy(self, tmp_path):
        path = write_session(tmp_path, default_events())
        parsed = parse_session_file(path)
        decisions = [item for item in parsed.events if item.kind == "policy.decision"]
        assert len(decisions) == 1
        envelope = decisions[0].envelope
        assert envelope["payload"]["fidelity"] == "L3"
        assert envelope["payload"]["payload"] == {
            "policy": "never",
            "source": "delegation",
        }

    def test_noise_ignored(self, tmp_path):
        path = write_session(tmp_path, default_events())
        parsed = parse_session_file(path)
        kinds = {item.kind for item in parsed.events}
        assert kinds == {
            "policy.decision",
            "turn.started",
            "turn.ended",
            "tool.started",
            "tool.completed",
            "tool.failed",
        }

    def test_malformed_json_line_skipped(self, tmp_path):
        raw = compress(
            "\n".join(
                [
                    json.dumps(header()),
                    "{not json",
                    json.dumps(event("turn/start", 0, {"turn": 1})),
                ]
            )
            + "\n"
        )
        path = write_session(tmp_path, [], raw=raw)
        parsed = parse_session_file(path)
        assert parsed.malformed_lines == 1
        assert [item.kind for item in parsed.events] == ["turn.started"]

    def test_invalid_arguments_json_kept_raw(self, tmp_path):
        events = [
            header(),
            event(
                "tool/call",
                0,
                {"callId": "c1", "name": "bash", "arguments": "not-json{"},
            ),
        ]
        parsed = parse_session_file(write_session(tmp_path, events))
        envelope = parsed.events[0].envelope
        assert envelope["payload"]["payload"]["arguments_raw"] == "not-json{"

    def test_missing_header_tolerated(self, tmp_path):
        events = [event("turn/start", 0, {"turn": 3})]
        parsed = parse_session_file(
            write_session(tmp_path, events), session_id_fallback="fallback-id"
        )
        assert parsed.header_event is None
        assert parsed.session_id == "fallback-id"
        assert parsed.events[0].envelope["session_id"] == "fallback-id"

    def test_model_from_request_header(self, tmp_path):
        path = write_session(tmp_path, default_events())
        parsed = parse_session_file(path)
        started = [
            item.envelope for item in parsed.events if item.kind == "tool.started"
        ]
        assert started[0]["payload"]["model"] == "deepseek-flash"

    def test_since_filter_advances_checkpoint(self, tmp_path):
        events = default_events()
        path = write_session(tmp_path, events)
        since = "2026-11-20T00:00:00Z"
        parsed = parse_session_file(path, since=since)
        assert parsed.events == []
        assert parsed.last_seq == 9

    def test_trailing_garbage_keeps_complete_frames(self, tmp_path):
        raw = compress("\n".join(json.dumps(item) for item in default_events()) + "\n")
        path = write_session(tmp_path, [], raw=raw + b"\x00\x00garbage")
        parsed = parse_session_file(path)
        assert parsed.session_id == SESSION_ID
        assert parsed.events

    def test_corrupt_file_raises(self, tmp_path):
        session_dir = tmp_path / WORKSPACE / SESSION_ID
        session_dir.mkdir(parents=True)
        path = session_dir / "session.v4.jsonl.zstd"
        path.write_bytes(b"\x28\xb5\x2f\xfdnot-a-real-frame")
        with pytest.raises(DshParseError):
            parse_session_file(path)

    def test_normalize_since(self):
        assert normalize_since("2026-11-20") == "2026-11-20T00:00:00Z"
        assert normalize_since(None) is None
        assert normalize_since(0) is not None
        with pytest.raises(DshConfigError):
            normalize_since("yesterday")


# ---------------------------------------------------------------------------
# Tail + checkpoint
# ---------------------------------------------------------------------------


@requires_zstd
class TestTail:
    def test_first_run_emits_and_checkpoints(self, tmp_path):
        sessions = tmp_path / "sessions"
        write_session(sessions, default_events())
        emitted = []
        state_path = tmp_path / "state.json"

        result = tail_once(
            sessions_dir=sessions,
            state_path=state_path,
            emit=emitted.append,
            since=None,
        )

        assert result.sessions == 1
        assert result.emitted == result.events == 8
        assert result.failed == 0
        assert len(emitted) == 8
        assert {e["kind"] for e in emitted} >= {
            "session.started",
            "turn.started",
            "tool.started",
            "tool.completed",
            "tool.failed",
            "policy.decision",
        }
        state = load_state(state_path)
        key = str(
            (sessions / WORKSPACE / SESSION_ID / "session.v4.jsonl.zstd").resolve()
        )
        assert state["sessions"][key]["last_seq"] == 9
        assert state["sessions"][key]["session_id"] == SESSION_ID

    def test_second_run_is_noop(self, tmp_path):
        sessions = tmp_path / "sessions"
        write_session(sessions, default_events())
        state_path = tmp_path / "state.json"
        tail_once(sessions_dir=sessions, state_path=state_path, emit=lambda e: None)
        emitted = []
        result = tail_once(
            sessions_dir=sessions, state_path=state_path, emit=emitted.append
        )
        assert result.events == 0
        assert emitted == []

    def test_new_file_discovered_later(self, tmp_path):
        sessions = tmp_path / "sessions"
        write_session(sessions, default_events())
        state_path = tmp_path / "state.json"
        tail_once(sessions_dir=sessions, state_path=state_path, emit=lambda e: None)

        other = "99999999-8888-7777-6666-555555555555"
        other_events = default_events()
        other_events[0] = header(id=other)
        write_session(
            sessions,
            other_events,
            workspace="--Users-me-Dev-other--",
            session_id=other,
        )
        emitted = []
        result = tail_once(
            sessions_dir=sessions, state_path=state_path, emit=emitted.append
        )
        assert result.sessions == 2
        assert len(result.session_ids) == 2
        assert any(e["session_id"] == other for e in emitted)

    def test_truncated_rewind_replays(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = write_session(sessions, default_events())
        state_path = tmp_path / "state.json"
        tail_once(sessions_dir=sessions, state_path=state_path, emit=lambda e: None)

        # Simulate a rewound/truncated log (smaller file, same path/session).
        smaller = [header(), event("turn/start", 0, {"turn": 1})]
        path.write_bytes(
            compress("\n".join(json.dumps(item) for item in smaller) + "\n")
        )
        before = load_state(state_path)["sessions"][str(path.resolve())]["last_seq"]
        assert before == 9

        emitted = []
        result = tail_once(
            sessions_dir=sessions, state_path=state_path, emit=emitted.append
        )
        assert result.events == 2  # header + turn/start replayed
        assert emitted[0]["kind"] == "session.started"

    def test_corrupt_file_skipped_other_sessions_processed(self, tmp_path):
        sessions = tmp_path / "sessions"
        write_session(sessions, default_events())
        bad = sessions / "--broken--" / "bad-session"
        bad.mkdir(parents=True)
        (bad / "session.v4.jsonl.zstd").write_bytes(b"not zstd at all")
        emitted = []
        result = tail_once(
            sessions_dir=sessions,
            state_path=tmp_path / "state.json",
            emit=emitted.append,
        )
        assert result.sessions == 1
        assert emitted
        assert result.errors and "decompress" in " ".join(result.errors).lower()

    def test_deleted_file_tolerated(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = write_session(sessions, default_events())
        state_path = tmp_path / "state.json"
        tail_once(sessions_dir=sessions, state_path=state_path, emit=lambda e: None)

        path.unlink()
        result = tail_once(
            sessions_dir=sessions, state_path=state_path, emit=lambda e: None
        )
        assert result.sessions == 0
        assert result.errors == []

    def test_emit_failure_is_fail_open(self, tmp_path):
        sessions = tmp_path / "sessions"
        write_session(sessions, default_events())
        state_path = tmp_path / "state.json"

        def boom(_envelope):
            raise RuntimeError("board down")

        result = tail_once(sessions_dir=sessions, state_path=state_path, emit=boom)
        assert result.failed == result.events == 8
        assert result.emitted == 0
        # Checkpoint still advances (at-most-once, no spin on the same events).
        assert load_state(state_path)["sessions"]

    def test_emit_result_false_counts_failed(self, tmp_path):
        class Failed:
            ok = False
            error = "nope"

        sessions = tmp_path / "sessions"
        write_session(sessions, default_events())
        result = tail_once(
            sessions_dir=sessions,
            state_path=tmp_path / "state.json",
            emit=lambda envelope: Failed(),
        )
        assert result.failed == 8 and result.emitted == 0

    def test_state_env_override_and_corrupt_state(self, tmp_path, monkeypatch):
        sessions = tmp_path / "sessions"
        write_session(sessions, default_events())
        override = tmp_path / "custom-state.json"
        monkeypatch.setenv("SUGAR_DSH_STATE", str(override))
        assert default_state_path() == override

        override.write_text("{broken", encoding="utf-8")
        allowed = tmp_path / "allowed-state.json"
        assert load_state(override)["sessions"] == {}
        save_state({"version": 1, "sessions": {}}, allowed)
        assert json.loads(allowed.read_text())["version"] == 1
        assert DEFAULT_STATE.endswith("dsh-tail-state.json")

    def test_watch_picks_up_new_events_once(self, tmp_path):
        sessions = tmp_path / "sessions"
        path = write_session(sessions, default_events())
        state_path = tmp_path / "state.json"
        emitted = []

        def on_cycle(cycle: TailResult):
            if cycle.cycles == 1 and len(emitted) == 8:
                with path.open("ab") as handle:
                    handle.write(
                        compress(
                            json.dumps(event("turn/start", 10, {"turn": 2})) + "\n"
                        )
                    )

        result = watch(
            sessions_dir=sessions,
            state_path=state_path,
            emit=emitted.append,
            interval=0.2,
            max_cycles=2,
            on_cycle=on_cycle,
        )
        assert result.cycles == 2
        assert len(emitted) == 9  # 8 from cycle 1 + the appended turn
        assert result.emitted == 9


# ---------------------------------------------------------------------------
# Connect / LaunchAgent
# ---------------------------------------------------------------------------


class TestConnect:
    def test_dsh_spec_is_l3_session_tail(self):
        spec = detect.HARNESS_SPECS[detect.DSH]
        assert spec.fidelity == "L3"
        assert spec.transport == "session-tail"
        assert spec.installable is True
        assert spec.adapter_version == "phase1b"

    def test_detects_via_dsh_home(self, home):
        (home / ".dsh").mkdir()
        detection = detect.detect_harness(detect.DSH, home=home, path="")
        assert detection.detected is True

    def test_installer_writes_launchagent_on_darwin(self, home, monkeypatch):
        monkeypatch.setattr(installers, "current_platform", lambda: "darwin")
        result = installers.install(detect.DSH, home=home)

        assert result.status == "installed"
        plist_path = (
            home / "Library" / "LaunchAgents" / ("io.roboticforce.sugar.dsh-tail.plist")
        )
        assert result.config_path == plist_path
        data = plistlib.loads(plist_path.read_bytes())
        assert data["Label"] == "io.roboticforce.sugar.dsh-tail"
        assert data["RunAtLoad"] is True
        assert data["KeepAlive"] is True
        arguments = data["ProgramArguments"]
        tail_index = arguments.index("tail")
        assert arguments[tail_index + 1] == "dsh"
        assert "--watch" in arguments
        assert "--state-file" in arguments
        assert data["StandardOutPath"].endswith("dsh-tail.log")
        assert data["StandardErrorPath"].endswith("dsh-tail.log")
        assert data["EnvironmentVariables"]["PATH"]

    def test_installer_idempotent_and_dry_run(self, home, monkeypatch):
        monkeypatch.setattr(installers, "current_platform", lambda: "darwin")
        first = installers.install(detect.DSH, home=home)
        content = first.config_path.read_text()

        assert installers.install(detect.DSH, home=home).status == "unchanged"
        assert first.config_path.read_text() == content

        dry = installers.install(detect.DSH, home=home, dry_run=True)
        assert dry.status == "unchanged"

    def test_installer_backup_when_command_changes(self, home, monkeypatch):
        monkeypatch.setattr(installers, "current_platform", lambda: "darwin")
        installers.install(detect.DSH, home=home)
        plist_path = (
            home / "Library" / "LaunchAgents" / ("io.roboticforce.sugar.dsh-tail.plist")
        )
        previous = plist_path.read_text()

        monkeypatch.setattr(
            installers,
            "sugar_program_arguments",
            lambda: ["/opt/other/sugar"],
        )
        result = installers.install(detect.DSH, home=home)

        assert result.status == "updated"
        assert plist_path.with_name(plist_path.name + ".bak").read_text() == previous
        data = plistlib.loads(plist_path.read_bytes())
        assert data["ProgramArguments"][0] == "/opt/other/sugar"

    def test_installer_dry_run_writes_nothing(self, home, monkeypatch):
        monkeypatch.setattr(installers, "current_platform", lambda: "darwin")
        result = installers.install(detect.DSH, home=home, dry_run=True)
        assert result.status == "would-install"
        assert not (home / "Library").exists()

    def test_non_darwin_manual_install(self, home, monkeypatch):
        monkeypatch.setattr(installers, "current_platform", lambda: "linux")
        result = installers.install(detect.DSH, home=home)
        assert result.status == "installed"
        assert result.config_path is None
        assert not (home / "Library").exists()
        joined = " ".join(result.notes)
        assert "sugar" in joined and "tail dsh --watch" in joined
        assert "session-tail" not in joined  # transport lives in the registry

    def test_connect_cli_records_registry(self, home, tmp_path, monkeypatch):
        monkeypatch.setattr(installers, "current_platform", lambda: "linux")
        (home / ".dsh").mkdir()
        result = invoke_cli(tmp_path, "connect", "--harness", "dsh", "--yes")
        assert result.exit_code == 0, result.output
        assert "session-tail" in result.output
        assert "sugar tail dsh" in result.output
        reg = json.loads((home / ".sugar" / "adapters.json").read_text())
        entry = reg["adapters"]["dsh"]
        assert entry["installed"] is True
        assert entry["fidelity"] == "L3"
        assert entry["transport"] == "session-tail"
        assert entry["adapter_version"] == "phase1b"

    def test_connect_check_marks_dsh_install(self, home, tmp_path):
        (home / ".dsh").mkdir()
        result = invoke_cli(tmp_path, "connect", "--check")
        assert result.exit_code == 0, result.output
        dsh_line = next(
            line
            for line in result.output.splitlines()
            if line.strip().startswith("dsh")
        )
        assert "install" in dsh_line
        assert "session-tail" in dsh_line
        assert not (home / "Library").exists()


# ---------------------------------------------------------------------------
# Coverage + CLI
# ---------------------------------------------------------------------------


def test_coverage_reports_dsh_l3_after_event(tmp_path, home):
    import time

    now_ms = int(time.time() * 1000)
    events = default_events()
    events[0] = header(createdAt=now_ms)
    for index, item in enumerate(events[1:], start=1):
        item["time"] = now_ms + index * 100
    sessions = tmp_path / "sessions"
    write_session(sessions, events)
    emitted = []
    tail_once(
        sessions_dir=sessions,
        state_path=tmp_path / "state.json",
        emit=emitted.append,
    )
    envelope = next(e for e in emitted if e["kind"] == "tool.started")

    async def go():
        spine = EventSpine(str(tmp_path / "events.db"), retention_days=0)
        await spine.start()
        try:
            await spine.append(envelope, source="dsh:session-tail")
            return await coverage_report(spine.db_path)
        finally:
            await spine.close()

    report = asyncio.run(go())
    entries = {entry["harness"]: entry for entry in report["harnesses"]}
    # No registry entry: coverage derives L3 from the DSH telemetry event itself.
    assert entries["dsh"]["installed"] is False
    assert entries["dsh"]["fidelity"] == "L3"
    assert entries["dsh"]["events_24h"] == 1
    assert "dsh" in report["summary"]["L3"]


class _DoctorBoard:
    """Minimal board: health + canonical ingest + activity (no coverage)."""

    def __init__(self):
        self.events = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/api/health"):
                    self._send(200, {"ok": True})
                elif self.path.startswith("/api/activity"):
                    self._send(
                        200,
                        {"events": outer.events, "last_id": len(outer.events)},
                    )
                else:
                    self._send(404, {"error": "not_found"})

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length)
                try:
                    event = json.loads(raw)
                except ValueError:
                    self._send(400, {"error": "validation_error"})
                    return
                if self.path.startswith("/api/events"):
                    outer.events.append(event)
                    self._send(201, {"event": event})
                else:
                    self._send(404, {"error": "not_found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def doctor_board():
    board = _DoctorBoard()
    try:
        yield board
    finally:
        board.close()


def test_doctor_verifies_dsh_session_tail(tmp_path, home, doctor_board, monkeypatch):
    import time

    monkeypatch.setattr(installers, "current_platform", lambda: "linux")
    # The tail finds one real (generated) session so the readiness check is real.
    now_ms = int(time.time() * 1000)
    events = default_events()
    events[0] = header(createdAt=now_ms)
    for index, item in enumerate(events[1:], start=1):
        item["time"] = now_ms + index * 100
    write_session(home / ".dsh" / "sessions", events)
    installed = invoke_cli(tmp_path, "connect", "--harness", "dsh", "--yes")
    assert installed.exit_code == 0, installed.output

    registry_path = home / ".sugar" / "adapters.json"
    result = run_doctor(server=doctor_board.base_url, registry_path=registry_path)

    assert result.ok is True
    probe = result.adapters[0]
    assert probe.harness == "dsh"
    assert probe.transport == "session-tail"
    assert probe.delivered is True
    assert probe.verified is True
    assert any(check.name == "dsh session tail" for check in result.checks)
    stored = doctor_board.events[0]
    assert stored["kind"] == "session.started"
    assert stored["harness"] == "dsh"
    assert stored["payload"]["fidelity"] == "L3"
    assert stored["payload"]["payload"]["probe"]


@requires_zstd
class TestTailCLI:
    def _sessions(self, tmp_path):
        sessions = tmp_path / "sessions"
        write_session(sessions, default_events())
        return sessions

    def test_once_summary_and_state(self, tmp_path):
        sessions = self._sessions(tmp_path)
        state = tmp_path / "state.json"
        result = invoke_cli(
            tmp_path,
            "tail",
            "dsh",
            "--once",
            "--sessions-dir",
            str(sessions),
            "--state-file",
            str(state),
            "--url",
            f"http://127.0.0.1:{closed_port()}",
        )
        assert result.exit_code == 0, result.output
        assert "dsh tail:" in result.output
        assert "1 session" in result.output
        assert state.exists()

    def test_quiet_has_no_output(self, tmp_path):
        sessions = self._sessions(tmp_path)
        result = invoke_cli(
            tmp_path,
            "tail",
            "dsh",
            "--quiet",
            "--sessions-dir",
            str(sessions),
            "--state-file",
            str(tmp_path / "state.json"),
            "--url",
            f"http://127.0.0.1:{closed_port()}",
        )
        assert result.exit_code == 0
        assert result.output == ""

    def test_no_sessions_exits_zero(self, tmp_path):
        result = invoke_cli(
            tmp_path,
            "tail",
            "dsh",
            "--sessions-dir",
            str(tmp_path / "empty"),
            "--state-file",
            str(tmp_path / "state.json"),
        )
        assert result.exit_code == 0
        assert "no DSH sessions" in result.output

    def test_once_and_watch_are_mutually_exclusive(self, tmp_path):
        result = invoke_cli(tmp_path, "tail", "dsh", "--once", "--watch")
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output

    def test_invalid_since_exits_nonzero(self, tmp_path):
        sessions = self._sessions(tmp_path)
        result = invoke_cli(
            tmp_path,
            "tail",
            "dsh",
            "--sessions-dir",
            str(sessions),
            "--state-file",
            str(tmp_path / "state.json"),
            "--since",
            "yesterday",
        )
        assert result.exit_code != 0
        assert "invalid" in result.output.lower()

    def test_tail_help_lists_options(self, tmp_path):
        result = invoke_cli(tmp_path, "tail", "dsh", "--help")
        assert result.exit_code == 0
        for option in (
            "--once",
            "--watch",
            "--sessions-dir",
            "--state-file",
            "--interval",
            "--url",
            "--since",
            "--quiet",
        ):
            assert option in result.output
