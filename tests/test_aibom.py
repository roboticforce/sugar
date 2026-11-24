"""Tests for the observed AIBOM inventory v0 (``sugar.aibom.v0``).

Covers pure aggregation, the bounded spine reader, ``GET /api/aibom`` and
``sugar aibom``. Every test uses throwaway SQLite files; nothing touches
``~/.sugar/events.db``.
"""

import asyncio
import json

import pytest
from click.testing import CliRunner
from starlette.testclient import TestClient

from sugar.dashboard.aibom import (
    DEFAULT_EVENT_LIMIT,
    MAX_EVENT_LIMIT,
    MAX_EVIDENCE_LINKS,
    SCHEMA,
    AibomQueryError,
    aggregate_aibom,
    aibom_report,
    format_aibom_table,
    normalize_limit,
    normalize_since,
    read_aibom_events,
)
from sugar.dashboard.app import create_app
from sugar.dashboard.spine import EventSpine
from sugar.main import cli


def run(coro):
    return asyncio.run(coro)


def make_event(kind="usage.reported", payload=None, **fields):
    body = {
        "kind": kind,
        "actor": "dsh",
        "actor_kind": "agent",
        "harness": "dsh",
        "ts": "2026-11-09T12:00:00Z",
        "payload": payload if payload is not None else {},
    }
    body.update(fields)
    return body


def nested(summary, **outer):
    return {"schema": "sugar.telemetry.v1", "payload": summary, **outer}


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


def cli_config(tmp_path):
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


def components_by_name(report):
    return {
        (component["type"], component["name"]): component
        for component in report["components"]
    }


# ---------------------------------------------------------------------------
# Normalization / validation
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_since_accepts_iso_and_z(self):
        assert normalize_since("2026-11-09T12:00:00Z") == "2026-11-09T12:00:00Z"
        assert normalize_since("2026-11-09T12:00:00") == "2026-11-09T12:00:00Z"
        assert normalize_since(None) is None
        assert normalize_since("") is None

    def test_since_rejects_garbage(self):
        with pytest.raises(AibomQueryError):
            normalize_since("yesterday")

    def test_limit_boundaries(self):
        assert normalize_limit(None) == DEFAULT_EVENT_LIMIT
        assert normalize_limit("10") == 10
        assert normalize_limit(MAX_EVENT_LIMIT * 10) == MAX_EVENT_LIMIT
        for bad in (0, -1, "abc", True):
            with pytest.raises(AibomQueryError):
                normalize_limit(bad)


# ---------------------------------------------------------------------------
# Pure aggregation
# ---------------------------------------------------------------------------


