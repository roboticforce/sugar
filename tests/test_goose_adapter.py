"""Tests for the Phase-1a Goose adapter (hook plugin, L3) and normalizer.

Goose 1.46 discovers hook plugins at ``~/.agents/plugins/<name>/``. These tests
cover: detection/connect integration, the plugin installer (idempotent, backups,
preserves user files), the normalizer mappings for every Goose hook event, the
coverage entry, the ``sugar hook --harness goose`` shim path, and the source
copy under ``integrations/goose-plugin/``.

No real Goose is installed or executed: HOME/PATH are isolated and the plugin
launcher is injected. Unix-socket tests skip on Windows.
"""

import asyncio
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from sugar.connect import detect, installers, registry
from sugar.dashboard.spine import EventSpine
from sugar.main import cli
from sugar.telemetry.coverage import coverage_report
from sugar.telemetry.normalize import (
    KNOWN_HARNESSES,
    normalize_harness_name,
    normalize_hook,
)
from sugar.telemetry.pipeline import TelemetryPipeline
from sugar.telemetry.schema import TelemetryValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
GOOSE_SOURCE = REPO_ROOT / "integrations" / "goose-plugin"
FAKE_SUGAR = "/opt/sugar/bin/sugar"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated HOME + deterministic detection (no PATH binaries, no /Apps)."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("PATH", "")
    for variable in (
        "SUGAR_ADAPTERS_PATH",
        "SUGAR_ADAPTERS_FILE",
        "SUGAR_BOARD_URL",
        "SUGAR_INGEST_SOCKET",
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
    monkeypatch.setattr(installers, "sugar_launcher", lambda: FAKE_SUGAR)
    return home_dir


def goose_detected(home: Path) -> None:
    (home / ".config" / "goose").mkdir(parents=True, exist_ok=True)


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


def invoke_cli(tmp_path: Path, *args, input_text=None):
    runner = CliRunner()
    return runner.invoke(
        cli, ["--config", str(cli_config(tmp_path)), *args], input=input_text
    )


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def goose_hook_command() -> str:
    return f"{FAKE_SUGAR} hook --harness goose"


class SocketCapture:
    """Minimal AF_UNIX listener recording newline-delimited frames."""

    def __init__(self, path: Path):
        self.path = str(path)
        self.messages = []
        self._server = None
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        if os.name == "nt" or not hasattr(socket, "AF_UNIX"):
            pytest.skip("AF_UNIX socket tests are POSIX-only")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.path)
        self._server.listen(5)
        self._server.settimeout(0.1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def _serve(self):
        while not self._stop.is_set():
            try:
                connection, _address = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(0.5)
                chunks = []
                try:
                    while True:
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                        if b"\n" in chunk:
                            break
                except socket.timeout:
                    pass
                self.messages.append(b"".join(chunks))

    def wait_for_message(self, timeout: float = 2.0) -> bytes:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.messages:
                return self.messages[0]
            time.sleep(0.01)
        raise AssertionError("no socket message received")

    def close(self):
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        try:
            os.unlink(self.path)
        except OSError:
            pass


@pytest.fixture
def socket_capture():
    import shutil
    import tempfile

    directory = tempfile.mkdtemp(prefix="sgg-")
    capture = SocketCapture(Path(directory) / "s").start()
    try:
        yield capture
    finally:
        capture.close()
        shutil.rmtree(directory, ignore_errors=True)


# ---------------------------------------------------------------------------
# Detection + connect matrix
# ---------------------------------------------------------------------------


class TestGooseDetection:
    def test_goose_is_l3_hook_plugin_and_installable(self):
        spec = detect.HARNESS_SPECS[detect.GOOSE]
        assert spec.fidelity == "L3"
        assert spec.transport == "hook-plugin"
        assert spec.installable is True
        assert detect.GOOSE in detect.INSTALLABLE_HARNESSES

    def test_dsh_stays_detect_only(self):
        spec = detect.HARNESS_SPECS[detect.DSH]
        assert spec.fidelity == "L0"
        assert spec.transport is None
        assert spec.installable is False

    def test_detects_goose_config_dir(self, home):
        goose_detected(home)
        detection = detect.detect_harness(detect.GOOSE, home=home, path="")
        assert detection.detected is True
        assert detection.installable is True
        assert detection.fidelity == "L3"
        assert detection.transport == "hook-plugin"

    def test_connect_check_shows_goose_install_and_dsh_detect_only(
        self, home, tmp_path
    ):
        goose_detected(home)
        (home / "Applications" / "DeepSeek Harness.app").mkdir(parents=True)
        result = invoke_cli(tmp_path, "connect", "--check")
        assert result.exit_code == 0, result.output
        lines = {
            line.split()[0]: line
            for line in result.output.splitlines()
            if line.strip().startswith(("goose", "dsh"))
        }
        assert "install" in lines["goose"]
        assert "detect-only" in lines["dsh"]
        assert "hook-plugin" in lines["goose"]
        assert not (home / ".agents").exists()

    def test_connect_harness_goose_installs_and_registers(self, home, tmp_path):
        goose_detected(home)
        result = invoke_cli(tmp_path, "connect", "--harness", "goose", "--yes")
        assert result.exit_code == 0, result.output
        assert "goose: installed" in result.output

        plugin_dir = home / ".agents" / "plugins" / "sugar-telemetry"
        assert (plugin_dir / "plugin.json").exists()
        assert (plugin_dir / "hooks" / "hooks.json").exists()

        reg = read_json(home / ".sugar" / "adapters.json")
        entry = reg["adapters"]["goose"]
        assert entry["installed"] is True
        assert entry["fidelity"] == "L3"
        assert entry["transport"] == "hook-plugin"
        assert entry["display_name"] == "Goose"

    def test_connect_goose_is_idempotent(self, home, tmp_path):
        goose_detected(home)
        invoke_cli(tmp_path, "connect", "--harness", "goose", "--yes")
        result = invoke_cli(tmp_path, "connect", "--harness", "goose", "--yes")
        assert result.exit_code == 0, result.output
        assert "unchanged" in result.output

    def test_connect_dry_run_writes_nothing(self, home, tmp_path):
        goose_detected(home)
        result = invoke_cli(tmp_path, "connect", "--dry-run", "--harness", "goose")
        assert result.exit_code == 0, result.output
        assert "would install" in result.output
        assert not (home / ".agents").exists()
        assert not (home / ".sugar" / "adapters.json").exists()


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------


class TestGooseInstaller:
    def test_fresh_install_writes_plugin_and_hooks(self, home):
        result = installers.install(detect.GOOSE, home=home)

        assert result.status == "installed"
        plugin_dir = home / ".agents" / "plugins" / "sugar-telemetry"
        assert result.config_path == plugin_dir / "hooks" / "hooks.json"

        manifest = read_json(plugin_dir / "plugin.json")
        assert manifest["name"] == "sugar-telemetry"
        assert manifest["version"] == installers.GOOSE_PLUGIN_VERSION
        assert manifest["description"]

        hooks = read_json(plugin_dir / "hooks" / "hooks.json")["hooks"]
        assert set(hooks) == set(installers.GOOSE_EVENTS)
        for event in installers.GOOSE_EVENTS:
            entry = hooks[event]
            assert entry["matcher"] == ".*"
            assert entry["hooks"] == [
                {
                    "type": "command",
                    "command": goose_hook_command(),
                    "timeout": 5,
                }
            ]

    def test_idempotent_second_install(self, home):
        first = installers.install(detect.GOOSE, home=home)
        hooks_path = first.config_path
        content = hooks_path.read_text()

        second = installers.install(detect.GOOSE, home=home)

        assert second.status == "unchanged"
        assert hooks_path.read_text() == content

    def test_preserves_user_files_and_writes_backups(self, home):
        plugin_dir = home / ".agents" / "plugins" / "sugar-telemetry"
        plugin_path = plugin_dir / "plugin.json"
        hooks_path = plugin_dir / "hooks" / "hooks.json"
        write_json(
            plugin_path,
            {"name": "sugar-telemetry", "custom": "keep-me", "version": "0.0.1"},
        )
        write_json(
            hooks_path,
            {
                "hooks": {
                    "Stop": {
                        "matcher": "Bash|Edit",
                        "hooks": [{"type": "command", "command": "user-stop-hook"}],
                    },
                    "CustomEvent": {"hooks": []},
                },
                "user_top_level": True,
            },
        )
        unrelated = plugin_dir / "notes.txt"
        unrelated.write_text("do not touch", encoding="utf-8")
        previous_plugin = plugin_path.read_text()
        previous_hooks = hooks_path.read_text()

        result = installers.install(detect.GOOSE, home=home)

        assert result.status == "updated"
        assert plugin_path.with_name("plugin.json.bak").read_text() == previous_plugin
        assert hooks_path.with_name("hooks.json.bak").read_text() == previous_hooks
        assert unrelated.read_text() == "do not touch"

        manifest = read_json(plugin_path)
        assert manifest["custom"] == "keep-me"
        assert manifest["version"] == installers.GOOSE_PLUGIN_VERSION

        hooks = read_json(hooks_path)
        assert hooks["user_top_level"] is True
        assert "CustomEvent" in hooks["hooks"]
        stop = hooks["hooks"]["Stop"]
        assert stop["matcher"] == "Bash|Edit"  # user's matcher preserved
        commands = [hook["command"] for hook in stop["hooks"]]
        assert "user-stop-hook" in commands
        assert goose_hook_command() in commands

    def test_launcher_path_change_updates_command_without_duplicates(
        self, home, monkeypatch
    ):
        installers.install(detect.GOOSE, home=home)
        monkeypatch.setattr(installers, "sugar_launcher", lambda: "/new/sugar")
        result = installers.install(detect.GOOSE, home=home)

        assert result.status == "updated"
        hooks = read_json(installers.goose_hooks_path(home))["hooks"]
        for event in installers.GOOSE_EVENTS:
            commands = [hook["command"] for hook in hooks[event]["hooks"]]
            assert commands == ["/new/sugar hook --harness goose"]

    def test_dry_run_writes_nothing(self, home):
        result = installers.install(detect.GOOSE, home=home, dry_run=True)
        assert result.status == "would-install"
        assert not (home / ".agents").exists()
        assert not (home / ".sugar").exists()

        installers.install(detect.GOOSE, home=home)
        hooks_path = installers.goose_hooks_path(home)
        before = hooks_path.read_text()
        dry = installers.install(detect.GOOSE, home=home, dry_run=True)
        assert dry.status == "unchanged"
        assert hooks_path.read_text() == before

    def test_invalid_hooks_json_is_refused(self, home):
        hooks_path = installers.goose_hooks_path(home)
        hooks_path.parent.mkdir(parents=True)
        hooks_path.write_text("{not json", encoding="utf-8")

        with pytest.raises(installers.InstallError):
            installers.install(detect.GOOSE, home=home)
        assert hooks_path.read_text() == "{not json"


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


class TestGooseNormalizer:
    def test_alias_and_known_harness(self):
        assert "goose" in KNOWN_HARNESSES
        assert "dsh" in KNOWN_HARNESSES
        assert normalize_harness_name("goose") == "goose"
        assert normalize_harness_name("Goose") == "goose"
        assert normalize_harness_name("block-goose") == "goose"
        assert normalize_harness_name("dsh") == "dsh"
        assert normalize_harness_name("deepseek-harness") == "dsh"

    @pytest.mark.parametrize(
        "event,tool,kind",
        [
            ("PreToolUse", "Bash", "tool.started"),
            ("PostToolUse", "Bash", "tool.completed"),
            ("PreToolUseResult", "Bash", "tool.completed"),
            ("AfterShellExecution", "Bash", "tool.completed"),
            ("PostToolUseFailure", "Bash", "tool.failed"),
            ("BeforeReadFile", "Read", "tool.started"),
            ("AfterFileEdit", "Write", "tool.completed"),
        ],
    )
    def test_tool_events_map_to_l3(self, event, tool, kind):
        normalized = normalize_hook(
            "goose",
            {
                "event": event,
                "tool_name": "shell",
                "tool_input": {"command": "pytest -q"},
                "tool_call_id": "call-1",
            },
        )
        assert normalized is not None
        assert normalized.harness == "goose"
        assert normalized.kind == kind
        assert normalized.fidelity == "L3"
        assert normalized.tool_name == tool
        assert normalized.tool_use_id == "call-1"

    @pytest.mark.parametrize(
        "event,payload,kind,fidelity",
        [
            ("SessionStart", {"session_id": "s1"}, "session.started", "L1"),
            ("SessionEnd", {"session_id": "s1"}, "session.ended", "L1"),
            ("UserPromptSubmit", {"message": "hi"}, "turn.started", "L1"),
            ("Stop", {"session_id": "s1"}, "run.ended", "L1"),
        ],
    )
    def test_lifecycle_events(self, event, payload, kind, fidelity):
        normalized = normalize_hook("goose", {"event": event, **payload})
        assert normalized is not None
        assert normalized.kind == kind
        assert normalized.fidelity == fidelity
        assert normalized.session_id == payload.get("session_id")

    def test_stop_ends_run_not_turn(self):
        # Claude Code's Stop ends a turn; Goose's Stop ends the run.
        assert normalize_hook("goose", {"event": "Stop"}).kind == "run.ended"
        assert normalize_hook("claude-code", {"hook_event_name": "Stop"}).kind == (
            "turn.ended"
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {"event": "PostToolUse", "error": "boom"},
            {"event": "PostToolUse", "success": False},
            {"event": "AfterShellExecution", "decision": "deny"},
            {"event": "AfterShellExecution", "exit_code": 2},
            {"event": "PreToolUseResult", "status": "failed"},
        ],
    )
    def test_completion_events_can_fail(self, payload):
        normalized = normalize_hook("goose", payload)
        assert normalized is not None
        assert normalized.kind == "tool.failed"
        assert normalized.status == "failed"

    def test_goose_field_mapping(self):
        normalized = normalize_hook(
            "goose",
            {
                "event": "PreToolUse",
                "session_id": "sess-1",
                "tool_name": "shell",
                "tool_input": {"command": "ls"},
                "tool_call_id": "call-9",
                "working_dir": "/tmp/project",
                "decision": "allow",
            },
        )
        assert normalized.session_id == "sess-1"
        assert normalized.tool_use_id == "call-9"
        assert normalized.cwd == "/tmp/project"
        assert normalized.summary["command"] == "ls"
        assert normalized.summary["decision"] == "allow"

    def test_tool_output_becomes_result(self):
        normalized = normalize_hook(
            "goose",
            {
                "event": "PostToolUse",
                "tool_output": {"stdout": "ok", "exit_code": 0},
            },
        )
        assert normalized.summary["result"]["stdout"] == "ok"

    def test_minimal_and_unknown_payloads_are_tolerated(self):
        assert normalize_hook("goose", {"event": "PreToolUse"}) is not None
        assert normalize_hook("goose", {"event": "NotARealEvent"}) is None
        assert normalize_hook("goose", {}) is None
        with pytest.raises(TelemetryValidationError):
            normalize_hook("not-a-harness", {})

    def test_dsh_is_known_but_has_no_mappings_yet(self):
        assert normalize_hook("dsh", {"event": "PreToolUse"}) is None

    def test_pipeline_stores_goose_event(self, tmp_path):
        async def go():
            spine = EventSpine(str(tmp_path / "events.db"), retention_days=0)
            await spine.start()
            pipeline = TelemetryPipeline(spine)
            try:
                result = await pipeline.ingest_hook(
                    "goose",
                    {
                        "event": "PreToolUse",
                        "session_id": "sess-1",
                        "tool_name": "shell",
                        "tool_input": {"command": "pytest -q"},
                        "tool_call_id": "call-1",
                    },
                )
                events, _last = await spine.list_events()
                return result, events
            finally:
                await spine.close()

        result, events = asyncio.run(go())
        assert result.stored is True
        assert len(events) == 1
        event = events[0]
        assert event["harness"] == "goose"
        assert event["kind"] == "tool.started"
        assert event["session_id"] == "sess-1"
        assert event["payload"]["fidelity"] == "L3"
        assert event["payload"]["tool_name"] == "Bash"


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_coverage_reports_goose_installed_l3(tmp_path):
    adapters = {
        "version": 1,
        "adapters": {
            "goose": {
                "display_name": "Goose",
                "installed": True,
                "fidelity": "L3",
                "transport": "hook-plugin",
                "adapter_version": "phase0",
                "notes": "plugin at ~/.agents/plugins/sugar-telemetry",
            }
        },
    }
    registry_path = tmp_path / "adapters.json"
    write_json(registry_path, adapters)

    report = asyncio.run(coverage_report(None, adapters=str(registry_path)))
    entries = {entry["harness"]: entry for entry in report["harnesses"]}
    assert entries["goose"]["installed"] is True
    assert entries["goose"]["fidelity"] == "L3"
    assert entries["goose"]["transport"] == "hook-plugin"
    assert "goose" in report["summary"]["L3"]


