"""Tests for the attention/risk API and trace insights (task-38)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app
from sugar.dashboard.attention import (
    DEFAULT_ATTENTION_LIMIT,
    MAX_ATTENTION_LIMIT,
    AttentionQueryError,
    build_trace_insights,
    collect_attention_items,
    normalize_attention_limit,
)
from sugar.dashboard.security import BoardToken, TokenRegistry


def iso(offset_seconds=0):
    moment = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_app(tmp_path, **kwargs):
    return create_app(
        str(tmp_path / "sugar.db"),
        events_db=str(tmp_path / "events.db"),
        ingest_socket="",
        **kwargs,
    )


def post_event(client, body):
    response = client.post("/api/events", json=body)
    assert response.status_code == 201, response.text
    return response.json()["event"]


def seed_session(client, session_id, *, project="alpha", failed=False, ended=True):
    post_event(
        client,
        {
            "kind": "session.started",
            "actor": "dsh",
            "harness": "dsh",
            "session_id": session_id,
            "project": project,
            "ts": iso(),
            "payload": {
                "schema": "sugar.telemetry.v1",
                "payload": {"title": session_id},
            },
        },
    )
    if failed:
        post_event(
            client,
            {
                "kind": "tool.failed",
                "actor": "dsh",
                "harness": "dsh",
                "session_id": session_id,
                "project": project,
                "ts": iso(),
                "payload": {"tool_name": "Bash", "status": "failed"},
            },
        )
    if ended:
        post_event(
            client,
            {
                "kind": "session.ended",
                "actor": "dsh",
                "harness": "dsh",
                "session_id": session_id,
                "project": project,
                "ts": iso(),
                "payload": {},
            },
        )


def create_task(client, title):
    response = client.post("/api/tasks", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["task"]


def transition(client, task_id, body):
    response = client.patch(f"/api/tasks/{task_id}", json=body)
    assert response.status_code == 200, response.text
    return response.json()["task"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SUGAR_ADAPTERS_FILE", str(tmp_path / "adapters.json"))
    monkeypatch.setenv("SUGAR_MODEL_PRICES", str(tmp_path / "prices.json"))
    app = make_app(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def get_items(client, **params):
    response = client.get("/api/attention", params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


class TestAttentionItems:
    def test_empty(self, client):
        assert get_items(client) == []

    def test_failed_session(self, client):
        seed_session(client, "s-fail", project="alpha", failed=True)
        items = get_items(client)
        item = next(item for item in items if item["kind"] == "failed_session")
        assert item["severity"] == "high"
        assert item["trace_id"] == "dsh:s-fail"
        assert item["project"] == "alpha"
        assert item["href"] == "/traces/dsh:s-fail"
        assert item["task_id"] is None
        assert set(item) == {
            "id",
            "kind",
            "severity",
            "title",
            "detail",
            "project",
            "cwd",
            "trace_id",
            "task_id",
            "ts",
            "href",
        }

    def test_failed_and_held_tasks(self, client):
        failed = create_task(client, "Failing task")
        transition(
            client,
            failed["id"],
            {"status": "failed", "error_message": "boom"},
        )
        held = create_task(client, "Held task")
        transition(
            client, held["id"], {"status": "hold", "reason": "waiting on review"}
        )
        items = get_items(client)
        by_task = {
            item["task_id"]: item for item in items if item["kind"] == "blocked_task"
        }
        assert by_task[failed["id"]]["severity"] == "high"
        assert "boom" in by_task[failed["id"]]["detail"]
        assert by_task[held["id"]]["severity"] == "medium"
        assert "waiting on review" in by_task[held["id"]]["detail"]
        assert by_task[held["id"]]["href"] == f"/tasks/{held['id']}"

    def test_unverified_completed_task(self, client):
        task = create_task(client, "Needs verification")
        transition(client, task["id"], {"status": "completed"})
        items = get_items(client)
        unverified = [
            item
            for item in items
            if item["kind"] == "unverified_evidence" and item["task_id"] == task["id"]
        ]
        assert len(unverified) == 1
        assert unverified[0]["severity"] == "medium"

        evidence = client.post(
            "/api/evidence",
            json={
                "kind": "verification.check_result",
                "harness": "claude-code",
                "task_id": task["id"],
                "status": "passed",
                "payload": {"check": "pytest"},
            },
        )
        assert evidence.status_code == 201
        items = get_items(client)
        assert not [
            item
            for item in items
            if item["kind"] == "unverified_evidence" and item["task_id"] == task["id"]
        ]

    def test_policy_denied_and_approval_pending(self, client):
        post_event(
            client,
            {
                "kind": "approval.denied",
                "actor": "dsh",
                "harness": "dsh",
                "session_id": "s-policy",
                "project": "alpha",
                "task_id": "t-policy",
                "ts": iso(),
                "payload": {"payload": {"approval": "deploy", "reason": "denied"}},
            },
        )
        post_event(
            client,
            {
                "kind": "approval.requested",
                "actor": "dsh",
                "harness": "dsh",
                "session_id": "s-pending",
                "project": "alpha",
                "ts": iso(),
                "payload": {"status": "pending"},
            },
        )
        items = get_items(client)
        denied = next(item for item in items if item["kind"] == "policy_denied")
        pending = next(item for item in items if item["kind"] == "approval_pending")
        assert denied["severity"] == "high"
        assert denied["trace_id"] == "dsh:s-policy"
        assert denied["task_id"] == "t-policy"
        assert pending["severity"] == "high"
        assert pending["trace_id"] == "dsh:s-pending"

    def test_coverage_gap(self, client, tmp_path):
        adapters = tmp_path / "adapters.json"
        adapters.write_text(
            json.dumps(
                {
                    "version": 1,
                    "adapters": {
                        "goose": {
                            "display_name": "Goose",
                            "installed": True,
                            "fidelity": "L1",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        post_event(
            client,
            {
                "kind": "tool.completed",
                "actor": "goose",
                "harness": "goose",
                "session_id": "s-goose",
                "ts": iso(),
                "payload": {"tool_name": "Bash"},
            },
        )
        items = get_items(client)
        gap = next(item for item in items if item["kind"] == "coverage_gap")
        assert gap["severity"] == "medium"
        assert gap["href"] == "/coverage"
        assert "Goose" in gap["title"]

    def test_spend_anomaly(self, client):
        post_event(
            client,
            {
                "kind": "usage.reported",
                "actor": "dsh",
                "harness": "dsh",
                "session_id": "s-spend",
                "project": "spendproj",
                "ts": iso(),
                "payload": {"tokens_in": 100, "tokens_out": 50},
            },
        )
        items = get_items(client)
        spend = next(item for item in items if item["kind"] == "spend_anomaly")
        assert spend["severity"] == "low"
        assert spend["project"] == "spendproj"
        assert spend["href"] == "/usage"

    def test_running_too_long(self, client):
        post_event(
            client,
            {
                "kind": "session.started",
                "actor": "dsh",
                "harness": "dsh",
                "session_id": "s-long",
                "project": "alpha",
                "ts": iso(-3 * 3600),
                "payload": {"payload": {"title": "long"}},
            },
        )
        items = get_items(client)
        long_item = next(item for item in items if item["kind"] == "running_too_long")
        assert long_item["severity"] == "medium"
        assert long_item["trace_id"] == "dsh:s-long"

    def test_project_filter(self, client):
        seed_session(client, "s-alpha", project="alpha", failed=True)
        seed_session(client, "s-beta", project="beta", failed=True)
        items = get_items(client, project="alpha")
        assert items
        assert all(item["project"] == "alpha" for item in items)
        assert all(item["trace_id"] != "dsh:s-beta" for item in items)

    def test_sort_and_limit(self, client):
        seed_session(client, "s-fail", project="alpha", failed=True)
        held = create_task(client, "Held")
        transition(client, held["id"], {"status": "hold"})
        items = get_items(client)
        severities = [item["severity"] for item in items]
        assert severities == sorted(
            severities, key=lambda value: {"high": 0, "medium": 1, "low": 2}[value]
        )
        limited = get_items(client, limit="1")
        assert len(limited) == 1
        assert limited[0]["severity"] == "high"

    def test_duplicate_policy_events_are_deduped(self, client):
        body = {
            "kind": "approval.denied",
            "actor": "dsh",
            "harness": "dsh",
            "session_id": "s-dup",
            "project": "alpha",
            "ts": "2026-01-01T10:00:00Z",
            "payload": {"payload": {"approval": "deploy"}},
        }
        post_event(client, body)
        post_event(client, body)
        items = get_items(client)
        denied = [item for item in items if item["kind"] == "policy_denied"]
        assert len(denied) == 1

    @pytest.mark.parametrize("limit", ["0", "abc", str(MAX_ATTENTION_LIMIT + 1)])
    def test_bad_limit_is_400(self, client, limit):
        response = client.get("/api/attention", params={"limit": limit})
        assert response.status_code == 400
        assert response.json()["error"] == "validation_error"

    def test_no_spine_is_503(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), ingest_socket="")
        with TestClient(app) as test_client:
            response = test_client.get("/api/attention")
            assert response.status_code == 503
            assert response.json()["error"] == "spine_unavailable"

    def test_token_mode_requires_read_scope(self, tmp_path):
        tokens = TokenRegistry(
            [
                BoardToken("reader", "read", "read-token"),
                BoardToken("writer", "write", "write-token"),
            ]
        )
        app = make_app(tmp_path, tokens=tokens)
        with TestClient(app) as test_client:
            assert test_client.get("/api/attention").status_code == 401
            response = test_client.get(
                "/api/attention", headers={"Authorization": "Bearer read-token"}
            )
            assert response.status_code == 200


class TestAttentionPure:
    def test_limit_helpers(self):
        assert normalize_attention_limit(None) == DEFAULT_ATTENTION_LIMIT
        with pytest.raises(AttentionQueryError):
            normalize_attention_limit("abc")
        with pytest.raises(AttentionQueryError):
            normalize_attention_limit("0")

    def test_collect_dedupes_and_sorts(self):
        traces = [
            {
                "trace_id": "dsh:failed",
                "status": "failed",
                "severity": "x",
                "title": "failed",
            },
            {
                "trace_id": "dsh:running",
                "status": "running",
                "title": "running",
                "started_at": "2026-01-01T10:00:00Z",
            },
        ]
        tasks = [
            {"id": "t1", "status": "hold", "title": "held"},
            {"id": "t2", "status": "completed", "title": "unverified"},
        ]
        items = collect_attention_items(
            traces=traces,
            tasks=tasks,
            verified_task_ids=set(),
            now="2026-01-01T12:00:00Z",
            threshold_seconds=3600,
        )
        kinds = [item["kind"] for item in items]
        assert "failed_session" in kinds
        assert "running_too_long" in kinds
        assert "blocked_task" in kinds
        assert "unverified_evidence" in kinds
        severities = [item["severity"] for item in items]
        assert severities.index("high") < severities.index("medium")

    def test_insights_pure_blocked_policy(self):
        blocked = build_trace_insights(
            {
                "trace": {"status": "failed", "harness": "dsh"},
                "spans": [{"kind": "approval", "name": "deploy", "status": "blocked"}],
            },
            verified=True,
        )
        assert blocked["risk"] == "blocked"
        assert any(flag["kind"] == "policy" for flag in blocked["flags"])

    def test_insights_pure_failed_and_ok(self):
        failed = build_trace_insights(
            {
                "trace": {"status": "failed", "harness": "dsh"},
                "spans": [{"kind": "tool", "name": "Bash", "status": "failed"}],
            },
            verified=True,
        )
        assert failed["risk"] == "failed"
        assert any(flag["kind"] == "failure" for flag in failed["flags"])
        ok = build_trace_insights(
            {"trace": {"status": "ok", "harness": "dsh"}, "spans": []},
            verified=True,
        )
        assert ok["risk"] == "ok"
        unverified = build_trace_insights(
            {"trace": {"status": "ok", "harness": "dsh"}, "spans": []},
            verified=False,
        )
        assert unverified["risk"] == "unverified"
        assert any(flag["kind"] == "unverified" for flag in unverified["flags"])
