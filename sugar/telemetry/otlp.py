"""OTLP/HTTP receiver mapping (Phase 1a JSON; Phase 1b protobuf + gzip).

Decodes OTLP ``ExportTraceServiceRequest`` / ``ExportLogsServiceRequest`` JSON or
protobuf into canonical ``sugar.telemetry.v1`` envelopes and feeds them through
the existing telemetry pipeline components (redaction, dedupe, spine append, SSE
activity).

Protobuf support is optional (the ``[telemetry]`` extra) and is imported lazily
so the rest of Sugar stays importable without it. Gzip decompression is bounded
so malicious bodies cannot expand past the normal OTLP body cap.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import zlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import redact
from .normalize import normalize_harness_name
from .schema import SCHEMA, build_envelope, now_iso

logger = logging.getLogger(__name__)

PROTOBUF_AVAILABLE = False
PROTOBUF_IMPORT_ERROR = ""
try:  # optional [telemetry] extra
    from google.protobuf.json_format import MessageToDict
    from google.protobuf.message import DecodeError as ProtobufDecodeError
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
        ExportLogsServiceRequest,
    )
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    PROTOBUF_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - exercised when the extra is absent
    MessageToDict = None
    ProtobufDecodeError = None
    ExportLogsServiceRequest = None
    ExportTraceServiceRequest = None
    PROTOBUF_IMPORT_ERROR = str(exc)

PROTOBUF_INSTALL_HINT = (
    "OTLP protobuf support requires the telemetry extra: "
    "pip install 'sugarai[telemetry]'"
)
CONTENT_TYPE_HINT = (
    "OTLP/HTTP supports application/json, application/x-protobuf, "
    "application/protobuf and application/octet-stream "
    "(optionally Content-Encoding: gzip)"
)
# Backwards-compatible name for callers/tests from Phase 1a.
PROTOBUF_HINT = PROTOBUF_INSTALL_HINT
JSON_CONTENT_TYPE = "application/json"
PROTOBUF_CONTENT_TYPES = frozenset(
    {"application/x-protobuf", "application/protobuf", "application/octet-stream"}
)
DEFAULT_MAX_OTLP_BYTES = 1_000_000

_TOOL_SPAN_PREFIXES = ("execute_tool", "function_span", "tool_call", "tool.")
_AGENT_SPAN_PREFIXES = ("invoke_agent", "invoke_workflow")
_MODEL_SPAN_NAMES = ("chat", "generate_content", "stream_response_from_provider")

_KNOWN_HARNESS_HINTS = {
    "goose": "goose",
    "adk": "adk",
    "google-adk": "adk",
    "google_adk": "adk",
    "codex": "codex",
    "claude-code": "claude-code",
    "claude": "claude-code",
    "dsh": "dsh",
}


class OTLPFormatError(ValueError):
    """Structurally invalid OTLP JSON/protobuf/gzip (surfaced as HTTP 400)."""


class OTLPProtobufUnavailable(RuntimeError):
    """OTLP protobuf sent but the optional ``[telemetry]`` extra is absent."""


class OTLPUnsupportedEncoding(ValueError):
    """Unsupported ``Content-Encoding`` (surfaced as HTTP 415)."""


class OTLPBodyTooLarge(ValueError):
    """Gzip body expands past the OTLP body cap (surfaced as HTTP 413)."""


# ---------------------------------------------------------------------------
# OTLP JSON decoding helpers (tolerant)
# ---------------------------------------------------------------------------


def _parse_any_value(value: Any) -> Any:
    """Decode an OTLP ``AnyValue`` (or pass through a plain JSON value)."""
    if not isinstance(value, Mapping):
        return value
    if "stringValue" in value:
        return value.get("stringValue")
    if "boolValue" in value:
        return bool(value.get("boolValue"))
    if "intValue" in value:
        return _as_int(value.get("intValue"), default=value.get("intValue"))
    if "doubleValue" in value:
        return _as_float(value.get("doubleValue"), default=value.get("doubleValue"))
    if "bytesValue" in value:
        return value.get("bytesValue")
    if "arrayValue" in value:
        values = value.get("arrayValue")
        if isinstance(values, Mapping):
            values = values.get("values") or []
        return [_parse_any_value(item) for item in (values or [])]
    if "kvlistValue" in value:
        entries = value.get("kvlistValue")
        if isinstance(entries, Mapping):
            entries = entries.get("values") or []
        return _attributes_to_dict(entries)
    if "value" in value:
        return _parse_any_value(value.get("value"))
    return value


def _attributes_to_dict(attributes: Any) -> Dict[str, Any]:
    """Decode OTLP attribute lists (or a plain key->AnyValue mapping)."""
    result: Dict[str, Any] = {}
    if isinstance(attributes, Mapping):
        for key, value in attributes.items():
            result[str(key)] = _parse_any_value(value)
        return result
    if not isinstance(attributes, Sequence):
        return result
    for item in attributes:
        if not isinstance(item, Mapping):
            continue
        key = item.get("key")
        if not isinstance(key, str) or not key:
            continue
        result[key] = _parse_any_value(item.get("value"))
    return result


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if isinstance(value, bool):
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(value, bool):
        return default
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _nanos_to_iso(value: Any) -> Optional[str]:
    nanos = _as_int(value)
    if nanos is None or nanos < 0:
        return None
    try:
        moment = datetime.fromtimestamp(nanos / 1_000_000_000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _span_status(span: Mapping[str, Any]) -> Dict[str, Any]:
    status = span.get("status")
    if not isinstance(status, Mapping):
        return {"code": 0, "label": "unset", "message": None}
    code = _as_int(status.get("code"), 0) or 0
    label = {0: "unset", 1: "ok", 2: "error"}.get(code, "unset")
    message = status.get("message")
    return {
        "code": code,
        "label": label,
        "message": str(message)[:2000] if isinstance(message, str) else None,
    }


def _parse_span(span: Mapping[str, Any]) -> Dict[str, Any]:
    start_nanos = _as_int(span.get("startTimeUnixNano"))
    end_nanos = _as_int(span.get("endTimeUnixNano"))
    duration_ms: Optional[int] = None
    if start_nanos is not None and end_nanos is not None and end_nanos >= start_nanos:
        duration_ms = int((end_nanos - start_nanos) / 1_000_000)
    return {
        "trace_id": _lower_hex(span.get("traceId")),
        "span_id": _lower_hex(span.get("spanId")),
        "parent_span_id": _lower_hex(span.get("parentSpanId")),
        "name": str(span.get("name") or ""),
        "kind": span.get("kind"),
        "start_time_unix_nano": start_nanos,
        "end_time_unix_nano": end_nanos,
        "attributes": _attributes_to_dict(span.get("attributes")),
        "status": _span_status(span),
        "duration_ms": duration_ms,
        "ts": _nanos_to_iso(start_nanos) or _nanos_to_iso(end_nanos) or now_iso(),
    }


def _lower_hex(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text or None


def decode_trace_request(payload: Any) -> List[Dict[str, Any]]:
    """Flatten an OTLP trace export into ``{resource, span}`` entries."""
    if not isinstance(payload, Mapping):
        raise OTLPFormatError("OTLP trace payload must be a JSON object")
    resource_spans = payload.get("resourceSpans", [])
    if resource_spans is None:
        return []
    if not isinstance(resource_spans, list):
        raise OTLPFormatError("resourceSpans must be an array")
    decoded: List[Dict[str, Any]] = []
    for resource_span in resource_spans:
        if not isinstance(resource_span, Mapping):
            continue
        resource = resource_span.get("resource")
        resource_attrs = _attributes_to_dict(
            resource.get("attributes") if isinstance(resource, Mapping) else None
        )
        scope_spans = resource_span.get("scopeSpans", [])
        if scope_spans is None:
            continue
        if not isinstance(scope_spans, list):
            raise OTLPFormatError("scopeSpans must be an array")
        for scope_span in scope_spans:
            if not isinstance(scope_span, Mapping):
                continue
            scope = scope_span.get("scope")
            scope_attrs = _attributes_to_dict(
                scope.get("attributes") if isinstance(scope, Mapping) else None
            )
            spans = scope_span.get("spans", [])
            if spans is None:
                continue
            if not isinstance(spans, list):
                raise OTLPFormatError("spans must be an array")
            for span in spans:
                if not isinstance(span, Mapping):
                    continue
                decoded.append(
                    {
                        "resource_attributes": resource_attrs,
                        "scope_attributes": scope_attrs,
                        "span": _parse_span(span),
                    }
                )
    return decoded


def _parse_log_body(record: Mapping[str, Any]) -> Any:
    body = _parse_any_value(record.get("body"))
    if isinstance(body, str):
        text = body.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except ValueError:
                return body
    return body


def decode_log_request(payload: Any) -> List[Dict[str, Any]]:
    """Flatten an OTLP log export into ``{resource, record}`` entries."""
    if not isinstance(payload, Mapping):
        raise OTLPFormatError("OTLP log payload must be a JSON object")
    resource_logs = payload.get("resourceLogs", [])
    if resource_logs is None:
        return []
    if not isinstance(resource_logs, list):
        raise OTLPFormatError("resourceLogs must be an array")
    decoded: List[Dict[str, Any]] = []
    for resource_log in resource_logs:
        if not isinstance(resource_log, Mapping):
            continue
        resource = resource_log.get("resource")
        resource_attrs = _attributes_to_dict(
            resource.get("attributes") if isinstance(resource, Mapping) else None
        )
        scope_logs = resource_log.get("scopeLogs", [])
        if scope_logs is None:
            continue
        if not isinstance(scope_logs, list):
            raise OTLPFormatError("scopeLogs must be an array")
        for scope_log in scope_logs:
            if not isinstance(scope_log, Mapping):
                continue
            scope = scope_log.get("scope")
            scope_attrs = _attributes_to_dict(
                scope.get("attributes") if isinstance(scope, Mapping) else None
            )
            records = scope_log.get("logRecords", [])
            if records is None:
                continue
            if not isinstance(records, list):
                raise OTLPFormatError("logRecords must be an array")
            for record in records:
                if not isinstance(record, Mapping):
                    continue
                body = _parse_log_body(record)
                ts = _nanos_to_iso(record.get("timeUnixNano")) or _nanos_to_iso(
                    record.get("observedTimeUnixNano")
                )
                decoded.append(
                    {
                        "resource_attributes": resource_attrs,
                        "scope_attributes": scope_attrs,
                        "record": {
                            "trace_id": _lower_hex(record.get("traceId")),
                            "span_id": _lower_hex(record.get("spanId")),
                            "time_unix_nano": _as_int(record.get("timeUnixNano")),
                            "ts": ts or now_iso(),
                            "severity_number": record.get("severityNumber"),
                            "severity_text": record.get("severityText"),
                            "body": body,
                            "event_name": record.get("eventName"),
                            "attributes": _attributes_to_dict(record.get("attributes")),
                        },
                    }
                )
    return decoded


# ---------------------------------------------------------------------------
# Canonical event mapping
# ---------------------------------------------------------------------------


def _clean_harness(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "-")
    if not text:
        return "unknown"
    known = normalize_harness_name(text)
    return known or _KNOWN_HARNESS_HINTS.get(text, text[:64]) or "unknown"


def infer_harness(harness_hint: Optional[str], service_name: Any = None) -> str:
    """Infer canonical harness: explicit hint > service.name > ``unknown``."""
    if harness_hint and str(harness_hint).strip():
        return _clean_harness(harness_hint)
    service = str(service_name or "").strip().lower()
    if not service:
        return "unknown"
    if "goose" in service:
        return "goose"
    if "adk" in service or "google" in service:
        return "adk"
    if "codex" in service:
        return "codex"
    if "claude" in service:
        return "claude-code"
    if "dsh" in service or "deepseek" in service:
        return "dsh"
    return "unknown"


def _first_attr(attributes: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = attributes.get(key)
        if value is not None and value != "":
            return value
    return None


def _text(value: Any, limit: int = 500) -> Optional[str]:
    if value is None or isinstance(value, (Mapping, list, tuple)):
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return _text(value, 200)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return {
            str(key)[:120]: _jsonable(item, depth + 1) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item, depth + 1) for item in value]
    return _text(value, 200)


def _span_common(
    entry: Mapping[str, Any], harness_hint: Optional[str]
) -> Dict[str, Any]:
    resource_attrs = entry.get("resource_attributes") or {}
    scope_attrs = entry.get("scope_attributes") or {}
    span = entry.get("span") or {}
    attributes = {**scope_attrs, **resource_attrs, **(span.get("attributes") or {})}
    service_name = resource_attrs.get("service.name")
    harness = infer_harness(harness_hint, service_name)
    session_id = _first_attr(
        attributes,
        "session.id",
        "gen_ai.conversation.id",
        "conversation.id",
        "session_id",
    )
    cwd = _first_attr(attributes, "cwd", "process.cwd", "working_directory")
    model = _first_attr(
        attributes, "gen_ai.request.model", "gen_ai.response.model", "model"
    )
    actor = (
        _first_attr(attributes, "gen_ai.agent.name", "agent.name")
        or service_name
        or harness
    )
    return {
        "attributes": attributes,
        "harness": harness,
        "session_id": _text(session_id, 500),
        "cwd": _text(cwd, 1000),
        "model": _text(model, 200),
        "actor": _text(actor, 200) or harness,
        "trace_id": span.get("trace_id"),
        "span_id": span.get("span_id"),
        "parent_span_id": span.get("parent_span_id"),
        "duration_ms": span.get("duration_ms"),
        "ts": span.get("ts") or now_iso(),
        "status": span.get("status") or {},
        "span_name": span.get("name") or "",
    }


def _span_kind(common: Mapping[str, Any]) -> Optional[str]:
    span_name = str(common.get("span_name") or "").lower()
    attributes = common.get("attributes") or {}
    if span_name.startswith(_TOOL_SPAN_PREFIXES) or _first_attr(
        attributes, "gen_ai.tool.name", "tool.name"
    ):
        status = common.get("status") or {}
        failed = status.get("code") == 2 or bool(
            _first_attr(attributes, "error", "tool.error", "exception.type")
        )
        return "tool.failed" if failed else "tool.completed"
    if span_name.startswith(_AGENT_SPAN_PREFIXES):
        return "run.ended"
    if span_name in _MODEL_SPAN_NAMES or span_name.startswith(
        ("generate_content", "stream_response_from_provider")
    ):
        if (
            _first_attr(
                attributes,
                "gen_ai.usage.input_tokens",
                "gen_ai.usage.output_tokens",
                "usage.input_tokens",
                "usage.output_tokens",
            )
            is not None
        ):
            return "usage.reported"
        return "run.ended"
    return None


def _span_to_envelope(
    entry: Mapping[str, Any], harness_hint: Optional[str]
) -> Optional[Dict[str, Any]]:
    common = _span_common(entry, harness_hint)
    kind = _span_kind(common)
    if kind is None:
        return None
    attributes = common["attributes"]
    status = common.get("status") or {}
    if kind in ("tool.failed", "run.ended", "turn.ended", "session.ended"):
        status_label = "failed" if status.get("code") == 2 else "completed"
    elif kind in ("tool.completed", "tool.started"):
        status_label = (
            "failed"
            if status.get("code") == 2
            else ("started" if kind == "tool.started" else "completed")
        )
    elif kind in ("run.started", "turn.started", "session.started"):
        status_label = "started"
    else:
        status_label = "completed"
    summary: Dict[str, Any] = {
        "message": _text(status.get("message"), 2000)
        or _text(common.get("span_name"), 500),
        "attributes": _jsonable(attributes),
    }
    tool_name = None
    tool_use_id = None
    fidelity = "L1"
    if kind.startswith("tool."):
        tool_name = _text(
            _first_attr(attributes, "gen_ai.tool.name", "tool.name"), 200
        ) or _text(common.get("span_name"), 200)
        tool_use_id = _text(
            _first_attr(attributes, "gen_ai.tool.call.id", "tool.call.id"), 200
        ) or common.get("span_id")
        summary.update(
            {
                "tool_name": tool_name,
                "status": status_label,
                "tool_input": _jsonable(
                    _first_attr(
                        attributes,
                        "gen_ai.tool.arguments",
                        "tool.arguments",
                        "tool.input",
                        "input",
                    )
                ),
                "tool_result": _jsonable(
                    _first_attr(
                        attributes,
                        "gen_ai.tool.result",
                        "tool.result",
                        "tool.output",
                        "output",
                    )
                ),
            }
        )
        fidelity = "L3"
    elif kind == "usage.reported":
        summary.update(
            {
                "tokens_in": _as_int(
                    _first_attr(
                        attributes,
                        "gen_ai.usage.input_tokens",
                        "usage.input_tokens",
                        "tokens_in",
                    )
                ),
                "tokens_out": _as_int(
                    _first_attr(
                        attributes,
                        "gen_ai.usage.output_tokens",
                        "usage.output_tokens",
                        "tokens_out",
                    )
                ),
                "cost_usd": _as_float(
                    _first_attr(
                        attributes, "gen_ai.usage.cost", "usage.cost_usd", "cost_usd"
                    )
                ),
                "model": common.get("model"),
            }
        )
        status_label = "completed"
        fidelity = "L2"
    else:
        summary["status"] = status.get("label") or status_label
    return build_envelope(
        kind=kind,
        harness=common["harness"],
        summary={key: value for key, value in summary.items() if value is not None},
        actor=common["actor"],
        actor_kind="agent",
        session_id=common["session_id"],
        trace_id=common["trace_id"],
        turn_id=_first_attr(attributes, "gen_ai.conversation.id"),
        span_id=common["span_id"],
        parent_span_id=common["parent_span_id"],
        tool_use_id=tool_use_id,
        tool_name=tool_name,
        status=status_label,
        duration_ms=common["duration_ms"],
        cwd=common["cwd"],
        model=common["model"],
        fidelity=fidelity,
        ts=common["ts"],
        source="otlp",
    )


def spans_to_events(
    payload: Any, harness_hint: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Map an OTLP trace export to canonical telemetry envelopes."""
    events: List[Dict[str, Any]] = []
    for entry in decode_trace_request(payload):
        event = _span_to_envelope(entry, harness_hint)
        if event is not None:
            events.append(event)
    return events


