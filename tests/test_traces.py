"""Tests for the Trace Explorer backend (trace/span model + /api/traces)."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

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


# ---------------------------------------------------------------------------
# Enriched subagent / team task / artifact spans (task-36)
# ---------------------------------------------------------------------------


def parent_events():
    return [
        event(
            "session.started",
            session_id="s-parent",
            ts="2026-01-01T10:00:00Z",
            payload=nested({"title": "Parent session", "cwd": "/repo"}),
            project="sugar",
        ),
        event(
            "subagent.started",
            session_id="s-parent",
            ts="2026-01-01T10:00:01Z",
            payload=nested(
                {
                    "parent_session_id": "session-parent",
                    "child_session_id": "child-1",
                    "label": "researcher",
                    "mode": "continuable",
                }
            ),
        ),
        event(
            "team.task",
            session_id="s-parent",
            ts="2026-01-01T10:00:02Z",
            payload=nested(
                {
                    "team_id": "session-parent",
                    "team_task_id": "task-1",
                    "subject": "Research login",
                    "status": "running",
                    "owner_id": "child-1",
                    "owner_name": "Researcher",
                    "blocked_by": ["task-0"],
                    "parent_session_id": "session-parent",
                }
            ),
        ),
        event(
            "tool.started",
            session_id="s-parent",
            ts="2026-01-01T10:00:03Z",
            payload={
                "tool_use_id": "call-1",
                "tool_name": "write",
                "payload": {"arguments": {"path": "/repo/foo.py"}},
            },
        ),
        event(
            "tool.completed",
            session_id="s-parent",
            ts="2026-01-01T10:00:04Z",
            payload={
                "tool_use_id": "call-1",
                "tool_name": "write",
                "payload": {"result": {"path": "/repo/foo.py"}},
            },
        ),
        event(
            "artifact.created",
            session_id="s-parent",
            ts="2026-01-01T10:00:05Z",
            payload=nested(
                {
                    "path": "/repo/foo.py",
                    "description": "add foo",
                    "sha256": "sha123",
                    "turn": 1,
                    "tool_call_id": "call-1",
                }
            ),
        ),
        event(
            "session.ended",
            session_id="s-parent",
            ts="2026-01-01T10:00:06Z",
        ),
    ]


class TestEnrichedSpans:
    def test_subagent_started_span_attributes_and_parent(self, client):
        seed(client, parent_events())
        trace = client.get("/api/traces/dsh:s-parent").json()
        subagent = [span for span in trace["spans"] if span["kind"] == "subagent"][0]
        session_span = [span for span in trace["spans"] if span["kind"] == "session"][0]
        assert subagent["name"] == "researcher"
        assert subagent["parent_span_id"] == session_span["span_id"]
        assert subagent["status"] == "running"
        attributes = subagent["attributes"]
        assert attributes["parent_session_id"] == "session-parent"
        assert attributes["child_session_id"] == "child-1"
        assert attributes["label"] == "researcher"
        assert attributes["mode"] == "continuable"
        assert trace["trace"]["subagents"] == 1

    def test_child_subagent_session_is_a_separate_trace(self, client):
        seed(
            client,
            [
                *parent_events(),
                event(
                    "session.started",
                    session_id="child-1",
                    ts="2026-01-01T10:00:07Z",
                    payload=nested(
                        {
                            "title": "Child researcher",
                            "parent_session": "session-parent",
                            "origin": "subagent",
                        }
                    ),
                ),
                event(
                    "session.ended",
                    session_id="child-1",
                    ts="2026-01-01T10:00:08Z",
                ),
            ],
        )
        listed = client.get("/api/traces").json()["traces"]
        trace_ids = {trace["trace_id"] for trace in listed}
        assert trace_ids == {"dsh:s-parent", "dsh:child-1"}
        parent_detail = client.get("/api/traces/dsh:s-parent").json()
        subagent = [
            span for span in parent_detail["spans"] if span["kind"] == "subagent"
        ][0]
        assert subagent["attributes"]["child_session_id"] == "child-1"
        child_detail = client.get("/api/traces/dsh:child-1").json()
        assert child_detail["trace"]["trace_id"] == "dsh:child-1"
        header_subagent = [
            span for span in child_detail["spans"] if span["kind"] == "subagent"
        ][0]
        assert header_subagent["attributes"]["parent_session_id"] == "session-parent"
        assert header_subagent["attributes"]["child_session_id"] == "child-1"

    def test_team_task_span_attributes_and_subagent_parent(self, client):
        seed(client, parent_events())
        trace = client.get("/api/traces/dsh:s-parent").json()
        task = [span for span in trace["spans"] if span["kind"] == "task"][0]
        subagent = [span for span in trace["spans"] if span["kind"] == "subagent"][0]
        assert task["name"] == "Research login"
        assert task["parent_span_id"] == subagent["span_id"]
        assert task["status"] == "running"
        assert task["attributes"]["team_id"] == "session-parent"
        assert task["attributes"]["team_task_id"] == "task-1"
        assert task["attributes"]["owner_id"] == "child-1"
        assert task["attributes"]["blocked_by"] == ["task-0"]

    def test_team_task_without_subagent_parents_session(self, client):
        seed(
            client,
            [
                event("session.started", payload={"title": "team"}),
                event(
                    "team.task",
                    ts="2026-01-01T10:00:01Z",
                    payload={
                        "team_task_id": "task-2",
                        "subject": "Done",
                        "status": "completed",
                    },
                ),
                event("session.ended", ts="2026-01-01T10:00:02Z"),
            ],
        )
        trace = client.get("/api/traces/dsh:s1").json()
        task = [span for span in trace["spans"] if span["kind"] == "task"][0]
        session_span = [span for span in trace["spans"] if span["kind"] == "session"][0]
        assert task["parent_span_id"] == session_span["span_id"]
        assert task["status"] == "ok"

    def test_artifact_span_graph_attributes_and_tool_parent(self, client):
        seed(client, parent_events())
        trace = client.get("/api/traces/dsh:s-parent").json()
        artifact = [span for span in trace["spans"] if span["kind"] == "artifact"][0]
        tool = [span for span in trace["spans"] if span["kind"] == "tool"][0]
        assert artifact["parent_span_id"] == tool["span_id"]
        assert artifact["name"] == "add foo"
        attributes = artifact["attributes"]
        assert attributes["path"] == "/repo/foo.py"
        assert attributes["paths"] == ["/repo/foo.py"]
        assert attributes["description"] == "add foo"
        assert attributes["sha256"] == "sha123"
        assert attributes["tool_call_id"] == "call-1"
        assert attributes["artifacts"] == [
            {
                "path": "/repo/foo.py",
                "description": "add foo",
                "sha256": "sha123",
                "tool_call_id": "call-1",
            }
        ]
        assert tool["attributes"]["paths"] == ["/repo/foo.py"]
        assert trace["trace"]["artifacts"] == 1

    def test_duplicate_artifacts_are_deduped(self, client):
        artifact = event(
            "artifact.created",
            ts="2026-01-01T10:00:01Z",
            payload={"path": "/repo/a.py", "description": "a"},
        )
        seed(
            client,
            [
                event("session.started", payload={"title": "dedupe"}),
                artifact,
                {**artifact, "id": None, "ts": "2026-01-01T10:00:02Z"},
            ],
        )
        trace = client.get("/api/traces/dsh:s1").json()
        artifacts = [span for span in trace["spans"] if span["kind"] == "artifact"]
        assert len(artifacts) == 1
        assert trace["trace"]["artifacts"] == 1

    def test_paths_are_bounded(self, client):
        many_paths = [f"/repo/file-{index}.py" for index in range(50)]
        seed(
            client,
            [
                event("session.started", payload={"title": "paths"}),
                event(
                    "tool.started",
                    ts="2026-01-01T10:00:01Z",
                    payload={
                        "tool_use_id": "call-many",
                        "tool_name": "write",
                        "payload": {"arguments": {"paths": many_paths}},
                    },
                ),
                event(
                    "tool.completed",
                    ts="2026-01-01T10:00:02Z",
                    payload={
                        "tool_use_id": "call-many",
                        "tool_name": "write",
                        "payload": {"result": {"paths": many_paths}},
                    },
                ),
            ],
        )
        trace = client.get("/api/traces/dsh:s1").json()
        tool = [span for span in trace["spans"] if span["kind"] == "tool"][0]
        assert len(tool["attributes"]["paths"]) <= 20
        assert all(len(path) <= 1000 for path in tool["attributes"]["paths"])

    def test_exact_task35_canonical_envelope_shape(self, client):
        seed(
            client,
            [
                event(
                    "session.started",
                    session_id="session-parent",
                    ts="2026-01-01T10:00:00Z",
                    project="sugar",
                    payload=nested({"title": "Lead", "cwd": "/repo"}),
                ),
                event(
                    "subagent.started",
                    session_id="session-parent",
                    ts="2026-01-01T10:00:01Z",
                    project="sugar",
                    payload={
                        "schema": "sugar.telemetry.v1",
                        "fidelity": "L3",
                        "status": "started",
                        "cwd": "/repo",
                        "model": None,
                        "payload": {
                            "parent_session_id": "session-parent",
                            "child_session_id": "child-uuid",
                            "label": "security-reviewer",
                            "mode": "continuable",
                            "created_at": "2026-01-01T10:00:01Z",
                        },
                    },
                ),
                event(
                    "team.task",
                    session_id="session-parent",
                    ts="2026-01-01T10:00:02Z",
                    project="sugar",
                    payload=nested(
                        {
                            "team_id": "session-parent",
                            "team_task_id": "task-7",
                            "subject": "Review auth flow",
                            "status": "in_progress",
                            "owner_id": "child-uuid",
                            "owner_name": "security-reviewer",
                            "blocked_by": ["task-2"],
                            "parent_session_id": "session-parent",
                            "revision": 2,
                        },
                        fidelity="L2",
                        cwd="/repo",
                    ),
                ),
                event(
                    "tool.started",
                    session_id="session-parent",
                    ts="2026-01-01T10:00:03Z",
                    payload={
                        "tool_use_id": "call_w1",
                        "tool_name": "write",
                        "payload": {"arguments": {"path": "/repo/report.md"}},
                    },
                ),
                event(
                    "tool.completed",
                    session_id="session-parent",
                    ts="2026-01-01T10:00:04Z",
                    payload={
                        "tool_use_id": "call_w1",
                        "tool_name": "write",
                        "payload": {"result": {"path": "/repo/report.md"}},
                    },
                ),
                event(
                    "artifact.created",
                    session_id="session-parent",
                    ts="2026-01-01T10:00:05Z",
                    project="sugar",
                    payload={
                        "schema": "sugar.telemetry.v1",
                        "fidelity": "L3",
                        "cwd": "/repo",
                        "turn_id": "1",
                        "tool_use_id": "call_w1",
                        "payload": {
                            "path": "/repo/report.md",
                            "description": "review report",
                            "operation": "create",
                            "turn": 1,
                            "tool_call_id": "call_w1",
                            "sha256": "sha-report",
                        },
                    },
                ),
                event(
                    "session.ended",
                    session_id="session-parent",
                    ts="2026-01-01T10:00:06Z",
                ),
            ],
        )
        trace = client.get("/api/traces/dsh:session-parent").json()
        subagent = [span for span in trace["spans"] if span["kind"] == "subagent"][0]
        task = [span for span in trace["spans"] if span["kind"] == "task"][0]
        artifact = [span for span in trace["spans"] if span["kind"] == "artifact"][0]
        assert subagent["status"] == "running"
        assert subagent["attributes"]["created_at"] == "2026-01-01T10:00:01Z"
        assert task["status"] == "running"
        assert task["attributes"]["revision"] == 2
        assert task["parent_span_id"] == subagent["span_id"]
        assert artifact["attributes"]["operation"] == "create"
        assert artifact["attributes"]["sha256"] == "sha-report"

    def test_flat_enriched_payloads_are_tolerated(self, client):
        seed(
            client,
            [
                event("session.started", payload={"title": "flat"}),
                event(
                    "subagent.started",
                    ts="2026-01-01T10:00:01Z",
                    payload={
                        "parent_session_id": "s1",
                        "child_session_id": "child-flat",
                        "label": "flat child",
                    },
                ),
                event(
                    "team.task",
                    ts="2026-01-01T10:00:02Z",
                    payload={
                        "team_id": "s1",
                        "team_task_id": "task-flat",
                        "subject": "Flat task",
                        "status": "blocked",
                        "owner_id": "child-flat",
                    },
                ),
                event(
                    "artifact.created",
                    ts="2026-01-01T10:00:03Z",
                    payload={
                        "path": "/repo/flat.py",
                        "description": "flat artifact",
                        "sha256": "flat-sha",
                    },
                ),
            ],
        )
        trace = client.get("/api/traces/dsh:s1").json()
        subagent = [span for span in trace["spans"] if span["kind"] == "subagent"][0]
        task = [span for span in trace["spans"] if span["kind"] == "task"][0]
        artifact = [span for span in trace["spans"] if span["kind"] == "artifact"][0]
        assert subagent["attributes"]["label"] == "flat child"
        assert task["status"] == "blocked"
        assert task["parent_span_id"] == subagent["span_id"]
        assert artifact["attributes"]["paths"] == ["/repo/flat.py"]

    def test_duplicate_subagent_events_are_deduped(self, client):
        subagent = event(
            "subagent.started",
            ts="2026-01-01T10:00:01Z",
            payload={"child_session_id": "child-1", "label": "child"},
        )
        seed(
            client,
            [
                event("session.started", payload={"title": "subagents"}),
                subagent,
                {**subagent, "id": None, "ts": "2026-01-01T10:00:02Z"},
            ],
        )
        trace = client.get("/api/traces/dsh:s1").json()
        spans = [span for span in trace["spans"] if span["kind"] == "subagent"]
        assert len(spans) == 1
        assert trace["trace"]["subagents"] == 1

    def test_artifact_parents_turn_without_tool(self, client):
        seed(
            client,
            [
                event("session.started", payload={"title": "turn artifact"}),
                event(
                    "turn.started", ts="2026-01-01T10:00:01Z", payload={"turn_id": "1"}
                ),
                event(
                    "artifact.created",
                    ts="2026-01-01T10:00:02Z",
                    payload={"path": "/repo/t.py", "turn": 1},
                ),
                event(
                    "turn.ended", ts="2026-01-01T10:00:03Z", payload={"turn_id": "1"}
                ),
            ],
        )
        trace = client.get("/api/traces/dsh:s1").json()
        artifact = [span for span in trace["spans"] if span["kind"] == "artifact"][0]
        turn = [span for span in trace["spans"] if span["kind"] == "turn"][0]
        assert artifact["parent_span_id"] == turn["span_id"]


# ---------------------------------------------------------------------------
# Additive trace insights (task-38)
# ---------------------------------------------------------------------------


def now_iso(offset_seconds=0):
    moment = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def post_event(client, body):
    response = client.post("/api/events", json=body)
    assert response.status_code == 201, response.text
    return response.json()["event"]


@pytest.fixture
def clean_insight_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SUGAR_ADAPTERS_FILE", str(tmp_path / "no-adapters.json"))
    monkeypatch.setenv("SUGAR_MODEL_PRICES", str(tmp_path / "no-prices.json"))


class TestTraceInsights:
    def _session(self, client, session_id="s-insight", *, failed=False, ended=True):
        post_event(
            client,
            {
                "kind": "session.started",
                "actor": "dsh",
                "harness": "dsh",
                "session_id": session_id,
                "ts": now_iso(),
                "payload": nested({"title": "insight"}),
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
                    "ts": now_iso(),
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
                    "ts": now_iso(),
                    "payload": {},
                },
            )

    def test_failed_trace_insights(self, client, clean_insight_env):
        self._session(client, failed=True)
        insights = client.get("/api/traces/dsh:s-insight").json()["insights"]
        assert insights["risk"] == "failed"
        assert insights["headline"]
        assert any(flag["kind"] == "failure" for flag in insights["flags"])
        assert insights["bullets"]

    def test_policy_denied_insights(self, client, clean_insight_env):
        self._session(client, "s-policy")
        post_event(
            client,
            {
                "kind": "approval.denied",
                "actor": "dsh",
                "harness": "dsh",
                "session_id": "s-policy",
                "ts": now_iso(),
                "payload": {"payload": {"approval": "deploy"}},
            },
        )
        insights = client.get("/api/traces/dsh:s-policy").json()["insights"]
        assert insights["risk"] == "failed"
        assert any(flag["kind"] == "policy" for flag in insights["flags"])

    def test_unverified_insights(self, client, clean_insight_env):
        self._session(client)
        insights = client.get("/api/traces/dsh:s-insight").json()["insights"]
        assert insights["risk"] == "unverified"
        assert any(flag["kind"] == "unverified" for flag in insights["flags"])

    def test_ok_insights_with_verification(self, client, clean_insight_env):
        self._session(client)
        post_event(
            client,
            {
                "kind": "verification.check_result",
                "actor": "dsh",
                "harness": "dsh",
                "session_id": "s-insight",
                "ts": now_iso(),
                "payload": {"status": "passed", "check": "pytest"},
            },
        )
        insights = client.get("/api/traces/dsh:s-insight").json()["insights"]
        assert insights["risk"] == "ok"
        assert insights["flags"] == []

    def test_running_long_insights(self, client, clean_insight_env):
        post_event(
            client,
            {
                "kind": "session.started",
                "actor": "dsh",
                "harness": "dsh",
                "session_id": "s-long",
                "ts": now_iso(-3 * 3600),
                "payload": nested({"title": "long"}),
            },
        )
        insights = client.get("/api/traces/dsh:s-long").json()["insights"]
        assert insights["risk"] == "watch"
        assert any(flag["kind"] == "long_running" for flag in insights["flags"])

    def test_spend_insights(self, client, clean_insight_env):
        self._session(client)
        post_event(
            client,
            {
                "kind": "verification.check_result",
                "actor": "dsh",
                "harness": "dsh",
                "session_id": "s-insight",
                "ts": now_iso(),
                "payload": {"status": "passed"},
            },
        )
        post_event(
            client,
            {
                "kind": "usage.reported",
                "actor": "dsh",
                "harness": "dsh",
                "session_id": "s-insight",
                "ts": now_iso(),
                "payload": {"tokens_in": 100, "tokens_out": 50},
            },
        )
        insights = client.get("/api/traces/dsh:s-insight").json()["insights"]
        assert insights["risk"] == "watch"
        assert any(flag["kind"] == "spend" for flag in insights["flags"])

    def test_coverage_insights(self, client, tmp_path, monkeypatch):
        adapters = tmp_path / "adapters.json"
        adapters.write_text(
            json.dumps(
                {
                    "version": 1,
                    "adapters": {
                        "dsh": {
                            "display_name": "DeepSeek Harness",
                            "installed": True,
                            "fidelity": "L1",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("SUGAR_ADAPTERS_FILE", str(adapters))
        monkeypatch.setenv("SUGAR_MODEL_PRICES", str(tmp_path / "no-prices.json"))
        self._session(client)
        post_event(
            client,
            {
                "kind": "verification.check_result",
                "actor": "dsh",
                "harness": "dsh",
                "session_id": "s-insight",
                "ts": now_iso(),
                "payload": {"status": "passed"},
            },
        )
        insights = client.get("/api/traces/dsh:s-insight").json()["insights"]
        assert insights["risk"] == "watch"
        assert any(flag["kind"] == "coverage" for flag in insights["flags"])


# ---------------------------------------------------------------------------
# Trace tree parent/child links (task-48)
# ---------------------------------------------------------------------------


def tree_events(*, child_session_id="child-1", include_child=True):
    events = [
        event(
            "session.started",
            session_id="s-parent",
            ts="2026-01-01T10:00:00Z",
            payload=nested({"title": "Parent session"}),
        ),
        event(
            "subagent.started",
            session_id="s-parent",
            ts="2026-01-01T10:00:01Z",
            payload=nested(
                {
                    "parent_session_id": "s-parent",
                    "child_session_id": child_session_id,
                    "label": "researcher",
                }
            ),
        ),
        event(
            "session.ended",
            session_id="s-parent",
            ts="2026-01-01T10:00:02Z",
        ),
    ]
    if include_child:
        events.extend(
            [
                event(
                    "session.started",
                    session_id=child_session_id,
                    ts="2026-01-01T10:00:03Z",
                    payload=nested(
                        {
                            "title": "Child session",
                            "parent_session": "s-parent",
                            "origin": "subagent",
                        }
                    ),
                ),
                event(
                    "session.ended",
                    session_id=child_session_id,
                    ts="2026-01-01T10:00:04Z",
                ),
            ]
        )
    return events


class TestTraceTree:
    def test_list_parent_child_links(self, client):
        seed(client, tree_events())
        traces = {
            trace["trace_id"]: trace
            for trace in client.get("/api/traces").json()["traces"]
        }
        assert set(traces) == {"dsh:s-parent", "dsh:child-1"}
        parent = traces["dsh:s-parent"]
        child = traces["dsh:child-1"]
        assert parent["parent_trace_id"] is None
        assert parent["child_trace_ids"] == ["dsh:child-1"]
        assert parent["subagent_count"] == 1
        assert child["parent_trace_id"] == "dsh:s-parent"
        assert child["child_trace_ids"] == []
        assert child["subagent_count"] == 0

    def test_detail_links_on_trace_object(self, client):
        seed(client, tree_events())
        parent_detail = client.get("/api/traces/dsh:s-parent").json()
        assert parent_detail["trace"]["child_trace_ids"] == ["dsh:child-1"]
        assert parent_detail["trace"]["subagent_count"] == 1
        assert parent_detail["trace"]["parent_trace_id"] is None
        child_detail = client.get("/api/traces/dsh:child-1").json()
        assert child_detail["trace"]["parent_trace_id"] == "dsh:s-parent"
        assert child_detail["trace"]["child_trace_ids"] == []

    def test_detail_child_ids_without_child_trace(self, client):
        seed(client, tree_events(include_child=False))
        parent_detail = client.get("/api/traces/dsh:s-parent").json()
        assert parent_detail["trace"]["child_trace_ids"] == ["dsh:child-1"]
        assert parent_detail["trace"]["subagent_count"] == 1

    def test_unmatched_child_is_ignored_in_list(self, client):
        seed(client, tree_events(include_child=False))
        traces = client.get("/api/traces").json()["traces"]
        assert [trace["trace_id"] for trace in traces] == ["dsh:s-parent"]
        parent = traces[0]
        assert parent["child_trace_ids"] == []
        # The subagent.started event is still observed on the parent trace.
        assert parent["subagent_count"] == 1

    def test_duplicate_subagent_events_are_deduped(self, client):
        events = tree_events()
        events.insert(
            2,
            event(
                "subagent.started",
                session_id="s-parent",
                ts="2026-01-01T10:00:01Z",
                payload=nested(
                    {
                        "parent_session_id": "s-parent",
                        "child_session_id": "child-1",
                        "label": "researcher-again",
                    }
                ),
            ),
        )
        seed(client, events)
        parent = next(
            trace
            for trace in client.get("/api/traces").json()["traces"]
            if trace["trace_id"] == "dsh:s-parent"
        )
        assert parent["child_trace_ids"] == ["dsh:child-1"]
        assert parent["subagent_count"] == 1

    def test_child_session_prefix_is_normalized(self, client):
        events = tree_events(child_session_id="session-child-1")
        seed(client, events)
        traces = {
            trace["trace_id"]: trace
            for trace in client.get("/api/traces").json()["traces"]
        }
        parent = traces["dsh:s-parent"]
        child = traces["dsh:session-child-1"]
        # The event advertises the bare child id while the trace keeps its raw id.
        assert parent["child_trace_ids"] == ["dsh:session-child-1"]
        assert child["parent_trace_id"] == "dsh:s-parent"

    def test_legacy_summary_has_empty_tree_fields(self, client):
        seed(client, session_events())
        summary = client.get("/api/traces").json()["traces"][0]
        assert summary["parent_trace_id"] is None
        assert summary["child_trace_ids"] == []
        assert summary["subagent_count"] == 0

    def test_child_trace_ids_are_bounded(self, client):
        events = [
            event(
                "session.started",
                session_id="s-parent",
                ts="2026-01-01T10:00:00Z",
                payload=nested({"title": "Parent"}),
            )
        ]
        for index in range(25):
            child_id = f"child-{index}"
            events.append(
                event(
                    "subagent.started",
                    session_id="s-parent",
                    ts="2026-01-01T10:00:01Z",
                    payload=nested(
                        {
                            "parent_session_id": "s-parent",
                            "child_session_id": child_id,
                        }
                    ),
                )
            )
            events.append(
                event(
                    "session.started",
                    session_id=child_id,
                    ts="2026-01-01T10:00:02Z",
                    payload=nested({"parent_session": "s-parent"}),
                )
            )
        seed(client, events)
        parent = next(
            trace
            for trace in client.get("/api/traces", params={"limit": 100}).json()[
                "traces"
            ]
            if trace["trace_id"] == "dsh:s-parent"
        )
        assert len(parent["child_trace_ids"]) == 20
        assert parent["subagent_count"] == 25
