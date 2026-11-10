"""Tests for remote/team mode on the board server (docs/board-server.md).

Covers scoped bearer tokens, the browser cookie exchange, CLI refusal rules and
the token-file parser. The default loopback/no-auth behaviour is asserted to be
unchanged.
"""

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app
from sugar.dashboard.security import (
    AUTH_COOKIE_NAME,
    BoardAuthMiddleware,
    BoardToken,
    TokenConfigError,
    TokenRegistry,
    build_token_registry,
    load_token_file,
    origin_allowed,
)
from sugar.main import cli

READ_SECRET = "read-token-3f1c9a2d"
WRITE_SECRET = "write-token-9d8c7b6a"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def token_registry() -> TokenRegistry:
    return TokenRegistry(
        [
            BoardToken("alice", "read", READ_SECRET),
            BoardToken("bob", "write", WRITE_SECRET),
        ]
    )


def bearer(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"}


def write_token_file(tmp_path: Path, lines, *, mode: int = 0o600) -> Path:
    path = tmp_path / "board-tokens"
    path.write_text("\n".join(str(line) for line in lines) + "\n", encoding="utf-8")
    os.chmod(path, mode)
    return path


def cli_config(tmp_path: Path) -> Path:
    """A minimal config so CLI tests never touch the repo's .sugar directory."""
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


def _asgi_scope(path: str, method: str = "GET", headers=None):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in (headers or {}).items()
        ],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "root_path": "",
    }


async def _run_middleware(middleware, scope):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await middleware(scope, receive, send)
    return messages


# ---------------------------------------------------------------------------
# Default mode regression
# ---------------------------------------------------------------------------


def test_local_mode_is_unchanged(tmp_path):
    app = create_app(str(tmp_path / "sugar.db"))
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/tasks").status_code == 200
        assert (
            client.post("/api/tasks", json={"title": "local task"}).status_code == 201
        )
        # A stray ?token= is ignored when no tokens are configured.
        response = client.get("/?token=whatever", follow_redirects=False)
        assert response.status_code == 200
        assert "set-cookie" not in response.headers


# ---------------------------------------------------------------------------
# Token file parsing
# ---------------------------------------------------------------------------


class TestTokenFile:
    def test_valid_file_scopes(self, tmp_path):
        path = write_token_file(
            tmp_path,
            [
                "# comment",
                "",
                "alice:read:read-secret-123",
                "bob:write:write-secret-456",
            ],
        )
        registry = build_token_registry(token_file=path)
        assert len(registry) == 2
        assert registry.find("read-secret-123").scope == "read"
        assert registry.find("write-secret-456").scope == "write"
        assert registry.find("wrong") is None
        assert "alice=read" in registry.describe()
        assert "read-secret-123" not in registry.describe()

    def test_write_implies_read(self):
        token = BoardToken("bob", "write", "secret")
        assert token.allows("GET")
        assert token.allows("HEAD")
        assert token.allows("POST")
        assert token.allows("DELETE")
        read = BoardToken("alice", "read", "secret")
        assert read.allows("GET")
        assert read.allows("HEAD")
        assert not read.allows("POST")
        assert not read.allows("DELETE")

    def test_missing_file(self, tmp_path):
        with pytest.raises(TokenConfigError, match="not found"):
            load_token_file(tmp_path / "nope")

    def test_empty_file(self, tmp_path):
        path = write_token_file(tmp_path, ["# only comments", ""])
        with pytest.raises(TokenConfigError, match="no tokens"):
            build_token_registry(token_file=path)

    @pytest.mark.parametrize(
        "line,message",
        [
            ("alice:admin:secret", "invalid scope"),
            ("alice:read", "name:scope:token"),
            (":read:secret", "name must not be empty"),
            ("alice:read:", "must not be empty"),
            ("alice:read:has space", "whitespace"),
            ("alice:read:has;semicolon", "semicolons"),
            ("alice:read:tökén", "printable ASCII"),
        ],
    )
    def test_bad_lines(self, tmp_path, line, message):
        path = write_token_file(tmp_path, [line])
        with pytest.raises(TokenConfigError, match=message) as excinfo:
            build_token_registry(token_file=path)
        assert "line 1" in str(excinfo.value)

    def test_duplicate_names(self, tmp_path):
        path = write_token_file(
            tmp_path, ["alice:read:first-secret-1", "Alice:write:second-secret-2"]
        )
        with pytest.raises(TokenConfigError, match="duplicate token name"):
            build_token_registry(token_file=path)

    def test_duplicate_secrets(self, tmp_path):
        path = write_token_file(
            tmp_path, ["alice:read:same-secret", "bob:write:same-secret"]
        )
        with pytest.raises(TokenConfigError, match="duplicate token secret"):
            build_token_registry(token_file=path)

    def test_adhoc_token_is_write_scope(self):
        registry = build_token_registry(adhoc_token="adhoc-secret-123")
        token = registry.find("adhoc-secret-123")
        assert token.scope == "write"
        assert token.allows("POST")

    def test_adhoc_token_duplicates_file_secret(self, tmp_path):
        path = write_token_file(tmp_path, ["alice:read:shared-secret-123"])
        with pytest.raises(TokenConfigError, match="duplicate token secret"):
            build_token_registry(token_file=path, adhoc_token="shared-secret-123")

    def test_no_token_source(self):
        with pytest.raises(TokenConfigError, match="no board tokens"):
            build_token_registry()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")
    def test_permissive_mode_is_refused(self, tmp_path):
        path = write_token_file(tmp_path, ["alice:read:read-secret-123"], mode=0o644)
        with pytest.raises(TokenConfigError, match="chmod 600") as excinfo:
            build_token_registry(token_file=path)
        assert "read-secret-123" not in str(excinfo.value)

    @pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")
    def test_0600_is_accepted(self, tmp_path):
        path = write_token_file(tmp_path, ["alice:read:read-secret-123"], mode=0o600)
        assert len(build_token_registry(token_file=path)) == 1


