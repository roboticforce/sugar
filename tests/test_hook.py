"""Tests for the fail-open ``sugar hook`` shim.

Real Unix sockets and a real local HTTP server are used (no network, no real
harness), including through the Click command. Every test stays inside a
throwaway HOME/config.
"""

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner

from sugar.connect.hook import (
    build_socket_message,
    default_board_url,
    default_socket_path,
    forward_hook,
    hook_endpoint,
    read_stdin_bytes,
)
from sugar.main import cli


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


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


def invoke_hook(tmp_path, *args, input_text):
    runner = CliRunner()
    return runner.invoke(
        cli,
        ["--config", str(cli_config(tmp_path)), "hook", *args],
        input=input_text,
    )


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class SocketCapture:
    """A one-connection-at-a-time AF_UNIX listener that records newline frames."""

    def __init__(self, path: Path):
        self.path = str(path)
        self.messages = []
        self._server = None
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        if os.name == "nt" or not hasattr(socket, "AF_UNIX"):
            # Windows harness hooks use the HTTP fallback; the shim still
            # guards AF_UNIX at runtime, but exercising it is POSIX-only.
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
    # Keep the AF_UNIX path short: macOS caps it at ~104 bytes and pytest's
    # tmp_path is already deep.
    import shutil
    import tempfile

    directory = tempfile.mkdtemp(prefix="sgh-")
    capture = SocketCapture(Path(directory) / "s").start()
    try:
        yield capture
    finally:
        capture.close()
        shutil.rmtree(directory, ignore_errors=True)


