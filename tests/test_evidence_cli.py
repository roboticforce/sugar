"""Tests for the ``sugar evidence`` CLI and its stdlib board client."""

import json
import socket
import urllib.error

import pytest
from click.testing import CliRunner

from sugar.activity import ActivityError
from sugar.evidence.cli import (
    _request_json,
    build_record_body,
    parse_payload_items,
)
from sugar.main import cli


class FakeResponse:
    def __init__(self, data=None, status=200):
        self._data = json.dumps(data if data is not None else {}).encode()
        self.status = status

    def read(self, amount=-1):
        return self._data if amount < 0 else self._data[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def runner():
    return CliRunner()


def invoke(runner, args, **kwargs):
    return runner.invoke(cli, ["evidence", *args], **kwargs)


class TestRecordCommand:
    def test_parses_payload_items(self):
        assert parse_payload_items(
            ["check=pytest", "tokens=12", "ok=true", 'meta={"a":1}']
        ) == {"check": "pytest", "tokens": 12, "ok": True, "meta": {"a": 1}}
        with pytest.raises(Exception):
            parse_payload_items(["missing-equals"])

    def test_build_record_body(self):
        body = build_record_body(
            kind="verification.check_result",
            status="passed",
            task_id="t_1",
            harness="claude-code",
            check="pytest",
            command="pytest -q",
            commit="abc123",
            title="tests",
            summary="all green",
            payload_items=["tokens=12", "custom=value"],
        )
        assert body["kind"] == "verification.check_result"
        assert body["status"] == "passed"
        assert body["task_id"] == "t_1"
        assert body["harness"] == "claude-code"
        payload = body["payload"]
        assert payload["status"] == "passed"
        assert payload["check"] == "pytest"
        assert payload["command"] == "pytest -q"
        assert payload["commit"] == "abc123"
        assert payload["title"] == "tests"
        assert payload["summary"] == "all green"
        assert payload["tokens"] == 12
        assert payload["custom"] == "value"

    def test_record_posts_canonical_body_and_prints(self, runner, monkeypatch):
        captured = {}

        def fake_record(body, *, server=None, token=None):
            captured["body"] = body
            captured["server"] = server
            return {
                "id": 7,
                "kind": body["kind"],
                "harness": "claude-code",
                "payload": {"status": "passed"},
            }

        monkeypatch.setattr("sugar.evidence.cli.record_evidence", fake_record)
        result = invoke(
            runner,
            [
                "record",
                "--kind",
                "verification.check_result",
                "--status",
                "passed",
                "--task-id",
                "t_123",
                "--harness",
                "claude-code",
                "--check",
                "pytest",
                "--command",
                "pytest -q",
                "--commit",
                "abc123",
                "--payload",
                "tokens=12",
                "--url",
                "http://board.test:7331",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "7" in result.output
        assert "verification.check_result" in result.output
        assert "passed" in result.output
        assert captured["server"] == "http://board.test:7331"
        body = captured["body"]
        assert body["payload"]["tokens"] == 12
        assert body["payload"]["check"] == "pytest"
        assert body["status"] == "passed"

    def test_record_json_summary(self, runner, monkeypatch):
        monkeypatch.setattr(
            "sugar.evidence.cli.record_evidence",
            lambda body, **kwargs: {
                "id": 9,
                "kind": body["kind"],
                "payload": {"payload": {"status": "failed"}},
            },
        )
        result = invoke(
            runner,
            [
                "record",
                "--kind",
                "verification.test_run",
                "--status",
                "failed",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data == {"id": 9, "kind": "verification.test_run", "status": "failed"}

    def test_record_usage_errors_exit_2(self, runner):
        assert invoke(runner, ["record"]).exit_code == 2
        assert invoke(runner, ["record", "--kind", "not-a-kind"]).exit_code == 2
        assert (
            invoke(
                runner,
                [
                    "record",
                    "--kind",
                    "verification.check_result",
                    "--payload",
                    "invalid",
                ],
            ).exit_code
            == 2
        )

    def test_record_board_failure_exits_1(self, runner, monkeypatch):
        def fail(body, **kwargs):
            raise ActivityError("cannot reach the Sugar board at http://x")

        monkeypatch.setattr("sugar.evidence.cli.record_evidence", fail)
        result = invoke(runner, ["record", "--kind", "verification.check_result"])
        assert result.exit_code == 1
        assert "cannot reach" in result.output

    def test_record_bad_url_is_config_error_exit_2(self, runner, monkeypatch):
        monkeypatch.setenv("SUGAR_BOARD_URL", "ftp://not-http")
        result = invoke(runner, ["record", "--kind", "verification.check_result"])
        assert result.exit_code == 2
        assert "invalid board URL" in result.output

    def test_record_env_url_and_token_headers(self, runner, monkeypatch):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            return FakeResponse(
                {
                    "event": {
                        "id": 11,
                        "kind": request.data and "verification.check_result",
                        "payload": {"status": "passed"},
                    }
                },
                status=201,
            )

        monkeypatch.setenv("SUGAR_BOARD_URL", "http://env-board:8123")
        monkeypatch.setenv("SUGAR_BOARD_TOKEN", "secret-token")
        monkeypatch.setattr("sugar.evidence.cli.urllib.request.urlopen", fake_urlopen)
        result = invoke(
            runner,
            ["record", "--kind", "verification.check_result", "--status", "passed"],
        )
        assert result.exit_code == 0, result.output
        assert captured["url"] == "http://env-board:8123/api/evidence"
        assert captured["authorization"] == "Bearer secret-token"
        assert "secret-token" not in result.output


class TestListCommand:
    def test_list_table(self, runner, monkeypatch):
        captured = {}

        def fake_fetch(query, *, server=None, token=None):
            captured["query"] = query
            return {
                "events": [
                    {
                        "id": 1,
                        "ts": "2026-01-01T10:00:00Z",
                        "kind": "verification.check_result",
                        "harness": "claude-code",
                        "task_id": "t_1",
                        "payload": {"payload": {"status": "passed", "check": "pytest"}},
                    }
                ],
                "last_id": 1,
                "summary": {"total": 1, "by_status": {"passed": 1}, "by_kind": {}},
            }

        monkeypatch.setattr("sugar.evidence.cli.fetch_evidence", fake_fetch)
        result = invoke(
            runner,
            [
                "list",
                "--since",
                "2026-01-01",
                "--task-id",
                "t_1",
                "--kind",
                "verification.check_result",
                "--status",
                "passed",
                "--limit",
                "5",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "TIME" in result.output
        assert "KIND" in result.output
        assert "STATUS" in result.output
        assert "HARNESS" in result.output
        assert "TASK" in result.output
        assert "SUMMARY" in result.output
        assert "verification.check_result" in result.output
        assert "pytest" in result.output
        assert captured["query"]["limit"] == 5
        assert captured["query"]["task_id"] == "t_1"
        assert captured["query"]["status"] == "passed"
        assert captured["query"]["since"] == "2026-01-01T00:00:00Z"

    def test_list_json_is_raw_response(self, runner, monkeypatch):
        response = {
            "events": [{"id": 1, "kind": "commit.linked"}],
            "last_id": 1,
            "summary": {"total": 1, "by_status": {}, "by_kind": {"commit.linked": 1}},
        }
        monkeypatch.setattr(
            "sugar.evidence.cli.fetch_evidence",
            lambda query, **kwargs: response,
        )
        result = invoke(runner, ["list", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.output) == response

    def test_list_empty_and_invalid_filters(self, runner, monkeypatch):
        monkeypatch.setattr(
            "sugar.evidence.cli.fetch_evidence",
            lambda query, **kwargs: {
                "events": [],
                "last_id": 0,
                "summary": {"total": 0, "by_status": {}, "by_kind": {}},
            },
        )
        result = invoke(runner, ["list"])
        assert result.exit_code == 0
        assert "No evidence found" in result.output
        assert invoke(runner, ["list", "--since", "not-a-time"]).exit_code == 2
        assert invoke(runner, ["list", "--kind", "bogus"]).exit_code == 2
        assert invoke(runner, ["list", "--status", "bogus"]).exit_code == 2
        assert invoke(runner, ["list", "--limit", "0"]).exit_code == 2
        assert invoke(runner, ["list", "--limit", "501"]).exit_code == 2

    def test_list_board_failure_exits_1(self, runner, monkeypatch):
        def fail(query, **kwargs):
            raise ActivityError("board rejected the request")

        monkeypatch.setattr("sugar.evidence.cli.fetch_evidence", fail)
        result = invoke(runner, ["list"])
        assert result.exit_code == 1
        assert "board rejected" in result.output


class TestSummaryCommand:
    def test_summary_human(self, runner, monkeypatch):
        captured = {}

        def fake_fetch(query, *, server=None, token=None):
            captured["query"] = query
            return {
                "events": [],
                "last_id": 0,
                "summary": {
                    "total": 3,
                    "by_status": {"failed": 1, "passed": 2},
                    "by_kind": {"verification.check_result": 3},
                },
            }

        monkeypatch.setattr("sugar.evidence.cli.fetch_evidence", fake_fetch)
        result = invoke(
            runner, ["summary", "--since", "2026-01-01", "--task-id", "t_1"]
        )
        assert result.exit_code == 0, result.output
        assert "total: 3" in result.output
        assert "failed=1" in result.output
        assert "passed=2" in result.output
        assert "verification.check_result=3" in result.output
        assert captured["query"]["limit"] == 1
        assert captured["query"]["task_id"] == "t_1"

    def test_summary_json(self, runner, monkeypatch):
        response = {
            "events": [],
            "last_id": 0,
            "summary": {"total": 0, "by_status": {}, "by_kind": {}},
        }
        monkeypatch.setattr(
            "sugar.evidence.cli.fetch_evidence", lambda query, **kwargs: response
        )
        result = invoke(runner, ["summary", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.output) == response

    def test_summary_board_failure_exits_1(self, runner, monkeypatch):
        def fail(query, **kwargs):
            raise ActivityError("unreachable")

        monkeypatch.setattr("sugar.evidence.cli.fetch_evidence", fail)
        assert invoke(runner, ["summary"]).exit_code == 1


class TestBoardClientAndCommands:
    def test_unreachable_board_is_clear(self, runner, monkeypatch):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.URLError(socket.timeout("timed out"))

        monkeypatch.setattr("sugar.evidence.cli.urllib.request.urlopen", fake_urlopen)
        result = invoke(runner, ["list", "--url", "http://127.0.0.1:1"])
        assert result.exit_code == 1
        assert "timed out" in result.output
        assert "http://127.0.0.1:1" in result.output

    def test_http_error_detail_is_safe(self, runner, monkeypatch):
        class FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__(
                    "http://board/api/evidence",
                    400,
                    "Bad Request",
                    {},
                    None,
                )

            def read(self, amount=-1):
                return json.dumps(
                    {"error": "validation_error", "detail": "bad since"}
                ).encode()

        def fake_urlopen(request, timeout=None):
            raise FakeHTTPError()

        monkeypatch.setattr("sugar.evidence.cli.urllib.request.urlopen", fake_urlopen)
        result = invoke(runner, ["list"])
        assert result.exit_code == 1
        assert "HTTP 400" in result.output
        assert "bad since" in result.output

    def test_request_json_builds_query_and_post_body(self, monkeypatch):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["body"] = request.data
            return FakeResponse({"ok": True})

        monkeypatch.setattr("sugar.evidence.cli.urllib.request.urlopen", fake_urlopen)
        query_result = _request_json(
            "GET",
            "/api/evidence",
            query={"limit": 2, "task_id": "t_1"},
            server="http://b",
        )
        assert query_result == {"ok": True}
        assert captured["url"] == "http://b/api/evidence?limit=2&task_id=t_1"
        assert captured["method"] == "GET"
        post_result = _request_json(
            "POST", "/api/evidence", body={"kind": "commit.linked"}, server="http://b"
        )
        assert post_result == {"ok": True}
        assert json.loads(captured["body"]) == {"kind": "commit.linked"}

    def test_evidence_group_registered(self, runner):
        result = runner.invoke(cli, ["evidence", "--help"])
        assert result.exit_code == 0
        assert "record" in result.output
        assert "list" in result.output
        assert "summary" in result.output
