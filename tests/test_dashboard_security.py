"""Security guardrail tests for the local dashboard (docs/local-dashboard.md §7).

Covers the cross-origin write gate (a web page must not be able to POST to the
loopback API), request size limits, response headers, and spine file permissions.
"""

import os
import stat

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app
from sugar.dashboard.security import MAX_BODY_BYTES, origin_allowed


class TestOriginPolicy:
    @pytest.mark.parametrize(
        "origin,allowed",
        [
            (None, True),
            ("", True),
            ("http://127.0.0.1:7331", True),
            ("http://localhost:7331", True),
            ("http://[::1]:7331", True),
            ("https://127.0.0.1", True),
            ("https://evil.example", False),
            ("http://127.0.0.1.evil.example", False),
            ("null", False),
            ("not a url", False),
        ],
    )
    def test_origin_allowed(self, origin, allowed):
        assert origin_allowed(origin) is allowed

    def test_cross_origin_write_is_refused(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), events_db=str(tmp_path / "e.db"))
        with TestClient(app) as client:
            response = client.post(
                "/api/tasks",
                json={"title": "csrf attempt"},
                headers={"Origin": "https://evil.example"},
            )
            assert response.status_code == 403
            assert response.json()["error"] == "forbidden_origin"
            # nothing was created
            assert client.get("/api/tasks").json()["count"] == 0

    def test_cross_origin_ingest_is_refused(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), events_db=str(tmp_path / "e.db"))
        with TestClient(app) as client:
            response = client.post(
                "/api/events",
                json={"kind": "tool.invoked"},
                headers={"Origin": "https://evil.example"},
            )
            assert response.status_code == 403
            assert client.get("/api/activity").json()["events"] == []

    def test_loopback_origin_is_allowed(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), events_db=str(tmp_path / "e.db"))
        with TestClient(app) as client:
            response = client.post(
                "/api/tasks",
                json={"title": "same-origin task"},
                headers={"Origin": "http://127.0.0.1:7331"},
            )
            assert response.status_code == 201, response.text

    def test_native_client_without_origin_is_allowed(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), events_db=str(tmp_path / "e.db"))
        with TestClient(app) as client:
            assert (
                client.post("/api/tasks", json={"title": "curl task"}).status_code
                == 201
            )

    def test_cross_origin_read_is_allowed(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), events_db=str(tmp_path / "e.db"))
        with TestClient(app) as client:
            response = client.get(
                "/api/tasks", headers={"Origin": "https://evil.example"}
            )
            assert response.status_code == 200


class TestRequestLimits:
    def test_oversized_body_is_refused(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), events_db=str(tmp_path / "e.db"))
        payload = {"title": "big", "padding": "a" * (MAX_BODY_BYTES + 10)}
        with TestClient(app) as client:
            response = client.post("/api/tasks", json=payload)
            assert response.status_code == 413
            assert response.json()["error"] == "payload_too_large"

    def test_reasonable_body_is_accepted(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), events_db=str(tmp_path / "e.db"))
        payload = {"title": "normal", "description": "x" * 10_000}
        with TestClient(app) as client:
            assert client.post("/api/tasks", json=payload).status_code == 201


class TestHeaders:
    def test_security_headers_present(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"))
        with TestClient(app) as client:
            response = client.get("/api/health")
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["x-frame-options"] == "DENY"
            assert response.headers["referrer-policy"] == "no-referrer"

    def test_headers_on_errors(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"))
        with TestClient(app) as client:
            response = client.get("/api/tasks/does-not-exist")
            assert response.status_code == 404
            assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")
class TestSpinePermissions:
    def test_events_db_is_private(self, tmp_path):
        db_path = tmp_path / "events.db"
        app = create_app(str(tmp_path / "sugar.db"), events_db=str(db_path))
        with TestClient(app) as client:
            assert client.post("/api/events", json={"kind": "x"}).status_code == 201
        mode = stat.S_IMODE(db_path.stat().st_mode)
        assert mode == 0o600, oct(mode)