_LOG_EVENT_ALIASES = {
    "tool/call": "tool.started",
    "tool_call": "tool.started",
    "tool/start": "tool.started",
    "tool_start": "tool.started",
    "tool/result": "tool.completed",
    "tool_result": "tool.completed",
    "tool/end": "tool.completed",
    "tool_end": "tool.completed",
    "tool/failure": "tool.failed",
    "tool/failed": "tool.failed",
    "tool/error": "tool.failed",
    "session/start": "session.started",
    "session_start": "session.started",
    "session/end": "session.ended",
    "session_end": "session.ended",
    "turn/start": "turn.started",
    "turn_start": "turn.started",
    "turn/end": "turn.ended",
    "turn_end": "turn.ended",
    "run/start": "run.started",
    "run_start": "run.started",
    "run/end": "run.ended",
    "run_end": "run.ended",
    "agent/start": "run.started",
    "agent_start": "run.started",
    "agent/end": "run.ended",
    "agent_end": "run.ended",
    "toolcall": "tool.started",
    "toolresult": "tool.completed",
    "toolfail": "tool.failed",
    "toolerror": "tool.failed",
    "sessionstart": "session.started",
    "sessionend": "session.ended",
    "turnstart": "turn.started",
    "turnend": "turn.ended",
    "runstart": "run.started",
    "runend": "run.ended",
    "usage": "usage.reported",
    "usage/reported": "usage.reported",
    "usage.reported": "usage.reported",
    "tool.call": "tool.started",
    "tool.result": "tool.completed",
    "tool.failure": "tool.failed",
    "tool.error": "tool.failed",
    "session.start": "session.started",
    "session.end": "session.ended",
    "turn.start": "turn.started",
    "turn.end": "turn.ended",
    "run.start": "run.started",
    "run.end": "run.ended",
    "agent.start": "run.started",
    "agent.end": "run.ended",
    "tool.completed": "tool.completed",
    "tool.started": "tool.started",
    "tool.failed": "tool.failed",
    "session.started": "session.started",
    "session.ended": "session.ended",
    "turn.started": "turn.started",
    "turn.ended": "turn.ended",
    "run.started": "run.started",
    "run.ended": "run.ended",
}


