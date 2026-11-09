"""Tests for time-bucketed trends (``docs/trends.md``).

Covers the pure computation (bucketing, grouping, cycle-time pairing, tolerant
usage), the bounded spine reader, the HTTP API and the ``sugar trends`` CLI.
Every DB is a throwaway file under ``tmp_path``.
"""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app
from sugar.dashboard.security import BoardToken, TokenRegistry
from sugar.dashboard.spine import EventSpine
from sugar.dashboard.trends import (
    DEFAULT_EVENT_LIMIT,
    MAX_EVENT_LIMIT,
    TrendsQueryError,
    bucket_start,
    compute_trends,
    format_trends_table,
    normalize_limit,
    normalize_timestamp,
    read_trend_events,
    trends_report,
    validate_bucket,
    validate_group_by,
)
from sugar.main import cli

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SINCE = "2026-11-02T00:00:00Z"
UNTIL = "2026-11-04T00:00:00Z"

SAMPLE_EVENTS = [
    {
        "ts": "2026-11-02T10:00:00Z",
        "kind": "task.created",
        "actor": "alice",
        "harness": "dsh",
        "project": "p1",
        "task_id": "t1",
    },
    {
        "ts": "2026-11-02T10:05:00Z",
        "kind": "task.claimed",
        "actor": "alice",
        "harness": "dsh",
        "project": "p1",
        "task_id": "t1",
    },
    {
        "ts": "2026-11-02T10:07:00Z",
        "kind": "task.completed",
        "actor": "alice",
        "harness": "dsh",
        "project": "p1",
        "task_id": "t1",
    },
    {
        "ts": "2026-11-03T11:00:00Z",
        "kind": "task.created",
        "actor": "bob",
        "task_id": "t2",
    },
    {
        "ts": "2026-11-03T11:01:00Z",
        "kind": "task.claimed",
        "actor": "bob",
        "task_id": "t2",
    },
    {
        "ts": "2026-11-03T11:02:00Z",
        "kind": "task.failed",
        "actor": "bob",
        "task_id": "t2",
    },
    {
        "ts": "2026-11-03T12:00:00Z",
        "kind": "usage.reported",
        "actor": "alice",
        "harness": "dsh",
        "project": "p1",
        "payload": {"tokens_in": 100, "tokens_out": 50, "cost_usd": 0.01},
    },
    {
        "ts": "2026-11-04T00:00:00Z",
        "kind": "tool.invoked",
        "actor": "bob",
        "payload": {"message": "boundary"},
    },
]


def run(coro):
    return asyncio.run(coro)


def seed_spine(path: Path, events) -> Path:
    async def go():
        spine = EventSpine(str(path), retention_days=0)
        await spine.start()
        for event in events:
            await spine.append(dict(event))
        await spine.close()

    run(go())
    return path


@pytest.fixture
def events_db(tmp_path):
    return seed_spine(tmp_path / "events.db", SAMPLE_EVENTS)


def make_event(ts, kind, **fields):
    event = {"ts": ts, "kind": kind, "payload": fields.pop("payload", {})}
    event.update(fields)
    return event


def cli_config(tmp_path: Path) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(
        "sugar:\n"
        "  storage:\n"
        f"    database: \"{tmp_path / 'sugar.db'}\"\n"
        "  logging:\n"
        f"    file: \"{tmp_path / 'sugar.log'}\"\n",
        encoding="utf-8",
    )
    return config


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------


