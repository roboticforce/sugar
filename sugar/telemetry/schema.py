"""Canonical telemetry envelope (``sugar.telemetry.v1``) for the event spine.

Canonical events reuse the existing spine columns for identity/routing and put
the extended Phase-0 fields under ``payload``. See ``docs/local-dashboard.md``
§11 for the spine itself; this module is the schema owner for telemetry.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

SCHEMA = "sugar.telemetry.v1"
FIDELITIES = ("L0", "L1", "L2", "L3")
DEFAULT_FIDELITY = "L1"
FIDELITY_RANK = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}

RUN_KINDS = ("run.started", "run.ended")
TURN_KINDS = ("turn.started", "turn.ended")
TOOL_KINDS = ("tool.started", "tool.completed", "tool.failed")
SESSION_KINDS = ("session.started", "session.ended")
USAGE_KINDS = ("usage.reported",)
EVIDENCE_KINDS = (
    "verification.test_run",
    "verification.check_result",
    "verification.review",
    "policy.decision",
    "approval.granted",
    "approval.denied",
    "artifact.created",
    "commit.linked",
)
TELEMETRY_KINDS = RUN_KINDS + TURN_KINDS + TOOL_KINDS + SESSION_KINDS + USAGE_KINDS
ALL_KINDS = TELEMETRY_KINDS + EVIDENCE_KINDS

KIND_GROUPS: Dict[str, tuple] = {
    "run": RUN_KINDS,
    "turn": TURN_KINDS,
    "tool": TOOL_KINDS,
    "session": SESSION_KINDS,
    "usage": USAGE_KINDS,
    "evidence": EVIDENCE_KINDS,
}


class TelemetryValidationError(ValueError):
    """Invalid canonical telemetry payload (surfaced as 400 for evidence)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_iso_timestamp(
    value: Any, *, default: Optional[str] = None
) -> Optional[str]:
    """Normalize an ISO-8601 value to ``...Z``; fall back to *default* when bad."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return default
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_fidelity(value: Any, default: str = DEFAULT_FIDELITY) -> str:
    if isinstance(value, str):
        text = value.strip().upper()
        if text in FIDELITIES:
            return text
    return default


def normalize_kind(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise TelemetryValidationError(
            "kind is required and must be a non-empty string"
        )
    return text[:64]


def _text(value: Any, limit: int = 200) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _duration_ms(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number)


def build_envelope(
    *,
    kind: Any,
    harness: Any,
    summary: Optional[Mapping[str, Any]] = None,
    actor: Any = None,
    actor_kind: Any = "agent",
    session_id: Any = None,
    task_id: Any = None,
    run_id: Any = None,
    project: Any = None,
    trace_id: Any = None,
    turn_id: Any = None,
    span_id: Any = None,
    parent_span_id: Any = None,
    tool_use_id: Any = None,
    tool_name: Any = None,
    status: Any = None,
    duration_ms: Any = None,
    cwd: Any = None,
    model: Any = None,
    fidelity: Any = DEFAULT_FIDELITY,
    ts: Any = None,
    source: str = "telemetry",
) -> Dict[str, Any]:
    """Build the spine-ready canonical event body (``payload`` is unredacted)."""
    kind_text = normalize_kind(kind)
    harness_text = _text(harness, 64)
    if not harness_text:
        raise TelemetryValidationError("harness is required")
    if summary is not None and not isinstance(summary, Mapping):
        raise TelemetryValidationError("payload summary must be a JSON object")
    event_ts = normalize_iso_timestamp(ts, default=now_iso())
    assert event_ts is not None
    fidelity_text = normalize_fidelity(fidelity)
    payload = {
        "schema": SCHEMA,
        "turn_id": _text(turn_id),
        "span_id": _text(span_id),
        "parent_span_id": _text(parent_span_id),
        "tool_use_id": _text(tool_use_id),
        "tool_name": _text(tool_name),
        "status": _text(status, 40),
        "duration_ms": _duration_ms(duration_ms),
        "cwd": _text(cwd, 1000),
        "model": _text(model),
        "fidelity": fidelity_text,
        "ts": event_ts,
        "payload": dict(summary) if summary is not None else {},
    }
    return {
        "kind": kind_text,
        "actor": _text(actor) or harness_text,
        "actor_kind": _text(actor_kind, 20) or "agent",
        "harness": harness_text,
        "session_id": _text(session_id),
        "task_id": _text(task_id),
        "run_id": _text(run_id),
        "project": _text(project),
        "trace_id": _text(trace_id),
        "ts": event_ts,
        "source": _text(source, 40) or "telemetry",
        "payload": payload,
    }


def build_evidence_event(body: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate/normalize a canonical evidence event body (strict kind list)."""
    if not isinstance(body, Mapping):
        raise TelemetryValidationError("evidence event must be a JSON object")
    kind = body.get("kind")
    if kind not in EVIDENCE_KINDS:
        raise TelemetryValidationError(
            "evidence kind must be one of: " + ", ".join(EVIDENCE_KINDS)
        )
    harness = body.get("harness") or "sugar"
    raw_payload = body.get("payload")
    if raw_payload is not None and not isinstance(raw_payload, Mapping):
        raise TelemetryValidationError("evidence payload must be a JSON object")
    # Evidence may already be in canonical envelope form (schema inside payload).
    summary = dict(raw_payload) if raw_payload is not None else {}
    extended = summary.get("schema") == SCHEMA
    if extended:
        # Allow an evidence event shaped like the canonical envelope: pull the
        # extended fields up so the rebuilt envelope stays canonical.
        for field in (
            "turn_id",
            "span_id",
            "parent_span_id",
            "tool_use_id",
            "tool_name",
            "status",
            "duration_ms",
            "cwd",
            "model",
            "fidelity",
            "ts",
        ):
            if field in summary and field not in body:
                body = {**body, field: summary.get(field)}
        summary = (
            summary.get("payload") if isinstance(summary.get("payload"), dict) else {}
        )
    return build_envelope(
        kind=kind,
        harness=harness,
        summary=summary,
        actor=body.get("actor"),
        actor_kind=body.get("actor_kind", "agent"),
        session_id=body.get("session_id"),
        task_id=body.get("task_id"),
        run_id=body.get("run_id"),
        project=body.get("project"),
        trace_id=body.get("trace_id"),
        turn_id=body.get("turn_id"),
        span_id=body.get("span_id"),
        parent_span_id=body.get("parent_span_id"),
        tool_use_id=body.get("tool_use_id"),
        tool_name=body.get("tool_name"),
        status=body.get("status"),
        duration_ms=body.get("duration_ms"),
        cwd=body.get("cwd"),
        model=body.get("model"),
        fidelity=body.get("fidelity", "L3"),
        ts=body.get("ts"),
        source=str(body.get("source") or "telemetry:evidence"),
    )


