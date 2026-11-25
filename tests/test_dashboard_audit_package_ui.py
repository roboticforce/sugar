"""Tests for the Audit view package download UX (Phase 4).

The dashboard is a single static HTML file with no JavaScript runtime in tests,
so these pin the served-HTML contract for the "Download audit package" panel:
GET-only fetch wiring with scope params, blob download path, response header
handling (files/SHA-256/signed), client-side SHA-256 fallback, status/error
states, sensitive-metadata + offline verification copy, and that the existing
AIBOM/coverage/limitations panels are untouched. The live endpoint is exercised
through ``TestClient`` when the backend ships it.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "sugar.db"))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def spine_client(tmp_path):
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
def package_html(html):
    """The package-download module slice (for read-only checks)."""
    start = html.index("audit package download (Phase 4)")
    end = html.index(
        "// ------------------------------------------------------------------ form",
        start,
    )
    return html[start:end]


def test_audit_package_panel_hooks(html):
    for element_id in (
        "audit-package-note",
        "audit-package-scope",
        "audit-until",
        "audit-package-download",
        "audit-package-status",
        "audit-package-result",
        "audit-package-filename",
        "audit-package-files",
        "audit-package-size",
        "audit-package-sha256",
        "audit-package-signed",
    ):
        assert f'id="{element_id}"' in html, f"missing #{element_id}"
    assert "Download audit package" in html
    assert 'aria-label="Audit package until date"' in html


def test_audit_package_fetches_with_scope_params(html):
    assert '"/api/audit/package"' in html
    assert "function auditPackageUrl" in html
    assert "function downloadAuditPackage" in html
    assert '"project=" + encodeURIComponent(state.auditProject)' in html
    assert '"since=" + encodeURIComponent(state.auditSince + "T00:00:00Z")' in html
    assert '"until=" + encodeURIComponent(state.auditUntil + "T23:59:59Z")' in html
    assert "state.auditUntil" in html
    assert "function renderAuditPackageScope" in html
    assert "function resetAuditPackageResult" in html


def test_audit_package_download_is_get_only(package_html):
    assert 'method: "GET"' in package_html
    assert "application/zip" in package_html
    for mutation in (
        "api.post(",
        "api.patch(",
        "api.del(",
        'api.request("POST"',
        'method: "POST"',
    ):
        assert mutation not in package_html, f"package download must not use {mutation}"


def test_audit_package_blob_download_path(html):
    assert "response.blob()" in html
    assert "URL.createObjectURL(blob)" in html
    assert "anchor.download = filename" in html
    assert "anchor.click()" in html
    assert "URL.revokeObjectURL(url)" in html
    assert "Content-Disposition" in html
    assert "function auditPackageFileName" in html
    assert "filename\\*" in html
    assert "audit-package-" in html  # sensible fallback filename


def test_audit_package_reads_contract_headers(html):
    for header in (
        "X-Sugar-Package-Files",
        "X-Sugar-Package-Sha256",
        "X-Sugar-Package-Signed",
        "Content-Disposition",
    ):
        assert header in html, f"missing header {header}"
    assert "response.headers.get(" in html


def test_audit_package_sha256_and_signed_handling(html):
    assert "async function auditSha256Hex" in html
    assert "crypto.subtle.digest" in html
    assert '"SHA-256"' in html
    assert 'byte.toString(16).padStart(2, "0")' in html
    assert "function auditPackageSigned" in html
    assert "unsigned" in html
    assert "HMAC-SHA256 signed" in html
    assert "hash-only (unsigned)" in html
    assert "function auditFormatBytes" in html


def test_audit_package_status_and_error_states(html):
    assert "Building audit package…" in html
    assert "Audit package unavailable on this server" in html
    assert "Audit package failed: " in html
    assert "button.disabled = state.auditPackageBuilding" in html
    assert 'className = "audit-package-status error"' in html


def test_audit_package_warns_sensitive_and_offline_verify(html):
    assert "Sensitive metadata" in html
    assert "sugar audit verify-package &lt;dir&gt;" in html
    assert "sugar audit verify-package" in html


def test_audit_export_panel_exact_commands_and_claims(html):
    assert (
        "sugar audit package --out audit-package --project sugar --since 2026-11-01"
        in html
    )
    assert "sugar audit verify-package audit-package" in html
    assert "SUGAR_AUDIT_HMAC_KEY=... sugar audit package --out audit-package" in html
    # Existing export/AIBOM commands remain available.
    assert "sugar audit export --out audit/2026-11.jsonl" in html
    assert "sugar audit verify audit/2026-11.jsonl audit/2026-11.manifest.json" in html
    assert "sugar aibom --project sugar --since 2026-11-01 --json" in html
    # What the package proves / does not prove.
    assert (
        "A downloaded package's SHA-256 (and optional HMAC signature) "
        "matches its package manifest"
    ) in html
    assert "That a package is complete outside the selected project/date scope" in html
    assert "The download above uses the same package builder" in html


def test_audit_package_keeps_existing_audit_view(html):
    for element_id in (
        "audit-pane",
        "audit-project",
        "audit-since",
        "aibom-summary",
        "aibom-rows",
        "aibom-limitations",
        "audit-coverage-levels",
        "audit-coverage-rows",
        "audit-claims",
        "audit-commands",
    ):
        assert f'id="{element_id}"' in html
    # Observed-AIBOM copy still present.
    assert "observed runtime inventory" in html
    assert "not a complete or standards-certified AIBOM" in html


def test_audit_package_no_regression_or_external_dependencies(html):
    for view in ("overview", "tasks", "activity", "traces", "audit"):
        assert f'data-view="{view}"' in html
    assert '"/api/events"' in html
    assert "http://" not in html
    assert "https://" not in html
    assert "<script src" not in html
    assert "<link " not in html


def test_audit_package_endpoint_contract_when_available(spine_client):
    response = spine_client.get("/api/audit/package")
    if response.status_code in (404, 405, 503):
        pytest.skip(
            "GET /api/audit/package lands with the parallel audit-package backend"
        )
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "zip" in content_type or "octet-stream" in content_type
    assert response.content
    for header in (
        "X-Sugar-Package-Files",
        "X-Sugar-Package-Sha256",
        "X-Sugar-Package-Signed",
    ):
        assert header in response.headers
    assert response.headers["X-Sugar-Package-Files"].isdigit()
    assert len(response.headers["X-Sugar-Package-Sha256"]) == 64
