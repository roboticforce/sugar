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
def spine_client(tmp_path):
    """Dashboard with an opted-in event spine (needed by GET /api/traces)."""
    app = create_app(
        str(tmp_path / "sugar.db"),
        events_db=str(tmp_path / "events.db"),
    )
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
    assert 'get("/api/traces?limit=100")' in html
    assert "started " in html


def test_trace_api_wiring(html):
    assert "/api/traces?limit=100" in html
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


def test_traces_endpoint_contract_when_available(spine_client, client):
    response = spine_client.get("/api/traces?limit=100")
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

    # An unknown/malformed trace id is a 404 on the detail endpoint.
    assert spine_client.get("/api/traces/does-not-exist").status_code == 404

    if traces:
        trace_id = traces[0].get("trace_id") or traces[0].get("id")
        detail = spine_client.get(f"/api/traces/{trace_id}")
        assert detail.status_code == 200
        detail_payload = detail.json()
        assert "spans" in detail_payload or "trace" in detail_payload


def test_trace_list_respects_api_limit(html):
    assert "/api/traces?limit=100" in html
    assert "/api/traces?limit=200" not in html


# ---------------------------------------------------------------------------
# Architecture / artifact graph mode (Phase 1d/3)
# ---------------------------------------------------------------------------


def test_trace_graph_mode_toggle_hooks(html):
    assert 'id="trace-mode-switch"' in html
    assert 'role="tablist"' in html
    assert 'id="trace-mode-timeline"' in html
    assert 'id="trace-mode-graph"' in html
    assert 'data-trace-mode="timeline"' in html
    assert 'data-trace-mode="graph"' in html
    # Swimlanes/timeline remain the default mode.
    assert 'id="trace-mode-timeline" data-trace-mode="timeline" class="active"' in html
    assert 'aria-selected="true"' in html
    assert 'id="trace-visual-title"' in html
    assert 'id="trace-graph-body"' in html
    assert 'id="trace-graph-note"' in html
    assert 'id="trace-graph-scroll"' in html
    assert 'id="trace-graph"' in html
    assert 'aria-label="Trace architecture and artifact graph"' in html


def test_trace_graph_layers_and_builders(html):
    for layer in (
        "Inputs",
        "Models",
        "Outputs / Artifacts",
        "Verification / Approvals",
    ):
        assert layer in html, f"missing graph layer {layer}"
    assert "TRACE_GRAPH_LAYERS" in html
    assert "function traceLayerForKind" in html
    assert "function traceGraphModel" in html
    assert "function renderTraceGraph" in html
    assert "TRACE_GRAPH_NODE_CAP = 80" in html
    # The backend summary field is tokens_total; the UI falls back to it.
    assert "tokens_total" in html
    assert "function setTraceVisual" in html
    assert "function renderTraceVisual" in html


def test_trace_graph_artifact_and_parent_edges(html):
    # Artifact nodes come from artifact spans and attributes.paths/artifacts.
    assert "attrs.paths" in html
    assert "attrs.artifacts" in html
    assert "function traceArtifactPaths" in html
    assert '"artifact:" + path' in html
    assert "nodeBySpan" in html
    # Parent -> child and artifact/subagent edge styling is present.
    assert ".trace-graph-edge.artifact" in html
    assert ".trace-graph-edge.subagent" in html
    assert "marker-start" not in html  # arrowhead only, no stray markers


def test_trace_graph_aggregates_and_caps_nodes(html):
    assert "toolGroups" in html
    assert "aggregatedCalls" in html
    assert "repeated tool call(s) aggregated by name/kind." in html
    assert "Showing " in html
    assert "nodes (cap " in html
    assert "truncated" in html
    assert "perLayer" in html


def test_trace_graph_click_selects_span(html):
    assert "selectTraceSpan(node.spanId)" in html
    assert html.count("selectTraceSpan(node.spanId)") >= 2
    assert 'group.setAttribute("role", "button")' in html
    assert 'group.setAttribute("tabindex", "0")' in html
    assert 'traceSvgEl("title")' in html
    assert "node.spanIds.indexOf(state.traceSelectedSpanId)" in html


def test_trace_graph_empty_state_copy(html):
    assert (
        "No artifact or path metadata recorded for this trace — showing span flow only."
        in html
    )
    assert "No spans recorded for this trace." in html


def test_trace_graph_is_vanilla_svg_and_scrollable(html):
    assert "document.createElementNS" in html
    assert "TRACE_SVG_NS" in html
    assert "http://" not in html
    assert "trace-graph-edge" in html
    assert ".trace-graph-scroll { flex: 1; min-height: 220px; overflow: auto; }" in html
    assert ".trace-graph-node.selected rect" in html


