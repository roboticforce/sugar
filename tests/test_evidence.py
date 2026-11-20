"""Tests for the evidence read API and query helpers (Phase 1c)."""

import asyncio

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app
from sugar.dashboard.security import BoardToken, TokenRegistry
from sugar.evidence.query import (
    EvidenceQueryError,
    build_summary,
    event_status,
    query_evidence,
)


def make_app(tmp_path, **kwargs):
    return create_app(
        str(tmp_path / "sugar.db"),
        events_db=str(tmp_path / "events.db"),
        ingest_socket="",
        **kwargs,
    )


def post_evidence(
    client,
    *,
    kind="verification.check_result",
    status="passed",
    task_id="t_1",
    harness="claude-code",
    ts="2026-01-01T10:00:00Z",
    payload=None,
):
    body = {
        "kind": kind,
        "harness": harness,
        "task_id": task_id,
        "ts": ts,
        "status": status,
        "payload": {"status": status, **(payload or {})},
    }
    response = client.post("/api/evidence", json=body)
    assert response.status_code == 201, response.text
    return response.json()["event"]


@pytest.fixture
def client(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as test_client:
        yield test_client


class TestEvidenceEndpoint:
    def test_empty_response_shape(self, client):
        response = client.get("/api/evidence")
        assert response.status_code == 200
        assert response.json() == {
            "events": [],
            "last_id": 0,
            "summary": {"total": 0, "by_status": {}, "by_kind": {}},
        }

    def test_lists_evidence_oldest_to_newest_with_summary(self, client):
        first = post_evidence(
            client,
            kind="verification.check_result",
            status="passed",
            payload={"check": "pytest"},
            ts="2026-01-01T10:00:00Z",
        )
        second = post_evidence(
            client,
            kind="policy.decision",
            status="failed",
            ts="2026-01-01T10:01:00Z",
        )
        third = post_evidence(
            client,
            kind="commit.linked",
            status="blocked",
            ts="2026-01-01T10:02:00Z",
        )
        response = client.get("/api/evidence")
        assert response.status_code == 200
        data = response.json()
        assert [event["id"] for event in data["events"]] == [
            first["id"],
            second["id"],
            third["id"],
        ]
        assert data["last_id"] == third["id"]
        assert data["summary"]["total"] == 3
        assert data["summary"]["by_status"] == {
            "blocked": 1,
            "failed": 1,
            "passed": 1,
        }
        assert data["summary"]["by_kind"] == {
            "commit.linked": 1,
            "policy.decision": 1,
            "verification.check_result": 1,
        }

    def test_payload_and_status_fields(self, client):
        event = post_evidence(
            client,
            status="passed",
            payload={"check": "pytest", "command": "pytest -q", "commit": "abc"},
        )
        data = client.get("/api/evidence").json()
        stored = data["events"][0]
        assert stored["kind"] == "verification.check_result"
        assert stored["harness"] == "claude-code"
        assert stored["task_id"] == "t_1"
        assert stored["payload"]["status"] == "passed"
        assert stored["payload"]["payload"]["check"] == "pytest"
        assert stored["payload"]["payload"]["command"] == "pytest -q"
        assert stored["id"] == event["id"]

    def test_excludes_non_evidence_events(self, client):
        post_evidence(client)
        response = client.post(
            "/api/events",
            json={
                "kind": "tool.completed",
                "actor": "claude-code",
                "payload": {"tool_name": "Bash"},
            },
        )
        assert response.status_code == 201
        data = client.get("/api/evidence").json()
        assert len(data["events"]) == 1
        assert data["events"][0]["kind"] == "verification.check_result"

    def test_filters_by_kind_task_status_and_time(self, client):
        post_evidence(
            client,
            kind="verification.check_result",
            status="passed",
            task_id="t_1",
            ts="2026-01-01T10:00:00Z",
        )
        post_evidence(
            client,
            kind="verification.check_result",
            status="failed",
            task_id="t_1",
            ts="2026-01-02T10:00:00Z",
        )
        post_evidence(
            client,
            kind="policy.decision",
            status="passed",
            task_id="t_2",
            ts="2026-01-03T10:00:00Z",
        )
        assert (
            len(client.get("/api/evidence?kind=policy.decision").json()["events"]) == 1
        )
        task_data = client.get("/api/evidence?task_id=t_1").json()
        assert task_data["summary"]["total"] == 2
        status_data = client.get("/api/evidence?status=passed").json()
        assert status_data["summary"]["total"] == 2
        assert status_data["summary"]["total"] == 2
        assert all(event_status(event) == "passed" for event in status_data["events"])
        since = client.get("/api/evidence?since=2026-01-02T00:00:00Z").json()
        assert since["summary"]["total"] == 2
        until = client.get("/api/evidence?until=2026-01-02T00:00:00Z").json()
        assert until["summary"]["total"] == 1
        bounded = client.get(
            "/api/evidence?since=2026-01-01T10:00:00Z&until=2026-01-02T10:00:00Z"
        ).json()
        assert bounded["summary"]["total"] == 2

    def test_pagination_keeps_full_summary(self, client):
        for index in range(5):
            post_evidence(
                client,
                kind="verification.check_result",
                status="passed" if index < 3 else "failed",
                ts=f"2026-01-01T10:0{index}:00Z",
            )
        page = client.get("/api/evidence?limit=2&offset=2").json()
        assert len(page["events"]) == 2
        assert page["summary"]["total"] == 5
        assert page["summary"]["by_status"] == {"failed": 2, "passed": 3}
        assert page["last_id"] == page["events"][-1]["id"]
        beyond = client.get("/api/evidence?limit=2&offset=50").json()
        assert beyond["events"] == []
        assert beyond["last_id"] == 0
        assert beyond["summary"]["total"] == 5

    def test_summary_status_falls_back_to_envelope_payload_status(self, client):
        response = client.post(
            "/api/evidence",
            json={
                "kind": "verification.review",
                "harness": "sugar",
                "status": "approved",
                "payload": {},
                "ts": "2026-01-01T10:00:00Z",
            },
        )
        assert response.status_code == 201
        data = client.get("/api/evidence").json()
        assert data["summary"]["by_status"] == {"approved": 1}
        assert data["events"][0]["payload"]["status"] == "approved"

    @pytest.mark.parametrize(
        "query",
        [
            "kind=bogus",
            "status=bogus",
            "limit=0",
            "limit=501",
            "limit=abc",
            "offset=-1",
            "offset=abc",
            "since=not-a-time",
            "until=2026-13-99",
        ],
    )
    def test_invalid_queries_are_400(self, client, query):
        response = client.get(f"/api/evidence?{query}")
        assert response.status_code == 400
        assert response.json()["error"] == "validation_error"

    def test_accepts_limit_bounds(self, client):
        assert client.get("/api/evidence?limit=1").status_code == 200
        assert client.get("/api/evidence?limit=500").status_code == 200

    def test_no_spine_is_503(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), ingest_socket="")
        with TestClient(app) as test_client:
            response = test_client.get("/api/evidence")
            assert response.status_code == 503
            assert response.json()["error"] == "spine_unavailable"

    def test_token_mode_requires_read_scope(self, tmp_path):
        tokens = TokenRegistry(
            [
                BoardToken("reader", "read", "read-token"),
                BoardToken("writer", "write", "write-token"),
            ]
        )
        app = make_app(tmp_path, tokens=tokens)
        with TestClient(app) as test_client:
            assert test_client.get("/api/evidence").status_code == 401
            read = test_client.get(
                "/api/evidence", headers={"Authorization": "Bearer read-token"}
            )
            assert read.status_code == 200
            # Seed via the write-scoped token, then read the result.
            created = test_client.post(
                "/api/evidence",
                json={
                    "kind": "verification.test_run",
                    "status": "passed",
                    "payload": {},
                },
                headers={"Authorization": "Bearer write-token"},
            )
            assert created.status_code == 201
            read = test_client.get(
                "/api/evidence", headers={"Authorization": "Bearer read-token"}
            )
            assert read.json()["summary"]["total"] == 1

    def test_post_evidence_still_works(self, client):
        event = post_evidence(client)
        assert event["kind"] == "verification.check_result"
        assert event["payload"]["status"] == "passed"


