"""Tests for the dashboard Trace Explorer UI (Phase 1d).

The dashboard is a single static HTML page with no JavaScript runtime in tests,
so these pin the served-HTML contract: the Traces tab, trace list hooks,
swimlane/call-tree/inspector hooks, span kinds/colours, absent-endpoint
degradation, keyboard/close affordances, and that Tasks/Activity/SSE are not
disturbed. A fake ``/api/traces`` payload is used to prove the UI references
every frozen field, and the live endpoint (when present) is exercised through
``TestClient``.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app

SPAN_KINDS = (
    "session",
    "turn",
    "tool",
    "mcp",
    "llm",
    "approval",
    "artifact",
    "subagent",
    "task",
)

# Frozen task-32 payload shape (list + flat spans). Used to check that every
# field the backend guarantees is referenced by the UI's normalisers.
FAKE_TRACE = {
    "trace_id": "tr-123",
    "title": "Fix the spend view",
    "project": "sugar",
    "cwd": "/Users/steve/Dev/sugar",
    "status": "passed",
    "started_at": "2026-11-24T21:00:00Z",
    "ended_at": "2026-11-24T21:05:00Z",
    "duration_ms": 300000,
    "turns": 2,
    "tool_calls": 3,
    "models": ["claude-sonnet-4"],
    "tokens": {"in": 1000, "out": 250, "total": 1250},
}
FAKE_SPAN = {
    "span_id": "sp-1",
    "parent_span_id": None,
    "name": "session",
    "kind": "session",
    "status": "passed",
    "started_at": "2026-11-24T21:00:00Z",
    "ended_at": "2026-11-24T21:05:00Z",
    "duration_ms": 300000,
    "actor": "main",
    "model": "claude-sonnet-4",
    "project": "sugar",
    "cwd": "/Users/steve/Dev/sugar",
    "input": {"prompt": "go"},
    "output": {"result": "done"},
    "attributes": {"turn": 1},
}


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "sugar.db"))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    return response.text


def test_traces_tab_exists_alongside_tasks_and_activity(html):
    assert 'data-view="tasks"' in html
    assert 'data-view="activity"' in html
    assert 'data-view="traces"' in html
    assert ">Traces<" in html
    assert 'role="tablist"' in html


def test_traces_pane_has_required_hooks(html):
    for element_id in (
        "traces-pane",
        "trace-list-wrap",
        "trace-list-note",
        "trace-refresh",
        "trace-list",
        "trace-state",
        "trace-explorer",
        "trace-back",
        "trace-explorer-title",
        "trace-summary",
        "trace-explorer-note",
        "trace-tree-count",
        "trace-tree",
        "trace-scale-note",
        "trace-lane-count",
        "trace-lanes",
        "trace-inspector-close",
        "trace-inspector",
    ):
        assert f'id="{element_id}"' in html, f"missing #{element_id}"


def test_trace_list_renders_session_columns(html):
    assert "function traceRow" in html
    for label in ("duration", "turns", "tools", "tokens"):
        assert label in html
    for field in ("trace-title", "trace-sub", "trace-metrics", "trace-status"):
        assert field in html
    assert 'get("/api/traces?limit=200")' in html
    assert "started " in html


def test_trace_api_wiring(html):
    assert "/api/traces?limit=200" in html
    assert '"/api/traces/"' in html
    assert "encodeURIComponent(trace.id)" in html
    assert "function loadTraces" in html
    assert "function loadTraceDetail" in html
    assert "function traceDetailFrom" in html


def test_absent_endpoint_and_empty_states(html):
    assert "Traces unavailable on this server" in html
    assert "Trace details unavailable on this server" in html
    assert "No traces yet." in html
    assert "Loading traces…" in html
    assert "No spans recorded for this trace." in html
    # 404/405/503 are all treated as "server does not expose traces".
    assert "error.status === 404" in html
    assert "error.status === 405" in html


def test_span_kinds_are_covered_and_colour_coded(html):
    for kind in SPAN_KINDS:
        assert f'"{kind}"' in html, f"missing span kind {kind}"
    assert "TRACE_KIND_STYLE" in html
    assert "traceKindColor" in html
    assert "trace-kind-dot" in html


def test_swimlane_timeline_is_scaled_and_scrollable(html):
    assert 'id="trace-lanes"' in html
    assert "trace-span-track" in html
    assert "trace-bar" in html
    assert "trace-ticks" in html
    assert "function renderTraceLanes" in html
    assert "formatDuration" in html
    # Bars are positioned from real start/end offsets against the trace window.
    assert "span.startMs - minStart" in html
    assert "span.durationMs / total" in html
    # Lanes come from actors/agents with main/dsh/subagent priority.
    assert "TRACE_LANE_ORDER" in html
    for lane in ("main", "dsh", "subagent"):
        assert f'"{lane}"' in html
    # Mobile: the timeline gets a min width and horizontal scroll.
    assert ".trace-lanes { min-width: 620px; }" in html


def test_call_tree_and_inspector_are_wired(html):
    assert 'id="trace-tree"' in html
    assert 'id="trace-inspector"' in html
    assert "function renderTraceTree" in html
    assert "function renderTraceInspector" in html
    assert "traceCollapsed" in html
    assert "trace-tree-toggle" in html
    assert "selectTraceSpan" in html
    # Selection is wired from both the call tree and the swimlane bars.
    assert html.count("selectTraceSpan(span.id)") >= 2
    assert "trace-json" in html
    assert '"Input"' in html
    assert '"Output"' in html
    assert '"Attributes"' in html


def test_escape_and_back_navigation(html):
    assert "function traceEscape" in html
    assert "traceEscape()" in html
    assert 'title="Back to traces (Esc)"' in html
    assert "closeTraceExplorer" in html
    assert "← Traces" in html


def test_every_fake_contract_field_is_consumed(html):
    fields = set(FAKE_TRACE) | set(FAKE_SPAN)
    for field in sorted(fields):
        assert field in html, f"trace UI does not reference {field!r}"
    # The detail envelope supports both {trace, spans} and {traces: [...]}.
    assert "payload.trace" in html
    assert "payload.spans" in html


def test_existing_views_and_dependencies_are_preserved(html):
    assert 'id="rows"' in html
    assert 'id="activity-pane"' in html
    assert '"/api/events"' in html
    assert 'id="coverage-open"' in html
    assert 'id="evidence-open"' in html
    assert "New task" in html
    # Single-file UI: no CDNs, no external scripts/styles, no graph library.
    assert "https://" not in html
    assert "http://" not in html
    assert "<script src" not in html
    assert "<link " not in html


def test_traces_endpoint_contract_when_available(client):
    response = client.get("/api/traces?limit=200")
    if response.status_code in (404, 405, 503):
        pytest.skip("GET /api/traces lands with a parallel trace-backend change")
    assert response.status_code == 200
    payload = response.json()
    traces = payload if isinstance(payload, list) else payload.get("traces")
    assert isinstance(traces, list)
    for trace in traces:
        assert "trace_id" in trace or "id" in trace
        assert "started_at" in trace or "start" in trace
        assert isinstance(trace.get("spans", []), list)

    if traces:
        trace_id = traces[0].get("trace_id") or traces[0].get("id")
        detail = client.get(f"/api/traces/{trace_id}")
        assert detail.status_code == 200
        detail_payload = detail.json()
        assert "spans" in detail_payload or "trace" in detail_payload