# ---------------------------------------------------------------------------
# `sugar hook --harness goose`
# ---------------------------------------------------------------------------


class TestGooseHookCLI:
    def test_forwards_to_socket(self, home, tmp_path, socket_capture):
        result = invoke_cli(
            tmp_path,
            "hook",
            "--harness",
            "goose",
            "--socket",
            socket_capture.path,
            input_text='{"event":"PostToolUse","tool_name":"shell"}',
        )
        assert result.exit_code == 0, result.output
        assert result.output == ""
        envelope = json.loads(socket_capture.wait_for_message().decode())
        assert envelope == {
            "harness": "goose",
            "body": {"event": "PostToolUse", "tool_name": "shell"},
        }

    def test_dsh_is_accepted_as_reserved_harness(self, home, tmp_path, socket_capture):
        result = invoke_cli(
            tmp_path,
            "hook",
            "--harness",
            "dsh",
            "--socket",
            socket_capture.path,
            input_text='{"event":"anything"}',
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(socket_capture.wait_for_message().decode())
        assert envelope["harness"] == "dsh"

    def test_fail_open_without_endpoints(self, home, tmp_path):
        import socket as socket_module

        probe = socket_module.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        result = invoke_cli(
            tmp_path,
            "hook",
            "--harness",
            "goose",
            "--socket",
            str(tmp_path / "missing.sock"),
            "--url",
            f"http://127.0.0.1:{port}",
            input_text='{"event":"Stop"}',
        )
        assert result.exit_code == 0
        assert result.output == ""


# ---------------------------------------------------------------------------
# Source plugin copy
# ---------------------------------------------------------------------------


class TestGoosePluginSource:
    def test_manifest_is_valid(self):
        manifest = read_json(GOOSE_SOURCE / "plugin.json")
        assert manifest["name"] == installers.GOOSE_PLUGIN_NAME
        assert manifest["version"] == installers.GOOSE_PLUGIN_VERSION

    def test_hooks_cover_every_event_with_command_hooks(self):
        hooks = read_json(GOOSE_SOURCE / "hooks" / "hooks.json")["hooks"]
        assert set(hooks) == set(installers.GOOSE_EVENTS)
        for event in installers.GOOSE_EVENTS:
            entry = hooks[event]
            assert entry["matcher"] == ".*"
            hook = entry["hooks"][0]
            assert hook["type"] == "command"
            assert "hook --harness goose" in hook["command"]
            assert hook["timeout"] == 5

    def test_readme_documents_connect(self):
        readme = (GOOSE_SOURCE / "README.md").read_text(encoding="utf-8")
        assert "sugar connect --harness goose" in readme
        assert "hooks/hooks.json" in readme
