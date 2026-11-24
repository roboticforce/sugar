"""Tests for usage/cost attribution (``docs/usage.md``).

Pure aggregation math, the bounded spine reader, ``GET /api/usage`` and the
``sugar usage`` CLI. Every test uses throwaway SQLite files; nothing touches
``~/.sugar/events.db``.
"""

import asyncio
import json

import pytest
from click.testing import CliRunner
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app
from sugar.dashboard.security import BoardToken, TokenRegistry
from sugar.dashboard.spine import EventSpine
from sugar.dashboard.usage import (
    DEFAULT_EVENT_LIMIT,
    GROUP_BY_DIMENSIONS,
    MAX_EVENT_LIMIT,
    UsageQueryError,
    aggregate_usage,
    extract_usage,
    format_usage_table,
    normalize_limit,
    normalize_timestamp,
    normalize_usage_payload,
    read_usage_events,
    usage_report,
    validate_group_by,
)
from sugar.main import cli

READ_SECRET = "usage-read-token-3f1c9a2d"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_ambient_prices(tmp_path, monkeypatch):
    """Keep estimates deterministic unless a test opts into a price table."""
    monkeypatch.setenv("SUGAR_MODEL_PRICES", str(tmp_path / "no-prices.json"))


_MISSING = object()


def make_event(
    kind="usage.reported",
    payload=_MISSING,
    *,
    ts="2026-11-09T12:00:00Z",
    **fields,
):
    event = {
        "kind": kind,
        "ts": ts,
        "actor": "dsh",
        "actor_kind": "agent",
        "harness": "dsh",
        "task_id": "t_1",
        "run_id": "r_1",
        "project": "sugar",
    }
    event.update(fields)
    if payload is _MISSING:
        payload = (
            {"tokens_in": 100, "tokens_out": 50, "cost_usd": 0.01}
            if (kind.endswith("reported") or kind == "run.ended")
            else {}
        )
    event["payload"] = payload
    return event