class TestBucketing:
    @pytest.mark.parametrize(
        "ts,bucket,expected",
        [
            ("2026-11-09T14:37:12Z", "hour", "2026-11-09T14:00:00Z"),
            ("2026-11-09T14:00:00Z", "hour", "2026-11-09T14:00:00Z"),
            ("2026-11-09T23:59:59Z", "day", "2026-11-09T00:00:00Z"),
            ("2026-11-11T10:00:00Z", "week", "2026-11-09T00:00:00Z"),
            ("2026-11-15T23:00:00Z", "week", "2026-11-09T00:00:00Z"),
            ("2026-11-09T00:00:00Z", "week", "2026-11-09T00:00:00Z"),
            ("2026-11-09T05:00:00+05:00", "day", "2026-11-09T00:00:00Z"),
            ("2026-11-09", "day", "2026-11-09T00:00:00Z"),
        ],
    )
    def test_bucket_start(self, ts, bucket, expected):
        assert bucket_start(ts, bucket) == expected

    def test_week_starts_on_monday_utc(self):
        # 2026-11-09 is a Monday; 2026-11-08 (Sunday) belongs to the prior week.
        assert bucket_start("2026-11-09T00:00:00Z", "week") == "2026-11-09T00:00:00Z"
        assert bucket_start("2026-11-08T23:59:59Z", "week") == "2026-11-02T00:00:00Z"

    def test_bucket_and_timestamp_validation(self):
        # Blank/missing values take the default; unknown values are rejected.
        assert validate_bucket(None) == "day"
        assert validate_bucket("") == "day"
        assert validate_bucket("DAY") == "day"
        with pytest.raises(TrendsQueryError):
            validate_bucket("minute")
        with pytest.raises(TrendsQueryError):
            bucket_start("not-a-timestamp", "day")
        with pytest.raises(TrendsQueryError):
            bucket_start("2026-11-09T00:00:00Z", "year")

    def test_validate_group_by(self):
        assert validate_group_by(None) == "actor"
        assert validate_group_by("") == "actor"
        assert validate_group_by("Project") == "project"
        with pytest.raises(TrendsQueryError):
            validate_group_by("task")

    def test_normalize_timestamp(self):
        assert normalize_timestamp("2026-11-09") == "2026-11-09T00:00:00Z"
        assert (
            normalize_timestamp("2026-11-09T14:00:00-05:00") == "2026-11-09T19:00:00Z"
        )
        with pytest.raises(TrendsQueryError):
            normalize_timestamp("yesterday")


# ---------------------------------------------------------------------------
# Pure computation
# ---------------------------------------------------------------------------


