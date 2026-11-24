"""Tests for the Trace Explorer backend (trace/span model + /api/traces)."""

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app
from sugar.dashboard.security import BoardToken, TokenRegistry
from sugar.dashboard.traces import (
    DEFAULT_TRACE_LIMIT,
    MAX_TRACE_LIMIT,
    TraceQueryError,
    build_trace,
    build_traces,
    normalize_trace_limit,
    split_trace_id,
    summarize_trace,
)


def event(
    kind,
    *,
    session_id="s1",
    harness="dsh",
    ts="2026-01-01T10:00:00Z",
    actor="dsh",
    payload=None,
    project=None,
    **fields,
):
    body = {
        "kind": kind,
        "actor": actor,
        "actor_kind": "agent",
        "harness": harness,
        "session_id": session_id,
        "ts": ts,
        "payload": payload or {},
    }
    if project:
        body["project"] = project
    body.update(fields)
    return body


def nested(summary, **outer):
    return {"schema": "sugar.telemetry.v1", "payload": summary, **outer}


def session_events():
    return [
        event(
            "session.started",
            ts="2026-01-01T10:00:00Z",
            payload=nested(
                {
                    "title": "Fix login",
                    "cwd": "/repo",
                    "origin": "subagent",
                    "parent_session": "parent-1",
                }
            ),
            project="sugar",
        ),
        event("turn.started", ts="2026-01-01T10:00:01Z", payload={"turn_id": "1"}),
        event(
            "tool.started",
            ts="2026-01-01T10:00:02Z",
            payload={
                "turn_id": "1",
                "tool_use_id": "call-1",
                "tool_name": "Bash",
                "payload": {"arguments": {"command": "pytest -q"}},
            },
        ),
        event(
            "tool.completed",
            ts="2026-01-01T10:00:03Z",
            payload={
                "turn_id": "1",
                "tool_use_id": "call-1",
                "tool_name": "Bash",
                "duration_ms": 1000,
                "payload": {"result": "3 passed"},
            },
        ),
        event(
            "usage.reported",
            ts="2026-01-01T10:00:04Z",
            payload={
                "turn_id": "1",
                "model": "deepseek-flash",
                "tokens_in": 100,
                "tokens_out": 50,
            },
        ),
        event(
            "approval.granted",
            ts="2026-01-01T10:00:05Z",
            payload={"turn_id": "1", "payload": {"approval": "deploy"}},
        ),
        event(
            "commit.linked",
            ts="2026-01-01T10:00:06Z",
            payload={"turn_id": "1", "payload": {"commit": "abc123"}},
        ),
        event("turn.ended", ts="2026-01-01T10:00:07Z", payload={"turn_id": "1"}),
        event("session.ended", ts="2026-01-01T10:00:08Z"),
    ]


def make_app(tmp_path, **kwargs):
    return create_app(
        str(tmp_path / "sugar.db"),
        events_db=str(tmp_path / "events.db"),
        ingest_socket="",
        **kwargs,
    )