def _event_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _log_kind(body: Mapping[str, Any], record: Mapping[str, Any]) -> Optional[str]:
    candidates = [
        record.get("event_name"),
        body.get("event"),
        body.get("event_name"),
        body.get("kind"),
        body.get("type"),
        record.get("attributes", {}).get("event.name"),
        record.get("attributes", {}).get("event"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        key = _event_key(candidate)
        if key in _LOG_EVENT_ALIASES:
            return _LOG_EVENT_ALIASES[key]
        slash = key.replace("_", "/")
        if slash in _LOG_EVENT_ALIASES:
            return _LOG_EVENT_ALIASES[slash]
    return None


def _log_to_envelope(
    entry: Mapping[str, Any], harness_hint: Optional[str]
) -> Optional[Dict[str, Any]]:
    resource_attrs = entry.get("resource_attributes") or {}
    scope_attrs = entry.get("scope_attributes") or {}
    record = entry.get("record") or {}
    record_attrs = record.get("attributes") or {}
    attributes = {**scope_attrs, **resource_attrs, **record_attrs}
    body = record.get("body")
    body_map: Dict[str, Any] = {}
    inner: Dict[str, Any] = {}
    nested = body
    if isinstance(body, str):
        stripped = body.strip()
        if stripped.startswith("{"):
            try:
                nested = json.loads(stripped)
            except ValueError:
                nested = body
        elif not stripped:
            nested = {}
    if isinstance(nested, Mapping):
        body_map = dict(nested)
        data = body_map.get("data")
        inner = dict(data) if isinstance(data, Mapping) else {}
        merged = {**body_map, **inner}
    else:
        merged = {}

    # DSH session-log records: {"type":"session-log","event":"tool/call","data":{...}}
    kind = _log_kind({**body_map, **inner}, {**record, "attributes": attributes})
    if kind is None:
        if attributes.get("gen_ai.usage.input_tokens") is not None:
            kind = "usage.reported"
        elif (
            _first_attr(attributes, "gen_ai.tool.name", "tool.name")
            or _first_attr(merged, "name", "tool")
        ) and attributes.get("event.name") in (None, "tool/call", "tool/result"):
            kind = "tool.completed"
    if kind is None:
        # gen_ai log has no usable semantic event; drop it.
        return None

    service_name = resource_attrs.get("service.name")
    harness = infer_harness(harness_hint, service_name)
    session_id = _first_attr(
        attributes,
        "session.id",
        "gen_ai.conversation.id",
        "session_id",
    ) or _first_attr(body_map, "session_id", "sessionId")
    tool_value = merged.get("tool")
    nested_tool_name = (
        _first_attr(tool_value, "name", "tool_name")
        if isinstance(tool_value, Mapping)
        else None
    )
    tool_name = _text(
        _first_attr(attributes, "gen_ai.tool.name", "tool.name")
        or _first_attr(merged, "name", "tool_name")
        or nested_tool_name,
        200,
    )
    tool_use_id = _text(
        _first_attr(attributes, "gen_ai.tool.call.id", "tool.call.id")
        or _first_attr(merged, "call_id", "callId", "tool_call_id", "id"),
        200,
    ) or record.get("span_id")
    if kind.endswith(".failed"):
        status = "failed"
    elif kind.endswith(".completed") or kind.endswith(".ended"):
        status = (
            "failed" if _first_attr(merged, "error", "error_message") else "completed"
        )
    elif kind.endswith(".started"):
        status = "started"
    else:
        status = None
    if kind == "tool.failed" or _first_attr(merged, "error", "error_message"):
        if kind.startswith("tool."):
            kind = "tool.failed"
            status = "failed"
    summary: Dict[str, Any] = {
        "message": _text(
            _first_attr(merged, "message", "command", "name", "body")
            or body_map.get("message")
            or (body if isinstance(body, str) else None),
            2000,
        ),
        "attributes": _jsonable(attributes),
    }
    if body_map:
        summary["body"] = _jsonable(body_map)
    if kind.startswith("tool."):
        summary["tool_name"] = tool_name
        summary["tool_input"] = _jsonable(
            _first_attr(merged, "arguments", "input", "args", "parameters")
        )
        summary["tool_result"] = _jsonable(
            _first_attr(merged, "result", "output", "response")
        )
        summary["status"] = status
    if kind == "usage.reported":
        summary.update(
            {
                "tokens_in": _as_int(
                    _first_attr(attributes, "gen_ai.usage.input_tokens", "tokens_in")
                    or _first_attr(merged, "tokens_in", "input_tokens")
                ),
                "tokens_out": _as_int(
                    _first_attr(attributes, "gen_ai.usage.output_tokens", "tokens_out")
                    or _first_attr(merged, "tokens_out", "output_tokens")
                ),
                "cost_usd": _as_float(
                    _first_attr(attributes, "gen_ai.usage.cost", "cost_usd")
                    or _first_attr(merged, "cost_usd", "cost")
                ),
                "model": _text(_first_attr(attributes, "gen_ai.request.model"), 200)
                or _text(_first_attr(merged, "model"), 200),
            }
        )
    fidelity = (
        "L3" if kind.startswith("tool.") else "L2" if kind == "usage.reported" else "L1"
    )
    return build_envelope(
        kind=kind,
        harness=harness,
        summary={key: value for key, value in summary.items() if value is not None},
        actor=_text(
            _first_attr(attributes, "gen_ai.agent.name", "agent.name")
            or _first_attr(merged, "actor", "agent")
            or service_name
            or harness,
            200,
        ),
        actor_kind="agent",
        session_id=_text(session_id, 500),
        trace_id=record.get("trace_id") or _text(_first_attr(merged, "trace_id"), 200),
        span_id=record.get("span_id") or _text(_first_attr(merged, "span_id"), 200),
        parent_span_id=_text(_first_attr(merged, "parent_span_id"), 200),
        tool_use_id=tool_use_id,
        tool_name=tool_name,
        status=status,
        cwd=_text(_first_attr(attributes, "cwd", "process.cwd"), 1000)
        or _text(_first_attr(merged, "cwd"), 1000),
        model=_text(
            _first_attr(attributes, "gen_ai.request.model")
            or _first_attr(merged, "model"),
            200,
        ),
        fidelity=fidelity,
        ts=record.get("ts") or now_iso(),
        source="otlp",
    )


def logs_to_events(
    payload: Any, harness_hint: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Map an OTLP log export to canonical telemetry envelopes."""
    events: List[Dict[str, Any]] = []
    for entry in decode_log_request(payload):
        event = _log_to_envelope(entry, harness_hint)
        if event is not None:
            events.append(event)
    return events


# ---------------------------------------------------------------------------
# Ingest through the shared pipeline components
# ---------------------------------------------------------------------------


def _stable_dedupe_id(envelope: Mapping[str, Any]) -> str:
    """Stable id for dedupe when the source has no span/tool id."""
    payload = envelope.get("payload") or {}
    basis = json.dumps(
        {
            "kind": envelope.get("kind"),
            "harness": envelope.get("harness"),
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


async def ingest_otlp_events(
    events: Sequence[Mapping[str, Any]],
    pipeline: Any,
    *,
    source: str = "otlp",
) -> int:
    """Redact -> dedupe -> append -> SSE for already-mapped envelopes."""
    stored = 0
    for envelope in events:
        try:
            payload = envelope["payload"]
            payload["payload"] = redact.redact_payload(
                payload.get("payload"),
                level=pipeline.redaction_level,
                max_bytes=pipeline.max_bytes,
            )
            envelope["source"] = source
            payload = envelope["payload"]
            dedupe_id = (
                payload.get("tool_use_id")
                or payload.get("span_id")
                or _stable_dedupe_id(envelope)
            )
            if pipeline.deduper.is_duplicate(
                envelope.get("harness") or "unknown",
                envelope.get("session_id"),
                dedupe_id,
                envelope.get("kind") or "",
            ):
                continue
            event = await pipeline.spine.append(envelope, source=source)
        except Exception:
            logger.warning("otlp: event ingest failed", exc_info=True)
            continue
        if pipeline.broadcaster is not None:
            try:
                pipeline.broadcaster.publish("activity", event)
            except Exception:  # pragma: no cover - broadcaster must not break ingest
                logger.debug("otlp: activity broadcast failed", exc_info=True)
        stored += 1
    return stored


# ---------------------------------------------------------------------------
# Protobuf + gzip wire support (Phase 1b)
# ---------------------------------------------------------------------------


def protobuf_available() -> bool:
    """True when ``opentelemetry-proto`` (the ``[telemetry]`` extra) is importable."""
    return PROTOBUF_AVAILABLE


def classify_content_type(content_type: Optional[str]) -> Optional[str]:
    """Return ``"json"``, ``"protobuf"`` or ``None`` for an OTLP content type."""
    if not content_type:
        return None
    main = content_type.split(";")[0].strip().lower()
    if main == JSON_CONTENT_TYPE:
        return "json"
    if main in PROTOBUF_CONTENT_TYPES:
        return "protobuf"
    return None


def normalize_content_encoding(content_encoding: Optional[str]) -> str:
    """Normalize ``Content-Encoding``; raise a 415-worthy error if unsupported."""
    encoding = (content_encoding or "").strip().lower()
    if encoding in ("", "identity"):
        return "identity"
    if encoding == "gzip":
        return "gzip"
    raise OTLPUnsupportedEncoding(
        f"unsupported Content-Encoding {content_encoding!r}; "
        "supported encodings: identity, gzip"
    )


def decompress_gzip(data: bytes, max_bytes: int = DEFAULT_MAX_OTLP_BYTES) -> bytes:
    """Decompress gzip with a hard output cap (zip-bomb resistant)."""
    if not isinstance(data, (bytes, bytearray)):
        raise OTLPFormatError("gzip body must be bytes")
    if max_bytes <= 0:
        raise OTLPBodyTooLarge("OTLP body cap must be positive")
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        output = decompressor.decompress(bytes(data), max_bytes + 1)
    except zlib.error as exc:
        raise OTLPFormatError("malformed gzip body") from exc
    if len(output) > max_bytes or decompressor.unconsumed_tail:
        raise OTLPBodyTooLarge(f"decompressed OTLP body exceeds {max_bytes} bytes")
    try:
        output += decompressor.flush()
    except zlib.error as exc:
        raise OTLPFormatError("malformed gzip body") from exc
    if len(output) > max_bytes:
        raise OTLPBodyTooLarge(f"decompressed OTLP body exceeds {max_bytes} bytes")
    if not decompressor.eof:
        raise OTLPFormatError("truncated gzip body")
    return output


def decode_content_encoding(
    body: bytes,
    content_encoding: Optional[str],
    max_bytes: int = DEFAULT_MAX_OTLP_BYTES,
) -> bytes:
    """Apply ``Content-Encoding`` with a bounded decompressed-size cap."""
    encoding = normalize_content_encoding(content_encoding)
    if encoding == "identity":
        return bytes(body)
    return decompress_gzip(body, max_bytes=max_bytes)


def _b64_id_to_hex(value: str) -> str:
    """Convert a protobuf-JSON base64 trace/span ID to the OTLP JSON hex form."""
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return value
    return raw.hex() if raw else value


def _convert_id_fields(item: Any, fields: Sequence[str]) -> None:
    if not isinstance(item, Mapping):
        return
    for field in fields:
        value = item.get(field)
        if isinstance(value, str) and value:
            item[field] = _b64_id_to_hex(value)


def _protobuf_ids_to_hex(data: Any) -> Any:
    """Rewrite MessageToDict base64 IDs to hex (the mapper expects OTLP JSON)."""
    if not isinstance(data, Mapping):
        return data
    for resource_spans in data.get("resourceSpans") or []:
        if not isinstance(resource_spans, Mapping):
            continue
        for scope_spans in resource_spans.get("scopeSpans") or []:
            if not isinstance(scope_spans, Mapping):
                continue
            for span in scope_spans.get("spans") or []:
                _convert_id_fields(span, ("traceId", "spanId", "parentSpanId"))
    for resource_logs in data.get("resourceLogs") or []:
        if not isinstance(resource_logs, Mapping):
            continue
        for scope_logs in resource_logs.get("scopeLogs") or []:
            if not isinstance(scope_logs, Mapping):
                continue
            for record in scope_logs.get("logRecords") or []:
                _convert_id_fields(record, ("traceId", "spanId"))
    return data


def _require_protobuf() -> None:
    if not PROTOBUF_AVAILABLE:
        raise OTLPProtobufUnavailable(PROTOBUF_INSTALL_HINT)


def decode_trace_protobuf(body: bytes) -> Dict[str, Any]:
    """Parse an ``ExportTraceServiceRequest`` into the OTLP-JSON mapper shape."""
    _require_protobuf()
    try:
        message = ExportTraceServiceRequest.FromString(bytes(body))
        data = MessageToDict(message, use_integers_for_enums=True)
    except (ProtobufDecodeError, TypeError, ValueError) as exc:
        raise OTLPFormatError("invalid OTLP protobuf trace body") from exc
    return _protobuf_ids_to_hex(data)


def decode_log_protobuf(body: bytes) -> Dict[str, Any]:
    """Parse an ``ExportLogsServiceRequest`` into the OTLP-JSON mapper shape."""
    _require_protobuf()
    try:
        message = ExportLogsServiceRequest.FromString(bytes(body))
        data = MessageToDict(message, use_integers_for_enums=True)
    except (ProtobufDecodeError, TypeError, ValueError) as exc:
        raise OTLPFormatError("invalid OTLP protobuf log body") from exc
    return _protobuf_ids_to_hex(data)


def json_content_type(content_type: Optional[str]) -> bool:
    """True for ``application/json`` (with optional parameters)."""
    if not content_type:
        return False
    return content_type.split(";")[0].strip().lower() == "application/json"


__all__ = [
    "CONTENT_TYPE_HINT",
    "DEFAULT_MAX_OTLP_BYTES",
    "JSON_CONTENT_TYPE",
    "OTLPBodyTooLarge",
    "OTLPFormatError",
    "OTLPProtobufUnavailable",
    "OTLPUnsupportedEncoding",
    "PROTOBUF_AVAILABLE",
    "PROTOBUF_CONTENT_TYPES",
    "PROTOBUF_HINT",
    "PROTOBUF_INSTALL_HINT",
    "SCHEMA",
    "classify_content_type",
    "decode_content_encoding",
    "decode_log_protobuf",
    "decode_log_request",
    "decode_trace_protobuf",
    "decode_trace_request",
    "decompress_gzip",
    "ingest_otlp_events",
    "infer_harness",
    "json_content_type",
    "logs_to_events",
    "normalize_content_encoding",
    "protobuf_available",
    "spans_to_events",
]
