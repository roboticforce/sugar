"""Tests for the dashboard spend view + task project filter.

The dashboard is a single static HTML page with no JavaScript runtime in tests,
so these pin the served-HTML contract: the Usage "Group by" selector and every
frozen dimension, tokens in/out/total rendering, reported vs optional estimated
cost labelling, the task project/CWD surface and project filter, preservation of
the Activity project/model filters, and graceful degradation when usage data or
the optional pricing fields are missing.

When ``GET /api/usage`` is available (it is on this base), the frozen response
shape is exercised through ``TestClient`` with a temporary event spine.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app

GROUP_DIMENSIONS = ("harness", "actor", "project", "task", "model", "day")


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "sugar.db"))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def spine_client(tmp_path):
    """Dashboard with an opted-in event spine (needed by GET /api/usage)."""
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


def test_usage_group_selector_has_all_dimensions(html):
    assert 'id="usage-group"' in html
    assert "Group by" in html
    assert 'aria-label="Group usage by"' in html
    for dimension in GROUP_DIMENSIONS:
        assert f'value="{dimension}"' in html, f"missing group dimension {dimension}"
    assert 'value="harness" selected' in html


def test_usage_fetches_the_selected_dimension(html):
    assert '"/api/usage?group_by="' in html
    assert '"&limit=5000"' in html
    assert '"usage-group"' in html
    assert "state.usageGroup" in html
    assert "loadUsage" in html
    assert "USAGE_GROUP_LABELS" in html


def test_usage_estimated_cost_is_optional_and_clearly_labeled(html):
    # Estimated cost only appears when the backend reports it; tokens/cost are
    # not fabricated client-side.
    assert "estimated_cost_usd" in html
    assert "cost_basis" in html
    assert "reported cost" in html
    assert "estimated cost" in html
    assert " est." in html
    assert "no estimated cost reported" in html
    # Missing/blank pricing fields must not render an estimate.
    assert "value === null || value === undefined" in html


def test_usage_renders_tokens_in_out_total_and_empty_state(html):
    assert "tokens in" in html
    assert "tokens out" in html
    assert "tokens total" in html
    assert "usage events" in html
    assert "no usage in range" in html
    assert "usage-detail" in html


def test_usage_dimension_errors_are_graceful(html):
    # model groups land with the backend slice; a 400 must show a local message
    # instead of breaking the panel or raising a toast.
    assert "error.status === 400" in html
    assert "is unavailable on this server" in html
    assert "grouping by " in html


def test_task_table_shows_project_and_cwd(html):
    assert "function taskProject" in html
    assert "function taskCwd" in html
    assert "function taskContext" in html
    assert '"title-context"' in html
    assert '"task-project"' in html
    assert '"task-cwd"' in html
    # CWD is also exposed as a tooltip.
    assert "path.title = cwd" in html


def test_task_context_is_optional_and_degrades_gracefully(html):
    # Legacy rows may not carry context.project / context.cwd at all.
    assert 'context && typeof context === "object"' in html
    assert 'return "";' in html
    assert ".project" in html
    assert ".cwd" in html


def test_project_filter_is_derived_from_task_contexts(html):
    assert 'id="project-filter"' in html
    assert "All projects" in html
    assert "renderTaskProjectFilter" in html
    assert "state.projectFilter" in html
    assert "taskProject(task) !== state.projectFilter" in html
    # Project/CWD also participate in text search.
    assert "taskProject(task), taskCwd(task)" in html


def test_task_drawer_shows_project_and_cwd(html):
    assert 'addKV("Project", project)' in html
    assert 'addKV("CWD", cwd)' in html


def test_activity_project_and_model_filters_are_preserved(html):
    assert 'id="activity-project"' in html
    assert 'id="activity-model"' in html
    assert "All models" in html
    assert "state.feedProject" in html
    assert "state.feedModel" in html
    # Existing surfaces stay intact.
    assert 'id="rows"' in html
    assert 'id="activity-pane"' in html
    assert '"/api/events"' in html
    assert 'id="coverage-open"' in html
    assert 'id="evidence-open"' in html
    assert "New task" in html
    # Single-file UI: no CDNs, no external scripts/styles.
    assert "https://" not in html
    assert "http://" not in html
    assert "<script src" not in html
    assert "<link " not in html


def test_usage_endpoint_shape_when_available(spine_client):
    response = spine_client.get("/api/usage?group_by=harness&limit=5000")
    if response.status_code == 503:
        pytest.skip("event spine is disabled in this test environment")
    assert response.status_code == 200
    report = response.json()
    assert report["group_by"] == "harness"
    assert isinstance(report["groups"], list)
    totals = report["totals"]
    for field in ("events", "tokens_in", "tokens_out", "tokens_total", "cost_usd"):
        assert field in totals
    # Optional task-29 pricing fields: if present, they must be numeric with a
    # basis label and never client-invented.
    if "estimated_cost_usd" in totals:
        assert isinstance(totals["estimated_cost_usd"], (int, float))
        assert "cost_basis" in totals
    for group in report["groups"]:
        assert "key" in group
        if "estimated_cost_usd" in group:
            assert isinstance(group["estimated_cost_usd"], (int, float))

    # The model dimension may not have landed on the backend yet (task-29): the
    # UI is expected to survive either the new 200 response or the old 400.
    model = spine_client.get("/api/usage?group_by=model&limit=5000")
    assert model.status_code in (200, 400)
    if model.status_code == 200:
        assert model.json()["group_by"] == "model"
    else:
        assert model.json().get("error") == "validation_error"
