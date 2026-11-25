"""Tests for the audit package download API (task-50)."""

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from sugar.audit.package import verify_audit_package
from sugar.dashboard.app import create_app
from sugar.dashboard.security import BoardToken, TokenRegistry

EXPECTED_FILES = {
    "README.md",
    "aibom.json",
    "claims.json",
    "coverage.json",
    "events.jsonl",
    "events.manifest.json",
    "evidence.json",
    "package.manifest.json",
    "tasks.json",
    "traces.json",
}


def make_app(tmp_path, **kwargs):
    return create_app(
        str(tmp_path / "sugar.db"),
        events_db=str(tmp_path / "events.db"),
        ingest_socket="",
        **kwargs,
    )


def post_event(client, body):
    response = client.post("/api/events", json=body)
    assert response.status_code == 201, response.text
    return response.json()["event"]


def session_events(client, session_id, project, ts):
    post_event(
        client,
        {
            "kind": "session.started",
            "actor": "dsh",
            "harness": "dsh",
            "session_id": session_id,
            "project": project,
            "ts": ts,
            "payload": {"payload": {"title": session_id}},
        },
    )
    post_event(
        client,
        {
            "kind": "session.ended",
            "actor": "dsh",
            "harness": "dsh",
            "session_id": session_id,
            "project": project,
            "ts": ts,
            "payload": {},
        },
    )


def extract_package(response, tmp_path):
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    destination = tmp_path / "extracted"
    destination.mkdir(exist_ok=True)
    archive.extractall(destination)
    return archive, destination


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SUGAR_ADAPTERS_FILE", str(tmp_path / "adapters.json"))
    monkeypatch.setenv("SUGAR_MODEL_PRICES", str(tmp_path / "prices.json"))
    app = make_app(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


class TestAuditPackageDownload:
    def test_download_zip_and_verify(self, client, tmp_path):
        session_events(client, "s-alpha", "alpha", "2026-11-01T10:00:00Z")
        task = client.post("/api/tasks", json={"title": "Audited task"}).json()["task"]
        assert (
            client.patch(
                f"/api/tasks/{task['id']}", json={"status": "completed"}
            ).status_code
            == 200
        )
        response = client.get("/api/audit/package")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/zip")
        filename = response.headers["content-disposition"]
        assert filename.startswith('attachment; filename="sugar-audit-all-')
        assert re.match(r'attachment; filename="sugar-audit-all-\d{8}\.zip"', filename)
        archive, destination = extract_package(response, tmp_path)
        assert set(archive.namelist()) == EXPECTED_FILES
        assert int(response.headers["x-sugar-package-files"]) == len(archive.namelist())
        assert (
            response.headers["x-sugar-package-sha256"]
            == hashlib.sha256(response.content).hexdigest()
        )
        assert response.headers["x-sugar-package-signed"] == "false"
        verify = verify_audit_package(destination)
        assert verify.ok, verify.errors

    def test_scope_parameters_are_applied(self, client, tmp_path):
        session_events(client, "s-alpha", "alpha", "2026-11-01T10:00:00Z")
        session_events(client, "s-beta", "beta", "2026-11-02T10:00:00Z")
        response = client.get(
            "/api/audit/package",
            params={
                "project": "alpha",
                "since": "2026-11-01T00:00:00Z",
                "until": "2026-11-01T23:59:59Z",
            },
        )
        assert response.status_code == 200, response.text
        assert "sugar-audit-alpha-" in response.headers["content-disposition"]
        archive, destination = extract_package(response, tmp_path)
        events = [
            json.loads(line)
            for line in (destination / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert events
        assert all(event["project"] == "alpha" for event in events)
        assert all(
            "2026-11-01T00:00:00Z" <= event["ts"] <= "2026-11-01T23:59:59Z"
            for event in events
        )
        manifest = json.loads(
            (destination / "package.manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["scope"]["project"] == "alpha"
        assert manifest["scope"]["since"] == "2026-11-01T00:00:00Z"
        assert verify_audit_package(destination).ok

    def test_unsafe_project_is_sanitized_in_filename(self, client):
        response = client.get(
            "/api/audit/package", params={"project": "weird/project name"}
        )
        assert response.status_code == 200
        assert (
            'filename="sugar-audit-weird-project-name-'
            in response.headers["content-disposition"]
        )

    @pytest.mark.parametrize(
        "params",
        [
            {"since": "not-a-date"},
            {"until": "not-a-date"},
            {"since": "2026-12-01T00:00:00Z", "until": "2026-11-01T00:00:00Z"},
            {"project": "x" * 201},
        ],
    )
    def test_invalid_scope_is_400(self, client, params):
        response = client.get("/api/audit/package", params=params)
        assert response.status_code == 400
        assert response.json()["error"] == "validation_error"

    def test_no_spine_is_503(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), ingest_socket="")
        with TestClient(app) as test_client:
            response = test_client.get("/api/audit/package")
            assert response.status_code == 503
            assert response.json()["error"] == "spine_unavailable"

    def test_read_token_scope(self, tmp_path):
        tokens = TokenRegistry(
            [
                BoardToken("reader", "read", "read-token"),
                BoardToken("writer", "write", "write-token"),
            ]
        )
        app = make_app(tmp_path, tokens=tokens)
        with TestClient(app) as test_client:
            assert test_client.get("/api/audit/package").status_code == 401
            allowed = test_client.get(
                "/api/audit/package", headers={"Authorization": "Bearer read-token"}
            )
            assert allowed.status_code == 200
            assert allowed.headers["content-type"].startswith("application/zip")

    def test_signed_package_when_env_is_set(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("SUGAR_AUDIT_SIGN_KEY", "download-signing-key")
        session_events(client, "s-signed", "alpha", "2026-11-01T10:00:00Z")
        response = client.get("/api/audit/package")
        assert response.status_code == 200, response.text
        assert response.headers["x-sugar-package-signed"] == "true"
        _archive, destination = extract_package(response, tmp_path)
        verify = verify_audit_package(destination, sign_key_env="SUGAR_AUDIT_SIGN_KEY")
        assert verify.ok, verify.errors
        assert verify.signed is True
        monkeypatch.setenv("SUGAR_AUDIT_SIGN_KEY", "wrong-key")
        assert (
            verify_audit_package(destination, sign_key_env="SUGAR_AUDIT_SIGN_KEY").ok
            is False
        )

    def test_package_failure_is_generic_500(self, client, monkeypatch):
        from sugar.dashboard import audit_package_api

        async def fail(**kwargs):
            raise RuntimeError("/secret/home/path token=abc")

        monkeypatch.setattr(audit_package_api, "build_audit_download", fail)
        response = client.get("/api/audit/package")
        assert response.status_code == 500
        assert response.json() == {
            "error": "package_error",
            "detail": "could not build audit package",
        }
        assert "/secret" not in response.text
        assert "token=abc" not in response.text

    def test_temporary_files_are_cleaned_on_failure(self, client, monkeypatch):
        from sugar.dashboard import audit_package_api

        captured = {}

        async def fail_build(out_dir, **kwargs):
            captured["out_dir"] = Path(out_dir)
            raise RuntimeError("boom")

        monkeypatch.setattr(audit_package_api, "build_audit_package", fail_build)
        assert client.get("/api/audit/package").status_code == 500
        assert captured["out_dir"]
        assert not captured["out_dir"].exists()
        assert not captured["out_dir"].parent.exists()