class HttpCapture:
    def __init__(self):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length)
                outer.requests.append(
                    {
                        "path": self.path,
                        "body": body,
                        "headers": dict(self.headers),
                    }
                )
                payload = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def wait_for_request(self, timeout: float = 2.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.requests:
                return self.requests[0]
            time.sleep(0.01)
        raise AssertionError("no HTTP request received")

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def http_capture():
    capture = HttpCapture()
    try:
        yield capture
    finally:
        capture.close()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("SUGAR_INGEST_SOCKET", raising=False)
    monkeypatch.delenv("SUGAR_BOARD_URL", raising=False)
    monkeypatch.delenv("SUGAR_BOARD_TOKEN", raising=False)
    monkeypatch.delenv("SUGAR_HOOK_DEBUG", raising=False)
    return home


# ---------------------------------------------------------------------------
# Envelope + helpers
# ---------------------------------------------------------------------------


class TestEnvelope:
    def test_message_shape(self):
        message = build_socket_message(b'{"tool":"Bash"}', "cursor")
        assert message.endswith(b"\n")
        envelope = json.loads(message.decode())
        assert envelope == {"harness": "cursor", "body": {"tool": "Bash"}}

    def test_invalid_json_is_wrapped(self):
        envelope = json.loads(build_socket_message(b"not json", "codex").decode())
        assert envelope["harness"] == "codex"
        assert envelope["body"]["raw"] == "not json"

    def test_non_object_json_is_wrapped(self):
        envelope = json.loads(build_socket_message(b"[1,2]", "cursor").decode())
        assert envelope["body"] == {"raw_value": [1, 2]}

    def test_empty_stdin_is_empty_object(self):
        envelope = json.loads(build_socket_message(b"", "claude-code").decode())
        assert envelope["body"] == {}

    def test_hook_endpoint(self):
        assert (
            hook_endpoint("http://127.0.0.1:7331", "cursor")
            == "http://127.0.0.1:7331/api/hooks/cursor"
        )
        full = "http://127.0.0.1:7331/api/hooks/claude-code"
        assert hook_endpoint(full, "cursor") == full

    def test_env_defaults(self, monkeypatch, isolated_home):
        monkeypatch.setenv("SUGAR_INGEST_SOCKET", "~/custom.sock")
        monkeypatch.setenv("SUGAR_BOARD_URL", "http://127.0.0.1:9999/")
        assert default_socket_path() == Path("~/custom.sock").expanduser()
        assert default_board_url() == "http://127.0.0.1:9999"

    def test_read_stdin_bytes_bounded(self):
        import io

        assert read_stdin_bytes(io.BytesIO(b'{"a":1}'), limit=5) == b'{"a":'
        assert read_stdin_bytes(io.StringIO("hello"), limit=100) == b"hello"


# ---------------------------------------------------------------------------
# forward_hook
# ---------------------------------------------------------------------------


class TestForwardHook:
    def test_socket_is_preferred_over_http(self, socket_capture, http_capture):
        raw = b'{"hook_event_name":"PostToolUse","tool_name":"Bash"}'
        result = forward_hook(
            raw,
            "cursor",
            socket_path=socket_capture.path,
            url=http_capture.base_url,
        )
        assert result.delivered is True
        assert result.transport == "socket"
        frame = socket_capture.wait_for_message()
        envelope = json.loads(frame.decode())
        assert envelope["harness"] == "cursor"
        assert envelope["body"]["tool_name"] == "Bash"
        assert http_capture.requests == []

    def test_http_fallback_when_socket_missing(self, http_capture, tmp_path):
        raw = b'{"hook_event_name":"SessionStart"}'
        result = forward_hook(
            raw,
            "cursor",
            socket_path=tmp_path / "missing.sock",
            url=http_capture.base_url,
        )
        assert result.delivered is True
        assert result.transport == "http"
        request = http_capture.wait_for_request()
        assert request["path"] == "/api/hooks/cursor"
        assert request["body"] == raw
        assert "application/json" in request["headers"].get("Content-Type", "")

    def test_full_hook_url_is_not_double_appended(self, http_capture):
        result = forward_hook(
            b"{}",
            "claude-code",
            socket_path=Path("/nonexistent-socket"),
            url=http_capture.base_url + "/api/hooks/claude-code",
        )
        assert result.delivered is True
        request = http_capture.wait_for_request()
        assert request["path"] == "/api/hooks/claude-code"

    def test_http_token_header(self, http_capture, monkeypatch):
        monkeypatch.setenv("SUGAR_BOARD_TOKEN", "secret-token")
        forward_hook(
            b"{}",
            "cursor",
            socket_path=Path("/nonexistent-socket"),
            url=http_capture.base_url,
        )
        request = http_capture.wait_for_request()
        assert request["headers"].get("Authorization") == "Bearer secret-token"
        assert b"secret-token" not in request["body"]

    def test_all_endpoints_unreachable_fails_open(self, tmp_path):
        result = forward_hook(
            b"{}",
            "cursor",
            socket_path=tmp_path / "missing.sock",
            url=f"http://127.0.0.1:{free_port()}",
            http_timeout=0.3,
        )
        assert result.delivered is False
        assert result.detail

    def test_unknown_harness(self):
        result = forward_hook(b"{}", "nope")
        assert result.delivered is False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestHookCLI:
    def test_forwards_to_socket(self, tmp_path, socket_capture):
        result = invoke_hook(
            tmp_path,
            "--harness",
            "cursor",
            "--socket",
            socket_capture.path,
            input_text='{"hook_event_name":"PreToolUse"}',
        )
        assert result.exit_code == 0, result.output
        assert result.output == ""
        envelope = json.loads(socket_capture.wait_for_message().decode())
        assert envelope == {
            "harness": "cursor",
            "body": {"hook_event_name": "PreToolUse"},
        }

    def test_forwards_to_http_fallback(self, tmp_path, http_capture):
        result = invoke_hook(
            tmp_path,
            "--harness",
            "codex",
            "--socket",
            str(tmp_path / "missing.sock"),
            "--url",
            http_capture.base_url,
            input_text='{"hook_event_name":"Stop"}',
        )
        assert result.exit_code == 0, result.output
        request = http_capture.wait_for_request()
        assert request["path"] == "/api/hooks/codex"

    def test_invalid_stdin_still_exits_zero(self, tmp_path, socket_capture):
        result = invoke_hook(
            tmp_path,
            "--harness",
            "cursor",
            "--socket",
            socket_capture.path,
            input_text="this is not json",
        )
        assert result.exit_code == 0
        envelope = json.loads(socket_capture.wait_for_message().decode())
        assert envelope["body"]["raw"] == "this is not json"

    def test_no_endpoint_exits_zero_and_silent(self, tmp_path):
        result = invoke_hook(
            tmp_path,
            "--harness",
            "cursor",
            "--socket",
            str(tmp_path / "missing.sock"),
            "--url",
            f"http://127.0.0.1:{free_port()}",
            input_text="{}",
        )
        assert result.exit_code == 0
        assert result.output == ""

    def test_quiet_flag_accepted(self, tmp_path, socket_capture):
        result = invoke_hook(
            tmp_path,
            "--harness",
            "claude-code",
            "--socket",
            socket_capture.path,
            "--quiet",
            input_text="{}",
        )
        assert result.exit_code == 0
        assert result.output == ""

    def test_debug_env_prints_to_output(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SUGAR_HOOK_DEBUG", "1")
        result = invoke_hook(
            tmp_path,
            "--harness",
            "cursor",
            "--socket",
            str(tmp_path / "missing.sock"),
            "--url",
            f"http://127.0.0.1:{free_port()}",
            input_text="{}",
        )
        assert result.exit_code == 0
        assert "sugar hook:" in result.output
        assert "delivered=False" in result.output

    def test_unknown_harness_is_rejected_by_cli(self, tmp_path):
        result = invoke_hook(
            tmp_path,
            "--harness",
            "nope",
            input_text="{}",
        )
        assert result.exit_code != 0