# ---------------------------------------------------------------------------
# Bearer auth and scopes
# ---------------------------------------------------------------------------


class TestBearerAuth:
    @pytest.fixture
    def client(self, tmp_path):
        app = create_app(str(tmp_path / "auth.db"), tokens=token_registry())
        with TestClient(app) as test_client:
            yield test_client

    def test_health_is_public(self, client):
        assert client.get("/api/health").status_code == 200

    def test_health_is_minimal_in_token_mode(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        # Liveness only: no db path, read-only flag or uptime without auth.
        assert set(body) == {"ok", "version"}
        assert body["ok"] is True
        assert "db" not in body

    def test_missing_token_is_401(self, client):
        response = client.get("/api/tasks")
        assert response.status_code == 401
        assert response.json()["error"] == "unauthorized"
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_wrong_token_is_401_and_never_echoed(self, client):
        response = client.get("/api/tasks", headers=bearer("definitely-wrong"))
        assert response.status_code == 401
        assert "definitely-wrong" not in response.text
        assert response.json()["error"] == "unauthorized"

    def test_malformed_authorization_is_401(self, client):
        assert (
            client.get("/api/tasks", headers={"Authorization": "Token abc"}).status_code
            == 401
        )
        assert (
            client.get("/api/tasks", headers={"Authorization": "Bearer"}).status_code
            == 401
        )

    def test_read_token_can_read(self, client):
        assert client.get("/api/tasks", headers=bearer(READ_SECRET)).status_code == 200
        assert client.head("/api/tasks", headers=bearer(READ_SECRET)).status_code == 200
        assert client.get("/api/stats", headers=bearer(READ_SECRET)).status_code == 200

    def test_bearer_scheme_is_case_insensitive(self, client):
        response = client.get(
            "/api/tasks", headers={"Authorization": f"bearer {READ_SECRET}"}
        )
        assert response.status_code == 200

    def test_read_token_cannot_write(self, client):
        response = client.post(
            "/api/tasks", json={"title": "nope"}, headers=bearer(READ_SECRET)
        )
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden_scope"
        assert (
            client.get("/api/tasks", headers=bearer(READ_SECRET)).json()["count"] == 0
        )

    def test_write_token_can_write(self, client):
        created = client.post(
            "/api/tasks", json={"title": "remote task"}, headers=bearer(WRITE_SECRET)
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["task"]["id"]
        patched = client.patch(
            f"/api/tasks/{task_id}",
            json={"title": "renamed"},
            headers=bearer(WRITE_SECRET),
        )
        assert patched.status_code == 200
        assert (
            client.delete(
                f"/api/tasks/{task_id}", headers=bearer(WRITE_SECRET)
            ).status_code
            == 200
        )

    def test_query_string_token_is_not_accepted_on_api(self, client):
        response = client.get(f"/api/tasks?token={READ_SECRET}")
        assert response.status_code == 401

    def test_activity_endpoint_requires_auth(self, tmp_path):
        app = create_app(
            str(tmp_path / "spine.db"),
            tokens=token_registry(),
            events_db=str(tmp_path / "events.db"),
        )
        with TestClient(app) as client:
            assert client.get("/api/activity").status_code == 401
            assert (
                client.get("/api/activity", headers=bearer(READ_SECRET)).status_code
                == 200
            )


def test_read_only_mode_still_rejects_writes_with_write_token(tmp_path):
    app = create_app(str(tmp_path / "ro.db"), read_only=True, tokens=token_registry())
    with TestClient(app) as client:
        response = client.post(
            "/api/tasks", json={"title": "nope"}, headers=bearer(WRITE_SECRET)
        )
        assert response.status_code == 403
        assert response.json()["error"] == "read_only"
        assert client.get("/api/tasks", headers=bearer(READ_SECRET)).status_code == 200


# ---------------------------------------------------------------------------
# Browser cookie flow
# ---------------------------------------------------------------------------


class TestCookieFlow:
    @pytest.fixture
    def client(self, tmp_path):
        app = create_app(str(tmp_path / "cookie.db"), tokens=token_registry())
        with TestClient(app) as test_client:
            yield test_client

    def test_token_url_sets_cookie_and_redirects(self, client):
        response = client.get("/?token=" + READ_SECRET, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert READ_SECRET not in response.headers["location"]
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{AUTH_COOKIE_NAME}=")
        assert "HttpOnly" in cookie
        assert "samesite=strict" in cookie.lower()
        assert "Path=/" in cookie
        assert "Secure" not in cookie
        assert "no-store" in response.headers.get("cache-control", "")

    def test_cookie_authenticates_reads(self, client):
        client.get("/?token=" + READ_SECRET, follow_redirects=False)
        assert client.get("/api/tasks").status_code == 200
        assert client.get("/api/meta").status_code == 200

    def test_read_cookie_cannot_write(self, client):
        client.get("/?token=" + READ_SECRET, follow_redirects=False)
        response = client.post("/api/tasks", json={"title": "cookie write"})
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden_scope"

    def test_write_cookie_can_write(self, client):
        client.get("/?token=" + WRITE_SECRET, follow_redirects=False)
        response = client.post("/api/tasks", json={"title": "cookie task"})
        assert response.status_code == 201, response.text

    def test_invalid_token_url_is_401_without_cookie(self, client):
        response = client.get("/?token=wrong-token", follow_redirects=False)
        assert response.status_code == 401
        assert "set-cookie" not in response.headers
        assert "wrong-token" not in response.text

    def test_bad_cookie_is_401(self, client):
        client.cookies.set(AUTH_COOKIE_NAME, "not-a-valid-token")
        assert client.get("/api/tasks").status_code == 401

    def test_cookie_is_secure_on_https(self, tmp_path):
        app = create_app(str(tmp_path / "tls.db"), tokens=token_registry())
        with TestClient(app, base_url="https://testserver") as client:
            response = client.get("/?token=" + READ_SECRET, follow_redirects=False)
            assert response.status_code == 303
            assert "Secure" in response.headers["set-cookie"]

    def test_ui_itself_is_public(self, client):
        # Served so the browser can show the "open /?token=..." hint; all data
        # endpoints remain protected.
        assert client.get("/").status_code == 200


# ---------------------------------------------------------------------------
# Origin guard + token mode (remote browser writes)
# ---------------------------------------------------------------------------


class TestRemoteOriginPolicy:
    def test_origin_allowed_same_host(self):
        assert origin_allowed("http://board.lan:7331", "board.lan:7331") is True
        assert origin_allowed("https://board.example.com", "board.example.com") is True
        assert origin_allowed("http://evil.example", "board.lan:7331") is False
        assert origin_allowed("http://board.lan", "board.lan:7331") is False
        # Loopback origins stay allowed and no request_host changes local mode.
        assert origin_allowed("http://127.0.0.1:7331", "board.lan:7331") is True
        assert origin_allowed("http://127.0.0.1:7331") is True

    def test_token_mode_allows_same_host_browser_write(self, tmp_path):
        app = create_app(str(tmp_path / "origin.db"), tokens=token_registry())
        with TestClient(app) as client:
            response = client.post(
                "/api/tasks",
                json={"title": "same host"},
                headers={
                    **bearer(WRITE_SECRET),
                    "Origin": "http://testserver",
                },
            )
            assert response.status_code == 201, response.text

    def test_token_mode_blocks_foreign_origin_write(self, tmp_path):
        app = create_app(str(tmp_path / "origin.db"), tokens=token_registry())
        with TestClient(app) as client:
            response = client.post(
                "/api/tasks",
                json={"title": "evil"},
                headers={**bearer(WRITE_SECRET), "Origin": "https://evil.example"},
            )
            assert response.status_code == 403
            assert response.json()["error"] == "forbidden_origin"

    def test_local_mode_still_blocks_same_host_origin(self, tmp_path):
        app = create_app(str(tmp_path / "plain.db"))
        with TestClient(app) as client:
            response = client.post(
                "/api/tasks",
                json={"title": "same host"},
                headers={"Origin": "http://testserver"},
            )
            assert response.status_code == 403
            assert response.json()["error"] == "forbidden_origin"


# ---------------------------------------------------------------------------
# SSE auth (middleware level; streaming bodies are tested elsewhere)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_endpoint_requires_auth():
    called = {"value": False}

    async def inner(scope, receive, send):
        called["value"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"stream"})

    middleware = BoardAuthMiddleware(inner, tokens=token_registry())
    messages = await _run_middleware(middleware, _asgi_scope("/api/events"))
    assert messages[0]["status"] == 401
    assert called["value"] is False

    messages = await _run_middleware(
        middleware,
        _asgi_scope(
            "/api/events", headers={"cookie": f"{AUTH_COOKIE_NAME}={READ_SECRET}"}
        ),
    )
    assert called["value"] is True
    assert messages[0]["status"] == 200

    called["value"] = False
    messages = await _run_middleware(
        middleware, _asgi_scope("/api/events", headers=bearer(READ_SECRET))
    )
    assert called["value"] is True
    assert messages[0]["status"] == 200


# ---------------------------------------------------------------------------
# Tokens never reach the activity ledger / responses
# ---------------------------------------------------------------------------


def test_tokens_are_not_written_to_the_activity_spine(tmp_path):
    app = create_app(
        str(tmp_path / "ledger.db"),
        tokens=token_registry(),
        events_db=str(tmp_path / "events.db"),
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/tasks", json={"title": "audited task"}, headers=bearer(WRITE_SECRET)
        )
        assert created.status_code == 201
        activity = client.get("/api/activity", headers=bearer(WRITE_SECRET))
        assert activity.status_code == 200
        text = activity.text
        assert READ_SECRET not in text
        assert WRITE_SECRET not in text
        # The ledger is still useful: the write produced a task event.
        assert "task" in text


# ---------------------------------------------------------------------------
# CLI: bind refusal, token loading, --host back-compat
# ---------------------------------------------------------------------------


class TestDashboardCLI:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def config(self, tmp_path):
        return cli_config(tmp_path)

    @pytest.fixture
    def fake_run(self, monkeypatch):
        import sugar.dashboard.server as server_module

        calls = []

        async def fake_run_dashboard(db_path, **kwargs):
            calls.append({"db_path": db_path, **kwargs})

        monkeypatch.setattr(server_module, "run_dashboard", fake_run_dashboard)
        return calls

    def invoke(self, runner, config, *args):
        return runner.invoke(cli, ["--config", str(config), "dashboard", *args])

    def test_non_loopback_without_allow_remote_is_refused(self, runner, config):
        result = self.invoke(runner, config, "--bind", "0.0.0.0")
        assert result.exit_code == 1
        assert "--allow-remote" in result.output
        assert "loopback-only" in result.output

    def test_non_loopback_without_tokens_is_refused(self, runner, config):
        result = self.invoke(runner, config, "--bind", "0.0.0.0", "--allow-remote")
        assert result.exit_code == 1
        assert "--token-file" in result.output

    def test_token_file_with_bad_permissions_is_refused(self, runner, config, tmp_path):
        if os.name != "posix":
            pytest.skip("POSIX file modes only")
        path = write_token_file(tmp_path, ["alice:read:read-secret-123"], mode=0o644)
        result = self.invoke(
            runner,
            config,
            "--bind",
            "0.0.0.0",
            "--allow-remote",
            "--token-file",
            str(path),
        )
        assert result.exit_code == 1
        assert "chmod 600" in result.output

    def test_missing_token_file_is_refused(self, runner, config, tmp_path):
        result = self.invoke(
            runner,
            config,
            "--bind",
            "0.0.0.0",
            "--allow-remote",
            "--token-file",
            str(tmp_path / "missing-tokens"),
        )
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_host_and_bind_are_mutually_exclusive(self, runner, config):
        result = self.invoke(
            runner, config, "--host", "127.0.0.1", "--bind", "127.0.0.1"
        )
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output

    def test_host_is_a_deprecated_alias(self, runner, config, tmp_path, fake_run):
        result = self.invoke(
            runner,
            config,
            "--host",
            "127.0.0.1",
            "--db",
            str(tmp_path / "sugar.db"),
        )
        assert result.exit_code == 0, result.output
        assert "deprecated" in result.output
        assert fake_run[-1]["host"] == "127.0.0.1"
        assert fake_run[-1]["tokens"] is None

    def test_loopback_with_token_file_enables_auth(
        self, runner, config, tmp_path, fake_run
    ):
        tokens = write_token_file(
            tmp_path,
            ["alice:read:read-secret-abc123", "bob:write:write-secret-def456"],
        )
        result = self.invoke(
            runner,
            config,
            "--token-file",
            str(tokens),
            "--db",
            str(tmp_path / "sugar.db"),
        )
        assert result.exit_code == 0, result.output
        assert "alice=read" in result.output
        assert "read-secret-abc123" not in result.output
        assert "write-secret-def456" not in result.output
        registry = fake_run[-1]["tokens"]
        assert registry.find("read-secret-abc123").scope == "read"

    def test_remote_bind_with_token_file_starts(
        self, runner, config, tmp_path, fake_run
    ):
        tokens = write_token_file(tmp_path, ["bob:write:write-secret-def456"])
        result = self.invoke(
            runner,
            config,
            "--bind",
            "192.168.1.20",
            "--allow-remote",
            "--token-file",
            str(tokens),
            "--db",
            str(tmp_path / "sugar.db"),
        )
        assert result.exit_code == 0, result.output
        assert fake_run[-1]["host"] == "192.168.1.20"
        assert fake_run[-1]["tokens"].find("write-secret-def456").scope == "write"

    def test_adhoc_token_is_write_scope_and_never_echoed(
        self, runner, config, tmp_path, fake_run
    ):
        secret = "one-off-write-secret-xyz"
        result = self.invoke(
            runner,
            config,
            "--token",
            secret,
            "--db",
            str(tmp_path / "sugar.db"),
        )
        assert result.exit_code == 0, result.output
        assert "prefer --token-file" in result.output
        assert secret not in result.output
        assert fake_run[-1]["tokens"].find(secret).scope == "write"

    def test_empty_adhoc_token_is_refused(self, runner, config, tmp_path):
        result = self.invoke(
            runner, config, "--token", "", "--db", str(tmp_path / "sugar.db")
        )
        assert result.exit_code == 1
        assert "must not be empty" in result.output

    def test_help_documents_remote_flags(self, runner):
        result = runner.invoke(cli, ["dashboard", "--help"])
        assert result.exit_code == 0
        for flag in ("--bind", "--allow-remote", "--token-file", "--token"):
            assert flag in result.output
        assert "docs/board-server.md" in result.output