class TestAggregate:
    def test_empty_inventory_shape(self):
        report = aggregate_aibom([])
        assert report["schema"] == SCHEMA
        assert report["generated_at"].endswith("Z")
        assert report["scope"] == {"project": None, "since": None}
        assert report["components"] == []
        assert report["coverage"] == {"by_harness": {}, "by_fidelity": {}}
        assert report["events_scanned"] == 0
        assert report["truncated"] is False
        assert any("observed" in item.lower() for item in report["limitations"])
        assert any("CycloneDX" in item for item in report["limitations"])

    def test_models_harnesses_tools_and_artifacts(self):
        events = [
            make_event(
                "usage.reported",
                payload={"model": "deepseek-flash", "fidelity": "L2", "tokens_in": 10},
                session_id="s1",
                project="sugar",
                ts="2026-11-09T10:00:00Z",
            ),
            make_event(
                "usage.reported",
                payload=nested({"model": "deepseek-flash", "fidelity": "L2"}),
                session_id="s1",
                project="sugar",
                ts="2026-11-09T11:00:00Z",
            ),
            make_event(
                "usage.reported",
                payload={"model": "gpt-5", "fidelity": "L1"},
                harness="claude-code",
                session_id="s2",
                project="other",
                ts="2026-11-09T12:00:00Z",
            ),
            make_event(
                "tool.started",
                payload={"tool_name": "bash", "tool_use_id": "c1", "fidelity": "L3"},
                session_id="s1",
                ts="2026-11-09T10:01:00Z",
            ),
            make_event(
                "tool.started",
                payload={"tool_name": "bash", "tool_use_id": "c2", "fidelity": "L3"},
                session_id="s1",
                ts="2026-11-09T10:02:00Z",
            ),
            make_event(
                "tool.completed",
                payload={"tool_name": "bash", "tool_use_id": "c1", "fidelity": "L3"},
                session_id="s1",
                ts="2026-11-09T10:03:00Z",
            ),
            make_event(
                "tool.started",
                payload={"tool_name": "read", "tool_use_id": "c3", "fidelity": "L3"},
                session_id="s1",
                ts="2026-11-09T10:04:00Z",
            ),
            make_event(
                "artifact.created",
                payload={"path": "/tmp/report.md", "fidelity": "L3"},
                session_id="s1",
                project="sugar",
                ts="2026-11-09T10:05:00Z",
            ),
            make_event(
                "artifact.created",
                payload=nested({"path": "/tmp/report.md"}),
                session_id="s1",
                project="sugar",
                ts="2026-11-09T10:06:00Z",
            ),
        ]
        report = aggregate_aibom(events)
        by_name = components_by_name(report)

        model = by_name[("model", "deepseek-flash")]
        assert model["events"] == 2
        assert model["projects"] == ["sugar"]
        assert model["first_seen"] == "2026-11-09T10:00:00Z"
        assert model["last_seen"] == "2026-11-09T11:00:00Z"
        assert by_name[("model", "gpt-5")]["projects"] == ["other"]

        assert by_name[("harness", "dsh")]["events"] == 8
        assert by_name[("harness", "claude-code")]["events"] == 1

        assert by_name[("tool", "bash")]["events"] == 2  # result does not double count
        assert by_name[("tool", "read")]["events"] == 1
        assert by_name[("artifact", "/tmp/report.md")]["events"] == 2

        assert report["coverage"]["by_harness"] == {"claude-code": 1, "dsh": 8}
        assert report["coverage"]["by_fidelity"] == {"L1": 1, "L2": 2, "L3": 5}

    def test_component_ordering_is_stable(self):
        events = [
            make_event("tool.started", payload={"tool_name": "zeta"}),
            make_event("tool.started", payload={"tool_name": "alpha"}),
            make_event("usage.reported", payload={"model": "m2"}),
            make_event("artifact.created", payload={"path": "/b"}),
            make_event("artifact.created", payload={"path": "/a"}),
        ]
        report = aggregate_aibom(events)
        types = [component["type"] for component in report["components"]]
        assert types == ["model", "harness", "tool", "tool", "artifact", "artifact"]
        assert [c["name"] for c in report["components"] if c["type"] == "tool"] == [
            "alpha",
            "zeta",
        ]
        assert [c["name"] for c in report["components"] if c["type"] == "artifact"] == [
            "/a",
            "/b",
        ]

    def test_canonical_nested_payloads_are_tolerated(self):
        events = [
            make_event(
                "tool.started",
                payload=nested({"tool_use_id": "c1"}, tool_name="bash", fidelity="L3"),
            ),
            make_event(
                "artifact.created",
                payload=nested({"path": "/tmp/x.pdf"}, tool_use_id="c2"),
            ),
        ]
        report = aggregate_aibom(events)
        by_name = components_by_name(report)
        assert by_name[("tool", "bash")]["events"] == 1
        assert by_name[("artifact", "/tmp/x.pdf")]["events"] == 1
        assert report["coverage"]["by_fidelity"] == {"L3": 1}

    def test_evidence_links_and_bounds(self):
        events = [
            make_event(
                "tool.started",
                payload={"tool_name": "bash", "tool_use_id": f"c{index}"},
                session_id="s1",
                ts=f"2026-11-09T10:0{index}:00Z",
            )
            for index in range(7)
        ]
        report = aggregate_aibom(events)
        tool = components_by_name(report)[("tool", "bash")]
        assert len(tool["evidence_links"]) == MAX_EVIDENCE_LINKS
        assert tool["evidence_links"][0] == {
            "trace_id": "dsh:s1",
            "span_id": "c0",
        }
        assert len({link["span_id"] for link in tool["evidence_links"]}) == (
            MAX_EVIDENCE_LINKS
        )

    def test_explicit_trace_id_is_used(self):
        events = [
            make_event(
                "usage.reported",
                payload={"model": "m1"},
                session_id="s1",
                trace_id="custom-trace",
            )
        ]
        report = aggregate_aibom(events)
        model = components_by_name(report)[("model", "m1")]
        assert model["evidence_links"][0]["trace_id"] == "custom-trace"

    def test_duplicate_evidence_links_are_deduped(self):
        event = make_event(
            "tool.started",
            payload={"tool_name": "bash", "tool_use_id": "c1"},
            session_id="s1",
        )
        report = aggregate_aibom([event, dict(event, id=2)])
        tool = components_by_name(report)[("tool", "bash")]
        assert tool["evidence_links"] == [{"trace_id": "dsh:s1", "span_id": "c1"}]

    def test_mcp_server_only_when_unambiguously_observed(self):
        events = [
            make_event(
                "tool.started",
                payload={
                    "tool_name": "mcp__filesystem__read_file",
                    "tool_use_id": "c1",
                },
            ),
            make_event(
                "tool.started",
                payload={
                    "tool_name": "mcp__filesystem__read_file",
                    "tool_use_id": "c2",
                },
            ),
            make_event(
                "tool.started", payload={"tool_name": "bash", "tool_use_id": "c3"}
            ),
            make_event(
                "tool.started",
                payload={"tool_name": "server__tool", "tool_use_id": "c4"},
            ),
            make_event(
                "tool.started",
                payload={"tool_name": "mcp__incomplete", "tool_use_id": "c5"},
            ),
        ]
        report = aggregate_aibom(events)
        by_name = components_by_name(report)
        server = by_name[("mcp_server", "filesystem")]
        assert server["events"] == 2
        assert by_name[("tool", "mcp__filesystem__read_file")]["events"] == 2
        # No invented MCP servers from ordinary/ambiguous tool names.
        assert sorted(name for kind, name in by_name if kind == "mcp_server") == [
            "filesystem"
        ]

    def test_explicit_mcp_server_field_is_observed(self):
        report = aggregate_aibom(
            [
                make_event(
                    "tool.started",
                    payload={
                        "tool_name": "read_file",
                        "tool_use_id": "c1",
                        "mcp_server": "filesystem",
                    },
                )
            ]
        )
        by_name = components_by_name(report)
        assert by_name[("mcp_server", "filesystem")]["events"] == 1
        assert by_name[("tool", "read_file")]["events"] == 1

    def test_artifacts_without_path_are_skipped(self):
        report = aggregate_aibom(
            [
                make_event("artifact.created", payload={"description": "no path"}),
                make_event("artifact.created", payload={"path": "   "}),
            ]
        )
        assert [
            item for item in report["components"] if item["type"] == "artifact"
        ] == []

    def test_no_secrets_or_contents_in_inventory(self):
        report = aggregate_aibom(
            [
                make_event(
                    "tool.started",
                    payload={
                        "tool_name": "bash",
                        "tool_use_id": "c1",
                        "prompt": "super-secret-prompt",
                        "command": "curl -H 'Authorization: Bearer secret'",
                        "result": "SECRET_CONTENT_TOKEN",
                    },
                )
            ]
        )
        serialized = json.dumps(report)
        assert "super-secret-prompt" not in serialized
        assert "Bearer secret" not in serialized
        assert "SECRET_CONTENT_TOKEN" not in serialized
        assert "bash" in serialized

    def test_tool_result_without_start_still_counts(self):
        report = aggregate_aibom(
            [
                make_event(
                    "tool.completed",
                    payload={"tool_name": "bash", "tool_use_id": "c9"},
                )
            ]
        )
        tool = components_by_name(report)[("tool", "bash")]
        assert tool["events"] == 1


