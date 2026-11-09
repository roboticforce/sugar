"""Tests for the agent activity producer: ``sugar emit``, MCP tool, HTTP helper.

All network calls are monkeypatched; no test touches a live board.
See ``docs/telemetry.md``.
"""

import io
import json
import urllib.error

import pytest
from click.testing import CliRunner

from sugar import activity
from sugar.activity import (
    DEFAULT_BOARD_URL,
    DEFAULT_TIMEOUT,
    ActivityConfigError,
    ActivityError,
    build_event_body,
    emit_activity,
    emit_activity_async,
    ingest_url,
    post_event,
    resolve_board_token,
    resolve_board_url,
)
from sugar.main import cli

try:
    from sugar.mcp.task_server import (
        FASTMCP_AVAILABLE,
        create_task_mcp_server,
        emit_activity_event,
    )
except ImportError:  # pragma: no cover - MCP deps are optional
    FASTMCP_AVAILABLE = False
    create_task_mcp_server = None
    emit_activity_event = None


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload, status=201):
        self.status = status
        self._body = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def clean_env(monkeypatch):
    """Remove board/identity env vars so tests are deterministic."""
    for name in (
        "SUGAR_BOARD_URL",
        "SUGAR_BOARD_TOKEN",
        "SUGAR_ACTOR",
        "SUGAR_AGENT_ID",
        "SUGAR_ACTOR_KIND",
        "SUGAR_SESSION",
        "SUGAR_HARNESS",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def fake_post(monkeypatch):
    """Replace the HTTP helper; keeps the real URL/body building in emit_activity."""
    calls = []

    def _fake_post(body, **kwargs):
        calls.append({"body": body, **kwargs})
        return {
            "id": 42,
            "kind": body.get("kind"),
            "actor": body.get("actor"),
            "task_id": body.get("task_id"),
        }

    monkeypatch.setattr(activity, "post_event", _fake_post)
    return calls


# ---------------------------------------------------------------------------
# Shared HTTP helper
# ---------------------------------------------------------------------------


class TestActivityHelper:
    def test_resolve_board_url(self, clean_env):
        assert resolve_board_url() == DEFAULT_BOARD_URL
        clean_env.setenv("SUGAR_BOARD_URL", "http://board.test:9000/")
        assert resolve_board_url() == "http://board.test:9000"
        assert resolve_board_url("https://board.example") == "https://board.example"
        assert resolve_board_url("https://board.example/") == "https://board.example"

    @pytest.mark.parametrize(
        "bad", ["not-a-url", "ftp://board", "http://", "http://user:pass@board"]
    )
    def test_resolve_board_url_rejects_bad(self, clean_env, bad):
        with pytest.raises(ActivityConfigError):
            resolve_board_url(bad)

    def test_ingest_url(self, clean_env):
        assert ingest_url() == DEFAULT_BOARD_URL + "/api/events"
        assert ingest_url("http://board:9000/") == "http://board:9000/api/events"

    def test_resolve_board_token(self, clean_env):
        assert resolve_board_token() is None
        clean_env.setenv("SUGAR_BOARD_TOKEN", "env-token")
        assert resolve_board_token() == "env-token"
        assert resolve_board_token("explicit") == "explicit"
        assert resolve_board_token("   ") is None

    def test_build_event_body(self):
        body = build_event_body(
            "tool.invoked",
            actor="claude-code",
            actor_kind="agent",
            harness="claude-code",
            session_id="s1",
            task_id="t1",
            project="p1",
            payload={"message": "hi"},
        )
        assert body == {
            "kind": "tool.invoked",
            "actor": "claude-code",
            "actor_kind": "agent",
            "harness": "claude-code",
            "session_id": "s1",
            "task_id": "t1",
            "project": "p1",
            "payload": {"message": "hi"},
        }
        assert build_event_body("run.started") == {"kind": "run.started"}
        with pytest.raises(ActivityConfigError):
            build_event_body("")
        with pytest.raises(ActivityConfigError):
            build_event_body("x", payload=[])  # type: ignore[arg-type]

    def test_post_event_request_and_success(self, monkeypatch, clean_env):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            captured["timeout"] = timeout
            return _FakeResponse({"event": {"id": 7, "kind": "tool.invoked"}})

        monkeypatch.setattr(activity.urllib.request, "urlopen", fake_urlopen)
        event = post_event(
            {"kind": "tool.invoked"},
            server="http://board:9000/",
            token="super-secret-token",
        )
        request = captured["request"]
        assert event == {"id": 7, "kind": "tool.invoked"}
        assert request.full_url == "http://board:9000/api/events"
        assert request.get_method() == "POST"
        assert request.get_header("Authorization") == "Bearer super-secret-token"
        assert request.get_header("Content-type") == "application/json"
        assert captured["timeout"] == DEFAULT_TIMEOUT
        assert json.loads(request.data) == {"kind": "tool.invoked"}

    def test_post_event_without_token(self, monkeypatch, clean_env):
        def fake_urlopen(request, timeout=None):
            assert request.get_header("Authorization") is None
            return _FakeResponse({"event": {"id": 1}})

        monkeypatch.setattr(activity.urllib.request, "urlopen", fake_urlopen)
        assert post_event({"kind": "x"}) == {"id": 1}

    def test_post_event_http_error_is_safe(self, monkeypatch, clean_env):
        def fake_urlopen(request, timeout=None):
            body = json.dumps(
                {"error": "unauthorized", "detail": "missing or invalid board token"}
            ).encode()
            raise urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", {}, io.BytesIO(body)
            )

        monkeypatch.setattr(activity.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(ActivityError) as excinfo:
            post_event(
                {"kind": "tool.invoked", "payload": {"secret": "do-not-log"}},
                server="http://board:9000",
                token="super-secret-token",
            )
        message = str(excinfo.value)
        assert "HTTP 401" in message
        assert "missing or invalid board token" in message
        assert "super-secret-token" not in message
        assert "do-not-log" not in message

    def test_post_event_connection_error(self, monkeypatch, clean_env):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError(
                ConnectionRefusedError(61, "Connection refused")
            )

        monkeypatch.setattr(activity.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(ActivityError) as excinfo:
            post_event({"kind": "x"}, server="http://127.0.0.1:7999", token="tok")
        message = str(excinfo.value)
        assert "cannot reach" in message
        assert "127.0.0.1:7999" in message
        assert "tok" not in message

    def test_post_event_timeout(self, monkeypatch, clean_env):
        def fake_urlopen(request, timeout=None):
            raise TimeoutError("slow")

        monkeypatch.setattr(activity.urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(ActivityError, match="timed out"):
            post_event({"kind": "x"})

    def test_post_event_invalid_response(self, monkeypatch, clean_env):
        monkeypatch.setattr(
            activity.urllib.request,
            "urlopen",
            lambda request, timeout=None: _FakeResponse(b"not json"),
        )
        with pytest.raises(ActivityError, match="unexpected response"):
            post_event({"kind": "x"})

    def test_post_event_non_json_body_rejected(self):
        with pytest.raises(ActivityConfigError):
            post_event({"kind": object()})  # type: ignore[dict-item]

    def test_emit_activity_builds_body(self, monkeypatch, clean_env):
        calls = []

        def fake_post(body, **kwargs):
            calls.append({"body": body, **kwargs})
            return {"id": 3}

        monkeypatch.setattr(activity, "post_event", fake_post)
        event = emit_activity(
            "run.started",
            actor="dsh",
            actor_kind="agent",
            harness="dsh",
        )
        assert event == {"id": 3}
        assert calls[0]["body"] == {
            "kind": "run.started",
            "actor": "dsh",
            "actor_kind": "agent",
            "harness": "dsh",
        }

    @pytest.mark.asyncio
    async def test_emit_activity_async(self, monkeypatch, clean_env):
        calls = []

        def fake_post(body, **kwargs):
            calls.append(body)
            return {"id": 11}

        monkeypatch.setattr(activity, "post_event", fake_post)
        event = await emit_activity_async("tool.invoked", actor="dsh")
        assert event == {"id": 11}
        assert calls[0]["kind"] == "tool.invoked"


# ---------------------------------------------------------------------------
# sugar emit CLI
# ---------------------------------------------------------------------------


class TestEmitCLI:
    def test_default_invocation(self, runner, clean_env, fake_post):
        result = runner.invoke(cli, ["emit"])
        assert result.exit_code == 0, result.output
        assert "emitted event 42" in result.output
        body = fake_post[0]["body"]
        assert body["kind"] == "tool.invoked"
        assert body["harness"] == "sugar"
        assert body["actor_kind"] == "human"
        assert "@" in body["actor"]
        assert "payload" not in body
        assert fake_post[0]["server"] is None
        assert fake_post[0]["token"] is None

    def test_actor_env_and_explicit_override(self, runner, clean_env, fake_post):
        clean_env.setenv("SUGAR_ACTOR", "env-agent")
        clean_env.setenv("SUGAR_ACTOR_KIND", "agent")
        result = runner.invoke(cli, ["emit"])
        assert result.exit_code == 0, result.output
        assert fake_post[0]["body"]["actor"] == "env-agent"
        assert fake_post[0]["body"]["actor_kind"] == "agent"

        result = runner.invoke(
            cli,
            ["emit", "--actor", "explicit-agent", "--actor-kind", "system"],
        )
        assert result.exit_code == 0, result.output
        assert fake_post[1]["body"]["actor"] == "explicit-agent"
        assert fake_post[1]["body"]["actor_kind"] == "system"

    def test_payload_merging_and_json_coercion(self, runner, clean_env, fake_post):
        result = runner.invoke(
            cli,
            [
                "emit",
                "--kind",
                "tool.invoked",
                "--task-id",
                "task-1",
                "--session-id",
                "sess-1",
                "--project",
                "proj-1",
                "--message",
                "running pytest",
                "--payload",
                "n=3",
                "--payload",
                "ok=true",
                "--payload",
                'meta={"a":1}',
                "--payload",
                "text=hello",
                "--payload",
                "nothing=null",
            ],
        )
        assert result.exit_code == 0, result.output
        body = fake_post[0]["body"]
        assert body["kind"] == "tool.invoked"
        assert body["harness"] == "sugar"
        assert body["task_id"] == "task-1"
        assert body["session_id"] == "sess-1"
        assert body["project"] == "proj-1"
        assert body["payload"] == {
            "n": 3,
            "ok": True,
            "meta": {"a": 1},
            "text": "hello",
            "nothing": None,
            "message": "running pytest",
        }

    def test_json_output(self, runner, clean_env, fake_post):
        result = runner.invoke(
            cli, ["emit", "--kind", "run.started", "--task-id", "t-9", "--json"]
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert set(payload) == {"id", "kind", "task_id", "actor"}
        assert payload["id"] == 42
        assert payload["kind"] == "run.started"
        assert payload["task_id"] == "t-9"
        assert payload["actor"]

    def test_server_and_token_passthrough_never_printed(
        self, runner, clean_env, fake_post
    ):
        result = runner.invoke(
            cli,
            [
                "emit",
                "--server",
                "http://board.test:9000",
                "--token",
                "super-secret-token",
            ],
        )
        assert result.exit_code == 0, result.output
        assert fake_post[0]["server"] == "http://board.test:9000"
        assert fake_post[0]["token"] == "super-secret-token"
        assert "super-secret-token" not in result.output

    def test_env_server_and_token_defaults_are_resolved(self, runner, clean_env):
        clean_env.setenv("SUGAR_BOARD_URL", "http://env-board:9100")
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            return _FakeResponse({"event": {"id": 5, "kind": "tool.invoked"}})

        clean_env.setattr(activity.urllib.request, "urlopen", fake_urlopen)
        clean_env.setenv("SUGAR_BOARD_TOKEN", "env-token")
        result = runner.invoke(cli, ["emit"])
        assert result.exit_code == 0, result.output
        assert captured["url"] == "http://env-board:9100/api/events"
        assert captured["auth"] == "Bearer env-token"
        assert "env-token" not in result.output

    def test_bad_payload_is_exit_2(self, runner, clean_env, fake_post):
        result = runner.invoke(cli, ["emit", "--payload", "not-a-pair"])
        assert result.exit_code == 2
        assert "--payload" in result.output
        assert fake_post == []

    def test_empty_kind_is_exit_2(self, runner, clean_env, fake_post):
        result = runner.invoke(cli, ["emit", "--kind", ""])
        assert result.exit_code == 2
        assert fake_post == []

    def test_invalid_actor_kind_is_exit_2(self, runner, clean_env, fake_post):
        result = runner.invoke(cli, ["emit", "--actor-kind", "robot"])
        assert result.exit_code == 2
        assert fake_post == []

    def test_bad_server_url_is_exit_2(self, runner, clean_env):
        result = runner.invoke(cli, ["emit", "--server", "not-a-url"])
        assert result.exit_code == 2
        assert "invalid board URL" in result.output

    @pytest.mark.parametrize(
        "error",
        [
            ActivityError("board rejected the event (HTTP 401): unauthorized"),
            ActivityError("cannot reach the Sugar board at http://x/api/events"),
            ActivityError("timed out after 5.0s contacting the board"),
        ],
    )
    def test_transport_errors_are_exit_1(self, runner, clean_env, monkeypatch, error):
        def fake_post(body, **kwargs):
            raise error

        monkeypatch.setattr(activity, "post_event", fake_post)
        result = runner.invoke(cli, ["emit", "--token", "super-secret-token"])
        assert result.exit_code == 1
        assert "❌" in result.output
        assert "super-secret-token" not in result.output

    def test_http_error_message_does_not_include_token(
        self, runner, clean_env, monkeypatch
    ):
        def fake_urlopen(request, timeout=None):
            body = b'{"error":"unauthorized","detail":"bad token"}'
            raise urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", {}, io.BytesIO(body)
            )

        monkeypatch.setattr(activity.urllib.request, "urlopen", fake_urlopen)
        result = runner.invoke(
            cli, ["emit", "--token", "super-secret-token", "--message", "do-not-log"]
        )
        assert result.exit_code == 1
        assert "super-secret-token" not in result.output
        assert "do-not-log" not in result.output

    def test_help_has_examples_and_flags(self, runner):
        result = runner.invoke(cli, ["emit", "--help"])
        assert result.exit_code == 0
        for flag in (
            "--kind",
            "--actor",
            "--actor-kind",
            "--task-id",
            "--harness",
            "--payload",
            "--server",
            "--token",
            "--json",
        ):
            assert flag in result.output
        assert "sugar emit --kind tool.invoked" in result.output
        assert "tests/test_api.py" in result.output
        assert "docs/telemetry.md" in result.output


# ---------------------------------------------------------------------------
# MCP sugar_emit_event
# ---------------------------------------------------------------------------


def _mcp_tool(name: str):
    if not FASTMCP_AVAILABLE:  # pragma: no cover - optional dependency
        pytest.skip("MCP dependencies not installed")
    mcp = create_task_mcp_server()
    return mcp._tool_manager._tools[name].fn


@pytest.mark.skipif(not FASTMCP_AVAILABLE, reason="MCP dependencies not installed")
class TestMcpEmitTool:
    def test_tool_is_registered_with_expected_signature(self):
        mcp = create_task_mcp_server()
        assert "sugar_emit_event" in mcp._tool_manager._tools
        tool = _mcp_tool("sugar_emit_event")
        import inspect

        parameters = list(inspect.signature(tool).parameters)
        assert parameters == ["kind", "message", "task_id", "payload", "actor"]

    @pytest.mark.asyncio
    async def test_tool_posts_merged_payload(self, monkeypatch, clean_env):
        calls = []

        async def fake_emit(kind, **kwargs):
            calls.append({"kind": kind, **kwargs})
            return {"id": 77}

        monkeypatch.setattr("sugar.mcp.task_server.emit_activity_async", fake_emit)
        tool = _mcp_tool("sugar_emit_event")
        result = await tool(
            kind="tool.invoked",
            message="running pytest",
            task_id="task-1",
            payload={"tool": "bash"},
            actor="dsh",
        )
        assert result == {"ok": True, "id": 77}
        call = calls[0]
        assert call["kind"] == "tool.invoked"
        assert call["actor"] == "dsh"
        assert call["actor_kind"] == "agent"
        assert call["task_id"] == "task-1"
        assert call["harness"] == "mcp"
        assert call["payload"] == {"tool": "bash", "message": "running pytest"}

    @pytest.mark.asyncio
    async def test_tool_never_raises_on_activity_error(self, monkeypatch, clean_env):
        async def fake_emit(kind, **kwargs):
            raise ActivityError("cannot reach the Sugar board")

        monkeypatch.setattr("sugar.mcp.task_server.emit_activity_async", fake_emit)
        tool = _mcp_tool("sugar_emit_event")
        result = await tool(kind="tool.invoked")
        assert result["ok"] is False
        assert "cannot reach" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_never_raises_on_unexpected_error(self, monkeypatch, clean_env):
        async def fake_emit(kind, **kwargs):
            raise RuntimeError("boom: super-secret-token")

        monkeypatch.setattr("sugar.mcp.task_server.emit_activity_async", fake_emit)
        tool = _mcp_tool("sugar_emit_event")
        result = await tool(kind="tool.invoked")
        assert result["ok"] is False
        assert "RuntimeError" in result["error"]
        assert "super-secret-token" not in result["error"]

    @pytest.mark.asyncio
    async def test_tool_rejects_non_object_payload(self, clean_env):
        tool = _mcp_tool("sugar_emit_event")
        result = await tool(kind="tool.invoked", payload=["nope"])  # type: ignore[arg-type]
        assert result == {"ok": False, "error": "payload must be a JSON object"}

    @pytest.mark.asyncio
    async def test_helper_returns_error_on_empty_kind(self, monkeypatch, clean_env):
        async def fake_emit(kind, **kwargs):
            raise ActivityConfigError("event kind must not be empty")

        monkeypatch.setattr("sugar.mcp.task_server.emit_activity_async", fake_emit)
        result = await emit_activity_event("")
        assert result["ok"] is False
        assert "kind" in result["error"]
