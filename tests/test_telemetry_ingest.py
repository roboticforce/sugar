"""Tests for the Phase-0 telemetry ingest core.

Covers canonical schema, per-harness normalization, redaction, dedupe, the
shared pipeline, Unix-socket transport, HTTP hook/evidence endpoints and the
coverage report. No network: only temp SQLite files and Unix sockets.
"""

import asyncio
import json
import shutil
import socket
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app
from sugar.dashboard.security import BoardToken, TokenRegistry
from sugar.dashboard.spine import EventSpine
from sugar.telemetry import schema
from sugar.telemetry.coverage import (
    coverage_report,
    load_adapters,
    read_recent_events,
)
from sugar.telemetry.dedupe import Deduper, dedupe_key
from sugar.telemetry.normalize import (
    normalize_hook,
    normalize_harness_name,
)
from sugar.telemetry.pipeline import TelemetryPipeline
from sugar.telemetry.redact import (
    redact_payload,
    redact_text,
    resolve_level,
    resolve_max_bytes,
)
from sugar.telemetry.socket import IngestSocketServer, resolve_socket_path

CLAUDE_PRE = {
    "session_id": "sess-1",
    "cwd": "/repo",
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_use_id": "toolu_1",
    "tool_input": {
        "command": "pytest tests/test_api.py",
        "description": "run the API tests",
    },
}
CLAUDE_POST = {
    "session_id": "sess-1",
    "cwd": "/repo",
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_use_id": "toolu_1",
    "tool_response": {"stdout": "3 passed", "exit_code": 0},
}
CLAUDE_FAIL = {
    "session_id": "sess-1",
    "hook_event_name": "PostToolUseFailure",
    "tool_name": "Bash",
    "tool_use_id": "toolu_2",
    "error": "exit code 1",
}
CODEX_PRE = {
    "event": "tool_call",
    "session_id": "sess-2",
    "call_id": "call-1",
    "cwd": "/repo",
    "tool": {"name": "shell", "input": {"command": "ls -la"}},
}
CURSOR_PRE = {
    "event": "beforeShellExecution",
    "conversation_id": "conv-1",
    "generation_id": "gen-1",
    "cwd": "/repo",
    "command": "pytest -q",
}
CURSOR_SESSION = {"event": "sessionStart", "conversation_id": "conv-1"}


@pytest.fixture
def short_socket():
    """Unix-socket path short enough for macOS AF_UNIX limits (~104 bytes)."""
    directory = Path(tempfile.mkdtemp(prefix="sg-", dir="/tmp"))
    path = directory / "i.sock"
    try:
        yield path
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def run(coro):
    return asyncio.run(coro)


async def make_spine(path) -> EventSpine:
    spine = EventSpine(str(path), retention_days=0)
    await spine.start()
    return spine


