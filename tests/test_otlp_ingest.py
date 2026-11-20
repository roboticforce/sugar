"""Tests for the OTLP/HTTP JSON receiver (Phase 1a).

Covers tolerant OTLP decoding, span/log -> canonical telemetry mapping for ADK,
Goose, Codex and DSH, and the ``POST /v1/traces`` / ``POST /v1/logs`` endpoints
(content types, auth, body cap, redaction, dedupe, disabled spine).
"""

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app
from sugar.dashboard.security import MAX_BODY_BYTES, BoardToken, TokenRegistry
from sugar.dashboard.spine import EventSpine
from sugar.telemetry import otlp
from sugar.telemetry.otlp import (
    OTLPFormatError,
    decode_log_request,
    decode_trace_request,
    infer_harness,
    logs_to_events,
    spans_to_events,
)
from sugar.telemetry.schema import SCHEMA

START_NS = 1_700_000_000_000_000_000
END_NS = START_NS + 1_500_000_000


def any_value(value):
    """Build an OTLP AnyValue JSON object for a plain Python value."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, dict):
        return {
            "kvlistValue": {
                "values": [
                    {"key": str(key), "value": any_value(item)}
                    for key, item in value.items()
                ]
            }
        }
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [any_value(item) for item in value]}}
    return {"stringValue": str(value)}


def attrs(values):
    return [{"key": key, "value": any_value(value)} for key, value in values.items()]


def span(
    name,
    attributes=None,
    *,
    trace_id="a" * 32,
    span_id="b" * 16,
    parent_span_id="c" * 16,
    status_code=1,
    status_message=None,
    start_ns=START_NS,
    end_ns=END_NS,
):
    payload = {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent_span_id,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": attrs(attributes or {}),
        "status": {"code": status_code},
    }
    if status_message:
        payload["status"]["message"] = status_message
    return payload


def trace_payload(spans, resource=None, scope=None):
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": attrs(resource or {})},
                "scopeSpans": [
                    {
                        "scope": {"name": scope or "test"},
                        "spans": spans,
                    }
                ],
            }
        ]
    }


def log_record(
    body=None,
    attributes=None,
    *,
    event_name=None,
    trace_id="d" * 32,
    span_id="e" * 16,
    time_ns=START_NS,
):
    record = {
        "traceId": trace_id,
        "spanId": span_id,
        "timeUnixNano": str(time_ns),
        "attributes": attrs(attributes or {}),
    }
    if body is not None:
        if isinstance(body, dict) and not any(key.endswith("Value") for key in body):
            record["body"] = {"stringValue": json.dumps(body)}
        else:
            record["body"] = body if isinstance(body, dict) else any_value(body)
    if event_name:
        record["eventName"] = event_name
    return record


def log_payload(records, resource=None, scope=None):
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": attrs(resource or {})},
                "scopeLogs": [
                    {
                        "scope": {"name": scope or "test"},
                        "logRecords": records,
                    }
                ],
            }
        ]
    }


def adk_execute_tool_span(**overrides):
    attributes = {
        "gen_ai.tool.name": "Bash",
        "gen_ai.tool.call.id": "call-adk-1",
        "session.id": "sess-adk",
        "cwd": "/repo",
        "gen_ai.agent.name": "adk-agent",
        "gen_ai.tool.arguments": {"command": "pytest tests/"},
    }
    attributes.update(overrides)
    return span("execute_tool", attributes)


def goose_agent_span():
    return span(
        "invoke_agent",
        {
            "gen_ai.agent.name": "goose-agent",
            "session.id": "sess-goose",
            "cwd": "/repo",
        },
        span_id="f" * 16,
    )


def codex_tool_span(status_code=2):
    return span(
        "function_span",
        {
            "tool.name": "shell",
            "gen_ai.tool.call.id": "call-codex-1",
            "session.id": "sess-codex",
            "error": "exit code 1" if status_code == 2 else None,
        },
        status_code=status_code,
        status_message="command failed",
        trace_id="1" * 32,
        span_id="2" * 16,
    )


def model_span(with_tokens=True):
    attributes = {
        "gen_ai.request.model": "gpt-test",
        "session.id": "sess-model",
    }
    if with_tokens:
        attributes.update(
            {
                "gen_ai.usage.input_tokens": 1200,
                "gen_ai.usage.output_tokens": 300,
            }
        )
    return span("chat", attributes, trace_id="3" * 32, span_id="4" * 16)


def dsh_log(body, **kwargs):
    return log_record(body=body, **kwargs)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


class TestDecode:
    def test_decode_trace_request_flattens_and_decodes_values(self):
        payload = trace_payload(
            [
                span(
                    "execute_tool",
                    {
                        "gen_ai.tool.name": "Bash",
                        "nested": {"a": 1},
                        "count": "17",
                        "items": [1, 2],
                    },
                )
            ],
            resource={"service.name": "goose"},
        )
        decoded = decode_trace_request(payload)
        assert len(decoded) == 1
        entry = decoded[0]
        assert entry["resource_attributes"]["service.name"] == "goose"
        assert entry["span"]["name"] == "execute_tool"
        assert entry["span"]["trace_id"] == "a" * 32
        assert entry["span"]["parent_span_id"] == "c" * 16
        assert entry["span"]["duration_ms"] == 1500
        assert entry["span"]["ts"] == "2023-11-14T22:13:20Z"
        assert entry["span"]["attributes"]["nested"] == {"a": 1}
        assert entry["span"]["attributes"]["count"] == "17"
        assert entry["span"]["attributes"]["items"] == [1, 2]

    def test_decode_trace_tolerates_empty_and_odd_entries(self):
        assert decode_trace_request({}) == []
        assert decode_trace_request({"resourceSpans": None}) == []
        payload = {
            "resourceSpans": [
                None,
                {"resource": None, "scopeSpans": [None, {"spans": [None, {}]}]},
            ]
        }
        decoded = decode_trace_request(payload)
        assert len(decoded) == 1  # the empty span still decodes

    def test_decode_errors(self):
        with pytest.raises(OTLPFormatError):
            decode_trace_request([])
        with pytest.raises(OTLPFormatError):
            decode_trace_request({"resourceSpans": "nope"})
        with pytest.raises(OTLPFormatError):
            decode_log_request([])
        with pytest.raises(OTLPFormatError):
            decode_log_request({"resourceLogs": {}})

    def test_decode_log_request_json_body(self):
        payload = log_payload(
            [dsh_log({"type": "session-log", "event": "tool/call"})],
            resource={"service.name": "dsh"},
        )
        decoded = decode_log_request(payload)
        assert len(decoded) == 1
        record = decoded[0]["record"]
        assert decoded[0]["resource_attributes"]["service.name"] == "dsh"
        assert record["body"]["event"] == "tool/call"
        assert record["ts"] == "2023-11-14T22:13:20Z"

    def test_missing_attributes_tolerated(self):
        payload = trace_payload([{"name": "execute_tool"}])
        events = spans_to_events(payload)
        assert len(events) == 1
        assert events[0]["kind"] == "tool.completed"


# ---------------------------------------------------------------------------
# Span mapping
# ---------------------------------------------------------------------------


class TestSpanMapping:
    def test_adk_execute_tool_span(self):
        events = spans_to_events(
            trace_payload(
                [adk_execute_tool_span()],
                resource={"service.name": "adk", "gen_ai.agent.name": "adk"},
            )
        )
        assert len(events) == 1
        event = events[0]
        payload = event["payload"]
        assert event["kind"] == "tool.completed"
        assert event["harness"] == "adk"
        assert event["actor"] == "adk-agent"
        assert event["session_id"] == "sess-adk"
        assert event["trace_id"] == "a" * 32
        assert payload["schema"] == SCHEMA
        assert payload["span_id"] == "b" * 16
        assert payload["parent_span_id"] == "c" * 16
        assert payload["tool_name"] == "Bash"
        assert payload["tool_use_id"] == "call-adk-1"
        assert payload["status"] == "completed"
        assert payload["duration_ms"] == 1500
        assert payload["cwd"] == "/repo"
        assert payload["fidelity"] == "L3"
        assert payload["payload"]["tool_input"] == {"command": "pytest tests/"}

    def test_goose_execute_tool_and_invoke_agent(self):
        events = spans_to_events(
            trace_payload(
                [adk_execute_tool_span(), goose_agent_span()],
                resource={"service.name": "goose"},
            )
        )
        assert [event["kind"] for event in events] == ["tool.completed", "run.ended"]
        assert events[0]["harness"] == "goose"
        run_event = events[1]
        assert run_event["harness"] == "goose"
        assert run_event["payload"]["fidelity"] == "L1"
        assert run_event["payload"]["status"] == "completed"
        assert run_event["payload"]["duration_ms"] == 1500
        assert run_event["actor"] == "goose-agent"

    def test_codex_tool_error(self):
        events = spans_to_events(
            trace_payload([codex_tool_span(2)], resource={"service.name": "codex"})
        )
        event = events[0]
        assert event["kind"] == "tool.failed"
        assert event["harness"] == "codex"
        assert event["payload"]["tool_name"] == "shell"
        assert event["payload"]["status"] == "failed"
        assert event["payload"]["payload"]["message"] == "command failed"

    def test_model_usage_span(self):
        events = spans_to_events(
            trace_payload([model_span(True)], resource={"service.name": "codex"})
        )
        event = events[0]
        assert event["kind"] == "usage.reported"
        assert event["payload"]["model"] == "gpt-test"
        assert event["payload"]["fidelity"] == "L2"
        summary = event["payload"]["payload"]
        assert summary["tokens_in"] == 1200
        assert summary["tokens_out"] == 300
        assert summary["model"] == "gpt-test"

    def test_model_span_without_tokens_is_run(self):
        events = spans_to_events(trace_payload([model_span(False)]))
        assert events[0]["kind"] == "run.ended"
        assert events[0]["payload"]["fidelity"] == "L1"

    def test_unknown_span_is_ignored(self):
        events = spans_to_events(trace_payload([span("some.other.thing", {})]))
        assert events == []

    def test_harness_hint_wins_and_inference(self):
        assert infer_harness("goose", "adk") == "goose"
        assert infer_harness(None, "google-adk") == "adk"
        assert infer_harness(None, "codex-cli") == "codex"
        assert infer_harness(None, "unknown-thing") == "unknown"
        assert infer_harness("claude", None) == "claude-code"
        payload = trace_payload(
            [adk_execute_tool_span()], resource={"service.name": "custom-service"}
        )
        events = spans_to_events(payload, harness_hint="goose")
        assert events[0]["harness"] == "goose"


# ---------------------------------------------------------------------------
# Log mapping
# ---------------------------------------------------------------------------


class TestLogMapping:
    def test_dsh_session_log_tool_call_and_result(self):
        call = dsh_log(
            {
                "type": "session-log",
                "session_id": "sess-dsh",
                "event": "tool/call",
                "data": {
                    "name": "Bash",
                    "call_id": "call-dsh-1",
                    "arguments": {"command": "pytest -q"},
                },
            },
            trace_id="9" * 32,
            span_id="8" * 16,
        )
        result = dsh_log(
            {
                "type": "session-log",
                "session_id": "sess-dsh",
                "event": "tool/result",
                "data": {
                    "name": "Bash",
                    "call_id": "call-dsh-1",
                    "result": {"stdout": "ok"},
                },
            },
            trace_id="9" * 32,
            span_id="8" * 16,
        )
        events = logs_to_events(
            log_payload([call, result], resource={"service.name": "dsh"})
        )
        assert [event["kind"] for event in events] == [
            "tool.started",
            "tool.completed",
        ]
        started = events[0]
        assert started["harness"] == "dsh"
        assert started["session_id"] == "sess-dsh"
        assert started["trace_id"] == "9" * 32
        assert started["payload"]["tool_name"] == "Bash"
        assert started["payload"]["tool_use_id"] == "call-dsh-1"
        assert started["payload"]["status"] == "started"
        assert started["payload"]["fidelity"] == "L3"
        assert started["payload"]["payload"]["tool_input"] == {"command": "pytest -q"}
        completed = events[1]
        assert completed["payload"]["status"] == "completed"
        assert completed["payload"]["payload"]["tool_result"] == {"stdout": "ok"}

    def test_dsh_session_log_tool_failure(self):
        record = dsh_log(
            {
                "type": "session-log",
                "session_id": "sess-dsh",
                "event": "tool/result",
                "data": {
                    "name": "Bash",
                    "call_id": "call-dsh-2",
                    "error": "exit 1",
                },
            }
        )
        events = logs_to_events(log_payload([record], resource={"service.name": "dsh"}))
        assert events[0]["kind"] == "tool.failed"
        assert events[0]["payload"]["status"] == "failed"

    def test_dsh_session_and_turn_logs(self):
        records = [
            dsh_log(
                {"type": "session-log", "event": "session/start", "session_id": "s"}
            ),
            dsh_log({"type": "session-log", "event": "turn/end", "session_id": "s"}),
            dsh_log({"type": "session-log", "event": "session/end", "session_id": "s"}),
        ]
        events = logs_to_events(log_payload(records, resource={"service.name": "dsh"}))
        assert [event["kind"] for event in events] == [
            "session.started",
            "turn.ended",
            "session.ended",
        ]

    def test_gen_ai_event_name_logs(self):
        record = log_record(
            body="tool finished",
            attributes={
                "event.name": "tool/result",
                "gen_ai.tool.name": "Bash",
                "gen_ai.tool.call.id": "call-log-1",
                "session.id": "sess-log",
            },
        )
        events = logs_to_events(
            log_payload([record], resource={"service.name": "codex"})
        )
        assert len(events) == 1
        event = events[0]
        assert event["kind"] == "tool.completed"
        assert event["harness"] == "codex"
        assert event["payload"]["tool_name"] == "Bash"
        assert event["payload"]["tool_use_id"] == "call-log-1"

    def test_gen_ai_usage_log(self):
        record = log_record(
            body="model usage",
            attributes={
                "gen_ai.request.model": "gpt-test",
                "gen_ai.usage.input_tokens": "100",
                "gen_ai.usage.output_tokens": "20",
            },
        )
        events = logs_to_events(log_payload([record], resource={"service.name": "adk"}))
        assert len(events) == 1
        assert events[0]["kind"] == "usage.reported"
        assert events[0]["payload"]["payload"]["tokens_in"] == 100
        assert events[0]["payload"]["payload"]["tokens_out"] == 20

    def test_unmapped_log_is_ignored(self):
        events = logs_to_events(
            log_payload([log_record(body="hello", attributes={"foo": "bar"})])
        )
        assert events == []


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


def make_app(tmp_path, **kwargs):
    return create_app(
        str(tmp_path / "sugar.db"),
        events_db=str(tmp_path / "events.db"),
        ingest_socket="",
        **kwargs,
    )


def stored_events(events_db):
    async def go():
        spine = EventSpine(str(events_db), retention_days=0)
        await spine.start()
        try:
            events, _ = await spine.list_events(limit=100)
            return events
        finally:
            await spine.close()

    return asyncio.run(go())


class TestOTLPEndpoints:
    def test_traces_endpoint_stores_canonical_events(self, tmp_path):
        app = make_app(tmp_path)
        payload = trace_payload(
            [adk_execute_tool_span()], resource={"service.name": "adk"}
        )
        with TestClient(app) as client:
            response = client.post("/v1/traces", json=payload)
            assert response.status_code == 200
            assert response.json() == {}
        events = stored_events(tmp_path / "events.db")
        assert len(events) == 1
        event = events[0]
        assert event["kind"] == "tool.completed"
        assert event["harness"] == "adk"
        assert event["source"] == "otlp:traces"
        assert event["payload"]["schema"] == SCHEMA
        assert event["trace_id"] == "a" * 32
        assert event["payload"]["span_id"] == "b" * 16

    def test_logs_endpoint_stores_events(self, tmp_path):
        app = make_app(tmp_path)
        payload = log_payload(
            [
                dsh_log(
                    {
                        "type": "session-log",
                        "session_id": "sess-dsh",
                        "event": "tool/call",
                        "data": {"name": "Bash", "call_id": "call-dsh"},
                    }
                )
            ],
            resource={"service.name": "dsh"},
        )
        with TestClient(app) as client:
            response = client.post("/v1/logs", json=payload)
            assert response.status_code == 200
            assert response.json() == {}
        events = stored_events(tmp_path / "events.db")
        assert len(events) == 1
        assert events[0]["kind"] == "tool.started"
        assert events[0]["source"] == "otlp:logs"

    def test_protobuf_content_type_is_415_with_hint(self, tmp_path):
        app = make_app(tmp_path)
        with TestClient(app) as client:
            response = client.post(
                "/v1/traces",
                content=b"\x0a\x00",
                headers={"Content-Type": "application/x-protobuf"},
            )
            assert response.status_code == 415
            assert "http/json" in response.text
            assert "OTEL_EXPORTER_OTLP_PROTOCOL" in response.text

    def test_missing_content_type_is_415(self, tmp_path):
        app = make_app(tmp_path)
        with TestClient(app) as client:
            response = client.post(
                "/v1/traces", content=json.dumps(trace_payload([])).encode()
            )
            assert response.status_code == 415

    def test_malformed_json_is_400(self, tmp_path):
        app = make_app(tmp_path)
        with TestClient(app) as client:
            response = client.post(
                "/v1/traces",
                content="{not json",
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 400
            assert response.json()["error"] == "validation_error"

    def test_non_object_json_is_400(self, tmp_path):
        app = make_app(tmp_path)
        with TestClient(app) as client:
            response = client.post("/v1/traces", json=[])
            assert response.status_code == 400

    def test_structurally_invalid_otlp_is_400(self, tmp_path):
        app = make_app(tmp_path)
        with TestClient(app) as client:
            response = client.post("/v1/traces", json={"resourceSpans": "nope"})
            assert response.status_code == 400
            assert response.json()["error"] == "validation_error"

    def test_empty_export_is_200(self, tmp_path):
        app = make_app(tmp_path)
        with TestClient(app) as client:
            assert client.post("/v1/traces", json={}).status_code == 200
            assert client.post("/v1/logs", json={}).status_code == 200
        assert stored_events(tmp_path / "events.db") == []

    def test_disabled_spine_is_503(self, tmp_path):
        app = create_app(str(tmp_path / "sugar.db"), ingest_socket="")
        with TestClient(app) as client:
            response = client.post("/v1/traces", json={})
            assert response.status_code == 503
            assert response.json()["error"] == "spine_unavailable"

    def test_token_mode_requires_bearer_write_token(self, tmp_path):
        tokens = TokenRegistry(
            [
                BoardToken("reader", "read", "read-token"),
                BoardToken("writer", "write", "write-token"),
            ]
        )
        app = make_app(tmp_path, tokens=tokens)
        payload = trace_payload([adk_execute_tool_span()])
        with TestClient(app) as client:
            assert client.post("/v1/traces", json=payload).status_code == 401
            read = client.post(
                "/v1/traces",
                json=payload,
                headers={"Authorization": "Bearer read-token"},
            )
            assert read.status_code == 403
            write = client.post(
                "/v1/traces",
                json=payload,
                headers={"Authorization": "Bearer write-token"},
            )
            assert write.status_code == 200
        assert len(stored_events(tmp_path / "events.db")) == 1

    def test_body_cap(self, tmp_path):
        app = make_app(tmp_path)
        with TestClient(app) as client:
            response = client.post(
                "/v1/traces",
                content=b"{" + b" " * (MAX_BODY_BYTES + 100) + b"}",
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(MAX_BODY_BYTES + 102),
                },
            )
            assert response.status_code == 413

    def test_retry_dedupes_events(self, tmp_path):
        app = make_app(tmp_path)
        payload = trace_payload([adk_execute_tool_span()])
        with TestClient(app) as client:
            assert client.post("/v1/traces", json=payload).status_code == 200
            assert client.post("/v1/traces", json=payload).status_code == 200
        assert len(stored_events(tmp_path / "events.db")) == 1

    def test_log_retry_dedupes_events(self, tmp_path):
        app = make_app(tmp_path)
        payload = log_payload(
            [
                dsh_log(
                    {
                        "type": "session-log",
                        "session_id": "sess-dsh",
                        "event": "tool/call",
                        "data": {"name": "Bash", "call_id": "call-dsh-retry"},
                    }
                )
            ],
            resource={"service.name": "dsh"},
        )
        with TestClient(app) as client:
            assert client.post("/v1/logs", json=payload).status_code == 200
            assert client.post("/v1/logs", json=payload).status_code == 200
        assert len(stored_events(tmp_path / "events.db")) == 1

    def test_redaction_applies_to_otlp_summary(self, tmp_path):
        app = make_app(tmp_path)
        secret = "ghp_" + "z" * 30
        payload = trace_payload(
            [
                adk_execute_tool_span(
                    **{"gen_ai.tool.arguments": {"command": f"export TOKEN={secret}"}}
                )
            ]
        )
        with TestClient(app) as client:
            assert client.post("/v1/traces", json=payload).status_code == 200
        event = stored_events(tmp_path / "events.db")[0]
        summary = event["payload"]["payload"]
        text = json.dumps(summary)
        assert secret not in text
        assert "[REDACTED]" in text

    def test_harness_query_hint(self, tmp_path):
        app = make_app(tmp_path)
        payload = trace_payload(
            [adk_execute_tool_span()], resource={"service.name": "custom-service"}
        )
        with TestClient(app) as client:
            response = client.post("/v1/traces?harness=goose", json=payload)
            assert response.status_code == 200
        event = stored_events(tmp_path / "events.db")[0]
        assert event["harness"] == "goose"

    def test_logs_usage_via_endpoint(self, tmp_path):
        app = make_app(tmp_path)
        record = log_record(
            body="usage",
            attributes={
                "gen_ai.request.model": "m",
                "gen_ai.usage.input_tokens": "10",
                "gen_ai.usage.output_tokens": "5",
            },
        )
        payload = log_payload([record], resource={"service.name": "adk"})
        with TestClient(app) as client:
            assert client.post("/v1/logs", json=payload).status_code == 200
        event = stored_events(tmp_path / "events.db")[0]
        assert event["kind"] == "usage.reported"
        assert event["payload"]["payload"]["tokens_in"] == 10

    def test_events_visible_via_activity_api(self, tmp_path):
        app = make_app(tmp_path)
        payload = trace_payload([adk_execute_tool_span()])
        with TestClient(app) as client:
            client.post("/v1/traces", json=payload)
            activity = client.get("/api/activity?limit=10").json()
        assert len(activity["events"]) == 1
        assert activity["events"][0]["kind"] == "tool.completed"


def test_otlp_module_public_helpers():
    assert otlp.json_content_type("application/json; charset=utf-8") is True
    assert otlp.json_content_type("application/x-protobuf") is False
    assert otlp.json_content_type(None) is False
    assert otlp.PROTOBUF_HINT