class TestComputeTrends:
    def test_zero_filled_buckets_for_empty_range(self):
        result = compute_trends([], SINCE, UNTIL, "day", "actor")
        assert result["buckets"] == [
            "2026-11-02T00:00:00Z",
            "2026-11-03T00:00:00Z",
            "2026-11-04T00:00:00Z",
        ]
        assert result["series"] == []
        assert result["event_count"] == 0
        assert result["truncated"] is False
        assert result["since"] == SINCE and result["until"] == UNTIL
        assert result["bucket"] == "day" and result["group_by"] == "actor"
        assert result["totals"] == {
            "tasks_created": 0,
            "tasks_claimed": 0,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "events": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
        }

    def test_points_are_complete_and_zero_filled(self):
        events = [make_event("2026-11-03T10:00:00Z", "tool.invoked", actor="a")]
        result = compute_trends(events, SINCE, UNTIL, "day", "actor")
        series = result["series"][0]
        assert [point["bucket"] for point in series["points"]] == result["buckets"]
        assert len(series["points"]) == 3
        assert set(series["points"][0]) == {
            "bucket",
            "tasks_created",
            "tasks_claimed",
            "tasks_completed",
            "tasks_failed",
            "events",
            "tokens_in",
            "tokens_out",
            "cost_usd",
        }
        assert series["points"][0]["events"] == 0
        assert series["points"][1]["events"] == 1
        assert result["event_count"] == 1

    def test_group_by_actor_harness_project_and_unknown(self):
        events = [
            make_event(
                "2026-11-02T10:00:00Z",
                "task.created",
                actor="a",
                harness="dsh",
                project="p1",
            ),
            make_event("2026-11-02T11:00:00Z", "task.created", actor=None),
        ]
        by_actor = compute_trends(events, SINCE, UNTIL, "day", "actor")
        by_harness = compute_trends(events, SINCE, UNTIL, "day", "harness")
        by_project = compute_trends(events, SINCE, UNTIL, "day", "project")
        assert [s["key"] for s in by_actor["series"]] == ["a", "unknown"]
        assert [s["key"] for s in by_harness["series"]] == ["dsh", "unknown"]
        assert [s["key"] for s in by_project["series"]] == ["p1", "unknown"]
        for result in (by_actor, by_harness, by_project):
            assert result["totals"]["tasks_created"] == 2
            assert result["event_count"] == 2

    def test_task_metrics_and_event_volume(self):
        events = [
            make_event("2026-11-02T10:00:00Z", "task.created", actor="a"),
            make_event("2026-11-02T10:01:00Z", "task.claimed", actor="a"),
            make_event("2026-11-02T10:02:00Z", "task.completed", actor="a"),
            make_event("2026-11-02T10:03:00Z", "task.failed", actor="a"),
            make_event("2026-11-02T10:04:00Z", "task.held", actor="a"),
            make_event("2026-11-02T10:05:00Z", "tool.invoked", actor="a"),
        ]
        result = compute_trends(events, SINCE, UNTIL, "day", "actor")
        totals = result["totals"]
        assert totals["tasks_created"] == 1
        assert totals["tasks_claimed"] == 1
        assert totals["tasks_completed"] == 1
        assert totals["tasks_failed"] == 1
        assert totals["events"] == 6  # held/invoked count only in events
        assert result["event_count"] == 6

    def test_usage_accumulation_is_tolerant(self):
        events = [
            make_event(
                "2026-11-02T10:00:00Z",
                "usage.reported",
                actor="a",
                payload={
                    "tokens_in": 100,
                    "tokens_out": "50",
                    "cost_usd": "0.0123",
                },
            ),
            make_event(
                "2026-11-02T11:00:00Z",
                "run.ended",
                actor="a",
                payload={"tokens_in": -10, "tokens_out": None, "cost_usd": "abc"},
            ),
            make_event(
                "2026-11-02T12:00:00Z",
                "usage.reported",
                actor="a",
                payload={"tokens_in": float("nan"), "cost_usd": 1.5},
            ),
        ]
        result = compute_trends(events, SINCE, UNTIL, "day", "actor")
        totals = result["totals"]
        assert totals["tokens_in"] == 100  # negative and NaN ignored
        assert totals["tokens_out"] == 50
        assert totals["cost_usd"] == 1.5123
        assert totals["events"] == 3  # payload quality does not hide the event
        assert result["event_count"] == 3

    def test_cycle_time_pairing(self):
        events = [
            # Latest claim before completion wins (retry).
            make_event(
                "2026-11-02T10:00:00Z", "task.claimed", actor="worker", task_id="t1"
            ),
            make_event(
                "2026-11-02T10:01:00Z", "task.claimed", actor="worker", task_id="t1"
            ),
            make_event(
                "2026-11-02T10:03:00Z", "task.completed", actor="worker", task_id="t1"
            ),
            # Completed with no claim: ignored.
            make_event(
                "2026-11-02T10:05:00Z", "task.completed", actor="worker", task_id="t2"
            ),
            # Completion before the claim: ignored (no prior claim).
            make_event(
                "2026-11-02T10:10:00Z", "task.claimed", actor="worker", task_id="t3"
            ),
            make_event(
                "2026-11-02T10:07:00Z", "task.completed", actor="worker", task_id="t3"
            ),
            # Pair contributes to the completing event's group.
            make_event(
                "2026-11-02T10:20:00Z", "task.claimed", actor="worker", task_id="t4"
            ),
            make_event(
                "2026-11-02T10:25:00Z", "task.completed", actor="reporter", task_id="t4"
            ),
        ]
        result = compute_trends(events, SINCE, UNTIL, "day", "actor")
        series = {item["key"]: item for item in result["series"]}
        # t1: 10:01 -> 10:03 = 120s. t4: 10:20 -> 10:25 = 300s in "reporter".
        assert series["worker"]["avg_cycle_time_s"] == 120.0
        assert series["reporter"]["avg_cycle_time_s"] == 300.0

    def test_cycle_time_null_without_pairs(self):
        events = [
            make_event("2026-11-02T10:00:00Z", "task.completed", actor="a", task_id="t")
        ]
        result = compute_trends(events, SINCE, UNTIL, "day", "actor")
        assert result["series"][0]["avg_cycle_time_s"] is None

    def test_invalid_or_out_of_range_events_ignored(self):
        events = [
            make_event("not-a-timestamp", "task.created", actor="a"),
            make_event("2026-10-01T00:00:00Z", "task.created", actor="a"),
            make_event("2026-12-01T00:00:00Z", "task.created", actor="a"),
            make_event("2026-11-02T10:00:00Z", "task.created", actor="a"),
        ]
        result = compute_trends(events, SINCE, UNTIL, "day", "actor")
        assert result["event_count"] == 1
        assert result["totals"]["tasks_created"] == 1

    def test_series_sorted_by_key_and_totals_add_up(self):
        events = [
            make_event("2026-11-02T10:00:00Z", "task.created", actor="zoe"),
            make_event("2026-11-02T10:00:00Z", "task.created", actor="amy"),
        ]
        result = compute_trends(events, SINCE, UNTIL, "day", "actor")
        assert [s["key"] for s in result["series"]] == ["amy", "zoe"]
        assert sum(s["totals"]["tasks_created"] for s in result["series"]) == 2

    def test_week_buckets_align_monday(self):
        result = compute_trends(
            [],
            "2026-11-02T00:00:00Z",
            "2026-11-23T00:00:00Z",
            "week",
            "actor",
        )
        assert result["buckets"] == [
            "2026-11-02T00:00:00Z",
            "2026-11-09T00:00:00Z",
            "2026-11-16T00:00:00Z",
            "2026-11-23T00:00:00Z",
        ]

    def test_since_after_until_and_excessive_buckets_rejected(self):
        with pytest.raises(TrendsQueryError, match="since"):
            compute_trends([], UNTIL, SINCE, "day", "actor")
        with pytest.raises(TrendsQueryError, match="too large"):
            compute_trends([], "2020-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "hour")

    def test_invalid_bucket_and_group_rejected(self):
        with pytest.raises(TrendsQueryError):
            compute_trends([], SINCE, UNTIL, "minute", "actor")
        with pytest.raises(TrendsQueryError):
            compute_trends([], SINCE, UNTIL, "day", "task")


# ---------------------------------------------------------------------------
# Reader / report
# ---------------------------------------------------------------------------


class TestReport:
    def test_report_uses_seeded_spine(self, events_db):
        report = run(trends_report(events_db, since=SINCE, until=UNTIL))
        assert report["since"] == SINCE
        assert report["until"] == UNTIL
        assert report["bucket"] == "day"
        assert report["group_by"] == "actor"
        assert report["buckets"] == [
            "2026-11-02T00:00:00Z",
            "2026-11-03T00:00:00Z",
            "2026-11-04T00:00:00Z",
        ]
        assert report["event_count"] == 8
        assert report["truncated"] is False
        series = {item["key"]: item for item in report["series"]}
        assert set(series) == {"alice", "bob"}
        assert series["alice"]["totals"]["tasks_completed"] == 1
        assert series["alice"]["totals"]["tokens_in"] == 100
        assert series["alice"]["avg_cycle_time_s"] == 120.0
        assert series["bob"]["totals"]["tasks_failed"] == 1
        assert series["bob"]["avg_cycle_time_s"] is None
        assert report["totals"]["events"] == 8

    def test_report_since_until_filters(self, events_db):
        report = run(
            trends_report(
                events_db,
                since="2026-11-03T00:00:00Z",
                until="2026-11-03T23:59:59Z",
                bucket="day",
            )
        )
        assert report["event_count"] == 4  # created/claimed/failed + usage? see sample
        # 2026-11-03 sample: task.created (bob), claimed, failed, usage (alice)
        assert report["event_count"] == 4
        assert [s["key"] for s in report["series"]] == ["alice", "bob"]

    def test_report_limit_sets_truncated(self, events_db):
        report = run(trends_report(events_db, since=SINCE, until=UNTIL, limit=2))
        assert report["event_count"] == 2
        assert report["truncated"] is True
        # First two events by id: alice task.created + task.claimed
        assert report["totals"]["tasks_created"] == 1
        assert report["totals"]["tasks_claimed"] == 1
        assert report["totals"]["events"] == 2

    def test_report_missing_db_is_empty(self, tmp_path):
        report = run(trends_report(tmp_path / "missing.db", since=SINCE, until=UNTIL))
        assert report["event_count"] == 0
        assert report["series"] == []
        assert report["truncated"] is False
        assert len(report["buckets"]) == 3

    def test_report_default_window_is_seven_days(self, tmp_path):
        seed_spine(
            tmp_path / "events.db",
            [make_event("2026-11-08T10:00:00Z", "task.created", actor="a")],
        )
        report = run(
            trends_report(tmp_path / "events.db", until="2026-11-09T00:00:00Z")
        )
        assert report["since"] == "2026-11-02T00:00:00Z"
        assert report["until"] == "2026-11-09T00:00:00Z"
        assert report["bucket"] == "day"
        assert report["group_by"] == "actor"
        assert len(report["buckets"]) == 8

    def test_report_default_until_is_now(self, tmp_path):
        before = datetime.now(timezone.utc)
        report = run(trends_report(tmp_path / "missing.db"))
        until = datetime.fromisoformat(report["until"].replace("Z", "+00:00"))
        since = datetime.fromisoformat(report["since"].replace("Z", "+00:00"))
        assert abs((until - before).total_seconds()) < 60
        assert until - since == timedelta(days=7)

    def test_report_validation(self, tmp_path):
        with pytest.raises(TrendsQueryError):
            run(trends_report(tmp_path / "x.db", bucket="minute"))
        with pytest.raises(TrendsQueryError):
            run(trends_report(tmp_path / "x.db", group_by="task"))
        with pytest.raises(TrendsQueryError):
            run(trends_report(tmp_path / "x.db", since=UNTIL, until=SINCE))
        with pytest.raises(TrendsQueryError):
            run(trends_report(tmp_path / "x.db", limit=0))
        with pytest.raises(TrendsQueryError):
            run(trends_report(tmp_path / "x.db", limit="abc"))

    def test_normalize_limit(self):
        assert normalize_limit(None) == DEFAULT_EVENT_LIMIT
        assert normalize_limit("") == DEFAULT_EVENT_LIMIT
        assert normalize_limit(10) == 10
        assert normalize_limit(10**9) == MAX_EVENT_LIMIT
        for bad in (0, -1, True, "abc"):
            with pytest.raises(TrendsQueryError):
                normalize_limit(bad)

    def test_reader_decodes_payload_and_table(self, events_db):
        events, truncated = run(
            read_trend_events(events_db, since=SINCE, until=UNTIL, limit=100)
        )
        assert truncated is False
        assert len(events) == 8
        usage = [e for e in events if e["kind"] == "usage.reported"][0]
        assert usage["payload"]["tokens_in"] == 100
        assert usage["payload"]["tokens_out"] == 50

    def test_reader_missing_table_is_empty(self, tmp_path):
        import sqlite3

        path = tmp_path / "foreign.db"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE other (id INTEGER)")
        events, truncated = run(read_trend_events(path))
        assert (events, truncated) == ([], False)


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path, events_db):
    app = create_app(str(tmp_path / "sugar.db"), events_db=str(events_db))
    with TestClient(app) as client:
        yield client