def test_trace_graph_does_not_regress_existing_explorer(html):
    assert 'id="trace-lanes"' in html
    assert 'id="trace-tree"' in html
    assert 'id="trace-inspector"' in html
    assert "renderTraceLanes" in html
    assert "renderTraceTree" in html
    assert "renderTraceInspector" in html
    assert "traceEscape" in html
    assert 'id="trace-back"' in html
    # Timeline stays the default; graph is opt-in only.
    assert 'state.traceVisual = mode === "graph" ? "graph" : "timeline"' in html


# ---------------------------------------------------------------------------
# Phase 3b: parent/child trace hierarchy, subagent links, LLM aggregation
# ---------------------------------------------------------------------------


def test_trace_hierarchy_summary_fields_are_normalized(html):
    # Additive backend summary fields (task-48) are consumed if present.
    for field in ("parent_trace_id", "child_trace_ids", "subagent_count"):
        assert field in html, f"missing hierarchy field {field}"
    for field in ("parentTraceId", "childTraceIds", "subagentCount"):
        assert field in html, f"missing normalized hierarchy field {field}"
    assert "function traceStringId" in html
    assert "function traceStringList" in html
    assert "function traceHierarchyRows" in html
    assert "function openTraceRelation" in html
    assert "function traceById" in html


def test_trace_list_hierarchy_indicators_and_flat_fallback(html):
    assert "traceHierarchyRows(traces)" in html
    assert "traceRow(row.trace, row.depth)" in html
    assert "trace-hierarchy-chip" in html
    assert "trace-hierarchy-badge" in html
    assert "child of " in html
    assert "child trace" in html
    assert "subagent" in html
    # Children follow their parent (indented); orphan/absent-parent traces stay
    # at depth 0, so a payload without hierarchy fields renders flat.
    assert "if (!nested.has(trace.id)) walk(trace, 0)" in html
    assert "main.style.paddingLeft = (level * 14)" in html
    assert "trace-row.child" in html
    assert "event.stopPropagation()" in html


def test_trace_detail_parent_navigation(html):
    assert 'id="trace-parent-link"' in html
    assert 'id="trace-relations"' in html
    assert "← Parent" in html
    assert "function renderTraceRelations" in html
    assert "renderTraceRelations(trace)" in html
    assert "parentLink.hidden = !trace.parentTraceId" in html
    assert "trace.parentTraceId" in html
    assert 'openTraceRelation(trace.parentTraceId, "Parent trace")' in html
    assert "Parent: " in html


def test_trace_detail_subagent_links(html):
    assert "child_session_id" in html
    assert "attrs.child_session_id" in html
    assert "childSessionId" in html
    assert "subagent: " in html
    assert "child session " in html
    assert "selectTraceSpan(span.id)" in html
    # Subagent count is also surfaced in the summary chips.
    assert 'add("subagents", subagents)' in html
    # Lanes annotate subagent child sessions.
    assert "subagent session " in html


def test_trace_graph_aggregates_llm_nodes(html):
    assert "llmGroups" in html
    assert "aggregatedLlm" in html
    assert "llmagg:" in html
    assert "repeated LLM call(s) aggregated by model/turn." in html
    assert 'sub: "llm · " + model' in html
    # Aggregation key is model + turn (or the model alone without a turn parent).
    assert (
        'const key = (span.model || "unknown-model") + "|" + llmTurnKey(span);' in html
    )
    assert "function llmTurnKey" in html
    # Existing tool aggregation and node cap are preserved.
    assert "toolGroups" in html
    assert "TRACE_GRAPH_NODE_CAP = 80" in html


def test_trace_timeline_repeated_llm_tooltip(html):
    assert "function traceLlmRepeatCounts" in html
    assert "traceLlmRepeatCounts()" in html
    assert "similar LLM calls this turn" in html
    assert "counts.get(key) || 0) + 1" in html


def test_trace_hierarchy_absent_fields_keep_flat_fallback(html):
    # Parent/child strips stay hidden without the additive fields.
    assert (
        'id="trace-parent-link" type="button" title="Open parent trace" hidden' in html
    )
    assert "box.hidden = nodes.length === 0" in html
    assert "if (trace.parentTraceId) addParent(trace.parentTraceId)" in html
    # The LLM aggregation note is conditional (older traces with one LLM span
    # produce no aggregation note).
    assert "if (model.aggregatedLlm > 0)" in html


def test_trace_hierarchy_summary_contract_is_optional(spine_client):
    response = spine_client.get("/api/traces?limit=100")
    if response.status_code in (404, 405, 503):
        pytest.skip("GET /api/traces requires the event spine")
    payload = response.json()
    traces = payload if isinstance(payload, list) else payload.get("traces")
    assert isinstance(traces, list)
    for trace in traces:
        if "parent_trace_id" in trace:
            assert trace["parent_trace_id"] is None or isinstance(
                trace["parent_trace_id"], str
            )
        if "child_trace_ids" in trace:
            assert isinstance(trace["child_trace_ids"], list)
        if "subagent_count" in trace:
            assert isinstance(trace["subagent_count"], int)
