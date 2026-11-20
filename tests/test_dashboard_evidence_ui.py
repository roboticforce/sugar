"""Tests for the Mission Control Phase 1c Evidence panel UI.

The dashboard is a single static HTML page with no JavaScript test harness, so
these tests pin the served-HTML contract for the evidence control: the header
trigger next to Coverage, the overlay panel hooks, accessibility attributes,
the evidence kinds/status filters, summary chips, required copy, and graceful
degradation when ``GET /api/evidence`` is absent. They also assert that the
existing Tasks/Activity/SSE/Coverage wiring is untouched.

When the parallel ``GET /api/evidence`` endpoint (task-26) is present, its
frozen wire shape is exercised through ``TestClient`` with a real (temporary)
event spine: an evidence event is ingested, then read back through the filters.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app

EVIDENCE_KINDS = (
    "verification.test_run",
    "verification.check_result",
    "verification.review",
    "policy.decision",
    "approval.granted",
    "approval.denied",
    "artifact.created",
    "commit.linked",
)

STATUS_OPTIONS = ("passed", "failed", "blocked", "ok", "error")


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "sugar.db"))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def spine_client(tmp_path):
    """Dashboard with an opted-in event spine (needed for POST /api/evidence)."""
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


def test_evidence_trigger_lives_in_the_header_next_to_coverage(html):
    header_start = html.index('class="topbar"')
    header_end = html.index("</header>", header_start)
    header = html[header_start:header_end]

    assert 'id="evidence-open"' in header
    assert 'id="evidence-badge"' in header
    assert 'id="coverage-open"' in header
    assert "Evidence" in header
    # The evidence trigger sits before the existing Coverage control.
    assert header.index("evidence-open") < header.index("coverage-open")
    assert 'aria-haspopup="dialog"' in header
    assert 'aria-controls="evidence-modal"' in header


def test_evidence_panel_exists_with_required_hooks(html):
    for element_id in (
        "evidence-modal",
        "evidence-panel",
        "evidence-title",
        "evidence-generated",
        "evidence-refresh",
        "evidence-close",
        "evidence-filters",
        "evidence-task",
        "evidence-kind",
        "evidence-status",
        "evidence-summary",
        "evidence-body",
        "evidence-state",
        "evidence-list",
        "evidence-updated",
        "evidence-limit-note",
    ):
        assert f'id="{element_id}"' in html, f"missing #{element_id}"


def test_evidence_panel_is_accessible(html):
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-labelledby="evidence-title"' in html
    assert 'tabindex="-1"' in html
    for label in (
        'aria-label="Filter evidence by task id"',
        'aria-label="Filter evidence by kind"',
        'aria-label="Filter evidence by status"',
    ):
        assert label in html
    assert 'title="Close (Esc)"' in html
    assert "closeEvidence" in html
    assert "Escape" in html


def test_evidence_kinds_are_filterable(html):
    assert 'id="evidence-kind"' in html
    assert "All kinds" in html
    for kind in EVIDENCE_KINDS:
        assert f'value="{kind}"' in html, f"missing kind option {kind}"


def test_evidence_status_filter_options(html):
    assert 'id="evidence-status"' in html
    assert "All statuses" in html
    for status in STATUS_OPTIONS:
        assert f'value="{status}"' in html, f"missing status option {status}"


def test_evidence_summary_chips_are_color_coded(html):
    assert "evidence-chip" in html
    for label in ("total", "passed", "failed", "blocked"):
        assert f"evidence-chip {label}" in html or f'"{label}"' in html
    for script_fragment in (
        '"passed", counts.passed',
        '"failed", counts.failed',
        '"blocked", counts.blocked',
    ):
        assert script_fragment in html
    for rule in (
        ".evidence-chip.passed b",
        ".evidence-chip.failed b",
        ".evidence-chip.blocked b",
    ):
        assert rule in html


def test_evidence_api_query_and_refresh_wiring(html):
    assert '"/api/evidence?' in html
    for param in ("task_id=", "kind=", "status=", "limit=", "offset="):
        assert param in html, f"missing query parameter {param}"
    assert "EVIDENCE_LIMIT = 100" in html
    assert "last_id" in html
    assert "setInterval" in html and "clearInterval" in html
    assert "15000" in html
    assert "auto-refresh 15s" in html
    # Auto-refresh is gated on the applied filters being unchanged.
    assert "evidenceFiltersMatch" in html


def test_evidence_states_and_pagination_copy(html):
    assert "Evidence unavailable on this server" in html
    # A server with only the POST ingest route answers GET with 405; that is the
    # same "read endpoint has not landed" state as an older server's 404.
    assert "error.status === 405" in html
    assert "No evidence yet — record one with `sugar evidence record ...`" in html
    assert "No evidence matches the current filters." in html
    assert "Loading evidence…" in html
    assert "showing " in html
    assert "limit " in html


def test_evidence_is_dependency_free_and_preserves_existing_ui(html):
    # Single-file UI: no CDNs, no external scripts/styles anywhere.
    assert "https://" not in html
    assert "http://" not in html
    assert "<script src" not in html
    assert "<link " not in html
    # Existing surfaces stay in place.
    assert 'id="rows"' in html
    assert 'id="activity-pane"' in html
    assert 'data-view="tasks"' in html
    assert 'data-view="activity"' in html
    assert '"/api/events"' in html
    assert '"/api/activity' in html
    assert 'id="coverage-open"' in html
    assert 'id="coverage-modal"' in html
    assert "New task" in html


def test_evidence_endpoint_contract_when_available(spine_client):
    """Exercise the frozen wire contract when the read endpoint has landed."""
    response = spine_client.get("/api/evidence")
    if response.status_code in (404, 405):
        pytest.skip("GET /api/evidence lands with a parallel Phase 1c change")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) >= {"events", "last_id", "summary"}
    assert isinstance(payload["events"], list)
    assert isinstance(payload["last_id"], int)
    summary = payload["summary"]
    assert isinstance(summary, dict)
    assert isinstance(summary.get("total"), int)
    assert isinstance(summary.get("by_status"), dict)
    assert isinstance(summary.get("by_kind"), dict)

    # Ingest a canonical evidence event through the existing POST endpoint and
    # read it back via the evidence filters.
    body = {
        "kind": "verification.check_result",
        "harness": "claude-code",
        "task_id": "task-evidence-ui",
        "status": "passed",
        "payload": {
            "check": "pytest",
            "command": "pytest -q",
            "exit_code": 0,
        },
    }
    posted = spine_client.post("/api/evidence", json=body)
    assert posted.status_code in (201, 202)

    filtered = spine_client.get(
        "/api/evidence?kind=verification.check_result&status=passed&limit=10"
    )
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    events = filtered_payload["events"]
    assert isinstance(events, list)
    assert events, "ingested evidence event did not appear in GET /api/evidence"
    for event in events:
        assert event.get("kind") == "verification.check_result"
        assert event.get("task_id") == "task-evidence-ui"
    assert filtered_payload["summary"]["total"] >= 1