def validate_envelope(body: Any) -> Dict[str, Any]:
    """Validate an already-built canonical envelope; returns it normalized.

    Raises :class:`TelemetryValidationError` when the schema/kind/fidelity or
    payload structure is invalid. Used by tests/tools that already have an
    envelope and want the same checks the ingest path applies.
    """
    if not isinstance(body, Mapping):
        raise TelemetryValidationError("telemetry envelope must be a JSON object")
    normalized = dict(body)
    normalized["kind"] = normalize_kind(body.get("kind"))
    harness = _text(body.get("harness"), 64)
    if not harness:
        raise TelemetryValidationError("harness is required")
    normalized["harness"] = harness
    payload = body.get("payload")
    if not isinstance(payload, Mapping):
        raise TelemetryValidationError("payload must be a JSON object")
    if payload.get("schema") != SCHEMA:
        raise TelemetryValidationError(f"payload.schema must be {SCHEMA!r}")
    fidelity = payload.get("fidelity")
    if fidelity not in FIDELITIES:
        raise TelemetryValidationError(
            "payload.fidelity must be one of: " + ", ".join(FIDELITIES)
        )
    nested = payload.get("payload")
    if nested is not None and not isinstance(nested, Mapping):
        raise TelemetryValidationError("payload.payload must be a JSON object")
    ts = normalize_iso_timestamp(body.get("ts") or payload.get("ts"))
    if ts is None:
        raise TelemetryValidationError("ts must be an ISO-8601 timestamp")
    normalized["ts"] = ts
    return normalized


def is_canonical_payload(payload: Any) -> bool:
    return isinstance(payload, Mapping) and payload.get("schema") == SCHEMA


def envelope_fidelity(event: Mapping[str, Any]) -> Optional[str]:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("fidelity")
    return value if value in FIDELITIES else None


def status_for_kind(kind: str) -> Optional[str]:
    if kind == "tool.started":
        return "started"
    if kind == "tool.completed":
        return "completed"
    if kind == "tool.failed":
        return "failed"
    return None


def payload_to_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), separators=(",", ":"))