def list_events(path):
    async def go():
        spine = await make_spine(path)
        try:
            events, _ = await spine.list_events(limit=100)
            return events
        finally:
            await spine.close()

    return run(go())


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_build_envelope_canonical_fields(self):
        body = schema.build_envelope(
            kind="tool.completed",
            harness="claude-code",
            summary={"command": "pytest"},
            actor="claude-code",
            session_id="sess-1",
            tool_use_id="toolu_1",
            tool_name="Bash",
            status="completed",
            duration_ms=1200,
            cwd="/repo",
            fidelity="L3",
            ts="2026-11-19T14:00:00Z",
        )
        payload = body["payload"]
        assert payload["schema"] == schema.SCHEMA
        assert payload["fidelity"] == "L3"
        assert payload["tool_use_id"] == "toolu_1"
        assert payload["tool_name"] == "Bash"
        assert payload["status"] == "completed"
        assert payload["duration_ms"] == 1200
        assert payload["ts"] == "2026-11-19T14:00:00Z"
        assert payload["payload"] == {"command": "pytest"}
        assert body["kind"] == "tool.completed"
        assert body["actor"] == "claude-code"
        assert body["actor_kind"] == "agent"
        assert body["session_id"] == "sess-1"

    def test_invalid_evidence_kind(self):
        with pytest.raises(schema.TelemetryValidationError):
            schema.build_evidence_event({"kind": "tool.completed"})

    def test_evidence_event_shape(self):
        body = schema.build_evidence_event(
            {
                "kind": "verification.test_run",
                "harness": "sugar",
                "task_id": "t1",
                "payload": {"passed": 12, "failed": 0},
            }
        )
        assert body["kind"] == "verification.test_run"
        assert body["payload"]["schema"] == schema.SCHEMA
        assert body["payload"]["fidelity"] == "L3"
        assert body["payload"]["payload"] == {"passed": 12, "failed": 0}

    def test_validate_envelope(self):
        body = schema.build_envelope(
            kind="tool.started",
            harness="cursor",
            summary={"command": "ls"},
            fidelity="L2",
            ts="2026-11-19T14:00:00Z",
        )
        normalized = schema.validate_envelope(body)
        assert normalized["kind"] == "tool.started"
        assert normalized["ts"] == "2026-11-19T14:00:00Z"
        for mutation in (
            {**body, "payload": {}},
            {**body, "payload": {**body["payload"], "schema": "other"}},
            {**body, "payload": {**body["payload"], "fidelity": "L9"}},
            {**body, "ts": "not-a-time"},
        ):
            with pytest.raises(schema.TelemetryValidationError):
                schema.validate_envelope(mutation)

    def test_kind_groups_and_fidelity(self):
        assert "tool.started" in schema.TOOL_KINDS
        assert "run.ended" in schema.KIND_GROUPS["run"]
        assert schema.normalize_fidelity("l3") == "L3"
        assert schema.normalize_fidelity("bogus") == "L1"


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_harness_aliases(self):
        assert normalize_harness_name("claude-code") == "claude-code"
        assert normalize_harness_name("claude") == "claude-code"
        assert normalize_harness_name("Cursor") == "cursor"
        assert normalize_harness_name("codex") == "codex"
        assert normalize_harness_name("goose") is None
        assert normalize_harness_name(None) is None

    def test_claude_pre_tool_use(self):
        event = normalize_hook("claude-code", CLAUDE_PRE)
        assert event is not None
        assert event.kind == "tool.started"
        assert event.fidelity == "L3"
        assert event.session_id == "sess-1"
        assert event.tool_use_id == "toolu_1"
        assert event.tool_name == "Bash"
        assert event.status == "started"
        assert event.cwd == "/repo"
        assert event.summary["command"] == "pytest tests/test_api.py"
        assert event.summary["description"] == "run the API tests"
        assert event.summary["tool_name"] == "Bash"

    def test_claude_post_and_failure(self):
        completed = normalize_hook("claude-code", CLAUDE_POST)
        assert completed.kind == "tool.completed"
        assert completed.status == "completed"
        assert completed.summary["result"]["stdout"] == "3 passed"
        failed = normalize_hook("claude-code", CLAUDE_FAIL)
        assert failed.kind == "tool.failed"
        assert failed.status == "failed"
        # Missing tool_response still gives L3 for Claude because args/status exist.
        assert failed.fidelity == "L3"

    def test_claude_session_turn_and_ignored(self):
        start = normalize_hook(
            "claude-code",
            {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": "/r"},
        )
        assert start.kind == "session.started"
        assert start.fidelity == "L1"
        stop = normalize_hook(
            "claude-code", {"hook_event_name": "Stop", "session_id": "s1"}
        )
        assert stop.kind == "turn.ended"
        assert (
            normalize_hook("claude-code", {"hook_event_name": "Notification"}) is None
        )
        assert normalize_hook("claude-code", {"hook_event_name": "PreCompact"}) is None

    def test_codex_generic_tool_payload(self):
        event = normalize_hook("codex", CODEX_PRE)
        assert event.kind == "tool.started"
        assert event.fidelity == "L2"
        assert event.tool_name == "Bash"
        assert event.tool_use_id == "call-1"
        assert event.session_id == "sess-2"
        assert event.summary["command"] == "ls -la"
        completed = normalize_hook(
            "codex",
            {
                "event": "tool_result",
                "session_id": "sess-2",
                "call_id": "call-1",
                "tool": {"name": "shell"},
                "output": {"ok": True},
            },
        )
        assert completed.kind == "tool.completed"
        assert completed.summary["result"] == {"ok": True}

    def test_cursor_events(self):
        started = normalize_hook("cursor", CURSOR_PRE)
        assert started.kind == "tool.started"
        assert started.fidelity == "L2"
        assert started.cwd == "/repo"
        assert started.summary["command"] == "pytest -q"
        completed = normalize_hook(
            "cursor",
            {
                "event": "afterShellExecution",
                "conversation_id": "conv-1",
                "command": "pytest -q",
                "output": "ok",
            },
        )
        assert completed.kind == "tool.completed"
        session = normalize_hook("cursor", CURSOR_SESSION)
        assert session.kind == "session.started"
        assert session.session_id == "conv-1"
        turn = normalize_hook(
            "cursor", {"event": "beforeSubmitPrompt", "conversation_id": "c"}
        )
        assert turn.kind == "turn.started"

    def test_usage_normalization(self):
        event = normalize_hook(
            "claude-code",
            {
                "event": "usage.reported",
                "session_id": "s1",
                "usage": {
                    "tokens_in": 100,
                    "tokens_out": 25,
                    "cost_usd": 0.5,
                    "model": "m",
                },
            },
        )
        assert event.kind == "usage.reported"
        assert event.fidelity == "L2"
        assert event.summary["tokens_in"] == 100
        assert event.summary["cost_usd"] == 0.5
        assert event.model is None  # usage model stays in summary, not envelope

    def test_bad_bodies(self):
        assert normalize_hook("claude-code", {"hook_event_name": "Unknown"}) is None
        with pytest.raises(schema.TelemetryValidationError):
            normalize_hook("goose", {})
        with pytest.raises(schema.TelemetryValidationError):
            normalize_hook("claude-code", ["not", "a", "dict"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_levels_resolution(self, monkeypatch):
        monkeypatch.delenv("SUGAR_TELEMETRY_REDACTION", raising=False)
        assert resolve_level() == "summary"
        assert resolve_level("FULL") == "full"
        assert resolve_level("nonsense") == "summary"
        monkeypatch.setenv("SUGAR_TELEMETRY_REDACTION", "metadata")
        assert resolve_level() == "metadata"
        monkeypatch.setenv("SUGAR_TELEMETRY_REDACTION", "bogus")
        assert resolve_level() == "summary"

    def test_metadata_level_drops_payload(self):
        assert (
            redact_payload({"command": "pytest", "secret": "x"}, level="metadata") == {}
        )
        assert redact_text("pytest", level="metadata") == ""

    def test_summary_redacts_secrets_and_truncates(self):
        secret = "ghp_" + "a" * 30
        payload = {
            "command": f"export TOKEN={secret} && curl -H 'Bearer abcdefghijkl'",
            "long": "x" * 2000,
            "nested": {"password": "hunter2", "items": list(range(50))},
        }
        redacted = redact_payload(payload, level="summary")
        text = json.dumps(redacted)
        assert secret not in text
        assert "Bearer abcdefghijkl" not in text
        assert "hunter2" not in text
        assert "[REDACTED]" in text
        assert len(redacted["long"]) <= 500
        assert redacted["_truncated"] is True
        assert len(redacted["nested"]["items"]) <= 20

    def test_full_level_keeps_content_but_secrets_still_redacted(self):
        token = "sk-" + "b" * 30
        redacted = redact_payload({"text": "y" * 1500, "token": token}, level="full")
        assert len(redacted["text"]) == 1500
        assert token not in json.dumps(redacted)
        assert "[REDACTED]" in redacted["token"]

    def test_max_bytes_truncation(self, monkeypatch):
        monkeypatch.setenv("SUGAR_TELEMETRY_MAX_BYTES", "200")
        redacted = redact_payload(
            {"text": "z" * 5000, "more": "q" * 5000}, level="summary"
        )
        encoded = json.dumps(redacted)
        assert len(encoded.encode()) <= 400
        assert redacted.get("_truncated") is True

    def test_invalid_max_bytes_falls_back(self, monkeypatch):
        monkeypatch.setenv("SUGAR_TELEMETRY_MAX_BYTES", "not-a-number")
        assert resolve_max_bytes() > 0
        monkeypatch.setenv("SUGAR_TELEMETRY_MAX_BYTES", "-5")
        assert resolve_max_bytes() > 0

    def test_redact_text_secret_patterns(self):
        for secret in (
            "ghp_" + "q" * 24,
            "sk-" + "c" * 24,
            "AKIA" + "A" * 16,
            "xoxb-" + "1" * 20,
            "password=supersecret",
        ):
            assert secret not in redact_text(secret)


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


class TestDedupe:
    def test_key_requires_tool_use_id(self):
        assert dedupe_key("claude-code", "s", "tool-1", "tool.started") == (
            "claude-code",
            "s",
            "tool-1",
            "tool.started",
        )
        assert dedupe_key("claude-code", "s", None, "tool.started") is None

    def test_ttl_and_recording(self):
        clock = {"now": 1000.0}
        deduper = Deduper(ttl_seconds=10, max_entries=10, clock=lambda: clock["now"])
        assert deduper.is_duplicate("c", "s", "t", "tool.started") is False
        assert deduper.is_duplicate("c", "s", "t", "tool.started") is True
        # Different kind is a different key.
        assert deduper.is_duplicate("c", "s", "t", "tool.completed") is False
        clock["now"] += 11
        assert deduper.is_duplicate("c", "s", "t", "tool.started") is False

    def test_no_tool_use_id_never_deduped(self):
        deduper = Deduper(ttl_seconds=60)
        assert deduper.is_duplicate("c", "s", None, "session.started") is False
        assert deduper.is_duplicate("c", "s", None, "session.started") is False

    def test_lru_bound(self):
        clock = {"now": 0.0}
        deduper = Deduper(ttl_seconds=100, max_entries=2, clock=lambda: clock["now"])
        deduper.is_duplicate("c", "s", "t1", "k")
        deduper.is_duplicate("c", "s", "t2", "k")
        deduper.is_duplicate("c", "s", "t3", "k")
        assert len(deduper) == 2


# ---------------------------------------------------------------------------
# Pipeline + socket
# ---------------------------------------------------------------------------


class TestPipeline:
    def test_ingest_hook_stores_canonical_event(self, tmp_path):
        async def go():
            spine = await make_spine(tmp_path / "events.db")
            pipeline = TelemetryPipeline(spine)
            try:
                result = await pipeline.ingest_hook("claude-code", CLAUDE_PRE)
                return result, await spine.count()
            finally:
                await spine.close()

        result, count = run(go())
        assert result.stored is True
        assert result.event["kind"] == "tool.started"
        assert result.event["payload"]["schema"] == schema.SCHEMA
        assert result.event["payload"]["fidelity"] == "L3"
        assert result.event["harness"] == "claude-code"
        assert count == 1

    def test_duplicate_hook_is_dropped(self, tmp_path):
        async def go():
            spine = await make_spine(tmp_path / "events.db")
            pipeline = TelemetryPipeline(spine)
            try:
                first = await pipeline.ingest_hook("claude-code", CLAUDE_PRE)
                second = await pipeline.ingest_hook("claude-code", CLAUDE_PRE)
                return first, second, await spine.count()
            finally:
                await spine.close()

        first, second, count = run(go())
        assert first.stored is True
        assert second.stored is False and second.duplicate is True
        assert count == 1

    def test_unsupported_hook_is_dropped(self, tmp_path):
        async def go():
            spine = await make_spine(tmp_path / "events.db")
            pipeline = TelemetryPipeline(spine)
            try:
                result = await pipeline.ingest_hook(
                    "claude-code", {"hook_event_name": "Notification"}
                )
                return result, await spine.count()
            finally:
                await spine.close()

        result, count = run(go())
        assert result.stored is False and result.dropped is True
        assert count == 0

    def test_redaction_applied_to_stored_event(self, tmp_path):
        async def go():
            spine = await make_spine(tmp_path / "events.db")
            pipeline = TelemetryPipeline(spine, redaction_level="metadata")
            try:
                result = await pipeline.ingest_hook("claude-code", CLAUDE_PRE)
                return result
            finally:
                await spine.close()

        result = run(go())
        assert result.event["payload"]["payload"] == {}

    def test_socket_pipeline_line(self, tmp_path):
        async def go():
            spine = await make_spine(tmp_path / "events.db")
            pipeline = TelemetryPipeline(spine)
            try:
                result = await pipeline.ingest_socket_message(
                    {"harness": "claude-code", "body": CLAUDE_PRE}
                )
                bad = await pipeline.ingest_socket_message({"harness": "claude-code"})
                return result, bad, await spine.count()
            finally:
                await spine.close()

        result, bad, count = run(go())
        assert result.stored is True
        assert bad.dropped is True
        assert count == 1

    def test_unix_socket_roundtrip_and_permissions(self, tmp_path, short_socket):
        socket_path = short_socket

        async def go():
            spine = await make_spine(tmp_path / "events.db")
            pipeline = TelemetryPipeline(spine)
            server = IngestSocketServer(pipeline, path=str(socket_path))
            try:
                assert await server.start() is True
                assert socket_path.exists()
                reader, writer = await asyncio.open_unix_connection(
                    path=str(socket_path)
                )
                writer.write(
                    (
                        json.dumps({"harness": "claude-code", "body": CLAUDE_PRE})
                        + "\n"
                    ).encode()
                )
                writer.write(b"not json\n")
                writer.write(
                    (
                        json.dumps({"harness": "claude-code", "body": CLAUDE_PRE})
                        + "\n"
                    ).encode()
                )
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                for _ in range(100):
                    if await spine.count():
                        break
                    await asyncio.sleep(0.01)
                return await spine.count()
            finally:
                await server.stop()
                await spine.close()

        count = run(go())
        assert count == 1  # duplicate second delivery deduped
        assert not socket_path.exists()

    def test_socket_resolution_disabled(self, monkeypatch):
        assert resolve_socket_path("") is None
        monkeypatch.setenv("SUGAR_INGEST_SOCKET", "")
        assert resolve_socket_path() is None
        monkeypatch.setenv("SUGAR_INGEST_SOCKET", "/tmp/custom.sock")
        assert resolve_socket_path() == Path("/tmp/custom.sock")
        monkeypatch.delenv("SUGAR_INGEST_SOCKET", raising=False)
        assert resolve_socket_path() == Path.home() / ".sugar" / "ingest.sock"

    def test_disabled_socket_does_not_start(self, tmp_path):
        async def go():
            spine = await make_spine(tmp_path / "events.db")
            server = IngestSocketServer(TelemetryPipeline(spine), path="")
            try:
                return server.enabled, await server.start()
            finally:
                await spine.close()

        enabled, started = run(go())
        assert enabled is False and started is False


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


def make_app(tmp_path, **kwargs):
    return create_app(
        str(tmp_path / "sugar.db"),
        events_db=str(tmp_path / "events.db"),
        ingest_socket="",
        **kwargs,
    )


class TestHTTPEndpoints:
    def test_hook_claude_code_200_and_event_stored(self, tmp_path):
        app = make_app(tmp_path)
        with TestClient(app) as client:
            response = client.post("/api/hooks/claude-code", json=CLAUDE_PRE)
            assert response.status_code == 200
            assert response.json() == {"ok": True}
            activity = client.get("/api/activity?limit=10").json()
        assert len(activity["events"]) == 1
        event = activity["events"][0]
        assert event["kind"] == "tool.started"
        assert event["payload"]["schema"] == schema.SCHEMA
        assert event["payload"]["fidelity"] == "L3"

    def test_hook_dedupe_returns_200(self, tmp_path):
        app = make_app(tmp_path)
        with TestClient(app) as client:
            assert (
                client.post("/api/hooks/claude-code", json=CLAUDE_PRE).status_code
                == 200
            )
            assert (
                client.post("/api/hooks/claude-code", json=CLAUDE_PRE).status_code
                == 200
            )
            activity = client.get("/api/activity?limit=10").json()
        assert len(activity["events"]) == 1

    def test_hook_unknown_harness_404(self, tmp_path):
        app = make_app(tmp_path)
        with TestClient(app) as client:
            response = client.post("/api/hooks/goose", json={})
            assert response.status_code == 404
            assert response.json() == {"error": "not_found"}

    def test_hook_malformed_is_400(self, tmp_path):
        app = make_app(tmp_path)
        with TestClient(app) as client:
            bad_json = client.post(
                "/api/hooks/claude-code",
                content="{not json",
                headers={"Content-Type": "application/json"},
            )
            assert bad_json.status_code == 400
            assert bad_json.json()["error"] == "validation_error"
            not_object = client.post("/api/hooks/claude-code", json=[1, 2])
            assert not_object.status_code == 400

    def test_hook_without_spine_is_200_fail_open(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), ingest_socket="")
        with TestClient(app) as client:
            response = client.post("/api/hooks/claude-code", json=CLAUDE_PRE)
            assert response.status_code == 200
            assert response.json() == {"ok": True}

    def test_hook_token_auth_inherited(self, tmp_path):
        tokens = TokenRegistry([BoardToken("reader", "read", "read-token")])
        app = make_app(tmp_path, tokens=tokens)
        with TestClient(app) as client:
            assert (
                client.post("/api/hooks/claude-code", json=CLAUDE_PRE).status_code
                == 401
            )
            response = client.post(
                "/api/hooks/claude-code",
                json=CLAUDE_PRE,
                headers={"Authorization": "Bearer read-token"},
            )
            # read token cannot write inside /api/* (hook is a write endpoint).
            assert response.status_code == 403

    def test_evidence_endpoint_201(self, tmp_path):
        app = make_app(tmp_path)
        body = {
            "kind": "verification.test_run",
            "harness": "sugar",
            "task_id": "task-1",
            "payload": {"passed": 12, "failed": 0},
        }
        with TestClient(app) as client:
            response = client.post("/api/evidence", json=body)
            assert response.status_code == 201, response.text
            event = response.json()["event"]
            assert event["kind"] == "verification.test_run"
            assert event["payload"]["schema"] == schema.SCHEMA
            assert event["payload"]["payload"] == {"passed": 12, "failed": 0}

    def test_evidence_invalid_kind_400(self, tmp_path):
        app = make_app(tmp_path)
        with TestClient(app) as client:
            response = client.post(
                "/api/evidence", json={"kind": "tool.started", "payload": {}}
            )
            assert response.status_code == 400
            assert response.json()["error"] == "validation_error"

    def test_evidence_without_spine_503(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), ingest_socket="")
        with TestClient(app) as client:
            response = client.post(
                "/api/evidence",
                json={"kind": "verification.test_run", "payload": {}},
            )
            assert response.status_code == 503
            assert response.json()["error"] == "spine_unavailable"

    def test_socket_wired_into_lifespan(self, tmp_path, short_socket):
        socket_path = short_socket
        app = create_app(
            str(tmp_path / "sugar.db"),
            events_db=str(tmp_path / "events.db"),
            ingest_socket=str(socket_path),
        )
        with TestClient(app) as client:
            assert socket_path.exists()
            assert (socket_path.stat().st_mode & 0o777) == 0o600
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client_sock:
                client_sock.settimeout(2)
                client_sock.connect(str(socket_path))
                client_sock.sendall(
                    (
                        json.dumps({"harness": "claude-code", "body": CLAUDE_PRE})
                        + "\n"
                    ).encode()
                )
            for _ in range(200):
                activity = client.get("/api/activity?limit=10").json()
                if activity["events"]:
                    break
                time.sleep(0.01)
            assert activity["events"], "socket event should reach the spine"
        assert not socket_path.exists()


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def write_registry(tmp_path, adapters):
    path = tmp_path / "adapters.json"
    path.write_text(json.dumps({"version": 1, "adapters": adapters}), encoding="utf-8")
    return path


class TestCoverage:
    def test_registry_plus_events(self, tmp_path):
        adapters = {
            "claude-code": {
                "display_name": "Claude Code",
                "installed": True,
                "fidelity": "L3",
                "transport": "http-hook",
                "adapter_version": "phase0",
                "notes": "",
            },
            "codex": {
                "display_name": "Codex",
                "installed": False,
                "detected": True,
                "fidelity": "L0",
                "transport": "",
                "notes": "hook trust required",
            },
            "goose": {
                "display_name": "Goose",
                "installed": False,
                "detected": True,
                "fidelity": "L0",
                "notes": "detected only",
            },
        }
        registry = write_registry(tmp_path, adapters)

        async def go():
            spine = await make_spine(tmp_path / "events.db")
            pipeline = TelemetryPipeline(spine)
            try:
                await pipeline.ingest_hook("claude-code", CLAUDE_PRE)
                await pipeline.ingest_hook("codex", CODEX_PRE)
                return await coverage_report(spine.db_path, adapters=registry)
            finally:
                await spine.close()

        report = run(go())
        assert set(report) == {"generated_at", "summary", "harnesses"}
        assert set(report["summary"]) == {"L0", "L1", "L2", "L3"}
        entries = {entry["harness"]: entry for entry in report["harnesses"]}
        assert entries["claude-code"]["installed"] is True
        assert entries["claude-code"]["fidelity"] == "L3"
        assert entries["claude-code"]["events_24h"] == 1
        assert entries["claude-code"]["last_event_at"]
        assert entries["codex"]["detected"] is True
        assert entries["codex"]["installed"] is False
        assert entries["codex"]["events_24h"] == 1
        assert entries["codex"]["notes"] == "hook trust required"
        assert entries["goose"]["detected"] is True
        assert entries["goose"]["events_24h"] == 0
        assert report["summary"]["L3"] == ["claude-code"]
        assert set(report["summary"]["L0"]) == {"codex", "goose"}

    def test_events_without_registry_are_detected(self, tmp_path):
        async def go():
            spine = await make_spine(tmp_path / "events.db")
            pipeline = TelemetryPipeline(spine)
            try:
                await pipeline.ingest_hook("cursor", CURSOR_PRE)
                return await coverage_report(
                    spine.db_path, adapters=tmp_path / "missing.json"
                )
            finally:
                await spine.close()

        report = run(go())
        assert [entry["harness"] for entry in report["harnesses"]] == ["cursor"]
        entry = report["harnesses"][0]
        assert entry["detected"] is True
        assert entry["installed"] is False
        assert entry["events_24h"] == 1
        assert "no adapter registry" in entry["notes"]

    def test_corrupt_registry_is_ignored(self, tmp_path):
        path = tmp_path / "adapters.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_adapters(path) == {}
        report = run(coverage_report(None, adapters=path))
        assert report["harnesses"] == []
        assert report["summary"] == {"L3": [], "L2": [], "L1": [], "L0": []}

    def test_registry_list_shape_and_missing_db(self, tmp_path):
        path = tmp_path / "adapters.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "adapters": [
                        {"harness": "cursor", "installed": True, "fidelity": "L2"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        report = run(coverage_report(tmp_path / "missing.db", adapters=path))
        assert report["harnesses"][0]["harness"] == "cursor"
        assert report["harnesses"][0]["installed"] is True
        assert report["harnesses"][0]["detected"] is True

    def test_coverage_api_shape_and_env_registry(self, tmp_path, monkeypatch):
        registry = write_registry(
            tmp_path,
            {
                "claude-code": {
                    "display_name": "Claude Code",
                    "installed": True,
                    "fidelity": "L3",
                    "transport": "http-hook",
                    "adapter_version": "phase0",
                }
            },
        )
        monkeypatch.setenv("SUGAR_ADAPTERS_FILE", str(registry))
        app = make_app(tmp_path)
        with TestClient(app) as client:
            client.post("/api/hooks/claude-code", json=CLAUDE_PRE)
            response = client.get("/api/coverage")
            assert response.status_code == 200
            report = response.json()
        assert report["summary"]["L3"] == ["claude-code"]
        entry = report["harnesses"][0]
        assert entry["harness"] == "claude-code"
        assert entry["display_name"] == "Claude Code"
        assert entry["transport"] == "http-hook"
        assert entry["events_24h"] == 1
        assert schema.normalize_iso_timestamp(report["generated_at"])

    def test_read_recent_events_missing_table(self, tmp_path):
        path = tmp_path / "foreign.db"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE other (id INTEGER)")
        assert run(read_recent_events(path)) == []
