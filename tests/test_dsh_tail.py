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
from sugar.dashboard.usage import aggregate_usage
from sugar.dsh.discover import decode_workspace, discover_sessions
from sugar.dsh.parse import (
    DshConfigError,
    DshParseError,
    ms_to_iso,
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
        fake_home = tmp_path / "home"
        fallback = fake_home / ".deepseek" / "sessions"
        write_session(fallback, default_events())
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("USERPROFILE", str(fake_home))
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

    def test_folder_fallback_id_does_not_replay(self, tmp_path):
        # The folder name is only a fallback; the header id is authoritative.
        # A mismatch must not look like a replaced log on every cycle.
        sessions = tmp_path / "sessions"
        write_session(sessions, default_events(), session_id="not-the-header-id")
        state_path = tmp_path / "state.json"
        first = tail_once(
            sessions_dir=sessions, state_path=state_path, emit=lambda e: None
        )
        assert first.events == 8
        emitted = []
        second = tail_once(
            sessions_dir=sessions, state_path=state_path, emit=emitted.append
        )
        assert second.events == 0
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
        assert "tail dsh --watch" in result.output
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


# ---------------------------------------------------------------------------
# DSH usage / token mapping
# ---------------------------------------------------------------------------


def usage_request_header(seq: int, model: str) -> dict:
    return event(
        "request/header",
        seq,
        {
            "header": "{'config': {'provider': 'deepseek-account', "
            f"'model': '{model}'}}",
            "reason": "initial",
        },
    )


def assistant_usage(
    seq: int,
    *,
    turn: int = 1,
    step: int = 1,
    input_tokens: int = 100,
    output_tokens: int = 20,
    cache_read: int = 0,
    cache_write: int = 0,
    total: int | None = None,
    event_type: str = "assistant/message",
) -> dict:
    usage = {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheReadTokens": cache_read,
        "cacheWriteTokens": cache_write,
        "totalTokens": (
            total
            if total is not None
            else input_tokens + output_tokens + cache_read + cache_write
        ),
    }
    return event(event_type, seq, {"turn": turn, "step": step, "usage": usage})


def usage_session(
    cwd: str = "/Users/me/Dev/app", model: str = "deepseek-flash"
) -> list:
    return [
        header(cwd=cwd),
        usage_request_header(0, model),
        event("turn/start", 1, {"turn": 1}),
        assistant_usage(
            2,
            step=1,
            input_tokens=100,
            output_tokens=20,
            total=120,
        ),
        assistant_usage(
            3,
            step=2,
            input_tokens=50,
            output_tokens=10,
            cache_read=200,
            cache_write=5,
            total=265,
        ),
        event("turn/end", 4, {"turn": 1, "reason": {"kind": "completed"}}),
    ]


@requires_zstd
class TestUsageMapping:
    def test_usage_reported_event_shape(self, tmp_path):
        parsed = parse_session_file(write_session(tmp_path, usage_session()))
        usage = [item for item in parsed.events if item.kind == "usage.reported"]
        assert len(usage) == 2

        envelope = usage[0].envelope
        assert envelope["kind"] == "usage.reported"
        assert envelope["harness"] == "dsh"
        assert envelope["actor"] == "dsh"
        assert envelope["actor_kind"] == "agent"
        assert envelope["source"] == "dsh:session-tail"
        assert envelope["session_id"] == SESSION_ID
        assert envelope["project"] == "app"
        assert envelope["ts"].endswith("Z")

        payload = envelope["payload"]
        assert payload["fidelity"] == "L2"
        assert payload["cwd"] == "/Users/me/Dev/app"
        assert payload["model"] == "deepseek-flash"
        assert payload["turn_id"] == "1"
        # Flat fields make the event visible to /api/usage's aggregator.
        assert payload["tokens_in"] == 100
        assert payload["tokens_out"] == 20
        assert payload["tokens_total"] == 120
        # Canonical summary stays under payload.payload.
        summary = payload["payload"]
        assert summary["tokens_in"] == 100
        assert summary["tokens_out"] == 20
        assert summary["tokens_total"] == 120
        assert summary["input_tokens"] == 100
        assert summary["step"] == 1
        assert summary["project"] == "app"
        assert summary["usage_source"] == "assistant/usage"

    def test_cached_prompt_tokens_are_included_in_tokens_in(self, tmp_path):
        parsed = parse_session_file(write_session(tmp_path, usage_session()))
        usage = [
            item.envelope for item in parsed.events if item.kind == "usage.reported"
        ]

        cached = usage[1]["payload"]["payload"]
        # tokens_in = fresh input + cache read + cache write so that
        # tokens_in + tokens_out == the provider's totalTokens.
        assert cached["input_tokens"] == 50
        assert cached["cache_read_tokens"] == 200
        assert cached["cache_write_tokens"] == 5
        assert cached["tokens_in"] == 255
        assert cached["tokens_out"] == 10
        assert cached["tokens_total"] == 265
        assert cached["provider_total_tokens"] == 265
        assert cached["tokens_total"] == cached["provider_total_tokens"]

    def test_usage_is_incremental_and_sums_to_provider_total(self, tmp_path):
        parsed = parse_session_file(write_session(tmp_path, usage_session()))
        usage = [
            item.envelope for item in parsed.events if item.kind == "usage.reported"
        ]
        totals = aggregate_usage(usage)["totals"]

        assert totals["events"] == 2
        assert totals["tokens_in"] == 100 + 255
        assert totals["tokens_out"] == 20 + 10
        assert totals["tokens_total"] == 120 + 265
        provider_sum = sum(
            envelope["payload"]["payload"]["provider_total_tokens"]
            for envelope in usage
        )
        assert totals["tokens_total"] == provider_sum

    def test_usage_events_appear_in_usage_aggregator(self, tmp_path):
        parsed = parse_session_file(write_session(tmp_path, usage_session()))
        usage = [
            item.envelope for item in parsed.events if item.kind == "usage.reported"
        ]
        report = aggregate_usage(usage, group_by="actor")
        assert report["totals"]["events"] == 2
        assert report["totals"]["tokens_total"] == 385
        assert report["groups"][0]["key"] == "dsh"
        assert report["groups"][0]["tokens_total"] == 385

    def test_usage_is_not_emitted_twice_for_attempt_and_message(self, tmp_path):
        events = [
            header(),
            usage_request_header(0, "deepseek-flash"),
            event("turn/start", 1, {"turn": 1}),
            # Retried/completed attempt and final message mirror the same usage.
            assistant_usage(2, step=1, total=120, event_type="assistant/attempt"),
            assistant_usage(3, step=1, total=120, event_type="assistant/message"),
            event("turn/end", 4, {"turn": 1}),
        ]
        parsed = parse_session_file(write_session(tmp_path, events))
        usage = [item for item in parsed.events if item.kind == "usage.reported"]
        assert len(usage) == 1
        assert usage[0].envelope["payload"]["payload"]["tokens_total"] == 120

    def test_usage_from_attempt_only(self, tmp_path):
        events = [
            header(),
            usage_request_header(0, "deepseek-flash"),
            assistant_usage(
                1,
                input_tokens=30,
                output_tokens=20,
                total=50,
                event_type="assistant/attempt",
            ),
        ]
        parsed = parse_session_file(write_session(tmp_path, events))
        usage = [item for item in parsed.events if item.kind == "usage.reported"]
        assert len(usage) == 1
        assert usage[0].envelope["payload"]["payload"]["tokens_total"] == 50

    def test_assistant_without_usage_is_ignored(self, tmp_path):
        events = [
            header(),
            event("assistant/message", 0, {"turn": 1, "step": 1}),
            event("assistant/attempt", 1, {"turn": 1, "step": 1, "stream": []}),
            event("turn/start", 2, {"turn": 1}),
        ]
        parsed = parse_session_file(write_session(tmp_path, events))
        assert [item.kind for item in parsed.events] == ["turn.started"]

    def test_usage_model_propagation_and_change(self, tmp_path):
        events = [
            header(),
            usage_request_header(0, "deepseek-flash"),
            assistant_usage(1, step=1, total=120),
            usage_request_header(2, "deepseek-pro"),
            assistant_usage(3, step=2, total=60),
        ]
        parsed = parse_session_file(write_session(tmp_path, events))
        usage = [
            item.envelope for item in parsed.events if item.kind == "usage.reported"
        ]
        # Canonical model lives at payload.model; usage summary stays model-free.
        assert [event["payload"]["model"] for event in usage] == [
            "deepseek-flash",
            "deepseek-pro",
        ]
        assert all("model" not in event["payload"]["payload"] for event in usage)

    def test_usage_project_derived_from_cwd(self, tmp_path):
        app = parse_session_file(
            write_session(tmp_path / "app", usage_session(cwd="/Users/me/Dev/my-app"))
        )
        usage = [item.envelope for item in app.events if item.kind == "usage.reported"]
        assert usage and usage[0]["project"] == "my-app"
        assert usage[0]["payload"]["payload"]["project"] == "my-app"

        root = parse_session_file(
            write_session(tmp_path / "root", usage_session(cwd="/"))
        )
        root_usage = [
            item.envelope for item in root.events if item.kind == "usage.reported"
        ]
        assert root_usage and root_usage[0]["project"] is None

    def test_usage_tolerates_strings_and_invalid_cache(self, tmp_path):
        events = [
            header(),
            usage_request_header(0, "deepseek-flash"),
            event(
                "assistant/message",
                1,
                {
                    "turn": 1,
                    "step": 1,
                    "usage": {
                        "inputTokens": "100",
                        "outputTokens": "20",
                        "cacheReadTokens": -3,
                        "cacheWriteTokens": "5",
                        "totalTokens": "120",
                    },
                },
            ),
            event(
                "assistant/message",
                2,
                {
                    "turn": 1,
                    "step": 2,
                    "usage": {"inputTokens": None, "outputTokens": "abc"},
                },
            ),
        ]
        parsed = parse_session_file(write_session(tmp_path, events))
        usage = [
            item.envelope for item in parsed.events if item.kind == "usage.reported"
        ]
        assert len(usage) == 1
        summary = usage[0]["payload"]["payload"]
        assert summary["input_tokens"] == 100
        assert summary["tokens_in"] == 105  # -3 cache read ignored, +5 cache write
        assert summary["tokens_out"] == 20
        assert summary["tokens_total"] == 125

    def test_compaction_summary_usage_is_not_counted(self, tmp_path):
        events = [
            header(),
            event(
                "compaction/summary",
                0,
                {
                    "usage": {
                        "inputTokens": 513,
                        "outputTokens": 2924,
                        "cacheReadTokens": 590080,
                        "totalTokens": 593517,
                    }
                },
            ),
        ]
        parsed = parse_session_file(write_session(tmp_path, events))
        assert [item for item in parsed.events if item.kind == "usage.reported"] == []

    def test_usage_tail_checkpoint_prevents_double_counting(self, tmp_path):
        sessions = tmp_path / "sessions"
        write_session(sessions, usage_session())
        state_path = tmp_path / "state.json"
        emitted = []

        first = tail_once(
            sessions_dir=sessions, state_path=state_path, emit=emitted.append
        )
        usage = [event for event in emitted if event["kind"] == "usage.reported"]
        assert first.events == 5  # header + turn.started + 2 usage + turn.ended
        assert len(usage) == 2
        assert aggregate_usage(usage)["totals"]["tokens_total"] == 385

        second_emitted = []
        second = tail_once(
            sessions_dir=sessions,
            state_path=state_path,
            emit=second_emitted.append,
        )
        assert second.events == 0
        assert second_emitted == []

    def test_usage_manual_duplicate_seq_is_not_reported_twice(self, tmp_path):
        duplicate = assistant_usage(2, step=1, total=120)
        events = [
            header(),
            usage_request_header(0, "deepseek-flash"),
            duplicate,
            duplicate,
        ]
        parsed = parse_session_file(write_session(tmp_path, events))
        usage = [item for item in parsed.events if item.kind == "usage.reported"]
        assert len(usage) == 1

    def test_usage_real_log_smoke(self):
        import glob

        files = sorted(
            glob.glob(
                os.path.expanduser("~/.dsh/sessions/**/session.v4.jsonl.zstd"),
                recursive=True,
            )
        )
        if not files:
            pytest.skip("no real DSH session logs on this host")
        checked = 0
        seen_usage = 0
        for path in files[:20]:
            try:
                parsed = parse_session_file(Path(path))
            except DshParseError:
                continue
            usage = [item for item in parsed.events if item.kind == "usage.reported"]
            if not usage:
                continue
            checked += 1
            seen_usage += len(usage)
            for item in usage:
                envelope = item.envelope
                assert envelope["kind"] == "usage.reported"
                assert envelope["harness"] == "dsh"
                assert envelope["payload"]["fidelity"] == "L2"
                summary = envelope["payload"]["payload"]
                assert isinstance(summary["tokens_in"], int)
                assert isinstance(summary["tokens_out"], int)
                assert summary["tokens_in"] >= 0 and summary["tokens_out"] >= 0
                assert (
                    summary["tokens_total"]
                    == summary["tokens_in"] + summary["tokens_out"]
                )
                if "provider_total_tokens" in summary:
                    assert summary["tokens_total"] == summary["provider_total_tokens"]
            if checked >= 3:
                break
        if checked == 0:
            pytest.skip("no real DSH logs with usage on this host")
        assert seen_usage > 0


# ---------------------------------------------------------------------------
# Trace enrichment: subagent / team / artifact events
# ---------------------------------------------------------------------------


TEAM_ID = "session-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
MEMBER_ID = "11111111-aaaa-bbbb-cccc-222222222222"
CHILD_ID = "99999999-aaaa-bbbb-cccc-000000000000"


def team_member_event(
    seq: int, member_id: str = MEMBER_ID, name: str = "researcher"
) -> dict:
    return event(
        "team/member",
        seq,
        {
            "version": 2,
            "teamId": TEAM_ID,
            "member": {
                "id": member_id,
                "name": name,
                "description": "research the spec",
                "provider": "spawn",
                "context": "fresh",
                "phase": "active",
            },
        },
    )


def team_task_event(
    seq: int,
    *,
    task_id: str = "task-1",
    revision: int = 1,
    status: str = "pending",
    owner_id: str | None = None,
    blocked_by: list | None = None,
    subject: str = "Research the spec",
) -> dict:
    task = {
        "id": task_id,
        "revision": revision,
        "subject": subject,
        "description": "long description",
        "status": status,
        "blockedBy": blocked_by or [],
        "writeScopes": ["docs/spec.md"],
    }
    if owner_id:
        task["ownerId"] = owner_id
    return event("team/task", seq, {"version": 2, "teamId": TEAM_ID, "task": task})


def catalog_event(
    seq: int,
    *,
    child_id: str = CHILD_ID,
    created_ms: int = 1_793_893_387_561,
    label: str = "Research the spec",
    mode: str = "continuable",
) -> dict:
    return event(
        "subagent/catalog",
        seq,
        {
            "version": 0,
            "childId": child_id,
            "childCreatedAt": created_ms,
            "mode": mode,
            "label": label,
        },
    )


def deliverables_event(
    seq: int,
    files: list,
    *,
    call_id: str = "call_present",
    turn: int = 13,
) -> dict:
    return event(
        "deliverables/presented",
        seq,
        {"turn": turn, "callId": call_id, "files": files},
    )


def file_tool_events(
    seq: int,
    *,
    call_id: str = "call_write",
    name: str = "write",
    meta: dict | None = None,
    is_error: bool = False,
    turn: int = 1,
) -> list:
    call = event(
        "tool/call",
        seq,
        {
            "turn": turn,
            "step": 1,
            "callId": call_id,
            "name": name,
            "arguments": json.dumps({"file_path": "/tmp/out.py"}),
        },
    )
    result = event(
        "tool/result",
        seq + 1,
        {
            "turn": turn,
            "step": 1,
            "toolCallId": call_id,
            "content": [{"type": "text", "text": "ok" if not is_error else "boom"}],
            "isError": is_error,
            "meta": meta,
        },
    )
    return [call, result]


@requires_zstd
class TestTraceEnrichment:
    def _parse(self, tmp_path, events):
        return parse_session_file(write_session(tmp_path, events))

    def test_subagent_started_event(self, tmp_path):
        events = [
            header(),
            catalog_event(0),
            catalog_event(1),  # duplicate catalog entry must not double-emit
        ]
        parsed = self._parse(tmp_path, events)
        subagents = [item for item in parsed.events if item.kind == "subagent.started"]
        assert len(subagents) == 1
        envelope = subagents[0].envelope
        assert envelope["harness"] == "dsh"
        assert envelope["actor"] == "dsh"
        assert envelope["source"] == "dsh:session-tail"
        assert envelope["session_id"] == SESSION_ID
        assert envelope["project"] == "app"
        assert envelope["payload"]["fidelity"] == "L3"
        summary = envelope["payload"]["payload"]
        assert summary["parent_session_id"] == SESSION_ID
        assert summary["child_session_id"] == CHILD_ID
        assert summary["label"] == "Research the spec"
        assert summary["mode"] == "continuable"
        assert summary["created_at"] == ms_to_iso(1_793_893_387_561)
        assert summary["created_at"].endswith("Z")

    def test_subagent_started_requires_child_id(self, tmp_path):
        events = [header(), event("subagent/catalog", 0, {"mode": "continuable"})]
        parsed = self._parse(tmp_path, events)
        assert [item for item in parsed.events if item.kind == "subagent.started"] == []

    def test_team_task_events_and_owner_resolution(self, tmp_path):
        events = [
            header(),
            team_member_event(0),
            team_task_event(1, revision=1, status="pending"),
            team_task_event(
                2,
                revision=2,
                status="in_progress",
                owner_id=MEMBER_ID,
            ),
            team_task_event(
                3,
                revision=2,
                status="in_progress",
                owner_id=MEMBER_ID,
            ),  # duplicate revision
        ]
        parsed = self._parse(tmp_path, events)
        tasks = [item for item in parsed.events if item.kind == "team.task"]
        assert len(tasks) == 2
        first = tasks[0].envelope
        assert first["payload"]["fidelity"] == "L2"
        assert first["session_id"] == SESSION_ID
        summary = first["payload"]["payload"]
        assert summary["team_id"] == TEAM_ID
        assert summary["team_task_id"] == "task-1"
        assert summary["subject"] == "Research the spec"
        assert summary["status"] == "pending"
        assert summary["parent_session_id"] == TEAM_ID
        assert summary["revision"] == 1
        assert "owner_id" not in summary
        second = tasks[1].envelope["payload"]["payload"]
        assert second["status"] == "in_progress"
        assert second["owner_id"] == MEMBER_ID
        assert second["owner_name"] == "researcher"
        assert second["revision"] == 2

    def test_team_task_owner_resolved_when_member_arrives_later(self, tmp_path):
        events = [
            header(),
            team_task_event(0, owner_id=MEMBER_ID, status="in_progress"),
            team_member_event(1),
        ]
        parsed = self._parse(tmp_path, events)
        task = next(item for item in parsed.events if item.kind == "team.task")
        assert task.envelope["payload"]["payload"]["owner_name"] == "researcher"

    def test_team_task_blocked_by_and_missing_fields(self, tmp_path):
        events = [
            header(),
            team_task_event(0, blocked_by=["task-2", "task-3"]),
            event("team/task", 1, {"teamId": TEAM_ID, "task": {"id": "task-9"}}),
            event("team/task", 2, {"teamId": TEAM_ID, "task": {}}),
        ]
        parsed = self._parse(tmp_path, events)
        tasks = [item for item in parsed.events if item.kind == "team.task"]
        assert len(tasks) == 2
        blocked = tasks[0].envelope["payload"]["payload"]
        assert blocked["blocked_by"] == ["task-2", "task-3"]
        bare = tasks[1].envelope["payload"]["payload"]
        assert bare["team_task_id"] == "task-9"

    def test_artifact_created_from_deliverables(self, tmp_path):
        files = [
            {"path": "/tmp/report.pdf", "description": "final report"},
            {"path": "/tmp/data.csv"},
        ]
        events = [
            header(),
            event("turn/start", 0, {"turn": 13}),
            deliverables_event(1, files),
            deliverables_event(2, files),  # exact duplicate must not double-emit
        ]
        parsed = self._parse(tmp_path, events)
        artifacts = [item for item in parsed.events if item.kind == "artifact.created"]
        assert len(artifacts) == 2
        first = artifacts[0].envelope
        assert first["payload"]["fidelity"] == "L3"
        assert first["payload"]["turn_id"] == "13"
        assert first["payload"]["tool_use_id"] == "call_present"
        summary = first["payload"]["payload"]
        assert summary["path"] == "/tmp/report.pdf"
        assert summary["description"] == "final report"
        assert summary["turn"] == 13
        assert summary["tool_call_id"] == "call_present"
        assert artifacts[1].envelope["payload"]["payload"]["path"] == "/tmp/data.csv"

    def test_artifact_created_from_write_result(self, tmp_path):
        events = [
            header(),
            *file_tool_events(
                0, meta={"operation": "create", "path": "/tmp/out.py", "diffs": []}
            ),
        ]
        parsed = self._parse(tmp_path, events)
        artifacts = [item for item in parsed.events if item.kind == "artifact.created"]
        assert len(artifacts) == 1
        summary = artifacts[0].envelope["payload"]["payload"]
        assert summary["path"] == "/tmp/out.py"
        assert summary["operation"] == "create"
        assert summary["turn"] == 1
        assert summary["tool_call_id"] == "call_write"
        assert artifacts[0].envelope["payload"]["fidelity"] == "L3"

    def test_failed_write_result_emits_no_artifact(self, tmp_path):
        events = [
            header(),
            *file_tool_events(
                0,
                meta={"operation": "create", "path": "/tmp/out.py"},
                is_error=True,
            ),
        ]
        parsed = self._parse(tmp_path, events)
        assert [item for item in parsed.events if item.kind == "artifact.created"] == []

    def test_workspace_changed_from_edit_result(self, tmp_path):
        diff = {
            "path": "/tmp/edited.py",
            "oldText": "x" * 5000,
            "newText": "y" * 5000,
        }
        events = [
            header(),
            *file_tool_events(
                0,
                name="edit",
                meta={"path": "/tmp/edited.py", "diffs": [diff]},
            ),
        ]
        parsed = self._parse(tmp_path, events)
        changed = [item for item in parsed.events if item.kind == "workspace.changed"]
        assert len(changed) == 1
        summary = changed[0].envelope["payload"]["payload"]
        assert summary["paths"] == ["/tmp/edited.py"]
        assert summary["operation"] == "edit"
        assert summary["diff_count"] == 1
        # No file contents in the emitted evidence.
        serialized = json.dumps(changed[0].envelope)
        assert "x" * 5000 not in serialized
        assert "y" * 5000 not in serialized

    def test_artifact_deduped_across_write_and_presented(self, tmp_path):
        events = [
            header(),
            *file_tool_events(
                0,
                meta={"operation": "create", "path": "/tmp/shared.py", "diffs": []},
            ),
            deliverables_event(
                2,
                [{"path": "/tmp/shared.py", "description": "final script"}],
                call_id="call_present",
                turn=1,
            ),
        ]
        parsed = self._parse(tmp_path, events)
        artifacts = [item for item in parsed.events if item.kind == "artifact.created"]
        assert len(artifacts) == 1
        summary = artifacts[0].envelope["payload"]["payload"]
        assert summary["path"] == "/tmp/shared.py"
        assert summary["operation"] == "create"
        assert summary["description"] == "final script"
        assert summary["tool_call_id"] == "call_write"

    def test_enrichment_tail_checkpoint_no_duplicates(self, tmp_path):
        sessions = tmp_path / "sessions"
        events = [
            header(),
            catalog_event(0),
            team_member_event(1),
            team_task_event(2, owner_id=MEMBER_ID),
            deliverables_event(3, [{"path": "/tmp/report.pdf"}]),
        ]
        write_session(sessions, events)
        state_path = tmp_path / "state.json"
        emitted = []
        first = tail_once(
            sessions_dir=sessions, state_path=state_path, emit=emitted.append
        )
        kinds = [event["kind"] for event in emitted]
        assert "subagent.started" in kinds
        assert "team.task" in kinds
        assert "artifact.created" in kinds
        assert kinds.count("subagent.started") == 1
        assert kinds.count("team.task") == 1
        assert kinds.count("artifact.created") == 1

        second_emitted = []
        second = tail_once(
            sessions_dir=sessions, state_path=state_path, emit=second_emitted.append
        )
        assert first.events == len(emitted)
        assert second.events == 0
        assert second_emitted == []

    def test_enrichment_missing_fields_tolerated(self, tmp_path):
        events = [
            header(),
            event(
                "deliverables/presented",
                0,
                {"turn": 1, "callId": "c", "files": "not-a-list"},
            ),
            event(
                "deliverables/presented",
                1,
                {
                    "turn": 2,
                    "callId": "c",
                    "files": [{"description": "no path"}, "junk"],
                },
            ),
            *file_tool_events(2, meta={"operation": "create"}),
            *file_tool_events(4, name="edit", meta={}),
        ]
        parsed = self._parse(tmp_path, events)
        assert [item for item in parsed.events if item.kind == "artifact.created"] == []
        assert [
            item for item in parsed.events if item.kind == "workspace.changed"
        ] == []

    def test_session_started_adds_parent_session_id(self, tmp_path):
        parent = "session-99999999-1111-2222-3333-444444444444"
        events = [header(origin="subagent", parentSession=parent, delegationDepth=1)]
        parsed = self._parse(tmp_path, events)
        summary = parsed.header_event["payload"]["payload"]
        assert summary["origin"] == "subagent"
        assert summary["parent_session"] == parent
        assert summary["parent_session_id"] == parent
        assert summary["delegation_depth"] == 1

    def test_tail_delivers_all_artifacts_from_one_deliverables_event(self, tmp_path):
        sessions = tmp_path / "sessions"
        write_session(
            sessions,
            [
                header(),
                deliverables_event(
                    0,
                    [{"path": "/tmp/a.pdf"}, {"path": "/tmp/b.pdf"}],
                ),
            ],
        )
        emitted = []
        result = tail_once(
            sessions_dir=sessions,
            state_path=tmp_path / "state.json",
            emit=emitted.append,
        )
        artifacts = [event for event in emitted if event["kind"] == "artifact.created"]
        assert len(artifacts) == 2
        assert {event["payload"]["payload"]["path"] for event in artifacts} == {
            "/tmp/a.pdf",
            "/tmp/b.pdf",
        }
        assert result.events == len(emitted)

    def test_tail_delivers_tool_result_artifact_next_to_tool_event(self, tmp_path):
        sessions = tmp_path / "sessions"
        write_session(
            sessions,
            [
                header(),
                *file_tool_events(
                    0,
                    meta={"operation": "create", "path": "/tmp/out.py", "diffs": []},
                ),
            ],
        )
        emitted = []
        result = tail_once(
            sessions_dir=sessions,
            state_path=tmp_path / "state.json",
            emit=emitted.append,
        )
        kinds = [event["kind"] for event in emitted]
        assert kinds.count("session.started") == 1
        assert kinds.count("tool.started") == 1
        assert kinds.count("tool.completed") == 1
        assert kinds.count("artifact.created") == 1
        assert result.events == len(emitted) == 4

    def test_real_log_enrichment_smoke(self):
        import glob

        files = sorted(
            glob.glob(
                os.path.expanduser("~/.dsh/sessions/**/session.v4.jsonl.zstd"),
                recursive=True,
            )
        )
        if not files:
            pytest.skip("no real DSH session logs on this host")
        seen_subagent = 0
        seen_artifact = 0
        seen_team_task = 0
        for path in files[:35]:
            try:
                parsed = parse_session_file(Path(path))
            except DshParseError:
                continue
            for item in parsed.events:
                summary = item.envelope["payload"]["payload"]
                if item.kind == "subagent.started":
                    seen_subagent += 1
                    assert summary["child_session_id"]
                    assert summary["parent_session_id"]
                    assert summary["created_at"].endswith("Z")
                elif item.kind == "artifact.created":
                    seen_artifact += 1
                    assert summary["path"]
                elif item.kind == "team.task":
                    seen_team_task += 1
                    assert summary["team_task_id"]
                    assert summary["status"] in {"pending", "in_progress", "completed"}
            if seen_subagent and seen_artifact and seen_team_task:
                break
        if not (seen_subagent or seen_artifact or seen_team_task):
            pytest.skip("no enrichment-bearing DSH logs on this host")
        assert seen_subagent > 0
        assert seen_artifact > 0
        assert seen_team_task > 0
