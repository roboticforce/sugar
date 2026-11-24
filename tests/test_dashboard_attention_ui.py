"""Tests for the Overview attention queue + trace narrative UI.

The dashboard is a single static HTML page with no JavaScript runtime in tests,
so these pin the served-HTML contract for the Human layer 2 surfaces: the
Overview landing tab, attention queue fetch/rendering/filtering, the compact
summary (tasks, traces today, optional usage), recent traces with risk badges,
overview → trace/task navigation, and the trace insight strip. Missing backend
endpoints are expected to degrade, so the live API check skips while
``GET /api/attention`` is absent.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app

# Frozen-looking attention payload used to prove the UI references each field
# and link target the backend is expected to provide.
FAKE_ATTENTION_ITEMS = [
    {
        "id": "att-1",
        "severity": "critical",
        "kind": "tool_failure",
        "title": "pytest failed in spend UI",
        "detail": "exit code 1",
        "project": "sugar",
        "ts": "2026-11-24T22:00:00Z",
        "trace_id": "tr-1",
    },
    {
        "id": "att-2",
        "severity": "high",
        "kind": "approval",
        "title": "Approval denied for deploy",
        "task_id": "task-1",
        "project": "sugar",
        "created_at": "2026-11-24T21:00:00Z",
    },
]

TRACE_INSIGHT_FIELDS = ("headline", "bullets", "risk")


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


def test_overview_is_the_first_tab_and_landing_view(html):
    assert 'id="view-switch"' in html
    assert 'data-view="overview"' in html
    assert ">Overview<" in html
    # Overview sits before Tasks in the tablist and is the active landing tab.
    assert html.index('data-view="overview"') < html.index('data-view="tasks"')
    assert 'data-view="overview" role="tab" aria-selected="true"' in html
    assert 'view: "overview"' in html
    assert "function loadOverview" in html


def test_overview_pane_has_required_hooks(html):
    for element_id in (
        "overview-pane",
        "overview-refresh",
        "overview-scroll",
        "attention-list",
        "attention-state",
        "attention-count",
        "attention-filter",
        "attention-filter-all",
        "attention-filter-high",
        "overview-task-metrics",
        "overview-trace-count",
        "overview-usage",
        "overview-usage-body",
        "overview-trace-list",
        "overview-trace-state",
        "overview-view-traces",
    ):
        assert f'id="{element_id}"' in html, f"missing #{element_id}"


def test_attention_queue_fetches_and_tolerates_shapes(html):
    assert 'api.get("/api/attention")' in html
    assert "function loadAttention" in html
    assert "function attentionItemsFrom" in html
    for key in ("items", "attention", "alerts", "queue", "notifications"):
        assert f'"{key}"' in html, f"missing attention envelope key {key}"
    # 404/405/503 all mean "this server has no attention endpoint".
    assert "error.status === 404" in html
    assert "error.status === 405" in html
    assert "error.status === 503" in html


def test_attention_cards_render_contract_fields(html):
    for field in (
        "severity",
        "kind",
        "title",
        "detail",
        "project",
        "trace_id",
        "task_id",
    ):
        assert field in html, f"attention card does not reference {field!r}"
    for field in ("ts", "created_at", "at"):
        assert field in html
    assert "attention-card" in html
    assert "attention-sev" in html
    assert "attention-kind" in html
    assert "attention-detail" in html
    assert "attention-meta" in html


def test_attention_severity_filter_is_wired(html):
    assert 'data-attention-filter="all"' in html
    assert 'data-attention-filter="high"' in html
    assert "function setAttentionFilter" in html
    assert "function attentionIsHigh" in html
    assert "ATTENTION_HIGH" in html
    for severity in ("critical", "high", "error", "urgent", "severe"):
        assert f'"{severity}"' in html, f"missing high severity {severity}"


def test_attention_empty_and_unavailable_states(html):
    assert "Nothing needs attention." in html
    assert "No high-severity items." in html
    assert "Loading attention…" in html
    assert "Attention unavailable on this server" in html
    assert "Attention failed to load: " in html


def test_overview_summary_reuses_live_apis(html):
    # Task counts come from the existing SSE stats snapshot.
    for label in ("pending", "active", "blocked"):
        assert f'"{label}"' in html
    assert "function renderOverviewSummary" in html
    assert "tracesTodayCount" in html
    assert "Traces today" in html
    # Usage/cost is optional and reuses the existing endpoint with no fake data.
    assert '"/api/usage?group_by=harness&limit=5000"' in html
    assert "usageEstimate" in html
    assert "reported cost" in html
    assert "estimated cost" in html


def test_recent_traces_rows_show_risk_badges(html):
    assert 'id="overview-trace-list"' in html
    assert "function overviewTraceRow" in html
    assert "function traceRisk" in html
    assert "riskBadgeNode" in html
    assert "risk-badge" in html
    assert "insights" in html
    assert "risk" in html
    assert "function openTraceById" in html


def test_overview_navigation_links_to_trace_and_task(html):
    assert "function openAttentionTarget" in html
    assert 'setView("tasks")' in html
    assert "selectTask(item.taskId)" in html
    assert 'setView("traces")' in html
    assert "openTrace(" in html
    assert '"Open trace →"' in html
    assert '"Open task →"' in html


def test_trace_insights_strip_is_wired(html):
    for element_id in (
        "trace-insights",
        "trace-insights-risk",
        "trace-insights-headline",
        "trace-insights-bullets",
    ):
        assert f'id="{element_id}"' in html, f"missing #{element_id}"
    assert "function renderTraceInsights" in html
    for field in TRACE_INSIGHT_FIELDS:
        assert field in html, f"trace insights do not reference {field!r}"
    # Bullets may link to a span; the strip hides itself when there is nothing.
    assert "selectTraceSpan(bullet.spanId)" in html
    assert "box.hidden = true" in html
    # The strip sits above the explorer grid.
    assert html.index('id="trace-insights"') < html.index('class="trace-grid"')


def test_existing_views_and_dependencies_are_preserved(html):
    assert 'data-view="tasks"' in html
    assert 'data-view="activity"' in html
    assert 'data-view="traces"' in html
    assert 'id="rows"' in html
    assert 'id="activity-pane"' in html
    assert 'id="traces-pane"' in html
    assert '"/api/events"' in html
    assert "New task" in html
    # Single-file UI: no CDNs, no external scripts/styles.
    assert "https://" not in html
    assert "http://" not in html
    assert "<script src" not in html
    assert "<link " not in html


def test_attention_endpoint_contract_when_available(client):
    response = client.get("/api/attention")
    if response.status_code in (404, 405, 503):
        pytest.skip("GET /api/attention lands with a parallel backend change")
    assert response.status_code == 200
    payload = response.json()
    if isinstance(payload, list):
        items = payload
    else:
        candidates = ("items", "attention", "alerts", "queue", "notifications")
        items = []
        for key in candidates:
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
    assert isinstance(items, list)
    for item in items:
        assert "severity" in item
        assert "title" in item
        assert isinstance(item["title"], str)
        assert item.get("trace_id") or item.get("task_id") or item.get("kind")
