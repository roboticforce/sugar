"""Tests for the Mission Control Phase 0 coverage matrix UI.

The dashboard is a single static HTML page with no JavaScript test harness, so
these tests pin the served-HTML contract for the coverage control: the header
trigger and badge, the overlay panel hooks, accessibility attributes, required
labels/copy, graceful degradation when ``GET /api/coverage`` is absent, and
that none of the existing Tasks/Activity/SSE wiring is disturbed.

When the parallel ``GET /api/coverage`` endpoint is present on the branch, the
frozen wire shape (``task-20``) is exercised through ``TestClient`` as well.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app

LEVELS = ("L3", "L2", "L1", "L0")


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


def test_activity_events_open_an_inspect_modal(html):
    for element_id in (
        "event-modal",
        "event-title",
        "event-close",
        "event-meta",
        "event-payload",
        "event-open-task",
    ):
        assert 'id="%s"' % element_id in html
    assert "openEventModal" in html
    assert "row.tabIndex = 0" in html
    assert 'Inspect " + (event.kind || "event") + " event' in html


def test_hidden_attribute_wins_over_layout_classes(html):
    # The status/type/search controls use the HTML hidden attribute when the
    # Activity view is active. A `display:flex` class must not override it or
    # the task filters stay visible (and do nothing) in the activity feed.
    assert "[hidden] { display: none !important; }" in html


def test_coverage_trigger_lives_in_the_header(html):
    header_start = html.index('class="topbar"')
    header_end = html.index("</header>", header_start)
    header = html[header_start:header_end]

    assert 'id="coverage-open"' in header
    assert 'id="coverage-badge"' in header
    assert "Coverage" in header
    # The trigger advertises the dialog it opens.
    assert 'aria-haspopup="dialog"' in header
    assert 'aria-controls="coverage-modal"' in header


def test_coverage_panel_exists_with_required_hooks(html):
    for element_id in (
        "coverage-modal",
        "coverage-panel",
        "coverage-title",
        "coverage-generated",
        "coverage-refresh",
        "coverage-close",
        "coverage-summary",
        "coverage-body",
        "coverage-state",
        "coverage-list",
        "coverage-updated",
    ):
        assert f'id="{element_id}"' in html, f"missing #{element_id}"


def test_coverage_panel_is_accessible(html):
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-labelledby="coverage-title"' in html
    assert 'tabindex="-1"' in html
    # Close affordances: a labelled close button plus Escape/backdrop handling.
    assert 'title="Close (Esc)"' in html
    assert "closeCoverage" in html
    assert "Escape" in html


def test_coverage_panel_supports_every_contract_field(html):
    # The inline script must consume every field in the frozen payload shape.
    for field in (
        "display_name",
        "detected",
        "installed",
        "fidelity",
        "transport",
        "events_24h",
        "last_event_at",
        "adapter_version",
        "notes",
        "generated_at",
        "summary",
        "harnesses",
    ):
        assert field in html, f"coverage UI does not reference {field!r}"
    # Fidelity levels are rendered as visible badges with their own colours.
    for level in LEVELS:
        assert level in html
    for level_class in (
        "coverage-fid.l3",
        "coverage-fid.l2",
        "coverage-fid.l1",
        "coverage-fid.l0",
    ):
        assert level_class in html


def test_coverage_uses_relative_api_and_handles_missing_server(html):
    assert '"/api/coverage"' in html
    assert "Coverage unavailable on this server" in html
    assert "No harnesses reported." in html
    assert "Loading coverage…" in html
    assert "Refresh" in html
    assert "auto-refresh 15s" in html
    assert "setInterval" in html and "clearInterval" in html
    assert "15000" in html


def test_coverage_is_dependency_free(html):
    # Single-file UI: no CDNs, no external scripts/styles anywhere.
    assert "https://" not in html
    assert "http://" not in html
    assert "<script src" not in html
    assert "<link " not in html


def test_existing_task_and_activity_ui_is_preserved(html):
    # The coverage overlay must not replace or rename the existing surfaces.
    assert 'id="rows"' in html
    assert 'id="activity-pane"' in html
    assert 'data-view="tasks"' in html
    assert 'data-view="activity"' in html
    assert '"/api/events"' in html
    assert '"/api/activity' in html
    assert "New task" in html


def test_coverage_endpoint_contract_when_available(client):
    """Exercise the frozen wire contract when the endpoint has landed."""
    response = client.get("/api/coverage")
    if response.status_code == 404:
        pytest.skip("GET /api/coverage lands with a parallel Phase 0 change")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) >= {"generated_at", "summary", "harnesses"}
    assert isinstance(payload["summary"], dict)
    for level in LEVELS:
        assert isinstance(payload["summary"].get(level), list)
    assert isinstance(payload["harnesses"], list)

    for item in payload["harnesses"]:
        for field in (
            "harness",
            "display_name",
            "detected",
            "installed",
            "fidelity",
            "transport",
            "last_event_at",
            "events_24h",
            "notes",
        ):
            assert field in item, f"harness entry missing {field!r}"
        assert item["fidelity"] in LEVELS