class TestTrendsAPI:
    def test_shape_and_zero_filled_buckets(self, api_client):
        response = api_client.get(
            "/api/trends",
            params={
                "since": SINCE,
                "until": UNTIL,
                "bucket": "day",
                "group_by": "actor",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert set(data) == {
            "since",
            "until",
            "bucket",
            "group_by",
            "buckets",
            "series",
            "totals",
            "event_count",
            "truncated",
        }
        assert data["buckets"] == [
            "2026-11-02T00:00:00Z",
            "2026-11-03T00:00:00Z",
            "2026-11-04T00:00:00Z",
        ]
        assert data["event_count"] == 8
        assert data["truncated"] is False
        assert data["totals"]["tasks_completed"] == 1
        series = data["series"][0]
        assert series["key"] == "alice"
        assert series["points"][0]["bucket"] == "2026-11-02T00:00:00Z"
        assert series["points"][0]["tasks_created"] == 1
        assert series["avg_cycle_time_s"] == 120.0

    def test_empty_range_still_returns_continuous_buckets(self, api_client):
        response = api_client.get(
            "/api/trends",
            params={
                "since": "2026-10-01T00:00:00Z",
                "until": "2026-10-03T00:00:00Z",
                "bucket": "day",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["buckets"]) == 3
        assert data["series"] == []
        assert data["event_count"] == 0

    def test_validation_errors(self, api_client):
        cases = [
            {"bucket": "minute"},
            {"group_by": "task"},
            {"since": "yesterday"},
            {"since": UNTIL, "until": SINCE},
            {"limit": "0"},
            {"limit": "abc"},
        ]
        for params in cases:
            response = api_client.get("/api/trends", params=params)
            assert response.status_code == 400, (params, response.text)
            payload = response.json()
            assert payload["error"] == "validation_error"
            assert payload.get("detail")

    def test_blank_query_params_use_defaults(self, api_client):
        response = api_client.get(
            "/api/trends",
            params={"since": "", "until": "", "bucket": "", "group_by": ""},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["bucket"] == "day"
        assert data["group_by"] == "actor"

    def test_spine_disabled_is_503(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"))
        with TestClient(app) as client:
            response = client.get("/api/trends")
            assert response.status_code == 503
            assert response.json()["error"] == "spine_unavailable"

    def test_token_auth_is_inherited(self, tmp_path, events_db):
        tokens = TokenRegistry([BoardToken("reader", "read", "read-token-123")])
        app = create_app(
            str(tmp_path / "sugar.db"),
            events_db=str(events_db),
            tokens=tokens,
        )
        with TestClient(app) as client:
            assert client.get("/api/trends").status_code == 401
            response = client.get(
                "/api/trends",
                params={"since": SINCE, "until": UNTIL},
                headers={"Authorization": "Bearer read-token-123"},
            )
            assert response.status_code == 200
            assert response.json()["event_count"] == 8


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestTrendsCLI:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def config(self, tmp_path):
        return cli_config(tmp_path)

    def invoke(self, runner, config, *args):
        return runner.invoke(cli, ["--config", str(config), "trends", *args])

    def test_json_output(self, runner, config, events_db):
        result = self.invoke(
            runner,
            config,
            "--db",
            str(events_db),
            "--since",
            SINCE,
            "--until",
            UNTIL,
            "--json",
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["event_count"] == 8
        assert data["totals"]["tasks_completed"] == 1
        assert data["bucket"] == "day"
        assert data["group_by"] == "actor"

    def test_table_output(self, runner, config, events_db):
        result = self.invoke(
            runner,
            config,
            "--db",
            str(events_db),
            "--since",
            SINCE,
            "--until",
            UNTIL,
            "--group-by",
            "harness",
        )
        assert result.exit_code == 0, result.output
        assert "Trends " in result.output
        assert "bucket=day" in result.output
        assert "group_by=harness" in result.output
        assert "group" in result.output
        assert "TOTAL" in result.output
        assert "dsh" in result.output
        assert "avg_cycle" in result.output

    def test_missing_db_is_empty_not_error(self, runner, config, tmp_path):
        result = self.invoke(
            runner,
            config,
            "--db",
            str(tmp_path / "missing.db"),
            "--since",
            SINCE,
            "--until",
            UNTIL,
        )
        assert result.exit_code == 0, result.output
        assert "(no events in range)" in result.output

    def test_invalid_bucket_is_exit_2(self, runner, config, events_db):
        result = self.invoke(
            runner, config, "--db", str(events_db), "--bucket", "minute"
        )
        assert result.exit_code == 2

    def test_invalid_range_is_click_error(self, runner, config, events_db):
        result = self.invoke(
            runner,
            config,
            "--db",
            str(events_db),
            "--since",
            UNTIL,
            "--until",
            SINCE,
        )
        assert result.exit_code == 1
        assert "since" in result.output

    def test_help_lists_options(self, runner):
        result = runner.invoke(cli, ["trends", "--help"])
        assert result.exit_code == 0
        for flag in (
            "--since",
            "--until",
            "--bucket",
            "--group-by",
            "--limit",
            "--db",
            "--json",
        ):
            assert flag in result.output
        assert "zero-filled" in result.output
        assert "docs/trends.md" not in result.output  # command docstring only

    def test_format_trends_table_empty(self):
        report = compute_trends([], SINCE, UNTIL, "day", "actor")
        table = format_trends_table(report)
        assert "TOTAL" in table
        assert "(no events in range)" in table
        assert ISO_Z.match(report["since"])