class TestEvidenceQueryHelpers:
    def test_build_summary_and_status_fallback(self):
        events = [
            {
                "kind": "verification.check_result",
                "payload": {"payload": {"status": "passed"}},
            },
            {"kind": "verification.check_result", "payload": {"status": "failed"}},
            {"kind": "policy.decision", "payload": {}},
        ]
        assert event_status(events[0]) == "passed"
        assert event_status(events[1]) == "failed"
        assert event_status(events[2]) is None
        summary = build_summary(events)
        assert summary == {
            "total": 3,
            "by_status": {"failed": 1, "passed": 1},
            "by_kind": {
                "policy.decision": 1,
                "verification.check_result": 2,
            },
        }

    def test_query_helper_validation_and_missing_db(self, tmp_path):
        async def go():
            with pytest.raises(EvidenceQueryError):
                await query_evidence(tmp_path / "missing.db", limit=0)
            with pytest.raises(EvidenceQueryError):
                await query_evidence(tmp_path / "missing.db", kind="tool.started")
            return await query_evidence(tmp_path / "missing.db")

        result = asyncio.run(go())
        assert result == {
            "events": [],
            "last_id": 0,
            "summary": {"total": 0, "by_status": {}, "by_kind": {}},
        }
        assert not (tmp_path / "missing.db").exists()
