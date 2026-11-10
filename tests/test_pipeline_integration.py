"""End-to-end tests for the telemetry pipeline.

The individual modules have unit tests; this test covers the wiring between them:
harness events posted to ``POST /api/events`` must immediately be readable from
``/api/activity`` and be reflected by the ``/api/usage`` and ``/api/trends``
aggregations (the same spine, no second store).
"""

from __future__ import annotations

from starlette.testclient import TestClient

from sugar.dashboard.app import create_app


def _event(kind: str, **payload) -> dict:
    body = {
        "kind": kind,
        "actor": "claude-code",
        "actor_kind": "agent",
        "harness": "claude-code",
        "task_id": "task-1",
        "project": "sugar",
    }
    if payload:
        body["payload"] = payload
    return body


def test_posted_events_flow_to_activity_usage_and_trends(tmp_path):
    app = create_app(
        str(tmp_path / "sugar.db"),
        events_db=str(tmp_path / "events.db"),
    )
    with TestClient(app) as client:
        for event in (
            _event("task.created", title="Integration task"),
            _event("task.claimed"),
            _event("tool.invoked", message="running pytest tests/test_api.py"),
            _event(
                "usage.reported",
                tokens_in=1000,
                tokens_out=200,
                cost_usd=0.25,
                model="demo-model",
            ),
            _event("task.completed"),
        ):
            response = client.post("/api/events", json=event)
            assert response.status_code == 201, response.text

        # 1. The activity feed reads the same spine.
        activity = client.get("/api/activity?limit=50").json()
        kinds = [event["kind"] for event in activity["events"]]
        assert {"task.created", "usage.reported", "task.completed"} <= set(kinds)

        # 2. Usage aggregation sees the harness-reported numbers.
        usage = client.get("/api/usage?group_by=actor").json()
        # usage counts usage-bearing events (the shared spine has all five).
        assert usage["totals"]["events"] == 1
        assert usage["totals"]["tokens_in"] == 1000
        assert usage["totals"]["tokens_out"] == 200
        assert usage["totals"]["cost_usd"] == 0.25
        assert [group["key"] for group in usage["groups"]] == ["claude-code"]

        # 3. Trends buckets the same events by actor.
        trends = client.get("/api/trends?group_by=actor&bucket=day").json()
        assert trends["totals"]["tasks_created"] == 1
        assert trends["totals"]["tasks_claimed"] == 1
        assert trends["totals"]["tasks_completed"] == 1
        assert trends["totals"]["cost_usd"] == 0.25
        assert trends["event_count"] >= 5
        series = {series["key"]: series for series in trends["series"]}
        assert "claude-code" in series
        assert series["claude-code"]["totals"]["tasks_completed"] == 1
        assert series["claude-code"]["avg_cycle_time_s"] is not None


def test_ingest_is_rejected_when_the_spine_is_disabled(tmp_path):
    app = create_app(str(tmp_path / "sugar.db"), events_db="")
    with TestClient(app) as client:
        response = client.post("/api/events", json=_event("run.started"))
        assert response.status_code == 503
        assert response.json()["error"] == "spine_unavailable"