# ---------------------------------------------------------------------------
# Bounded reader + report
# ---------------------------------------------------------------------------


class TestReport:
    @pytest.fixture
    def events_db(self, tmp_path):
        return make_events_db(
            tmp_path,
            [
                make_event(
                    "usage.reported",
                    payload={"model": "deepseek-flash"},
                    project="sugar",
                    ts="2026-11-01T10:00:00Z",
                ),
                make_event(
                    "tool.started",
                    payload={"tool_name": "bash", "tool_use_id": "c1"},
                    project="sugar",
                    ts="2026-11-02T10:00:00Z",
                ),
                make_event(
                    "usage.reported",
                    payload={"model": "gpt-5"},
                    project="other",
                    ts="2026-11-03T10:00:00Z",
                ),
                make_event(
                    "artifact.created",
                    payload={"path": "/tmp/a.md"},
                    project="sugar",
                    ts="2026-11-04T10:00:00Z",
                ),
            ],
        )

    def test_filters_project_and_since(self, events_db):
        report = run(aibom_report(events_db, project="sugar"))
        names = {(c["type"], c["name"]) for c in report["components"]}
        assert ("model", "deepseek-flash") in names
        assert ("model", "gpt-5") not in names
        assert report["scope"]["project"] == "sugar"
        assert report["events_scanned"] == 3

        since = run(aibom_report(events_db, since="2026-11-03T00:00:00Z"))
        names = {(c["type"], c["name"]) for c in since["components"]}
        assert ("model", "gpt-5") in names
        assert ("model", "deepseek-flash") not in names
        assert since["scope"]["since"] == "2026-11-03T00:00:00Z"

    def test_limit_is_bounded_and_truncated(self, events_db):
        report = run(aibom_report(events_db, limit=2))
        assert report["events_scanned"] == 2
        assert report["truncated"] is True
        assert report["limit"] == 2

    def test_invalid_arguments_raise(self, events_db):
        with pytest.raises(AibomQueryError):
            run(aibom_report(events_db, since="yesterday"))
        with pytest.raises(AibomQueryError):
            run(aibom_report(events_db, limit="abc"))

    def test_missing_database_is_empty(self, tmp_path):
        report = run(aibom_report(str(tmp_path / "missing.db")))
        assert report["components"] == []
        assert report["events_scanned"] == 0

    def test_reader_returns_oldest_first_and_decodes_payload(self, events_db):
        events, truncated = run(read_aibom_events(events_db, limit=100))
        assert truncated is False
        assert [
            event["payload"].get("tool_name")
            for event in events
            if event["kind"] == "tool.started"
        ] == ["bash"]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestAibomAPI:
    @pytest.fixture
    def client(self, tmp_path):
        app = create_app(
            str(tmp_path / "sugar.db"),
            events_db=str(tmp_path / "events.db"),
        )
        with TestClient(app) as test_client:
            yield test_client

    def post_events(self, client, events):
        for event in events:
            response = client.post("/api/events", json=event)
            assert response.status_code == 201, response.text

    def test_empty_inventory_200(self, client):
        response = client.get("/api/aibom")
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["schema"] == SCHEMA
        assert report["components"] == []
        assert report["limitations"]

    def test_inventory_shape_and_components(self, client):
        self.post_events(
            client,
            [
                make_event(
                    "usage.reported",
                    payload={"model": "deepseek-flash", "fidelity": "L2"},
                    session_id="s1",
                    project="sugar",
                ),
                make_event(
                    "tool.started",
                    payload={
                        "tool_name": "bash",
                        "tool_use_id": "c1",
                        "fidelity": "L3",
                    },
                    session_id="s1",
                ),
                make_event(
                    "artifact.created",
                    payload={"path": "/tmp/report.md", "fidelity": "L3"},
                    session_id="s1",
                    project="sugar",
                ),
            ],
        )
        response = client.get("/api/aibom")
        assert response.status_code == 200, response.text
        report = response.json()
        assert set(report) >= {
            "schema",
            "generated_at",
            "scope",
            "components",
            "coverage",
            "limitations",
        }
        names = {(c["type"], c["name"]) for c in report["components"]}
        assert ("model", "deepseek-flash") in names
        assert ("harness", "dsh") in names
        assert ("tool", "bash") in names
        assert ("artifact", "/tmp/report.md") in names
        assert report["coverage"]["by_harness"] == {"dsh": 3}
        assert report["coverage"]["by_fidelity"] == {"L2": 1, "L3": 2}

    def test_project_and_since_filters(self, client):
        self.post_events(
            client,
            [
                make_event(
                    "usage.reported",
                    payload={"model": "model-a"},
                    project="sugar",
                    ts="2026-11-01T10:00:00Z",
                ),
                make_event(
                    "usage.reported",
                    payload={"model": "model-b"},
                    project="other",
                    ts="2026-11-02T10:00:00Z",
                ),
            ],
        )
        report = client.get("/api/aibom", params={"project": "sugar"}).json()
        assert {c["name"] for c in report["components"]} == {"model-a", "dsh"}

        report = client.get(
            "/api/aibom", params={"since": "2026-11-02T00:00:00Z"}
        ).json()
        assert {c["name"] for c in report["components"]} == {"model-b", "dsh"}

    def test_limit_truncates(self, client):
        self.post_events(
            client,
            [
                make_event(
                    "artifact.created",
                    payload={"path": f"/tmp/{index}.md"},
                    ts=f"2026-11-09T10:0{index}:00Z",
                )
                for index in range(4)
            ],
        )
        report = client.get("/api/aibom", params={"limit": 2}).json()
        assert report["events_scanned"] == 2
        assert report["truncated"] is True

    def test_validation_errors_are_400(self, client):
        for params in ({"since": "not-a-date"}, {"limit": "abc"}, {"limit": "0"}):
            response = client.get("/api/aibom", params=params)
            assert response.status_code == 400, (params, response.text)
            assert response.json()["error"] == "validation_error"

    def test_spine_disabled_is_503(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"))
        with TestClient(app) as plain_client:
            response = plain_client.get("/api/aibom")
            assert response.status_code == 503
            assert response.json()["error"] == "spine_unavailable"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestAibomCLI:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def events_db(self, tmp_path):
        return make_events_db(
            tmp_path,
            [
                make_event(
                    "usage.reported",
                    payload={"model": "deepseek-flash"},
                    project="sugar",
                ),
                make_event(
                    "tool.started",
                    payload={"tool_name": "bash", "tool_use_id": "c1"},
                ),
                make_event(
                    "artifact.created",
                    payload={"path": "/tmp/report.md"},
                    project="sugar",
                ),
            ],
        )

    def invoke(self, runner, tmp_path, *args):
        return runner.invoke(
            cli, ["--config", str(cli_config(tmp_path)), "aibom", *args]
        )

    def test_json_output(self, runner, tmp_path, events_db):
        result = self.invoke(runner, tmp_path, "--db", events_db, "--json")
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert report["schema"] == SCHEMA
        assert report["events_scanned"] == 3
        names = {(c["type"], c["name"]) for c in report["components"]}
        assert ("model", "deepseek-flash") in names
        assert ("tool", "bash") in names
        assert ("artifact", "/tmp/report.md") in names
        assert report["limitations"]

    def test_events_db_alias_and_project_filter(self, runner, tmp_path, events_db):
        result = self.invoke(
            runner, tmp_path, "--events-db", events_db, "--project", "sugar", "--json"
        )
        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert report["scope"]["project"] == "sugar"
        names = {c["name"] for c in report["components"]}
        assert "deepseek-flash" in names
        assert "bash" not in names

    def test_table_output(self, runner, tmp_path, events_db):
        result = self.invoke(runner, tmp_path, "--db", events_db)
        assert result.exit_code == 0, result.output
        assert "TYPE" in result.output
        assert "NAME" in result.output
        assert "deepseek-flash" in result.output
        assert "Components:" in result.output
        assert "Coverage:" in result.output
        assert "Limitations:" in result.output
        assert "Observed runtime inventory only" in result.output
        assert "CycloneDX" in result.output

    def test_invalid_since_fails(self, runner, tmp_path, events_db):
        result = self.invoke(runner, tmp_path, "--db", events_db, "--since", "nope")
        assert result.exit_code != 0
        assert "ISO-8601" in result.output

    def test_empty_inventory_table(self, runner, tmp_path):
        db = make_events_db(tmp_path, [])
        result = self.invoke(runner, tmp_path, "--db", db)
        assert result.exit_code == 0, result.output
        assert "(no observed components)" in result.output


def test_format_aibom_table_empty_report():
    text = format_aibom_table(aggregate_aibom([]))
    assert "(no observed components)" in text
    assert "Limitations:" in text