def cli_config(tmp_path):
    """Minimal config so CLI tests never touch the repo's .sugar directory."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "sugar:\n"
        "  storage:\n"
        f"    database: \"{tmp_path / 'sugar.db'}\"\n"
        "  logging:\n"
        f"    file: \"{tmp_path / 'sugar.log'}\"\n",
        encoding="utf-8",
    )
    return path


def make_events_db(tmp_path, events, name="events.db"):
    path = str(tmp_path / name)

    async def build():
        spine = EventSpine(path, retention_days=0)
        await spine.start()
        try:
            for event in events:
                await spine.append(event)
        finally:
            await spine.close()

    run(build())
    return path


# ---------------------------------------------------------------------------
# Normalization (tolerance)
# ---------------------------------------------------------------------------


class TestNormalizePayload:
    def test_plain_numbers(self):
        usage = normalize_usage_payload(
            {"tokens_in": 1200, "tokens_out": 800, "cost_usd": 0.42}
        )
        assert usage == {
            "tokens_in": 1200,
            "tokens_out": 800,
            "cost_usd": 0.42,
            "tokens_total": 2000,
        }

    def test_missing_fields_become_zero(self):
        usage = normalize_usage_payload({"tokens_in": 12})
        assert usage is not None
        assert usage["tokens_in"] == 12
        assert usage["tokens_out"] == 0
        assert usage["cost_usd"] == 0.0
        assert usage["tokens_total"] == 12

    def test_int_strings_and_numeric_strings(self):
        usage = normalize_usage_payload(
            {"tokens_in": "1200", "tokens_out": " 800 ", "cost_usd": "0.42"}
        )
        assert usage is not None
        assert usage["tokens_in"] == 1200
        assert usage["tokens_out"] == 800
        assert usage["cost_usd"] == pytest.approx(0.42)

    def test_dollar_prefixed_cost_string(self):
        usage = normalize_usage_payload({"cost_usd": "$0.42"})
        assert usage is not None
        assert usage["cost_usd"] == pytest.approx(0.42)

    def test_negative_and_nan_are_ignored_per_field(self):
        usage = normalize_usage_payload(
            {
                "tokens_in": -5,
                "tokens_out": 300,
                "cost_usd": float("nan"),
            }
        )
        assert usage is not None
        assert usage["tokens_in"] == 0
        assert usage["tokens_out"] == 300
        assert usage["cost_usd"] == 0.0

    def test_all_invalid_is_not_usage(self):
        for payload in (
            {},
            None,
            "not json",
            [1, 2, 3],
            {"tokens_in": None, "tokens_out": "abc", "cost_usd": float("inf")},
            {"tokens_in": True, "tokens_out": False, "cost_usd": []},
            {"tokens_in": -1, "tokens_out": -2, "cost_usd": -0.5},
        ):
            assert normalize_usage_payload(payload) is None, payload

    def test_model_is_optional_and_trimmed(self):
        assert "model" not in normalize_usage_payload({"tokens_in": 1})
        usage = normalize_usage_payload({"tokens_in": 1, "model": "  sonnet  "})
        assert usage["model"] == "sonnet"

    def test_extract_usage_only_for_usage_kinds(self):
        payload = {"tokens_in": 5, "cost_usd": 0.01}
        assert extract_usage({"kind": "usage.reported", "payload": payload})
        assert extract_usage({"kind": "run.ended", "payload": payload})
        assert extract_usage({"kind": "task.created", "payload": payload}) is None
        assert extract_usage({"kind": "usage.reported"}) is None
        assert extract_usage("nope") is None


# ---------------------------------------------------------------------------
# Aggregation math
# ---------------------------------------------------------------------------


class TestAggregateUsage:
    def test_totals_math(self):
        events = [
            make_event(payload={"tokens_in": 100, "tokens_out": 50, "cost_usd": 0.01}),
            make_event(payload={"tokens_in": 200, "tokens_out": 20, "cost_usd": 0.02}),
            make_event(
                kind="run.ended",
                payload={"tokens_in": 0, "tokens_out": 300, "cost_usd": 0.03},
            ),
        ]
        report = aggregate_usage(events)
        assert report["groups"] == []
        assert report["totals"] == {
            "events": 3,
            "tokens_in": 300,
            "tokens_out": 370,
            "tokens_total": 670,
            "cost_usd": pytest.approx(0.06),
            "estimated_cost_usd": 0.0,
            "cost_basis": "reported",
        }

    def test_skips_garbage_events(self):
        events = [
            make_event(payload={"tokens_in": 10, "cost_usd": 0.1}),
            make_event(kind="task.created", payload={"tokens_in": 999}),
            make_event(payload={}),
            make_event(payload=None),
            make_event(payload={"tokens_in": "abc", "cost_usd": -1}),
        ]
        report = aggregate_usage(events)
        assert report["totals"]["events"] == 1
        assert report["totals"]["tokens_in"] == 10
        assert report["totals"]["cost_usd"] == pytest.approx(0.1)

    def test_cost_is_rounded_for_output(self):
        events = [
            make_event(payload={"cost_usd": 0.1}),
            make_event(payload={"cost_usd": 0.2}),
        ]
        assert aggregate_usage(events)["totals"]["cost_usd"] == pytest.approx(0.3)

    def test_bad_events_never_raise(self):
        assert aggregate_usage([None, "x", {}, {"kind": 5}])["totals"]["events"] == 0

    @pytest.mark.parametrize("dimension", GROUP_BY_DIMENSIONS)
    def test_every_group_dimension(self, dimension):
        events = [
            make_event(
                payload={"tokens_in": 100, "cost_usd": 0.5, "model": "model-a"},
                actor="alice",
                harness="claude-code",
                task_id="t_a",
                project="alpha",
                ts="2026-11-01T10:00:00Z",
            ),
            make_event(
                payload={"tokens_in": 50, "cost_usd": 0.25, "model": "model-b"},
                actor="bob",
                harness="dsh",
                task_id="t_b",
                project="beta",
                ts="2026-11-02T10:00:00Z",
            ),
            make_event(
                kind="run.ended",
                payload={"tokens_in": 10, "cost_usd": 0.1, "model": "model-a"},
                actor="alice",
                harness="claude-code",
                task_id="t_a",
                project="alpha",
                ts="2026-11-01T18:00:00Z",
            ),
        ]
        groups = aggregate_usage(events, group_by=dimension)["groups"]
        assert len(groups) == 2
        by_key = {group["key"]: group for group in groups}
        expected_keys = {
            "actor": {"alice", "bob"},
            "harness": {"claude-code", "dsh"},
            "task": {"t_a", "t_b"},
            "project": {"alpha", "beta"},
            "model": {"model-a", "model-b"},
            "day": {"2026-11-01", "2026-11-02"},
            "kind": {"usage.reported", "run.ended"},
        }[dimension]
        assert set(by_key) == expected_keys

    def test_groups_sorted_by_cost_desc(self):
        events = [
            make_event(payload={"cost_usd": 0.01}, harness="cheap"),
            make_event(payload={"cost_usd": 0.5}, harness="pricey"),
            make_event(payload={"cost_usd": 0.25}, harness="mid"),
        ]
        groups = aggregate_usage(events, group_by="harness")["groups"]
        assert [group["key"] for group in groups] == ["pricey", "mid", "cheap"]
        assert groups[0]["cost_usd"] == pytest.approx(0.5)

    def test_unknown_bucket(self):
        events = [
            make_event(payload={"cost_usd": 0.1}, actor=None, harness=None),
            make_event(payload={"cost_usd": 0.2}),
        ]
        by_actor = {
            group["key"]: group
            for group in aggregate_usage(events, group_by="actor")["groups"]
        }
        assert by_actor["unknown"]["events"] == 1
        assert by_actor["unknown"]["cost_usd"] == pytest.approx(0.1)
        assert by_actor["dsh"]["events"] == 1

        missing_ts = make_event(payload={"cost_usd": 0.1})
        del missing_ts["ts"]
        by_day = aggregate_usage([missing_ts], group_by="day")["groups"]
        assert by_day[0]["key"] == "unknown"

    def test_invalid_group_by(self):
        with pytest.raises(UsageQueryError):
            aggregate_usage([], group_by="bogus")
        assert validate_group_by(None) is None
        assert validate_group_by("  HARNESS ") == "harness"
        assert validate_group_by("  MODEL ") == "model"

    def test_empty_events(self):
        report = aggregate_usage([])
        assert report["totals"]["events"] == 0
        assert report["totals"]["cost_usd"] == 0.0
        assert report["groups"] == []


# ---------------------------------------------------------------------------
# Query parameter helpers
# ---------------------------------------------------------------------------


class TestQueryHelpers:
    def test_normalize_timestamp(self):
        assert normalize_timestamp("2026-11-01") == "2026-11-01T00:00:00Z"
        assert normalize_timestamp("2026-11-01T10:00:00Z") == "2026-11-01T10:00:00Z"
        assert (
            normalize_timestamp("2026-11-01T10:00:00+02:00") == "2026-11-01T08:00:00Z"
        )
        assert normalize_timestamp(None) is None
        assert normalize_timestamp("  ") is None
        with pytest.raises(UsageQueryError):
            normalize_timestamp("not-a-date")

    def test_normalize_limit(self):
        assert normalize_limit(None) == DEFAULT_EVENT_LIMIT
        assert normalize_limit("10") == 10
        assert normalize_limit(10**9) == MAX_EVENT_LIMIT
        for bad in ("abc", 0, -3):
            with pytest.raises(UsageQueryError):
                normalize_limit(bad)


# ---------------------------------------------------------------------------
# Spike reader
# ---------------------------------------------------------------------------

SPINE_EVENTS = [
    make_event(
        payload={"tokens_in": 100, "tokens_out": 10, "cost_usd": 0.10},
        actor="alice",
        harness="claude-code",
        ts="2026-11-01T10:00:00Z",
    ),
    make_event(
        payload={"tokens_in": 200, "tokens_out": 20, "cost_usd": 0.20},
        actor="bob",
        harness="dsh",
        ts="2026-11-02T10:00:00Z",
    ),
    make_event(
        kind="run.ended",
        payload={"tokens_in": 300, "tokens_out": 30, "cost_usd": 0.30},
        actor="alice",
        harness="dsh",
        ts="2026-11-03T10:00:00Z",
    ),
    make_event(
        kind="task.created",
        payload={"title": "not usage"},
        actor="alice",
        harness="claude-code",
        ts="2026-11-02T12:00:00Z",
    ),
]


@pytest.fixture
def events_db(tmp_path):
    return make_events_db(tmp_path, SPINE_EVENTS)


class TestReadUsageEvents:
    def test_reads_all_events(self, events_db):
        events, truncated = run(read_usage_events(events_db))
        assert len(events) == len(SPINE_EVENTS)
        assert truncated is False
        assert [event["id"] for event in events] == sorted(
            event["id"] for event in events
        )
        assert events[0]["payload"]["tokens_in"] == 100

    def test_since_until(self, events_db):
        events, _ = run(
            read_usage_events(
                events_db,
                since="2026-11-02T00:00:00Z",
                until="2026-11-02T23:59:59Z",
            )
        )
        assert len(events) == 2
        assert {event["actor"] for event in events} == {"bob", "alice"}

    def test_kinds_actor_harness(self, events_db):
        events, _ = run(read_usage_events(events_db, kinds="run.ended"))
        assert [event["kind"] for event in events] == ["run.ended"]

        events, _ = run(read_usage_events(events_db, actor="alice"))
        assert len(events) == 3
        assert all(event["actor"] == "alice" for event in events)

        events, _ = run(read_usage_events(events_db, harness="dsh"))
        assert len(events) == 2

    def test_since_id_pages_forward(self, events_db):
        first, _ = run(read_usage_events(events_db, limit=2))
        last_id = first[-1]["id"]
        second, _ = run(read_usage_events(events_db, since_id=last_id, limit=2))
        assert {event["id"] for event in first}.isdisjoint(
            {event["id"] for event in second}
        )
        combined = {event["id"] for event in first} | {event["id"] for event in second}
        assert len(combined) == 4

    def test_limit_is_bounded_and_reports_truncation(self, events_db):
        events, truncated = run(read_usage_events(events_db, limit=2))
        assert len(events) == 2
        assert truncated is True

        events, truncated = run(read_usage_events(events_db, limit=100))
        assert len(events) == 4
        assert truncated is False

    def test_missing_db_is_empty(self, tmp_path):
        events, truncated = run(read_usage_events(tmp_path / "does-not-exist.db"))
        assert events == [] and truncated is False

    def test_empty_sqlite_file_is_empty(self, tmp_path):
        import sqlite3

        path = tmp_path / "empty.db"
        sqlite3.connect(path).close()
        events, truncated = run(read_usage_events(path))
        assert events == [] and truncated is False

    def test_invalid_timestamp_rejected(self, events_db):
        with pytest.raises(UsageQueryError):
            run(read_usage_events(events_db, since="yesterday"))


class TestUsageReport:
    def test_report_shape_and_aggregation(self, events_db):
        report = run(usage_report(events_db, group_by="harness"))
        assert set(report) >= {
            "totals",
            "groups",
            "since",
            "until",
            "group_by",
            "events_scanned",
            "truncated",
        }
        assert report["group_by"] == "harness"
        # Only the three usage-bearing events contribute (task.created ignored).
        assert report["totals"]["events"] == 3
        assert report["totals"]["tokens_in"] == 600
        assert report["totals"]["tokens_out"] == 60
        assert report["totals"]["tokens_total"] == 660
        assert report["totals"]["cost_usd"] == pytest.approx(0.6)
        assert report["events_scanned"] == 4
        assert {group["key"] for group in report["groups"]} == {"claude-code", "dsh"}

    def test_report_filters(self, events_db):
        report = run(
            usage_report(
                events_db,
                since="2026-11-02",
                until="2026-11-02T23:59:59Z",
                group_by="actor",
            )
        )
        assert report["totals"]["events"] == 1
        assert report["groups"][0]["key"] == "bob"
        assert report["since"] == "2026-11-02T00:00:00Z"
        assert report["until"] == "2026-11-02T23:59:59Z"

    def test_empty_range(self, tmp_path):
        db = make_events_db(tmp_path, [])
        report = run(usage_report(db))
        assert report["totals"] == {
            "events": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "tokens_total": 0,
            "cost_usd": 0.0,
            "estimated_cost_usd": 0.0,
            "cost_basis": "unavailable",
        }
        assert report["groups"] == []
        assert report["truncated"] is False

    def test_truncated_report(self, events_db):
        report = run(usage_report(events_db, limit=2))
        assert report["truncated"] is True
        assert report["events_scanned"] == 2
        assert report["totals"]["events"] == 2


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


def post_events(client, events):
    for event in events:
        response = client.post("/api/events", json=event)
        assert response.status_code == 201, response.text


class TestUsageAPI:
    @pytest.fixture
    def client(self, tmp_path):
        app = create_app(
            str(tmp_path / "sugar.db"),
            events_db=str(tmp_path / "events.db"),
        )
        with TestClient(app) as test_client:
            yield test_client

    def test_totals_and_shape(self, client):
        post_events(client, SPINE_EVENTS[:3])

        response = client.get("/api/usage")
        assert response.status_code == 200, response.text
        report = response.json()
        assert set(report) >= {
            "totals",
            "groups",
            "since",
            "until",
            "group_by",
            "events_scanned",
            "truncated",
        }
        assert report["totals"] == {
            "events": 3,
            "tokens_in": 600,
            "tokens_out": 60,
            "tokens_total": 660,
            "cost_usd": pytest.approx(0.6),
            "estimated_cost_usd": 0.0,
            "cost_basis": "reported",
        }
        assert report["groups"] == []
        assert report["group_by"] is None

    def test_group_by_harness(self, client):
        post_events(client, SPINE_EVENTS)

        response = client.get("/api/usage", params={"group_by": "harness"})
        assert response.status_code == 200
        report = response.json()
        groups = report["groups"]
        assert [group["key"] for group in groups] == ["dsh", "claude-code"]
        assert groups[0]["cost_usd"] == pytest.approx(0.5)
        assert groups[0]["events"] == 2

    def test_group_by_model(self, client):
        post_events(
            client,
            [
                make_event(payload={"tokens_in": 10, "model": "model-a"}),
                make_event(payload={"tokens_in": 20, "model": "model-b"}),
                make_event(payload={"tokens_in": 5, "model": "model-a"}),
            ],
        )
        report = client.get("/api/usage", params={"group_by": "model"}).json()
        assert [group["key"] for group in report["groups"]] == [
            "model-a",
            "model-b",
        ]
        assert report["groups"][0]["events"] == 2
        assert report["group_by"] == "model"

    def test_group_by_day(self, client):
        post_events(client, SPINE_EVENTS)
        report = client.get("/api/usage", params={"group_by": "day"}).json()
        keys = [group["key"] for group in report["groups"]]
        assert "2026-11-01" in keys and "2026-11-02" in keys

    def test_filters(self, client):
        post_events(client, SPINE_EVENTS)

        response = client.get(
            "/api/usage",
            params={"since": "2026-11-03T00:00:00Z", "group_by": "actor"},
        )
        report = response.json()
        assert report["totals"]["events"] == 1
        assert report["totals"]["cost_usd"] == pytest.approx(0.3)
        assert report["groups"][0]["key"] == "alice"

        only_runs = client.get("/api/usage", params={"kinds": "run.ended"}).json()
        assert only_runs["totals"]["events"] == 1

        by_actor = client.get("/api/usage", params={"actor": "bob"}).json()
        assert by_actor["totals"]["events"] == 1

        by_harness = client.get("/api/usage", params={"harness": "dsh"}).json()
        assert by_harness["totals"]["events"] == 2

    def test_validation_errors_are_400(self, client):
        for params in (
            {"group_by": "bogus"},
            {"since": "not-a-date"},
            {"until": "2026-13-40"},
            {"limit": "abc"},
            {"limit": "0"},
        ):
            response = client.get("/api/usage", params=params)
            assert response.status_code == 400, (params, response.text)
            assert response.json()["error"] == "validation_error"

    def test_empty_range_is_zeros(self, client):
        report = client.get("/api/usage", params={"group_by": "actor"}).json()
        assert report["totals"]["events"] == 0
        assert report["groups"] == []

    def test_truncated_flag(self, client):
        post_events(client, SPINE_EVENTS)
        report = client.get("/api/usage", params={"limit": 2}).json()
        assert report["truncated"] is True
        assert report["events_scanned"] == 2

    def test_spine_disabled_is_503(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"))
        with TestClient(app) as plain_client:
            response = plain_client.get("/api/usage")
            assert response.status_code == 503
            assert response.json()["error"] == "spine_unavailable"

    def test_token_auth_required(self, tmp_path):
        tokens = TokenRegistry([BoardToken("alice", "read", READ_SECRET)])
        app = create_app(
            str(tmp_path / "sugar.db"),
            events_db=str(tmp_path / "events.db"),
            tokens=tokens,
        )
        with TestClient(app) as client:
            assert client.get("/api/usage").status_code == 401
            authorized = client.get(
                "/api/usage", headers={"Authorization": f"Bearer {READ_SECRET}"}
            )
            assert authorized.status_code == 200
            assert authorized.json()["totals"]["events"] == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestUsageCLI:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    def invoke(self, runner, tmp_path, *args):
        return runner.invoke(
            cli, ["--config", str(cli_config(tmp_path)), "usage", *args]
        )

    def test_json_output(self, runner, tmp_path, events_db):
        result = self.invoke(
            runner, tmp_path, "--db", events_db, "--group-by", "harness", "--json"
        )
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert report["totals"]["events"] == 3
        assert report["totals"]["tokens_total"] == 660
        assert report["group_by"] == "harness"
        assert {group["key"] for group in report["groups"]} == {"claude-code", "dsh"}

    def test_table_output(self, runner, tmp_path, events_db):
        result = self.invoke(runner, tmp_path, "--db", events_db)
        assert result.exit_code == 0, result.output
        assert "TOTAL" in result.output
        assert "events" in result.output and "cost_usd" in result.output
        assert "0.6000" in result.output

    def test_group_by_model_cli(self, runner, tmp_path):
        db = make_events_db(
            tmp_path,
            [
                make_event(payload={"tokens_in": 10, "model": "model-a"}),
                make_event(payload={"tokens_in": 20, "model": "model-b"}),
            ],
            name="models.db",
        )
        result = self.invoke(
            runner, tmp_path, "--db", db, "--group-by", "model", "--json"
        )
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert report["group_by"] == "model"
        assert {group["key"] for group in report["groups"]} == {
            "model-a",
            "model-b",
        }

    def test_table_shows_reported_and_estimated_columns(
        self, runner, tmp_path, events_db
    ):
        result = self.invoke(runner, tmp_path, "--db", events_db)
        assert result.exit_code == 0, result.output
        assert "cost_usd" in result.output
        assert "estimated_cost_usd" in result.output
        assert "cost_basis" in result.output

    def test_grouped_table_and_truncation_note(self, runner, tmp_path, events_db):
        result = self.invoke(
            runner, tmp_path, "--db", events_db, "--group-by", "harness", "--limit", "2"
        )
        assert result.exit_code == 0, result.output
        assert "claude-code" in result.output or "dsh" in result.output
        assert "truncated" in result.output

    def test_since_until_filter(self, runner, tmp_path, events_db):
        result = self.invoke(
            runner,
            tmp_path,
            "--db",
            events_db,
            "--since",
            "2026-11-03",
            "--until",
            "2026-11-03T23:59:59Z",
            "--json",
        )
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert report["totals"]["events"] == 1
        assert report["since"] == "2026-11-03T00:00:00Z"

    def test_empty_range(self, runner, tmp_path):
        result = self.invoke(runner, tmp_path, "--db", str(tmp_path / "nope.db"))
        assert result.exit_code == 0, result.output
        assert "TOTAL" in result.output
        assert "0.0000" in result.output

    def test_invalid_timestamp_exits_nonzero(self, runner, tmp_path, events_db):
        result = self.invoke(runner, tmp_path, "--db", events_db, "--since", "nope")
        assert result.exit_code != 0
        assert "invalid timestamp" in result.output

    def test_invalid_group_by_is_a_usage_error(self, runner, tmp_path, events_db):
        result = self.invoke(runner, tmp_path, "--db", events_db, "--group-by", "bogus")
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Text rendering
# ---------------------------------------------------------------------------


def test_format_usage_table_without_groups_hint():
    text = format_usage_table(
        {
            "totals": {
                "events": 1,
                "tokens_in": 10,
                "tokens_out": 5,
                "tokens_total": 15,
                "cost_usd": 0.0123,
            },
            "groups": [],
            "truncated": False,
        }
    )
    assert "TOTAL" in text
    assert "0.0123" in text
    assert "no group_by" in text


def test_format_usage_table_handles_missing_totals():
    text = format_usage_table({})
    assert "TOTAL" in text


# ---------------------------------------------------------------------------
# Optional pricing estimates
# ---------------------------------------------------------------------------


def write_prices(tmp_path, data):
    path = tmp_path / "model-prices.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestPricingEstimates:
    def test_no_price_file_means_no_estimates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SUGAR_MODEL_PRICES", str(tmp_path / "missing.json"))
        db = make_events_db(
            tmp_path,
            [
                make_event(
                    payload={
                        "tokens_in": 1_000_000,
                        "tokens_out": 500_000,
                        "model": "m1",
                    }
                )
            ],
        )
        report = run(usage_report(db))
        assert report["totals"]["cost_usd"] == 0.0
        assert report["totals"]["estimated_cost_usd"] == 0.0
        assert report["totals"]["cost_basis"] == "unavailable"

    def test_estimated_cost_math_and_model_groups(self, tmp_path, monkeypatch):
        path = write_prices(
            tmp_path,
            {"m1": {"input_per_mtok": 2.0, "output_per_mtok": 4.0}},
        )
        monkeypatch.setenv("SUGAR_MODEL_PRICES", str(path))
        db = make_events_db(
            tmp_path,
            [
                make_event(
                    payload={
                        "tokens_in": 1_000_000,
                        "tokens_out": 500_000,
                        "model": "m1",
                    }
                ),
                make_event(
                    payload={
                        "tokens_in": 250_000,
                        "tokens_out": 250_000,
                        "model": "m1",
                    }
                ),
            ],
        )
        report = run(usage_report(db, group_by="model"))
        # 1M*2 + 0.5M*4 = 4.0; 0.25M*2 + 0.25M*4 = 1.5
        assert report["totals"]["estimated_cost_usd"] == pytest.approx(5.5)
        assert report["totals"]["cost_usd"] == 0.0
        assert report["totals"]["cost_basis"] == "estimated"
        assert len(report["groups"]) == 1
        group = report["groups"][0]
        assert group["key"] == "m1"
        assert group["estimated_cost_usd"] == pytest.approx(5.5)
        assert group["cost_basis"] == "estimated"

    def test_reported_cost_is_never_overwritten(self, tmp_path, monkeypatch):
        path = write_prices(
            tmp_path,
            {"m1": {"input_per_mtok": 2.0, "output_per_mtok": 4.0}},
        )
        monkeypatch.setenv("SUGAR_MODEL_PRICES", str(path))
        db = make_events_db(
            tmp_path,
            [
                make_event(
                    payload={
                        "tokens_in": 1_000_000,
                        "tokens_out": 0,
                        "model": "m1",
                        "cost_usd": 0.42,
                    }
                )
            ],
        )
        report = run(usage_report(db))
        assert report["totals"]["cost_usd"] == pytest.approx(0.42)
        assert report["totals"]["estimated_cost_usd"] == pytest.approx(2.0)
        assert report["totals"]["cost_basis"] == "mixed"

    def test_partial_model_coverage(self, tmp_path, monkeypatch):
        path = write_prices(
            tmp_path,
            {"priced": {"input_per_mtok": 2.0, "output_per_mtok": 4.0}},
        )
        monkeypatch.setenv("SUGAR_MODEL_PRICES", str(path))
        db = make_events_db(
            tmp_path,
            [
                make_event(
                    payload={
                        "tokens_in": 1_000_000,
                        "tokens_out": 0,
                        "model": "priced",
                    }
                ),
                make_event(
                    payload={
                        "tokens_in": 1_000_000,
                        "tokens_out": 0,
                        "model": "unpriced",
                    }
                ),
            ],
        )
        report = run(usage_report(db, group_by="model"))
        assert report["totals"]["estimated_cost_usd"] == pytest.approx(2.0)
        assert report["totals"]["cost_basis"] == "estimated"
        groups = {group["key"]: group for group in report["groups"]}
        assert groups["priced"]["estimated_cost_usd"] == pytest.approx(2.0)
        assert groups["priced"]["cost_basis"] == "estimated"
        assert groups["unpriced"]["estimated_cost_usd"] == 0.0
        assert groups["unpriced"]["cost_basis"] == "unavailable"

    def test_malformed_price_file_is_ignored(self, tmp_path, monkeypatch):
        path = tmp_path / "model-prices.json"
        path.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("SUGAR_MODEL_PRICES", str(path))
        db = make_events_db(
            tmp_path,
            [
                make_event(
                    payload={
                        "tokens_in": 1000,
                        "model": "m1",
                        "cost_usd": 0.25,
                    }
                )
            ],
        )
        report = run(usage_report(db))
        assert report["totals"]["cost_usd"] == pytest.approx(0.25)
        assert report["totals"]["estimated_cost_usd"] == 0.0
        assert report["totals"]["cost_basis"] == "reported"

    def test_invalid_entries_are_ignored(self):
        from sugar.dashboard.pricing import parse_price_table

        parsed = parse_price_table(
            {
                "good": {"input_per_mtok": 1, "output_per_mtok": 2},
                "missing_output": {"input_per_mtok": 1},
                "negative": {"input_per_mtok": -1, "output_per_mtok": 1},
                "garbage": "nope",
                "": {"input_per_mtok": 1, "output_per_mtok": 1},
            }
        )
        assert set(parsed) == {"good"}
        assert parsed["good"].input_per_mtok == 1.0
        assert parsed["good"].output_per_mtok == 2.0

    def test_resolve_and_estimate_helpers(self, tmp_path, monkeypatch):
        from sugar.dashboard import pricing

        path = write_prices(
            tmp_path,
            {"m1": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}},
        )
        monkeypatch.setenv("SUGAR_MODEL_PRICES", str(path))
        assert pricing.resolve_price_path() == path
        table = pricing.load_price_table()
        assert table.get("M1") is not None  # case-insensitive fallback
        assert table.estimate_cost("m1", 1_000_000, 500_000) == pytest.approx(2.0)
        assert table.estimate_cost("unknown", 1_000_000, 0) is None
        assert pricing.estimate_cost("m1", 1_000_000, 0, table=table) == pytest.approx(
            1.0
        )