def seed(client, events, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    for item in events:
        response = client.post("/api/events", json=item, headers=headers)
        assert response.status_code == 201, response.text


@pytest.fixture
def client(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


class TestTraceList:
    def test_empty(self, client):
        response = client.get("/api/traces")
        assert response.status_code == 200
        assert response.json() == {"traces": []}

    def test_one_trace_summary(self, client):
        seed(client, session_events())
        report = client.get("/api/traces").json()
        assert len(report["traces"]) == 1
        summary = report["traces"][0]
        assert summary["trace_id"] == "dsh:s1"
        assert summary["harness"] == "dsh"
        assert summary["project"] == "sugar"
        assert summary["cwd"] == "/repo"
        assert summary["title"] == "Fix login"
        assert summary["started_at"] == "2026-01-01T10:00:00Z"
        assert summary["ended_at"] == "2026-01-01T10:00:08Z"
        assert summary["duration_ms"] == 8000
        assert summary["events"] == 9
        assert summary["turns"] == 1
        assert summary["tool_calls"] == 1
        assert summary["models"] == ["deepseek-flash"]
        assert summary["tokens_total"] == 150
        assert summary["status"] == "ok"

    def test_project_and_harness_filters(self, client):
        seed(
            client,
            [
                event(
                    "session.started",
                    session_id="s-alpha",
                    harness="dsh",
                    ts="2026-01-01T10:00:00Z",
                    project="alpha",
                    payload={"title": "alpha"},
                ),
                event(
                    "session.started",
                    session_id="s-beta",
                    harness="codex",
                    ts="2026-01-01T11:00:00Z",
                    project="beta",
                    payload={"title": "beta"},
                ),
            ],
        )
        alpha = client.get("/api/traces", params={"project": "alpha"}).json()
        assert [trace["trace_id"] for trace in alpha["traces"]] == ["dsh:s-alpha"]
        codex = client.get("/api/traces", params={"harness": "codex"}).json()
        assert [trace["trace_id"] for trace in codex["traces"]] == ["codex:s-beta"]

    def test_newest_first_and_limit(self, client):
        seed(
            client,
            [
                event(
                    "session.started",
                    session_id="old",
                    ts="2026-01-01T10:00:00Z",
                    payload={"title": "old"},
                ),
                event(
                    "session.started",
                    session_id="new",
                    ts="2026-01-01T12:00:00Z",
                    payload={"title": "new"},
                ),
            ],
        )
        report = client.get("/api/traces", params={"limit": 1}).json()
        assert [trace["trace_id"] for trace in report["traces"]] == ["dsh:new"]
        all_traces = client.get("/api/traces").json()["traces"]
        assert [trace["trace_id"] for trace in all_traces] == ["dsh:new", "dsh:old"]

    def test_since_filter(self, client):
        seed(
            client,
            [
                event(
                    "session.started",
                    session_id="old",
                    ts="2026-01-01T10:00:00Z",
                    payload={"title": "old"},
                ),
                event(
                    "session.started",
                    session_id="new",
                    ts="2026-01-02T10:00:00Z",
                    payload={"title": "new"},
                ),
            ],
        )
        report = client.get(
            "/api/traces", params={"since": "2026-01-02T00:00:00Z"}
        ).json()
        assert [trace["trace_id"] for trace in report["traces"]] == ["dsh:new"]

    @pytest.mark.parametrize(
        "params",
        [
            {"limit": "0"},
            {"limit": "abc"},
            {"limit": str(MAX_TRACE_LIMIT + 1)},
            {"since": "not-a-time"},
        ],
    )
    def test_bad_params_are_400(self, client, params):
        response = client.get("/api/traces", params=params)
        assert response.status_code == 400
        assert response.json()["error"] == "validation_error"

    def test_events_without_session_are_ignored(self, client):
        seed(
            client,
            [
                event("session.started", session_id=None, payload={"title": "x"}),
                event("tool.started", session_id="", payload={"tool_name": "Bash"}),
            ],
        )
        assert client.get("/api/traces").json() == {"traces": []}

    def test_no_spine_is_503(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), ingest_socket="")
        with TestClient(app) as test_client:
            response = test_client.get("/api/traces")
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
            assert test_client.get("/api/traces").status_code == 401
            seed(
                test_client,
                [event("session.started", payload={"title": "x"})],
                "write-token",
            )
            response = test_client.get(
                "/api/traces", headers={"Authorization": "Bearer read-token"}
            )
            assert response.status_code == 200
            assert len(response.json()["traces"]) == 1


class TestTraceDetail:
    def test_tree_and_span_fields(self, client):
        seed(client, session_events())
        detail = client.get("/api/traces/dsh:s1").json()
        assert detail["trace"]["trace_id"] == "dsh:s1"
        spans = detail["spans"]
        assert spans[0]["kind"] == "session"
        assert spans[0]["parent_span_id"] is None
        session_span = spans[0]
        by_kind = {}
        for span in spans:
            by_kind.setdefault(span["kind"], []).append(span)
        assert {
            "session",
            "turn",
            "tool",
            "llm",
            "approval",
            "artifact",
            "subagent",
        } <= set(by_kind)
        turn = by_kind["turn"][0]
        tool = by_kind["tool"][0]
        llm = by_kind["llm"][0]
        approval = by_kind["approval"][0]
        artifact = by_kind["artifact"][0]
        subagent = by_kind["subagent"][0]
        assert turn["parent_span_id"] == session_span["span_id"]
        assert tool["parent_span_id"] == turn["span_id"]
        assert llm["parent_span_id"] == turn["span_id"]
        assert approval["parent_span_id"] == turn["span_id"]
        assert artifact["parent_span_id"] == turn["span_id"]
        assert subagent["parent_span_id"] == session_span["span_id"]
        assert tool["name"] == "Bash"
        assert tool["status"] == "ok"
        assert tool["input"] == {"command": "pytest -q"}
        assert tool["output"] == {"result": "3 passed"}
        assert tool["duration_ms"] == 1000
        assert llm["model"] == "deepseek-flash"
        assert llm["input"] == {"tokens_in": 100}
        assert llm["output"] == {"tokens_out": 50}
        assert approval["status"] == "ok"
        assert artifact["name"] == "abc123"
        for span in spans:
            assert set(span) == {
                "span_id",
                "parent_span_id",
                "kind",
                "name",
                "actor",
                "harness",
                "model",
                "status",
                "start",
                "end",
                "duration_ms",
                "project",
                "cwd",
                "input",
                "output",
                "attributes",
            }

    def test_spans_are_stably_ordered(self, client):
        seed(client, session_events())
        first = client.get("/api/traces/dsh:s1").json()["spans"]
        second = client.get("/api/traces/dsh:s1").json()["spans"]
        assert [span["span_id"] for span in first] == [
            span["span_id"] for span in second
        ]
        starts = [span["start"] for span in first if span["start"]]
        assert starts == sorted(starts)

    def test_unknown_trace_404(self, client):
        response = client.get("/api/traces/nope:missing")
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_colon_in_session_id_roundtrips(self, client):
        seed(
            client,
            [
                event(
                    "session.started",
                    session_id="a:b",
                    payload={"title": "colon"},
                ),
                event("session.ended", session_id="a:b", ts="2026-01-01T10:00:01Z"),
            ],
        )
        listed = client.get("/api/traces").json()["traces"][0]
        assert listed["trace_id"] == "dsh:a:b"
        detail = client.get("/api/traces/dsh:a:b")
        assert detail.status_code == 200
        assert detail.json()["trace"]["trace_id"] == "dsh:a:b"

    def test_failed_tool_completion_without_start(self, client):
        seed(
            client,
            [
                event("session.started", payload={"title": "failed"}),
                event(
                    "tool.failed",
                    ts="2026-01-01T10:00:01Z",
                    payload={
                        "tool_use_id": "call-x",
                        "tool_name": "Bash",
                        "status": "failed",
                        "payload": {"result": "exit 1"},
                    },
                ),
                event("session.ended", ts="2026-01-01T10:00:02Z"),
            ],
        )
        summary = client.get("/api/traces").json()["traces"][0]
        assert summary["status"] == "failed"
        tool = [
            span
            for span in client.get("/api/traces/dsh:s1").json()["spans"]
            if span["kind"] == "tool"
        ][0]
        assert tool["status"] == "failed"
        assert tool["start"] == "2026-01-01T10:00:01Z"
        assert tool["output"] == {"result": "exit 1"}

    def test_running_trace_and_unfinished_tool(self, client):
        seed(
            client,
            [
                event("session.started", payload={"title": "running"}),
                event(
                    "tool.started",
                    ts="2026-01-01T10:00:01Z",
                    payload={"tool_use_id": "call-y", "tool_name": "Bash"},
                ),
            ],
        )
        summary = client.get("/api/traces").json()["traces"][0]
        assert summary["status"] == "running"
        assert summary["ended_at"] is None
        tool = [
            span
            for span in client.get("/api/traces/dsh:s1").json()["spans"]
            if span["kind"] == "tool"
        ][0]
        assert tool["status"] == "running"
        assert tool["end"] is None

    def test_mcp_tool_classification(self, client):
        seed(
            client,
            [
                event("session.started", payload={"title": "mcp"}),
                event(
                    "tool.started",
                    ts="2026-01-01T10:00:01Z",
                    payload={
                        "tool_use_id": "call-mcp",
                        "tool_name": "mcp__github__search",
                        "payload": {"arguments": {"query": "bug"}},
                    },
                ),
                event(
                    "tool.completed",
                    ts="2026-01-01T10:00:02Z",
                    payload={
                        "tool_use_id": "call-mcp",
                        "tool_name": "mcp__github__search",
                        "payload": {"result": "found"},
                    },
                ),
            ],
        )
        spans = client.get("/api/traces/dsh:s1").json()["spans"]
        tool = [span for span in spans if span["kind"] == "mcp"][0]
        assert tool["name"] == "mcp__github__search"

    def test_tool_pairing_by_span_id(self, client):
        seed(
            client,
            [
                event("session.started", payload={"title": "span ids"}),
                event(
                    "tool.started",
                    ts="2026-01-01T10:00:01Z",
                    payload={"span_id": "span-1", "tool_name": "Bash"},
                ),
                event(
                    "tool.completed",
                    ts="2026-01-01T10:00:02Z",
                    payload={"span_id": "span-1", "tool_name": "Bash"},
                ),
            ],
        )
        trace = client.get("/api/traces/dsh:s1").json()
        tools = [span for span in trace["spans"] if span["kind"] == "tool"]
        assert len(tools) == 1
        assert tools[0]["end"] == "2026-01-01T10:00:02Z"
        assert trace["trace"]["tool_calls"] == 1

    def test_blocked_approval_span_status(self, client):
        seed(
            client,
            [
                event("session.started", payload={"title": "blocked"}),
                event(
                    "approval.denied",
                    ts="2026-01-01T10:00:01Z",
                    payload={"status": "blocked"},
                ),
            ],
        )
        trace = client.get("/api/traces/dsh:s1").json()
        assert trace["trace"]["status"] == "failed"
        approval = [span for span in trace["spans"] if span["kind"] == "approval"][0]
        assert approval["status"] == "failed"

    def test_large_payloads_are_bounded(self, client):
        huge = "x" * 20000
        seed(
            client,
            [
                event("session.started", payload={"title": "big"}),
                event(
                    "tool.started",
                    ts="2026-01-01T10:00:01Z",
                    payload={
                        "tool_use_id": "call-big",
                        "tool_name": "Bash",
                        "payload": {"arguments": {"command": huge}},
                    },
                ),
            ],
        )
        spans = client.get("/api/traces/dsh:s1").json()["spans"]
        tool = [span for span in spans if span["kind"] == "tool"][0]
        assert len(tool["input"]["command"]) <= 1000
        encoded = json.dumps(tool)
        assert len(encoded) < 30000

    def test_bad_limit_is_400(self, client):
        assert client.get("/api/traces", params={"limit": "0"}).status_code == 400


class TestPureBuilders:
    def test_build_traces_deterministic(self):
        events = session_events()
        traces = build_traces(events)
        assert [trace["trace_id"] for trace in traces] == ["dsh:s1"]
        assert traces == build_traces(events)

    def test_summarize_and_empty(self):
        with pytest.raises(TraceQueryError):
            summarize_trace([])
        assert build_trace([]) is None
        summary = summarize_trace(session_events())
        assert summary["events"] == 9
        assert summary["tokens_total"] == 150

    def test_split_and_limit(self):
        assert split_trace_id("dsh:session") == ("dsh", "session")
        assert split_trace_id("session-only") == (None, "session-only")
        assert normalize_trace_limit(None) == DEFAULT_TRACE_LIMIT
        with pytest.raises(TraceQueryError):
            normalize_trace_limit("abc")

    def test_policy_decision_maps_to_approval(self):
        events = [
            event("session.started", payload={"title": "policy"}),
            event(
                "policy.decision",
                ts="2026-01-01T10:00:01Z",
                payload={"payload": {"policy": "allow-writes"}, "status": "policy"},
            ),
        ]
        spans = build_trace(events)["spans"]
        approval = [span for span in spans if span["kind"] == "approval"]
        assert approval and approval[0]["name"] == "allow-writes"

    def test_reader_missing_db_is_empty(self, tmp_path):
        from sugar.dashboard.traces import trace_detail_report, trace_list_report

        async def go():
            listed = await trace_list_report(tmp_path / "missing.db")
            detail = await trace_detail_report(tmp_path / "missing.db", "dsh:x")
            return listed, detail

        listed, detail = asyncio.run(go())
        assert listed == {"traces": []}
        assert detail is None
