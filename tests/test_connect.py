"""Tests for Phase-0 harness connect/detect/install/registry/plugin/doctor.

Every test uses a throwaway HOME and throwaway config files. No real harness is
detected or executed (PATH is emptied and binary probes are injected), and no
test writes to the developer's ``~/.claude``/``~/.cursor``/``~/.codex``.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner

from sugar.connect import detect, installers, registry
from sugar.connect.cli import build_rows, format_matrix, run_doctor_command
from sugar.connect.doctor import run_doctor
from sugar.main import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "integrations" / "claude-plugin"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated HOME plus deterministic detection (no PATH binaries)."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("SUGAR_ADAPTERS_PATH", raising=False)
    monkeypatch.delenv("SUGAR_ADAPTERS_FILE", raising=False)
    monkeypatch.delenv("SUGAR_BOARD_URL", raising=False)
    monkeypatch.delenv("SUGAR_INGEST_SOCKET", raising=False)
    monkeypatch.delenv("SUGAR_BOARD_TOKEN", raising=False)
    # On a developer Mac /Applications/DeepSeek Harness.app may exist; tests
    # only consider candidates under the isolated HOME.
    original = detect._candidate_paths

    def scoped_candidates(harness, home_path):
        return [
            path
            for path in original(harness, home_path)
            if str(path).startswith(str(home_path))
        ]

    monkeypatch.setattr(detect, "_candidate_paths", scoped_candidates)
    return home_dir


def cli_config(tmp_path):
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


def invoke_cli(tmp_path, *args, input_text=None):
    runner = CliRunner()
    return runner.invoke(
        cli,
        ["--config", str(cli_config(tmp_path)), *args],
        input=input_text,
    )


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def make_dirs(home: Path, *relative: str) -> None:
    for item in relative:
        (home / item).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# detect
# ---------------------------------------------------------------------------


class TestDetect:
    def test_detects_by_config_dirs(self, home):
        make_dirs(home, ".claude", ".cursor", ".codex", ".config/goose")
        detections = detect.detect_all(home=home, path="")

        assert detections[detect.CLAUDE_CODE].detected is True
        assert detections[detect.CURSOR].detected is True
        assert detections[detect.CODEX].detected is True
        assert detections[detect.GOOSE].detected is True
        assert detections[detect.DSH].detected is False

        assert detections[detect.CLAUDE_CODE].fidelity == "L3"
        assert detections[detect.CLAUDE_CODE].transport == "http-hook"
        assert detections[detect.CURSOR].fidelity == "L2"
        assert detections[detect.CURSOR].transport == "command-hook"
        assert detections[detect.GOOSE].installable is True
        assert detections[detect.GOOSE].fidelity == "L3"
        assert detections[detect.GOOSE].transport == "hook-plugin"

    def test_detects_binary_and_version(self, home):
        detection = detect.detect_harness(
            detect.CLAUDE_CODE,
            home=home,
            path="",
            which=lambda name, path: f"/fake/bin/{name}",
            get_version=lambda binary: "2.1.0 (Claude Code)",
        )
        assert detection.detected is True
        assert detection.binary == "/fake/bin/claude"
        assert detection.version == "2.1.0 (Claude Code)"
        assert any("claude in PATH" in item for item in detection.evidence)

    def test_version_probe_failure_is_tolerated(self, home):
        def boom(binary):
            raise TimeoutError("harness hung")

        detection = detect.detect_harness(
            detect.CURSOR,
            home=home,
            path="",
            which=lambda name, path: "/fake/cursor",
            get_version=boom,
        )
        assert detection.detected is True
        assert detection.version is None

    def test_nothing_detected(self, home):
        detections = detect.detect_all(home=home, path="")
        assert all(not detection.detected for detection in detections.values())

    def test_dsh_app_bundle(self, home):
        make_dirs(home, "Applications/DeepSeek Harness.app")
        detection = detect.detect_harness(detect.DSH, home=home, path="")
        assert detection.detected is True
        assert detection.display_name == "DeepSeek Harness"

    def test_unknown_harness(self, home):
        detection = detect.detect_harness("nope", home=home, path="")
        assert detection.detected is False
        assert detection.installable is False
        assert detection.note == "unknown harness"


# ---------------------------------------------------------------------------
# installers
# ---------------------------------------------------------------------------


class TestClaudeInstaller:
    def test_fresh_install_creates_http_hooks(self, home):
        result = installers.install(detect.CLAUDE_CODE, home=home)

        assert result.status == "installed"
        assert result.config_path == home / ".claude" / "settings.json"
        assert result.config_path.exists()
        data = read_json(result.config_path)
        for event in installers.CLAUDE_EVENTS:
            entries = data["hooks"][event]
            assert entries[-1]["matcher"] == ".*"
            hook = entries[-1]["hooks"][0]
            assert hook == {
                "type": "http",
                "url": "http://127.0.0.1:7331/api/hooks/claude-code",
                "timeout": 3,
            }

    def test_merge_preserves_existing_settings_and_hooks(self, home):
        settings_path = home / ".claude" / "settings.json"
        original = {
            "theme": "dark",
            "permissions": {"allow": ["Bash(git status)"]},
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "echo user-hook"}],
                    }
                ],
                "Notification": [
                    {
                        "matcher": ".*",
                        "hooks": [{"type": "command", "command": "notify"}],
                    }
                ],
            },
        }
        write_json(settings_path, original)
        previous_text = settings_path.read_text()

        result = installers.install(detect.CLAUDE_CODE, home=home)

        assert result.status == "updated"
        data = read_json(settings_path)
        assert data["theme"] == "dark"
        assert data["permissions"] == original["permissions"]
        assert data["hooks"]["Notification"] == original["hooks"]["Notification"]
        post = data["hooks"]["PostToolUse"]
        assert post[0]["hooks"][0]["command"] == "echo user-hook"
        assert len(post) == 2  # user's entry preserved, Sugar's appended

        backup = settings_path.with_name("settings.json.bak")
        assert backup.exists()
        assert backup.read_text() == previous_text

    def test_idempotent_second_install(self, home):
        first = installers.install(detect.CLAUDE_CODE, home=home)
        path = first.config_path
        content = path.read_text()

        second = installers.install(detect.CLAUDE_CODE, home=home)

        assert second.status == "unchanged"
        assert path.read_text() == content
        data = read_json(path)
        for event in installers.CLAUDE_EVENTS:
            sugar_hooks = [
                hook
                for entry in data["hooks"][event]
                for hook in entry["hooks"]
                if hook.get("url", "").endswith("/api/hooks/claude-code")
            ]
            assert len(sugar_hooks) == 1

    def test_existing_sugar_hook_is_not_duplicated(self, home):
        settings_path = home / ".claude" / "settings.json"
        hook = {
            "type": "http",
            "url": "http://127.0.0.1:7331/api/hooks/claude-code",
            "timeout": 3,
        }
        write_json(
            settings_path,
            {"hooks": {"SessionStart": [{"matcher": ".*", "hooks": [hook]}]}},
        )
        result = installers.install(detect.CLAUDE_CODE, home=home)
        assert result.status == "updated"  # other events still added
        data = read_json(settings_path)
        assert len(data["hooks"]["SessionStart"]) == 1

    def test_dry_run_writes_nothing(self, home):
        result = installers.install(detect.CLAUDE_CODE, home=home, dry_run=True)
        assert result.status == "would-install"
        assert not (home / ".claude").exists()
        assert result.backup_path is None

        settings_path = home / ".claude" / "settings.json"
        write_json(settings_path, {"theme": "dark"})
        before = settings_path.read_text()
        dry = installers.install(detect.CLAUDE_CODE, home=home, dry_run=True)
        assert dry.status == "would-update"
        assert settings_path.read_text() == before
        assert not settings_path.with_name("settings.json.bak").exists()

    def test_invalid_json_is_refused(self, home):
        settings_path = home / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text("{not json", encoding="utf-8")

        with pytest.raises(installers.InstallError):
            installers.install(detect.CLAUDE_CODE, home=home)
        assert settings_path.read_text() == "{not json"

    def test_invalid_hooks_shape_is_refused(self, home):
        settings_path = home / ".claude" / "settings.json"
        write_json(settings_path, {"hooks": []})
        with pytest.raises(installers.InstallError):
            installers.install(detect.CLAUDE_CODE, home=home)

    def test_board_url_override(self, home, monkeypatch):
        monkeypatch.setenv("SUGAR_BOARD_URL", "http://127.0.0.1:7999")
        result = installers.install(detect.CLAUDE_CODE, home=home)
        data = read_json(result.config_path)
        url = data["hooks"]["Stop"][-1]["hooks"][0]["url"]
        assert url == "http://127.0.0.1:7999/api/hooks/claude-code"


class TestCommandInstallers:
    def test_cursor_install_merges_existing(self, home):
        hooks_path = home / ".cursor" / "hooks.json"
        write_json(
            hooks_path,
            {
                "version": 1,
                "hooks": {
                    "stop": [{"command": "user-script", "timeout": 2}],
                },
            },
        )
        previous = hooks_path.read_text()

        result = installers.install(detect.CURSOR, home=home)

        assert result.status == "updated"
        data = read_json(hooks_path)
        assert data["version"] == 1
        assert data["hooks"]["stop"][0] == {"command": "user-script", "timeout": 2}
        assert data["hooks"]["stop"][-1] == {
            "command": "sugar hook --harness cursor",
            "timeout": 5,
        }
        for event in installers.COMMAND_EVENTS:
            commands = [entry["command"] for entry in data["hooks"][event]]
            assert "sugar hook --harness cursor" in commands
        backup = hooks_path.with_name("hooks.json.bak")
        assert backup.exists() and backup.read_text() == previous

    def test_cursor_idempotent(self, home):
        installers.install(detect.CURSOR, home=home)
        path = installers.cursor_hooks_path(home)
        content = path.read_text()
        assert installers.install(detect.CURSOR, home=home).status == "unchanged"
        assert path.read_text() == content

    def test_codex_install_and_trust_note(self, home):
        result = installers.install(detect.CODEX, home=home)
        data = read_json(result.config_path)
        command = data["hooks"]["preToolUse"][0]["command"]
        assert command == "sugar hook --harness codex"
        assert any("trust" in note.lower() for note in result.notes)
        assert any("/hooks" in note for note in result.notes)

    def test_command_dry_run_writes_nothing(self, home):
        result = installers.install(detect.CODEX, home=home, dry_run=True)
        assert result.status == "would-install"
        assert not (home / ".codex").exists()

    def test_detect_only_harness_is_skipped(self, home):
        result = installers.install(detect.DSH, home=home)
        assert result.status == "skipped"
        assert result.config_path is None
        assert result.notes

    def test_unknown_harness_raises(self, home):
        with pytest.raises(installers.InstallError):
            installers.install("nope", home=home)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_missing_registry_is_empty(self, home):
        data = registry.load_registry(home / ".sugar" / "adapters.json")
        assert data == {"version": 1, "adapters": {}}

    def test_corrupt_registry_degrades_to_empty(self, home):
        path = home / ".sugar" / "adapters.json"
        path.parent.mkdir(parents=True)
        path.write_text("{broken", encoding="utf-8")
        assert registry.load_registry(path)["adapters"] == {}

    def test_record_and_reload(self, home, monkeypatch):
        path = home / ".sugar" / "adapters.json"
        reg = registry.load_registry(path)
        entry = registry.adapter_entry(
            "Claude Code",
            installed=True,
            fidelity="L3",
            transport="http-hook",
            config_path=str(home / ".claude" / "settings.json"),
            notes="test",
        )
        registry.record_adapter(reg, "claude-code", entry)
        registry.save_registry(reg, path)

        loaded = registry.load_registry(path)
        assert loaded["version"] == 1
        row = loaded["adapters"]["claude-code"]
        assert row["installed"] is True
        assert row["fidelity"] == "L3"
        assert row["transport"] == "http-hook"
        assert row["adapter_version"] == "phase0"
        assert row["installed_at"].endswith("Z")
        assert set(row) == {
            "display_name",
            "installed",
            "fidelity",
            "transport",
            "installed_at",
            "config_path",
            "adapter_version",
            "notes",
        }

    def test_installed_adapters_filters(self):
        reg = {
            "version": 1,
            "adapters": {
                "claude-code": {"installed": True},
                "cursor": {"installed": False},
                "broken": "nope",
            },
        }
        assert set(registry.installed_adapters(reg)) == {"claude-code"}

    def test_default_registry_path_env_override(self, home, monkeypatch):
        target = home / "custom" / "adapters.json"
        monkeypatch.setenv("SUGAR_ADAPTERS_PATH", str(target))
        assert registry.default_registry_path(home=home) == target

    def test_telemetry_adapters_file_alias(self, home, monkeypatch):
        target = home / "alias" / "adapters.json"
        monkeypatch.setenv("SUGAR_ADAPTERS_FILE", str(target))
        assert registry.default_registry_path(home=home) == target


# ---------------------------------------------------------------------------
# connect CLI
# ---------------------------------------------------------------------------


class TestConnectCLI:
    def test_check_prints_matrix_and_writes_nothing(self, home, tmp_path):
        make_dirs(home, ".claude", ".cursor", "Applications/DeepSeek Harness.app")
        result = invoke_cli(tmp_path, "connect", "--check")

        assert result.exit_code == 0, result.output
        assert "HARNESS" in result.output
        assert "claude-code" in result.output and "yes" in result.output
        assert "detect-only" in result.output  # goose/dsh row label
        assert "--check" in result.output
        assert not (home / ".claude" / "settings.json").exists()
        assert not (home / ".cursor" / "hooks.json").exists()
        assert not (home / ".agents").exists()
        assert not (home / ".sugar" / "adapters.json").exists()

    def test_apply_installs_detected_and_writes_registry(self, home, tmp_path):
        make_dirs(home, ".claude")
        result = invoke_cli(tmp_path, "connect", "--yes")

        assert result.exit_code == 0, result.output
        assert (home / ".claude" / "settings.json").exists()
        assert not (home / ".cursor" / "hooks.json").exists()  # not detected
        reg = read_json(home / ".sugar" / "adapters.json")
        assert reg["adapters"]["claude-code"]["installed"] is True
        assert reg["adapters"]["claude-code"]["fidelity"] == "L3"

    def test_harness_selection_limits_installs(self, home, tmp_path):
        make_dirs(home, ".claude", ".cursor")
        result = invoke_cli(tmp_path, "connect", "--harness", "cursor", "--yes")

        assert result.exit_code == 0, result.output
        assert (home / ".cursor" / "hooks.json").exists()
        assert not (home / ".claude" / "settings.json").exists()
        reg = read_json(home / ".sugar" / "adapters.json")
        assert set(reg["adapters"]) == {"cursor"}

    def test_dry_run_never_writes(self, home, tmp_path):
        make_dirs(home, ".claude", ".cursor")
        result = invoke_cli(tmp_path, "connect", "--dry-run")

        assert result.exit_code == 0, result.output
        assert "would install" in result.output
        assert not (home / ".claude" / "settings.json").exists()
        assert not (home / ".cursor" / "hooks.json").exists()
        assert not (home / ".sugar" / "adapters.json").exists()

    def test_apply_writes_backup_and_preserves_user_config(self, home, tmp_path):
        settings_path = home / ".claude" / "settings.json"
        write_json(settings_path, {"model": "opus", "hooks": {}})
        previous = settings_path.read_text()

        result = invoke_cli(tmp_path, "connect", "--yes")

        assert result.exit_code == 0, result.output
        assert json.loads(settings_path.read_text())["model"] == "opus"
        backup = settings_path.with_name("settings.json.bak")
        assert backup.exists() and backup.read_text() == previous

    def test_idempotent_second_connect_says_unchanged(self, home, tmp_path):
        make_dirs(home, ".claude")
        invoke_cli(tmp_path, "connect", "--yes")
        result = invoke_cli(tmp_path, "connect", "--yes")
        assert result.exit_code == 0, result.output
        assert "unchanged" in result.output

    def test_codex_trust_step_is_printed(self, home, tmp_path):
        make_dirs(home, ".codex")
        result = invoke_cli(tmp_path, "connect", "--harness", "codex", "--yes")

        assert result.exit_code == 0, result.output
        assert "/hooks" in result.output
        assert "trust" in result.output.lower()
        assert "dangerously-bypass-hook-trust" in result.output

    def test_prompt_declined_changes_nothing(self, home, tmp_path):
        make_dirs(home, ".claude")
        result = invoke_cli(tmp_path, "connect", input_text="n\n")

        assert result.exit_code == 0, result.output
        assert "Nothing changed" in result.output
        assert not (home / ".claude" / "settings.json").exists()

    def test_nothing_detected_is_not_an_error(self, home, tmp_path):
        result = invoke_cli(tmp_path, "connect", "--yes")
        assert result.exit_code == 0, result.output
        assert "No supported harness detected" in result.output

    def test_detect_only_selection_is_skipped(self, home, tmp_path):
        make_dirs(home, "Applications/DeepSeek Harness.app")
        result = invoke_cli(tmp_path, "connect", "--harness", "dsh", "--yes")
        assert result.exit_code == 0, result.output
        assert "No supported harness detected" in result.output


class TestMatrix:
    def test_build_rows_actions(self, home):
        detections = detect.detect_all(home=home, path="")
        detections[detect.CLAUDE_CODE] = detect.detect_harness(
            detect.CLAUDE_CODE,
            home=home,
            path="",
            which=lambda name, path: "/fake/claude",
        )
        detections[detect.GOOSE] = detect.detect_harness(
            detect.GOOSE,
            home=home,
            path="",
            which=lambda name, path: "/fake/goose",
        )
        reg = {
            "version": 1,
            "adapters": {"cursor": {"installed": True, "fidelity": "L2"}},
        }
        detections[detect.CURSOR] = detect.detect_harness(
            detect.CURSOR,
            home=home,
            path="",
            which=lambda name, path: "/fake/cursor",
        )
        detections[detect.DSH] = detect.detect_harness(
            detect.DSH,
            home=home,
            path="",
            which=lambda name, path: "/fake/dsh",
        )
        rows = {row.harness: row for row in build_rows(detections, reg)}
        assert rows[detect.CLAUDE_CODE].action == "install"
        assert rows[detect.CURSOR].action == "keep (installed)"
        assert rows[detect.GOOSE].action == "install"
        assert rows[detect.DSH].action == "detect-only (phase 1)"
        assert rows[detect.CODEX].action == "skip (not detected)"

    def test_format_matrix_alignment(self, home):
        detections = detect.detect_all(home=home, path="")
        detections[detect.CLAUDE_CODE] = detect.Detection(
            detect.CLAUDE_CODE, "Claude Code", True, "L3", "http-hook", True
        )
        rows = build_rows(detections, {"version": 1, "adapters": {}})
        table = format_matrix(rows)
        assert "HARNESS" in table and "claude-code" in table
        assert "http-hook" in table


# ---------------------------------------------------------------------------
# Claude plugin package
# ---------------------------------------------------------------------------


class TestClaudePlugin:
    def test_plugin_manifest_is_valid(self):
        manifest = read_json(PLUGIN_ROOT / ".claude-plugin" / "plugin.json")
        assert manifest["name"] == "sugar-telemetry"
        assert manifest["hooks"] == "./hooks/hooks.json"
        assert manifest["version"]

    def test_plugin_hooks_use_http_transport(self):
        hooks_file = PLUGIN_ROOT / "hooks" / "hooks.json"
        data = read_json(hooks_file)
        hooks = data["hooks"]
        assert set(hooks) == set(installers.CLAUDE_EVENTS)
        for event in installers.CLAUDE_EVENTS:
            entries = hooks[event]
            assert isinstance(entries, list) and entries
            for entry in entries:
                assert entry["matcher"] == ".*"
                hook = entry["hooks"][0]
                assert hook["type"] == "http"
                assert hook["url"] == "http://127.0.0.1:7331/api/hooks/claude-code"
                assert hook["timeout"] == 3

    def test_readme_documents_install(self):
        readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        assert "/plugin install sugar-telemetry@" in readme
        assert "sugar doctor" in readme
        assert "http://127.0.0.1:7331/api/hooks/claude-code" in readme


# ---------------------------------------------------------------------------
# doctor (fake board over real HTTP)
# ---------------------------------------------------------------------------


class _BoardState:
    def __init__(self):
        self.hook_bodies = []
        self.activity_events = []


def make_fake_board(state=None, *, expose_coverage=True, empty_activity=False):
    state = state or _BoardState()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
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
                self._send(200, {"ok": True, "version": "test"})
            elif self.path.startswith("/api/coverage"):
                if not expose_coverage:
                    self._send(404, {"error": "not_found"})
                    return
                self._send(
                    200,
                    {
                        "generated_at": "2026-11-19T00:00:00Z",
                        "summary": {
                            "L3": ["claude-code"],
                            "L2": [],
                            "L1": [],
                            "L0": [],
                        },
                        "harnesses": [
                            {
                                "harness": "claude-code",
                                "display_name": "Claude Code",
                                "detected": True,
                                "installed": True,
                                "fidelity": "L3",
                                "transport": "http-hook",
                                "last_event_at": None,
                                "events_24h": 0,
                                "adapter_version": "phase0",
                                "notes": "",
                            }
                        ],
                    },
                )
            elif self.path.startswith("/api/activity"):
                events = [] if empty_activity else state.activity_events
                self._send(200, {"events": events, "last_id": len(events)})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self):
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length)
            if self.path.startswith("/api/hooks/"):
                state.hook_bodies.append(body)
                try:
                    parsed = json.loads(body)
                except ValueError:
                    parsed = {}
                state.activity_events.append(
                    {
                        "id": len(state.activity_events) + 1,
                        "kind": "run.started",
                        "session_id": parsed.get("session_id"),
                        "payload": parsed,
                    }
                )
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not_found"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, state


@pytest.fixture
def fake_board():
    server, state = make_fake_board()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", state
    finally:
        server.shutdown()
        server.server_close()


def install_registry(home: Path, harness: str = "claude-code") -> Path:
    path = home / ".sugar" / "adapters.json"
    reg = registry.load_registry(path)
    registry.record_adapter(
        reg,
        harness,
        registry.adapter_entry(
            "Claude Code" if harness == "claude-code" else harness,
            installed=True,
            fidelity="L3" if harness == "claude-code" else "L2",
            transport="http-hook" if harness == "claude-code" else "command-hook",
            config_path=str(home / ".claude" / "settings.json"),
        ),
    )
    registry.save_registry(reg, path)
    return path


class TestDoctor:
    def test_delivers_and_verifies(self, home, fake_board):
        server, state = fake_board
        install_registry(home)

        result = run_doctor(server=server)

        assert result.ok is True
        assert len(state.hook_bodies) == 1
        probe = result.adapters[0]
        assert probe.harness == "claude-code"
        assert probe.delivered is True
        assert probe.verified is True
        assert result.coverage is not None
        assert any(
            check.name == "dashboard health" and check.ok for check in result.checks
        )

    def test_cli_json_output(self, home, fake_board):
        server, _state = fake_board
        install_registry(home)
        result = invoke_cli(tmp_path_for(home), "doctor", "--server", server, "--json")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["adapters"][0]["verified"] is True
        assert payload["coverage"]["summary"]["L3"] == ["claude-code"]

    def test_unreachable_board_exits_1(self, home, tmp_path):
        import socket

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        install_registry(home)
        result = invoke_cli(tmp_path, "doctor", "--server", f"http://127.0.0.1:{port}")
        assert result.exit_code == 1
        assert "❌" in result.output

    def test_no_installed_adapters_exits_1(self, home):
        server, _state = make_fake_board(expose_coverage=False)
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            result = invoke_cli(tmp_path_for(home), "doctor", "--server", base)
        finally:
            server.shutdown()
            server.server_close()
        assert result.exit_code == 1
        assert "sugar connect" in result.output

    def test_unverified_delivery_fails(self, home):
        server, _state = make_fake_board(empty_activity=True)
        try:
            install_registry(home)
            result = run_doctor(server=f"http://127.0.0.1:{server.server_address[1]}")
        finally:
            server.shutdown()
            server.server_close()
        assert result.ok is False
        assert result.adapters[0].delivered is True
        assert result.adapters[0].verified is False

    def test_missing_coverage_is_tolerated(self, home):
        server, _state = make_fake_board(expose_coverage=False)
        try:
            install_registry(home)
            result = run_doctor(server=f"http://127.0.0.1:{server.server_address[1]}")
        finally:
            server.shutdown()
            server.server_close()
        assert result.ok is True
        assert result.coverage is None


def tmp_path_for(home: Path) -> Path:
    """A created config-file directory next to the isolated HOME."""
    path = home.parent / "cli-config"
    path.mkdir(parents=True, exist_ok=True)
    return path
