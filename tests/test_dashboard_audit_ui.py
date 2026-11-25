"""Tests for the dashboard Audit / AIBOM view.

The dashboard is a single static HTML page with no JavaScript runtime in tests,
so these pin the served-HTML contract for the read-only Audit tab: scope
controls, observed AIBOM inventory (summary + component table + evidence
links), coverage snapshot, limitations/banner copy, claims + CLI export
instructions, graceful unavailable/empty states, and that no mutation verbs are
wired into the audit module. The live `/api/aibom` and `/api/coverage` shapes
are exercised through ``TestClient`` with a temporary event spine.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app

AIBOM_TYPES = ("model", "harness", "tool", "mcp_server", "artifact")


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "sugar.db"))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def spine_client(tmp_path):
    """Dashboard with an opted-in event spine (needed by GET /api/aibom)."""
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


@pytest.fixture
def audit_html(html):
    """The Audit module slice of the inline script (for no-mutation checks)."""
    start = html.index("audit / AIBOM (Phase 1c)")
    end = html.index(
        "// ------------------------------------------------------------------ form",
        start,
    )
    return html[start:end]


def test_audit_tab_exists_after_traces(html):
    assert 'data-view="audit"' in html
    assert ">Audit<" in html
    assert html.index('data-view="traces"') < html.index('data-view="audit"')
    # Overview is still the landing tab; Audit is opt-in.
    assert 'data-view="overview" role="tab" aria-selected="true"' in html
    assert 'data-view="audit" role="tab" aria-selected="false"' in html


def test_audit_pane_has_required_hooks(html):
    for element_id in (
        "audit-pane",
        "audit-head",
        "audit-scope-note",
        "audit-project",
        "audit-since",
        "audit-refresh",
        "audit-scroll",
        "audit-banner",
        "aibom-summary",
        "aibom-rows",
        "aibom-state",
        "aibom-note",
        "aibom-limitations",
        "audit-coverage-levels",
        "audit-coverage-rows",
        "audit-coverage-state",
        "audit-coverage-note",
        "audit-claims",
        "audit-commands",
    ):
        assert f'id="{element_id}"' in html, f"missing #{element_id}"


def test_audit_scope_controls_are_wired(html):
    assert 'aria-label="Audit project scope"' in html
    assert 'aria-label="Audit inventory since date"' in html
    assert "function aibomScopeQuery" in html
    assert "function aibomUrl" in html
    assert "function renderAuditProjectOptions" in html
    assert "function auditProjectValues" in html
    assert "state.auditProject" in html
    assert "state.auditSince" in html


def test_aibom_fetch_uses_scope_parameters(html):
    assert '"/api/aibom"' in html
    assert '"project=" + encodeURIComponent(state.auditProject)' in html
    assert '"since=" + encodeURIComponent(state.auditSince + "T00:00:00Z")' in html
    assert "function loadAibom" in html
    assert "api.get(aibomUrl())" in html
    # Absent endpoint degrades to an explicit unavailable state.
    assert "AIBOM unavailable on this server" in html
    assert "AIBOM failed to load: " in html


def test_aibom_summary_covers_component_types(html):
    for component_type in AIBOM_TYPES:
        assert f'"{component_type}"' in html, f"missing AIBOM type {component_type}"
    assert "AIBOM_TYPE_LABELS" in html
    assert "function aibomTypeCounts" in html
    assert "function renderAibomSummary" in html
    assert "audit-count" in html


def test_aibom_component_table_mapping(html):
    for field in (
        "type",
        "name",
        "events",
        "projects",
        "first_seen",
        "last_seen",
        "evidence_links",
    ):
        assert field in html, f"component table does not reference {field!r}"
    for column in (
        "Type",
        "Name",
        "Events",
        "Projects",
        "First seen",
        "Last seen",
        "Evidence",
    ):
        assert f">{column}<" in html, f"missing table column {column}"
    assert "function aibomRow" in html
    assert "function aibomEvidenceLinks" in html
    assert "audit-table" in html


def test_aibom_evidence_links_open_traces(html):
    assert "openTraceById(traceId" in html
    assert "link.trace_id" in html
    assert 'el("button", null, "trace")' in html


def test_limitations_and_observed_banner(html):
    assert (
        "Observed runtime inventory — not a complete or standards-certified AIBOM"
        in html
    )
    assert "function renderAibomLimitations" in html
    assert "limitations" in html
    assert "Limitations unavailable on this server." in html


def test_coverage_snapshot_is_wired(html):
    assert 'api.get("/api/coverage")' in html
    assert "function auditCoverageCounts" in html
    assert "function renderAuditCoverage" in html
    for level in ("L3", "L2", "L1", "L0"):
        assert f'"{level}"' in html
    assert "audit-fid" in html
    assert "Coverage unavailable on this server" in html
    assert "No harnesses reported." in html


def test_audit_states(html):
    assert "Loading AIBOM…" in html
    assert "No AIBOM components observed in this scope." in html
    assert "Loading coverage…" in html
    assert "Loading limitations…" in html


def test_claims_and_cli_export_instructions(html):
    assert "What this view can and cannot prove" in html
    for claim in ("Can show", "Cannot show", "Export for an audit"):
        assert claim in html
    assert "not a complete or standards-certified AIBOM" in html
    assert "no mutations" in html
    assert "sugar audit export --out audit/2026-11.jsonl" in html
    assert "sugar audit verify audit/2026-11.jsonl audit/2026-11.manifest.json" in html
    assert "sugar aibom --project sugar --since 2026-11-01 --json" in html
    assert "The download above uses the same package builder" in html


def test_audit_module_is_read_only(audit_html):
    assert "api.get(" in audit_html
    for mutation in (
        "api.post(",
        "api.patch(",
        "api.del(",
        'api.request("POST"',
        'method: "POST"',
    ):
        assert mutation not in audit_html, f"audit module must not call {mutation}"


def test_existing_views_and_dependencies_are_preserved(html):
    for view in ("overview", "tasks", "activity", "traces", "audit"):
        assert f'data-view="{view}"' in html
    assert 'id="overview-pane"' in html
    assert 'id="tasks-pane"' in html
    assert 'id="activity-pane"' in html
    assert 'id="traces-pane"' in html
    assert '"/api/events"' in html
    assert "New task" in html
    # Single-file UI: no CDNs, no external scripts/styles.
    assert "https://" not in html
    assert "http://" not in html
    assert "<script src" not in html
    assert "<link " not in html


def test_aibom_and_coverage_contract_when_available(spine_client):
    aibom = spine_client.get("/api/aibom")
    if aibom.status_code in (404, 405, 503):
        pytest.skip("GET /api/aibom requires the event spine")
    assert aibom.status_code == 200
    report = aibom.json()
    for field in (
        "schema",
        "generated_at",
        "scope",
        "components",
        "limitations",
        "coverage",
        "events_scanned",
    ):
        assert field in report, f"AIBOM report missing {field!r}"
    assert isinstance(report["components"], list)
    assert isinstance(report["limitations"], list) and report["limitations"]
    assert report["scope"]["project"] is None
    for component in report["components"]:
        for field in (
            "type",
            "name",
            "events",
            "projects",
            "first_seen",
            "last_seen",
            "evidence_links",
        ):
            assert field in component

    coverage = spine_client.get("/api/coverage")
    if coverage.status_code in (404, 405, 503):
        pytest.skip("GET /api/coverage is unavailable in this environment")
    assert coverage.status_code == 200
    snapshot = coverage.json()
    assert isinstance(snapshot.get("harnesses"), list)
    assert isinstance(snapshot.get("summary"), dict)
    for level in ("L3", "L2", "L1", "L0"):
        assert level in snapshot["summary"]

    # Optional query parameters are accepted by the AIBOM endpoint.
    scoped = spine_client.get("/api/aibom?project=sugar&since=2026-01-01T00:00:00Z")
    assert scoped.status_code == 200
    assert scoped.json()["scope"]["project"] == "sugar"
