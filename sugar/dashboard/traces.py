"""Trace/span model over the flat event spine (Trace Explorer backend).

Events are grouped by ``session_id`` into traces; each trace is a small tree of
session -> turn -> tool/MCP/LLM/approval/artifact spans. Everything is derived
on read from the append-only spine (no new schema), bounded in both scan size and
payload depth.

Frozen HTTP contract (UI task-33 consumes it):

``GET /api/traces?limit=20&project=&harness=&since=``
    ``{"traces": [summary, ...]}``
``GET /api/traces/{trace_id}``
    ``{"trace": summary, "spans": [span, ...]}``

A summary looks like ``{"trace_id": "dsh:<session>", "harness": "dsh",
"project": ..., "cwd": ..., "title": ..., "started_at": ..., "ended_at": ...,
"duration_ms": ..., "events": N, "turns": N, "tool_calls": N,
"models": [...], "tokens_total": N, "status": "ok|failed|running"}``.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import aiosqlite

DEFAULT_TRACE_LIMIT = 20
MAX_TRACE_LIMIT = 100
DEFAULT_EVENT_SCAN = 20_000
MAX_EVENT_SCAN = 50_000

# Bounded payload projection: Trace Explorer must never ship a 20 MB event.
MAX_STRING = 1_000
MAX_ITEMS = 20
MAX_DEPTH = 4
MAX_SPAN_NAME = 300
MAX_ID = 300
MAX_INPUT_OUTPUT_KEYS = 30

SESSION_KINDS = ("session.started", "session.ended")
TURN_KINDS = ("turn.started", "turn.ended")
TOOL_START_KIND = "tool.started"
TOOL_END_KINDS = ("tool.completed", "tool.failed")
USAGE_KINDS = ("usage.reported", "run.ended")
APPROVAL_KINDS = ("approval.granted", "approval.denied", "policy.decision")
ARTIFACT_KINDS = ("artifact.created", "commit.linked")
SUBAGENT_KINDS = ("subagent.started",)
TEAM_TASK_KINDS = ("team.task",)
TASK_KINDS = ("task.created", "task.updated", "task.completed", "task.failed")

TRACE_STATUSES = ("ok", "failed", "running")


class TraceQueryError(ValueError):
    """Invalid trace query parameter (surfaced as HTTP 400)."""


# ---------------------------------------------------------------------------
# Small tolerant projection helpers
# ---------------------------------------------------------------------------


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _payload(event: Mapping[str, Any]) -> Dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    return _mapping(payload)


def _summary(event: Mapping[str, Any]) -> Dict[str, Any]:
    """Canonical envelope summary (``payload.payload``) for one event."""
    payload = _payload(event)
    nested = payload.get("payload")
    return _mapping(nested)


def _first_text(*values: Any, limit: int = MAX_STRING) -> Optional[str]:
    for value in values:
        if value is None or isinstance(value, (Mapping, list, tuple)):
            continue
        text = str(value).strip()
        if text:
            return text[:limit]
    return None


def _field(event: Mapping[str, Any], *keys: str) -> Any:
    """Look up a field in the canonical envelope, its summary, then the event."""
    payload = _payload(event)
    nested = _mapping(payload.get("payload"))
    for source in (payload, nested):
        for key in keys:
            if key in source and source[key] not in (None, ""):
                return source[key]
    for key in keys:
        if key in event and event[key] not in (None, ""):
            return event[key]
    return None


def _session_id(event: Mapping[str, Any]) -> Optional[str]:
    return _first_text(
        event.get("session_id"),
        _payload(event).get("session_id"),
        _summary(event).get("session_id"),
        limit=MAX_ID,
    )


def _harness(event: Mapping[str, Any]) -> str:
    value = _first_text(
        event.get("harness"),
        _payload(event).get("harness"),
        _summary(event).get("harness"),
        limit=MAX_ID,
    )
    return value or "unknown"


def _model(event: Mapping[str, Any]) -> Optional[str]:
    return _first_text(
        _field(event, "model", "model_name"),
        limit=MAX_ID,
    )


def _cwd(event: Mapping[str, Any]) -> Optional[str]:
    return _first_text(
        _field(event, "cwd", "working_directory"),
        limit=MAX_STRING,
    )


def _project(event: Mapping[str, Any]) -> Optional[str]:
    value = _first_text(
        event.get("project"),
        _field(event, "project", "project_id"),
        limit=MAX_ID,
    )
    if value:
        return value
    cwd = _cwd(event)
    if cwd:
        cleaned = cwd.rstrip("/\\")
        if cleaned:
            return cleaned.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:MAX_ID] or None
    return None


def _event_status(event: Mapping[str, Any]) -> Optional[str]:
    value = _field(event, "status")
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _event_turn_id(event: Mapping[str, Any]) -> Optional[str]:
    value = _field(event, "turn_id", "turn")
    if isinstance(value, Mapping):
        return None
    return _first_text(value, limit=MAX_ID)


def _event_tool_name(event: Mapping[str, Any]) -> Optional[str]:
    return _first_text(
        _field(event, "tool_name", "name", "tool"),
        limit=MAX_SPAN_NAME,
    )


def _event_tool_use_id(event: Mapping[str, Any]) -> Optional[str]:
    return _first_text(
        _field(event, "tool_use_id", "call_id", "tool_call_id", "id"),
        limit=MAX_ID,
    )


def _event_span_id(event: Mapping[str, Any]) -> Optional[str]:
    return _first_text(_field(event, "span_id"), limit=MAX_ID)


def _event_ts(event: Mapping[str, Any]) -> Optional[str]:
    ts = event.get("ts")
    return str(ts) if ts else None


def _event_id(event: Mapping[str, Any]) -> Optional[int]:
    value = event.get("id")
    return value if isinstance(value, int) else None


_PATH_KEYS = frozenset(
    {"path", "file", "file_path", "filepath", "artifact_path", "artifactpath"}
)
_PATH_LIST_KEYS = frozenset(
    {
        "paths",
        "files",
        "artifacts",
        "diffs",
        "diff",
        "write_scopes",
        "writescopes",
    }
)
_PATH_WALK_KEYS = frozenset(
    {"meta", "payload", "result", "output", "data", "arguments", "input", "summary"}
)


def _normalize_session_key(value: Any) -> Optional[str]:
    """Canonical session key for graph joins (strip DSH's ``session-`` prefix)."""
    text = _first_text(value, limit=MAX_ID)
    if not text:
        return None
    return text[8:] if text.startswith("session-") else text


def _add_path(out: List[str], value: Any) -> None:
    if not isinstance(value, str):
        return
    text = value.strip()[:MAX_STRING]
    if text and text not in out and len(out) < MAX_ITEMS:
        out.append(text)


def _collect_paths(
    value: Any, *, depth: int = 0, out: Optional[List[str]] = None
) -> List[str]:
    """Extract bounded file paths from tool/artifact payload shapes."""
    paths = out if out is not None else []
    if depth > MAX_DEPTH or len(paths) >= MAX_ITEMS:
        return paths
    if isinstance(value, Mapping):
        for key, item in list(value.items())[:MAX_ITEMS]:
            lower = str(key).lower()
            if lower in _PATH_KEYS:
                if isinstance(item, str):
                    _add_path(paths, item)
                elif isinstance(item, Mapping):
                    for path_key in ("path", "file", "file_path", "filepath", "name"):
                        _add_path(paths, item.get(path_key))
                elif isinstance(item, (list, tuple)):
                    for entry in item[:MAX_ITEMS]:
                        if isinstance(entry, str):
                            _add_path(paths, entry)
                        elif isinstance(entry, Mapping):
                            for path_key in (
                                "path",
                                "file",
                                "file_path",
                                "filepath",
                            ):
                                _add_path(paths, entry.get(path_key))
            elif lower in _PATH_LIST_KEYS:
                if isinstance(item, str):
                    _add_path(paths, item)
                elif isinstance(item, (list, tuple)):
                    for entry in item[:MAX_ITEMS]:
                        if isinstance(entry, str):
                            _add_path(paths, entry)
                        elif isinstance(entry, Mapping):
                            for path_key in (
                                "path",
                                "file",
                                "file_path",
                                "filepath",
                            ):
                                _add_path(paths, entry.get(path_key))
            elif lower in _PATH_WALK_KEYS:
                _collect_paths(item, depth=depth + 1, out=paths)
    elif isinstance(value, (list, tuple)):
        for item in value[:MAX_ITEMS]:
            _collect_paths(item, depth=depth + 1, out=paths)
    return paths


def _event_paths(event: Mapping[str, Any]) -> List[str]:
    paths: List[str] = []
    _collect_paths(_summary(event), out=paths)
    _collect_paths(_payload(event), out=paths)
    return paths


def _artifact_key(event: Mapping[str, Any]) -> str:
    paths = _event_paths(event)
    description = _first_text(_field(event, "description"), limit=MAX_STRING) or ""
    sha = _first_text(_field(event, "sha256", "digest"), limit=MAX_STRING) or ""
    commit = _first_text(_field(event, "commit", "sha"), limit=MAX_STRING) or ""
    if event.get("kind") == "commit.linked":
        return "commit:" + (commit or str(_event_id(event) or ""))
    if paths:
        # One graph node per file path (plus content hash), even if a producer
        # re-emits the same artifact with a different description.
        return "artifact:" + "|".join(paths) + "|" + sha
    return "artifact:" + description + "|" + sha


def _artifact_records(
    event: Mapping[str, Any], paths: Sequence[str]
) -> List[Dict[str, Any]]:
    description = _first_text(_field(event, "description"), limit=MAX_STRING)
    sha = _first_text(_field(event, "sha256", "digest"), limit=MAX_STRING)
    tool_call_id = _first_text(
        _field(event, "tool_call_id", "tool_use_id", "call_id"), limit=MAX_ID
    )
    operation = _first_text(_field(event, "operation"), limit=MAX_SPAN_NAME)
    records: List[Dict[str, Any]] = []
    for path in paths[:MAX_ITEMS]:
        record: Dict[str, Any] = {"path": path}
        if description:
            record["description"] = description
        if sha:
            record["sha256"] = sha
        if tool_call_id:
            record["tool_call_id"] = tool_call_id
        if operation:
            record["operation"] = operation
        records.append(record)
    return records


def _team_task_fields(event: Mapping[str, Any]) -> Dict[str, Any]:
    nested_task = _field(event, "task")
    task_map = nested_task if isinstance(nested_task, Mapping) else {}
    nested_team = _field(event, "team")
    team_map = nested_team if isinstance(nested_team, Mapping) else {}
    team_id = _first_text(
        _field(event, "team_id", "teamId"), team_map.get("id"), limit=MAX_ID
    )
    team_task_id = _first_text(
        _field(event, "team_task_id", "teamTaskId", "task_id"),
        task_map.get("id"),
        limit=MAX_ID,
    )
    subject = _first_text(
        _field(event, "subject", "title", "name"),
        task_map.get("subject"),
        task_map.get("title"),
        limit=MAX_SPAN_NAME,
    )
    status = (
        _first_text(_event_status(event), task_map.get("status"), limit=MAX_SPAN_NAME)
        or "ok"
    ).lower()
    owner_id = _first_text(
        _field(event, "owner_id", "ownerId"), task_map.get("ownerId"), limit=MAX_ID
    )
    owner_name = _first_text(
        _field(event, "owner_name", "ownerName"), limit=MAX_SPAN_NAME
    )
    parent_session = _first_text(
        _field(event, "parent_session_id", "parent_session"),
        team_map.get("teamId"),
        limit=MAX_ID,
    )
    blocked_raw = _field(event, "blocked_by", "blockedBy")
    if blocked_raw is None:
        blocked_raw = task_map.get("blockedBy") or task_map.get("blocked_by")
    blocked_by: List[str] = []
    if isinstance(blocked_raw, (list, tuple)):
        for item in blocked_raw[:MAX_ITEMS]:
            value = _first_text(item, limit=MAX_ID)
            if value and value not in blocked_by:
                blocked_by.append(value)
    elif blocked_raw not in (None, ""):
        value = _first_text(blocked_raw, limit=MAX_ID)
        if value:
            blocked_by.append(value)
    return {
        "team_id": team_id,
        "team_task_id": team_task_id,
        "subject": subject,
        "status": status,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "parent_session_id": parent_session,
        "blocked_by": blocked_by,
    }


def _span_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in ("failed", "error", "denied"):
        return "failed"
    if text == "blocked":
        return "blocked"
    if text in (
        "started",
        "running",
        "active",
        "in_progress",
        "in-progress",
        "pending",
    ):
        return "running"
    return "ok"


def _team_task_status(value: str) -> str:
    if value in ("failed", "error", "rejected"):
        return "failed"
    if value == "blocked":
        return "blocked"
    if value in ("running", "active", "in_progress", "in-progress"):
        return "running"
    return "ok"


def _subagent_fields(event: Mapping[str, Any]) -> Dict[str, Any]:
    parent_session = _first_text(
        _field(event, "parent_session_id", "parent_session", "parentSession"),
        limit=MAX_ID,
    )
    child_session = _first_text(
        _field(
            event,
            "child_session_id",
            "child_session",
            "childSessionId",
            "child_id",
            "childId",
        ),
        limit=MAX_ID,
    )
    label = _first_text(_field(event, "label", "name", "title"), limit=MAX_SPAN_NAME)
    mode = _first_text(_field(event, "mode"), limit=MAX_SPAN_NAME)
    created_at = _first_text(
        _field(event, "created_at", "createdAt", "child_created_at"), limit=MAX_STRING
    )
    return {
        "parent_session_id": parent_session,
        "child_session_id": child_session,
        "label": label,
        "mode": mode,
        "created_at": created_at,
    }


def _tokens(event: Mapping[str, Any]) -> int:
    if event.get("kind") not in USAGE_KINDS:
        return 0
    payload = _payload(event)
    nested = _summary(event)

    def number(source: Mapping[str, Any], key: str) -> Optional[int]:
        value = source.get(key)
        if isinstance(value, bool) or value is None:
            return None
        try:
            parsed = int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else 0

    total = number(payload, "tokens_total") or number(nested, "tokens_total")
    if total:
        return total
    return (number(payload, "tokens_in") or number(nested, "tokens_in") or 0) + (
        number(payload, "tokens_out") or number(nested, "tokens_out") or 0
    )


def _duration_ms(event: Mapping[str, Any]) -> Optional[int]:
    value = _field(event, "duration_ms")
    if value is None:
        return None
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _iso_to_ms(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    text = str(value).strip()
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _duration_between(start: Optional[str], end: Optional[str]) -> Optional[int]:
    start_ms = _iso_to_ms(start)
    end_ms = _iso_to_ms(end)
    if start_ms is None or end_ms is None or end_ms < start_ms:
        return None
    return end_ms - start_ms


def _bounded(value: Any, depth: int = 0) -> Any:
    """Project a payload value to a small JSON-safe shape."""
    if depth > MAX_DEPTH:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:MAX_STRING]
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_ITEMS:
                break
            result[str(key)[:120]] = _bounded(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth + 1) for item in value[:MAX_ITEMS]]
    return str(value)[:MAX_STRING]


def _bounded_mapping(value: Any) -> Dict[str, Any]:
    projected = _bounded(value)
    if isinstance(projected, Mapping):
        return dict(projected)
    return {}


def _ordered(events: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Stable event order: spine id when present, otherwise input order."""
    indexed = list(enumerate(events or ()))
    indexed.sort(
        key=lambda pair: (
            (
                0,
                _event_id(pair[1]),
            )
            if _event_id(pair[1]) is not None
            else (1, pair[0])
        )
    )
    return [dict(event) for _, event in indexed]


def _trace_id(harness: str, session_id: str) -> str:
    return f"{harness}:{session_id}"


def split_trace_id(trace_id: str) -> Tuple[Optional[str], str]:
    """Split ``<harness>:<session_id>`` at the first colon."""
    text = str(trace_id or "").strip()
    harness, separator, session = text.partition(":")
    if not separator:
        return None, text
    return harness or None, session


# ---------------------------------------------------------------------------
# Span construction
# ---------------------------------------------------------------------------


def _bounded_tool_input(event: Mapping[str, Any]) -> Dict[str, Any]:
    sources = (_summary(event), _payload(event))
    for source in sources:
        for key in ("arguments", "tool_input", "input", "args"):
            if source.get(key) not in (None, ""):
                return _bounded_mapping(source.get(key))
    shape: Dict[str, Any] = {}
    for source in sources:
        for key in ("command", "file", "description", "arguments_raw"):
            if key not in shape and source.get(key) not in (None, ""):
                shape[key] = _bounded(source.get(key))
    return shape


def _bounded_tool_output(event: Mapping[str, Any]) -> Dict[str, Any]:
    sources = (_summary(event), _payload(event))
    for source in sources:
        for key in ("result", "output", "tool_result", "response"):
            value = source.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, Mapping):
                return _bounded_mapping(value)
            return {"result": _bounded(value)}
        if source.get("is_error") is not None:
            return {"is_error": bool(source.get("is_error"))}
    return {}


def _span_attributes(
    event: Mapping[str, Any], *, extra: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    payload = _payload(event)
    nested = _summary(event)
    attributes: Dict[str, Any] = {
        "event_id": _event_id(event),
        "event_kind": event.get("kind"),
        "source": event.get("source"),
        "schema": payload.get("schema"),
        "fidelity": payload.get("fidelity"),
        "session_id": event.get("session_id") or nested.get("session_id"),
        "turn_id": _event_turn_id(event),
        "tool_use_id": _event_tool_use_id(event),
        "tool_name": _event_tool_name(event),
        "status": _event_status(event),
    }
    if nested:
        attributes["summary"] = _bounded(nested)
    if extra:
        attributes.update(_bounded_mapping(extra))
    return {key: value for key, value in attributes.items() if value is not None}


def _span(
    *,
    span_id: str,
    parent_span_id: Optional[str],
    kind: str,
    name: str,
    actor: Optional[str],
    harness: str,
    model: Optional[str],
    status: str,
    start: Optional[str],
    end: Optional[str],
    duration_ms: Optional[int],
    project: Optional[str],
    cwd: Optional[str],
    input_value: Optional[Mapping[str, Any]] = None,
    output_value: Optional[Mapping[str, Any]] = None,
    attributes: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "kind": kind,
        "name": str(name or kind)[:MAX_SPAN_NAME],
        "actor": actor,
        "harness": harness,
        "model": model,
        "status": status,
        "start": start,
        "end": end,
        "duration_ms": duration_ms,
        "project": project,
        "cwd": cwd,
        "input": dict(input_value or {}),
        "output": dict(output_value or {}),
        "attributes": dict(attributes or {}),
    }


class _SpanIds:
    def __init__(self) -> None:
        self.used: set = set()

    def make(self, preferred: Optional[str], fallback: str) -> str:
        base = (preferred or fallback).strip()[:MAX_ID] or fallback[:MAX_ID]
        candidate = base
        index = 2
        while candidate in self.used:
            suffix = f":{index}"
            candidate = base[: MAX_ID - len(suffix)] + suffix
            index += 1
        self.used.add(candidate)
        return candidate


def _mcp_tool(name: Optional[str]) -> bool:
    if not name:
        return False
    lowered = name.strip().lower()
    return lowered.startswith("mcp__") or "mcp" in lowered


def _turn_groups(
    events: Sequence[Mapping[str, Any]],
) -> List[Tuple[str, Optional[Mapping[str, Any]], Optional[Mapping[str, Any]]]]:
    starts: List[Mapping[str, Any]] = []
    ends: List[Mapping[str, Any]] = []
    for event in events:
        kind = event.get("kind")
        if kind == "turn.started":
            starts.append(event)
        elif kind == "turn.ended":
            ends.append(event)
    used_end: set = set()
    groups: List[
        Tuple[str, Optional[Mapping[str, Any]], Optional[Mapping[str, Any]]]
    ] = []
    for start in starts:
        turn_id = _event_turn_id(start)
        match_index = None
        if turn_id:
            for index, end in enumerate(ends):
                if index in used_end:
                    continue
                if _event_turn_id(end) == turn_id:
                    match_index = index
                    break
        if match_index is not None:
            used_end.add(match_index)
            groups.append(
                (turn_id or str(_event_id(start) or ""), start, ends[match_index])
            )
        else:
            groups.append((turn_id or str(_event_id(start) or ""), start, None))
    for index, end in enumerate(ends):
        if index in used_end:
            continue
        groups.append((_event_turn_id(end) or str(_event_id(end) or ""), None, end))
    groups.sort(
        key=lambda item: (
            _event_ts(item[1]) if item[1] is not None else _event_ts(item[2]),
            _event_id(item[1]) if item[1] is not None else _event_id(item[2]) or 0,
        )
    )
    return groups


def _build_turn_spans(
    events: Sequence[Mapping[str, Any]],
    session_span_id: str,
    ids: _SpanIds,
    trace_status: str,
    session_harness: str,
    session_project: Optional[str],
    session_cwd: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, str], Dict[str, Dict[str, Any]]]:
    spans: List[Dict[str, Any]] = []
    turn_ids: Dict[str, str] = {}
    turn_models: Dict[str, Dict[str, Any]] = {}
    for turn_key, start, end in _turn_groups(events):
        start_ts = _event_ts(start) if start is not None else None
        end_ts = _event_ts(end) if end is not None else None
        start_ts = start_ts or end_ts
        end_ts = end_ts or None
        status = "running"
        if end is not None:
            end_status = (_event_status(end) or "").lower()
            if end_status in ("failed", "error"):
                status = "failed"
            elif end_status == "blocked":
                status = "blocked"
            else:
                status = "ok"
        elif trace_status != "running":
            status = "failed"
        model = _model(start) if start is not None else None
        model = model or (_model(end) if end is not None else None)
        project = (_project(start) if start is not None else None) or (session_project)
        cwd = (_cwd(start) if start is not None else None) or session_cwd
        actor = _first_text(start.get("actor") if start else None, limit=MAX_ID)
        harness = _harness(start) if start is not None else session_harness
        name = (
            _first_text(
                (_summary(start) if start is not None else {}).get("turn"),
                (_summary(end) if end is not None else {}).get("turn"),
                turn_key,
                "turn",
                limit=MAX_SPAN_NAME,
            )
            or "turn"
        )
        span_id = ids.make(
            _event_span_id(start) if start is not None else None,
            f"turn:{turn_key or len(spans)}",
        )
        if turn_key:
            turn_ids[turn_key] = span_id
        if model:
            turn_models[turn_key] = {"model": model, "span_id": span_id}
        input_value = _bounded_mapping(_summary(start)) if start is not None else {}
        output_value = _bounded_mapping(_summary(end)) if end is not None else {}
        attributes = _span_attributes(
            start or end or {},
            extra={"turn_key": turn_key, "events": 1 + (1 if end else 0)},
        )
        spans.append(
            _span(
                span_id=span_id,
                parent_span_id=session_span_id,
                kind="turn",
                name=name,
                actor=actor,
                harness=harness,
                model=model,
                status=status,
                start=start_ts,
                end=end_ts,
                duration_ms=_duration_between(start_ts, end_ts),
                project=project,
                cwd=cwd,
                input_value=input_value,
                output_value=output_value,
                attributes=attributes,
            )
        )
    return spans, turn_ids, turn_models


def _build_tool_spans(
    events: Sequence[Mapping[str, Any]],
    session_span_id: str,
    turn_ids: Mapping[str, str],
    ids: _SpanIds,
    session_harness: str,
    session_project: Optional[str],
    session_cwd: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    starts: List[Mapping[str, Any]] = []
    completions: List[Mapping[str, Any]] = []
    for event in events:
        kind = event.get("kind")
        if kind == TOOL_START_KIND:
            starts.append(event)
        elif kind in TOOL_END_KINDS:
            completions.append(event)

    matched_starts: set = set()
    matched_completions: set = set()
    pairs: List[Tuple[Optional[Mapping[str, Any]], Optional[Mapping[str, Any]]]] = []

    for comp_index, completion in enumerate(completions):
        key = _event_tool_use_id(completion) or _event_span_id(completion)
        candidates = [
            (index, start)
            for index, start in enumerate(starts)
            if index not in matched_starts
        ]
        match_index: Optional[int] = None
        if key:
            for index, start in candidates:
                start_key = _event_tool_use_id(start) or _event_span_id(start)
                if start_key == key:
                    match_index = index
                    break
        if match_index is None:
            comp_name = _event_tool_name(completion)
            for index, start in candidates:
                if comp_name and _event_tool_name(start) == comp_name:
                    match_index = index
                    break
        if match_index is None and candidates:
            # Loose harnesses sometimes omit ids: use the nearest earlier start
            # with the same turn (falling back to the latest unpaired start).
            comp_turn = _event_turn_id(completion)
            for index, start in candidates:
                if comp_turn and _event_turn_id(start) == comp_turn:
                    match_index = index
            if match_index is None:
                match_index = candidates[-1][0]
        if match_index is not None:
            matched_starts.add(match_index)
            matched_completions.add(comp_index)
            pairs.append((starts[match_index], completion))
        else:
            matched_completions.add(comp_index)
            pairs.append((None, completion))

    for index, start in enumerate(starts):
        if index not in matched_starts:
            pairs.append((start, None))

    pairs.sort(
        key=lambda item: (
            _event_ts(item[0]) if item[0] is not None else _event_ts(item[1]),
            _event_id(item[0]) if item[0] is not None else _event_id(item[1]) or 0,
        )
    )

    spans: List[Dict[str, Any]] = []
    tool_ids: Dict[str, str] = {}
    for start, completion in pairs:
        reference = start or completion
        if reference is None:
            continue
        start_ts = _event_ts(start) if start is not None else None
        end_ts = _event_ts(completion) if completion is not None else None
        if start_ts is None:
            start_ts = end_ts
        if end_ts is not None and start_ts is not None:
            duration = _duration_between(start_ts, end_ts)
        else:
            duration = _duration_ms(completion) if completion is not None else None
        if completion is not None:
            completion_status = (_event_status(completion) or "").lower()
            if completion.get("kind") == "tool.failed" or completion_status in (
                "failed",
                "error",
            ):
                status = "failed"
            elif completion_status == "blocked":
                status = "blocked"
            else:
                status = "ok"
        else:
            status = "running"
        tool_name = (_event_tool_name(start) if start is not None else None) or (
            _event_tool_name(completion) if completion is not None else None
        )
        turn_id = (_event_turn_id(start) if start is not None else None) or (
            _event_turn_id(completion) if completion is not None else None
        )
        parent = turn_ids.get(turn_id or "", session_span_id)
        span_id = ids.make(
            _event_span_id(start) if start is not None else None,
            f"tool:{(tool_name or 'tool')}:{_event_id(reference) or len(spans)}",
        )
        model = _model(start) if start is not None else None
        model = model or (_model(completion) if completion is not None else None)
        project = (
            (_project(start) if start is not None else None)
            or (_project(completion) if completion is not None else None)
            or session_project
        )
        cwd = (
            (_cwd(start) if start is not None else None)
            or (_cwd(completion) if completion is not None else None)
            or session_cwd
        )
        harness = _harness(start) if start is not None else session_harness
        input_value = _bounded_tool_input(start) if start is not None else {}
        output_value = (
            _bounded_tool_output(completion) if completion is not None else {}
        )
        attributes = _span_attributes(
            reference,
            extra={
                "tool_use_id": _event_tool_use_id(reference),
                "events": 1
                + (1 if start is not None and completion is not None else 0),
                "start_event_kind": start.get("kind") if start is not None else None,
                "end_event_kind": (
                    completion.get("kind") if completion is not None else None
                ),
            },
        )
        paths = _event_paths(start) if start is not None else []
        if completion is not None:
            for path in _event_paths(completion):
                if path not in paths:
                    paths.append(path)
        if paths:
            attributes["path"] = paths[0]
            attributes["paths"] = paths[:MAX_ITEMS]
            attributes["artifacts"] = [{"path": path} for path in paths[:MAX_ITEMS]]
        tool_id = _event_tool_use_id(reference)
        if tool_id:
            tool_ids.setdefault(_normalize_session_key(tool_id) or tool_id, span_id)
        spans.append(
            _span(
                span_id=span_id,
                parent_span_id=parent,
                kind="mcp" if _mcp_tool(tool_name) else "tool",
                name=tool_name or "tool",
                actor=_first_text(
                    (start or completion or {}).get("actor"), limit=MAX_ID
                ),
                harness=harness,
                model=model,
                status=status,
                start=start_ts,
                end=end_ts,
                duration_ms=duration,
                project=project,
                cwd=cwd,
                input_value=input_value,
                output_value=output_value,
                attributes=attributes,
            )
        )
    return spans, tool_ids


def _build_explicit_subagent_spans(
    events: Sequence[Mapping[str, Any]],
    session_span_id: str,
    ids: _SpanIds,
    session_harness: str,
    session_project: Optional[str],
    session_cwd: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Build subagent spans from ``subagent.started`` events in this session."""
    spans: List[Dict[str, Any]] = []
    child_ids: Dict[str, str] = {}
    for event in events:
        if event.get("kind") not in SUBAGENT_KINDS:
            continue
        fields = _subagent_fields(event)
        child_key = (
            _normalize_session_key(fields["child_session_id"])
            or fields["child_session_id"]
        )
        if child_key and child_key in child_ids:
            continue
        span_id = ids.make(
            _event_span_id(event), f"subagent:{_event_id(event) or len(spans)}"
        )
        if child_key:
            child_ids[child_key] = span_id
        attributes = _span_attributes(event)
        for key in ("parent_session_id", "child_session_id", "label", "mode"):
            if fields.get(key):
                attributes[key] = fields[key]
        if fields.get("created_at"):
            attributes["created_at"] = fields["created_at"]
        spans.append(
            _span(
                span_id=span_id,
                parent_span_id=session_span_id,
                kind="subagent",
                name=fields["label"] or fields["child_session_id"] or "subagent",
                actor=_first_text(event.get("actor"), limit=MAX_ID),
                harness=_harness(event) or session_harness,
                model=None,
                status=_span_status(_event_status(event) or "running"),
                start=_event_ts(event),
                end=None,
                duration_ms=None,
                project=_project(event) or session_project,
                cwd=_cwd(event) or session_cwd,
                input_value=_bounded_mapping(_summary(event)),
                output_value={},
                attributes=attributes,
            )
        )
    return spans, child_ids


def _build_simple_spans(
    events: Sequence[Mapping[str, Any]],
    session_span_id: str,
    turn_ids: Mapping[str, str],
    tool_ids: Mapping[str, str],
    subagent_ids: Mapping[str, str],
    ids: _SpanIds,
    session_harness: str,
    session_project: Optional[str],
    session_cwd: Optional[str],
) -> List[Dict[str, Any]]:
    spans: List[Dict[str, Any]] = []
    seen_artifacts: set = set()
    for event in events:
        kind = event.get("kind")
        span_kind = "task"
        parent = session_span_id
        status = "ok"
        model: Optional[str] = None
        input_value: Dict[str, Any] = {}
        output_value: Dict[str, Any] = {}
        attributes: Dict[str, Any] = {}
        name = "span"
        if kind in USAGE_KINDS:
            span_kind = "llm"
            model = _model(event)
            name = model or "llm"
            tokens_in = _field(event, "tokens_in")
            tokens_out = _field(event, "tokens_out")
            input_value = {"tokens_in": tokens_in} if tokens_in is not None else {}
            output_value = {"tokens_out": tokens_out} if tokens_out is not None else {}
            attributes = _span_attributes(event, extra={"tokens_total": _tokens(event)})
            parent = turn_ids.get(_event_turn_id(event) or "", session_span_id)
        elif kind in APPROVAL_KINDS:
            span_kind = "approval"
            nested = _summary(event)
            payload = _payload(event)
            name = (
                _first_text(
                    nested.get("policy"),
                    nested.get("approval"),
                    nested.get("name"),
                    payload.get("policy"),
                    payload.get("approval"),
                    payload.get("name"),
                    "approval" if kind != "policy.decision" else "policy",
                    limit=MAX_SPAN_NAME,
                )
                or "approval"
            )
            input_value = _bounded_mapping(nested or payload)
            attributes = _span_attributes(event)
            approval_status = (_event_status(event) or "").lower()
            if kind == "approval.denied" or approval_status in (
                "denied",
                "failed",
                "error",
            ):
                status = "failed"
            elif approval_status == "blocked":
                status = "blocked"
            parent = turn_ids.get(_event_turn_id(event) or "", session_span_id)
        elif kind in ARTIFACT_KINDS:
            key = _artifact_key(event)
            if key in seen_artifacts:
                continue
            seen_artifacts.add(key)
            span_kind = "artifact"
            nested = _summary(event)
            payload = _payload(event)
            paths = _event_paths(event)
            description = _first_text(_field(event, "description"), limit=MAX_STRING)
            sha = _first_text(_field(event, "sha256", "digest"), limit=MAX_STRING)
            tool_call_id = _first_text(
                _field(event, "tool_call_id", "tool_use_id", "call_id"),
                limit=MAX_ID,
            )
            name = (
                _first_text(
                    description,
                    paths[0] if paths else None,
                    nested.get("commit"),
                    payload.get("commit"),
                    nested.get("commit"),
                    nested.get("artifact"),
                    nested.get("name"),
                    kind,
                    limit=MAX_SPAN_NAME,
                )
                or "artifact"
            )
            input_value = _bounded_mapping(nested or payload)
            attributes = _span_attributes(event)
            if paths:
                attributes["path"] = paths[0]
                attributes["paths"] = paths[:MAX_ITEMS]
            records = _artifact_records(event, paths)
            if records:
                attributes["artifacts"] = records
            if description:
                attributes["description"] = description
            if sha:
                attributes["sha256"] = sha
            if tool_call_id:
                attributes["tool_call_id"] = tool_call_id
            operation = _first_text(_field(event, "operation"), limit=MAX_SPAN_NAME)
            if operation:
                attributes["operation"] = operation
            parent = tool_ids.get(
                _normalize_session_key(tool_call_id) or tool_call_id or ""
            ) or turn_ids.get(_event_turn_id(event) or "", session_span_id)
        elif kind in TEAM_TASK_KINDS:
            span_kind = "task"
            fields = _team_task_fields(event)
            name = fields["subject"] or fields["team_task_id"] or "task"
            status = _team_task_status(fields["status"])
            nested = _summary(event)
            payload = _payload(event)
            input_value = _bounded_mapping(nested or payload)
            attributes = _span_attributes(event)
            for key in (
                "team_id",
                "team_task_id",
                "owner_id",
                "owner_name",
                "parent_session_id",
            ):
                if fields.get(key):
                    attributes[key] = fields[key]
            attributes["blocked_by"] = fields["blocked_by"]
            revision = _field(event, "revision")
            if revision is not None:
                attributes["revision"] = _bounded(revision)
            owner_key = _normalize_session_key(fields["owner_id"]) or fields["owner_id"]
            parent = (
                turn_ids.get(_event_turn_id(event) or "")
                or (subagent_ids.get(owner_key) if owner_key else None)
                or session_span_id
            )
        elif kind in TASK_KINDS:
            span_kind = "task"
            nested = _summary(event)
            payload = _payload(event)
            name = (
                _first_text(
                    nested.get("title"),
                    nested.get("task"),
                    payload.get("title"),
                    payload.get("task"),
                    event.get("task_id"),
                    "task",
                    limit=MAX_SPAN_NAME,
                )
                or "task"
            )
            input_value = _bounded_mapping(nested or payload)
            attributes = _span_attributes(event)
            status = "failed" if kind == "task.failed" else "ok"
            parent = turn_ids.get(_event_turn_id(event) or "", session_span_id)
        else:
            continue
        if "paths" not in attributes:
            event_paths = _event_paths(event)
            if event_paths:
                attributes["path"] = event_paths[0]
                attributes["paths"] = event_paths[:MAX_ITEMS]
                attributes["artifacts"] = [
                    {"path": path} for path in event_paths[:MAX_ITEMS]
                ]
        span_id = ids.make(
            _event_span_id(event),
            f"{span_kind}:{_event_id(event) or len(spans)}",
        )
        spans.append(
            _span(
                span_id=span_id,
                parent_span_id=parent,
                kind=span_kind,
                name=name,
                actor=_first_text(event.get("actor"), limit=MAX_ID),
                harness=_harness(event),
                model=model,
                status=status,
                start=_event_ts(event),
                end=_event_ts(event),
                duration_ms=_duration_ms(event),
                project=_project(event) or session_project,
                cwd=_cwd(event) or session_cwd,
                input_value=input_value,
                output_value=output_value,
                attributes=attributes,
            )
        )
    return spans


def _build_subagent_span(
    events: Sequence[Mapping[str, Any]],
    session_span_id: str,
    ids: _SpanIds,
    session_harness: str,
    session_project: Optional[str],
    session_cwd: Optional[str],
    *,
    skip_child_ids: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    for event in events:
        if event.get("kind") != "session.started":
            continue
        nested = _summary(event)
        origin = _first_text(nested.get("origin"), limit=MAX_SPAN_NAME)
        parent_session = _first_text(
            nested.get("parent_session"),
            nested.get("parent_session_id"),
            limit=MAX_ID,
        )
        child_session = _first_text(
            event.get("session_id"),
            nested.get("child_session_id"),
            limit=MAX_ID,
        )
        if not origin and not parent_session:
            continue
        child_key = _normalize_session_key(child_session)
        if skip_child_ids and child_key and child_key in skip_child_ids:
            continue
        span_id = ids.make(None, f"subagent:{_event_id(event) or 'session'}")
        return _span(
            span_id=span_id,
            parent_span_id=session_span_id,
            kind="subagent",
            name=origin or parent_session or "subagent",
            actor=_first_text(event.get("actor"), limit=MAX_ID),
            harness=session_harness,
            model=None,
            status="ok",
            start=_event_ts(event),
            end=None,
            duration_ms=None,
            project=session_project,
            cwd=session_cwd,
            input_value=_bounded_mapping(nested),
            output_value={},
            attributes=_span_attributes(
                event,
                extra={
                    "origin": origin,
                    "parent_session": parent_session,
                    "parent_session_id": parent_session,
                    "child_session_id": child_session,
                    "label": _first_text(
                        nested.get("label"), origin, limit=MAX_SPAN_NAME
                    ),
                    "mode": _first_text(nested.get("mode"), limit=MAX_SPAN_NAME),
                },
            ),
        )
    return None


# ---------------------------------------------------------------------------
# Summaries / tree building
# ---------------------------------------------------------------------------


def _trace_status(events: Sequence[Mapping[str, Any]], has_end: bool) -> str:
    for event in events:
        kind = event.get("kind")
        status = (_event_status(event) or "").lower()
        if kind in ("tool.failed", "approval.denied", "task.failed"):
            return "failed"
        if status in ("failed", "error", "denied", "blocked"):
            return "failed"
    if has_end:
        return "ok"
    return "running"


def _session_title(
    events: Sequence[Mapping[str, Any]],
    trace_id: str,
    project: Optional[str],
    cwd: Optional[str],
) -> str:
    for event in events:
        if event.get("kind") != "session.started":
            continue
        nested = _summary(event)
        title = _first_text(
            nested.get("title"),
            nested.get("name"),
            nested.get("summary"),
            nested.get("message"),
            nested.get("session_title"),
            nested.get("workspace"),
            _payload(event).get("title"),
            limit=MAX_SPAN_NAME,
        )
        if title:
            return title
    if project:
        return project
    if cwd:
        cleaned = cwd.rstrip("/\\")
        if cleaned:
            return cleaned.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:MAX_SPAN_NAME]
    return trace_id[:MAX_SPAN_NAME]


def _enriched_counts(events: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    subagent_keys: set = set()
    has_subagent_header = False
    artifact_keys: set = set()
    for event in events:
        kind = event.get("kind")
        if kind in SUBAGENT_KINDS:
            fields = _subagent_fields(event)
            key = (
                _normalize_session_key(fields["child_session_id"])
                or fields["child_session_id"]
                or fields["label"]
                or _event_id(event)
            )
            subagent_keys.add(str(key))
        elif kind == "session.started":
            nested = _summary(event)
            payload = _payload(event)
            if (
                nested.get("origin")
                or nested.get("parent_session")
                or payload.get("origin")
                or payload.get("parent_session")
            ):
                has_subagent_header = True
        elif kind in ARTIFACT_KINDS:
            artifact_keys.add(_artifact_key(event))
    return {
        "subagents": len(subagent_keys) + (1 if has_subagent_header else 0),
        "artifacts": len(artifact_keys),
    }


def summarize_trace(
    events: Sequence[Mapping[str, Any]], trace_id: Optional[str] = None
) -> Dict[str, Any]:
    """Build the frozen trace summary for one session's events."""
    ordered = _ordered(events)
    if not ordered:
        raise TraceQueryError("cannot summarize an empty trace")
    harness = next(
        (
            value
            for event in ordered
            for value in (_harness(event),)
            if value != "unknown"
        ),
        "unknown",
    )
    session_id = next(
        (value for event in ordered for value in (_session_id(event),) if value),
        "",
    )
    resolved_trace_id = trace_id or _trace_id(harness, session_id)

    started_candidates = [
        _event_ts(event) for event in ordered if _event_ts(event) is not None
    ]
    ended_candidates = [
        _event_ts(event) for event in ordered if event.get("kind") == "session.ended"
    ]
    started_at = min(started_candidates) if started_candidates else None
    session_starts = [
        _event_ts(event) for event in ordered if event.get("kind") == "session.started"
    ]
    if session_starts:
        started_at = min(session_starts)
    ended_at = max(ended_candidates) if ended_candidates else None
    has_end = bool(ended_candidates)
    status = _trace_status(ordered, has_end)
    project = next(
        (value for value in (_project(event) for event in ordered) if value), None
    )
    cwd = next((value for value in (_cwd(event) for event in ordered) if value), None)
    models = sorted({model for model in (_model(event) for event in ordered) if model})
    tokens_total = sum(_tokens(event) for event in ordered)
    trace_events = [event for event in ordered if event.get("kind") in TURN_KINDS]
    turns = len(
        {
            _event_turn_id(event)
            for event in trace_events
            if _event_turn_id(event) is not None
        }
    )
    if not turns and trace_events:
        turns = (
            1 if any(event.get("kind") == "turn.started" for event in ordered) else 0
        )
    tool_calls = sum(1 for event in ordered if event.get("kind") == TOOL_START_KIND)
    tool_starts = tool_calls
    tool_finishes = sum(1 for event in ordered if event.get("kind") in TOOL_END_KINDS)
    if tool_starts or tool_finishes:
        tool_calls = max(tool_starts, tool_finishes) if not tool_starts else tool_starts
        # Starts without a completion still count; completions without starts count too.
        tool_calls = tool_starts + max(0, tool_finishes - tool_starts)
    return {
        "trace_id": resolved_trace_id,
        "harness": harness,
        "project": project,
        "cwd": cwd,
        "title": _session_title(ordered, resolved_trace_id, project, cwd),
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": _duration_between(started_at, ended_at),
        "events": len(ordered),
        "turns": turns,
        "tool_calls": tool_calls,
        "models": models,
        "tokens_total": tokens_total,
        "status": status,
        **_enriched_counts(ordered),
    }


def build_trace(
    events: Sequence[Mapping[str, Any]], trace_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Build one ``{"trace": summary, "spans": [...]}`` tree."""
    if not events:
        return None
    ordered = _ordered(events)
    summary = summarize_trace(ordered, trace_id)
    # Recompute counts from the actual span builders so orphan/paired tools agree
    # with the summary.
    ids = _SpanIds()
    session_event = next(
        (event for event in ordered if event.get("kind") == "session.started"),
        ordered[0],
    )
    session_span_id = ids.make(
        _event_span_id(session_event), f"session:{summary['trace_id']}"
    )
    turn_spans, turn_ids, _ = _build_turn_spans(
        ordered,
        session_span_id,
        ids,
        summary["status"],
        summary["harness"],
        summary["project"],
        summary["cwd"],
    )
    tool_spans, tool_ids = _build_tool_spans(
        ordered,
        session_span_id,
        turn_ids,
        ids,
        summary["harness"],
        summary["project"],
        summary["cwd"],
    )
    explicit_subagents, subagent_ids = _build_explicit_subagent_spans(
        ordered,
        session_span_id,
        ids,
        summary["harness"],
        summary["project"],
        summary["cwd"],
    )
    simple_spans = _build_simple_spans(
        ordered,
        session_span_id,
        turn_ids,
        tool_ids,
        subagent_ids,
        ids,
        summary["harness"],
        summary["project"],
        summary["cwd"],
    )
    subagent = _build_subagent_span(
        ordered,
        session_span_id,
        ids,
        summary["harness"],
        summary["project"],
        summary["cwd"],
        skip_child_ids=set(subagent_ids),
    )
    summary["turns"] = len(turn_spans)
    summary["tool_calls"] = len(tool_spans)
    all_subagents = [*explicit_subagents]
    if subagent is not None:
        all_subagents.append(subagent)
    summary["subagents"] = len(all_subagents)
    summary["artifacts"] = sum(
        1 for span in simple_spans if span.get("kind") == "artifact"
    )

    session_start = next(
        (event for event in ordered if event.get("kind") == "session.started"), None
    )
    session_end = next(
        (event for event in reversed(ordered) if event.get("kind") == "session.ended"),
        None,
    )
    session_start_ts = (
        _event_ts(session_start) if session_start else summary["started_at"]
    )
    session_end_ts = _event_ts(session_end) if session_end else None
    session_input = _bounded_mapping(_summary(session_start)) if session_start else {}
    session_output = _bounded_mapping(_summary(session_end)) if session_end else {}
    session_attributes = _span_attributes(
        session_start or ordered[0],
        extra={
            "events": len(ordered),
            "segments": len(turn_spans),
            "tokens_total": summary["tokens_total"],
        },
    )
    session_span = _span(
        span_id=session_span_id,
        parent_span_id=None,
        kind="session",
        name=summary["title"],
        actor=_first_text((session_start or ordered[0]).get("actor"), limit=MAX_ID),
        harness=summary["harness"],
        model=None,
        status=summary["status"],
        start=session_start_ts,
        end=session_end_ts,
        duration_ms=_duration_between(session_start_ts, session_end_ts),
        project=summary["project"],
        cwd=summary["cwd"],
        input_value=session_input,
        output_value=session_output,
        attributes=session_attributes,
    )
    spans: List[Tuple[str, int, Dict[str, Any]]] = [
        (session_span["start"] or "", 0, session_span)
    ]
    for span in [*turn_spans, *tool_spans, *explicit_subagents, *simple_spans]:
        spans.append((span.get("start") or "", len(spans), span))
    if subagent is not None:
        spans.append((subagent.get("start") or "", len(spans), subagent))
    spans.sort(key=lambda item: (item[0], item[1]))
    return {"trace": summary, "spans": [item[2] for item in spans]}


def build_traces(
    events: Sequence[Mapping[str, Any]],
    *,
    limit: int = DEFAULT_TRACE_LIMIT,
    project: Optional[str] = None,
    harness: Optional[str] = None,
    since: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Group events by session and return newest-first trace summaries."""
    grouped: Dict[str, List[Mapping[str, Any]]] = {}
    for event in _ordered(events):
        session_id = _session_id(event)
        if not session_id:
            continue
        grouped.setdefault(session_id, []).append(event)
    summaries: List[Dict[str, Any]] = []
    for session_id, session_events in grouped.items():
        summary = summarize_trace(session_events)
        if project and str(summary.get("project") or "") != project:
            continue
        if harness and str(summary.get("harness") or "") != harness:
            continue
        if since and summary.get("started_at") and summary["started_at"] < since:
            continue
        summaries.append(summary)
    summaries.sort(
        key=lambda summary: (
            summary.get("started_at") or "",
            summary.get("trace_id") or "",
        ),
        reverse=True,
    )
    return summaries[: max(0, int(limit))]


# ---------------------------------------------------------------------------
# Reading the spine
# ---------------------------------------------------------------------------


def normalize_trace_limit(value: Any) -> int:
    if value is None or value == "":
        return DEFAULT_TRACE_LIMIT
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise TraceQueryError("limit must be an integer") from None
    if number < 1:
        raise TraceQueryError("limit must be >= 1")
    if number > MAX_TRACE_LIMIT:
        raise TraceQueryError(f"limit must be <= {MAX_TRACE_LIMIT}")
    return number


def normalize_trace_since(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise TraceQueryError(f"invalid since timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _decode_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    event = dict(row)
    payload = event.get("payload")
    if isinstance(payload, str) and payload:
        try:
            event["payload"] = json.loads(payload)
        except ValueError:
            event["payload"] = {"raw": payload}
    elif payload is None:
        event["payload"] = {}
    return event


async def read_trace_events(
    db_path: Any,
    *,
    session_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = DEFAULT_EVENT_SCAN,
    newest_first: bool = False,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Read trace-candidate events (sessions only) in ascending id order.

    ``newest_first`` reads the most recent rows before the bounded scan is cut
    (used by the list endpoint so recent sessions are not lost on a busy
    spine); the returned list is still oldest-first for the builder.
    """
    if db_path is None:
        return [], False
    path = Path(str(db_path)).expanduser()
    if not path.exists():
        return [], False
    bound = max(1, min(int(limit), MAX_EVENT_SCAN))
    clauses = ["session_id IS NOT NULL", "session_id != ''"]
    params: List[Any] = []
    if session_id:
        clauses.append("session_id = ?")
        params.append(str(session_id)[:MAX_ID])
    if since:
        clauses.append("ts >= ?")
        params.append(since)
    order = "DESC" if newest_first else "ASC"
    sql = (
        "SELECT * FROM events WHERE "
        + " AND ".join(clauses)
        + f" ORDER BY id {order} LIMIT ?"
    )
    try:
        async with aiosqlite.connect(str(path), isolation_level=None, timeout=5) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout = 5000")
            await db.execute("PRAGMA query_only = ON")
            cursor = await db.execute(sql, [*params, bound + 1])
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return [], False
        raise TraceQueryError(f"cannot read trace events: {exc}") from exc
    truncated = len(rows) > bound
    events = [_decode_row(dict(row)) for row in rows[:bound]]
    if newest_first:
        events.reverse()
    return events, truncated


async def trace_list_report(
    db_path: Any,
    *,
    limit: Any = DEFAULT_TRACE_LIMIT,
    project: Optional[str] = None,
    harness: Optional[str] = None,
    since: Any = None,
) -> Dict[str, Any]:
    bound = normalize_trace_limit(limit)
    since_ts = normalize_trace_since(since)
    project_filter = str(project).strip()[:MAX_ID] if project else None
    harness_filter = str(harness).strip()[:MAX_ID] if harness else None
    events, _ = await read_trace_events(
        db_path, since=since_ts, limit=MAX_EVENT_SCAN, newest_first=True
    )
    traces = build_traces(
        events,
        limit=bound,
        project=project_filter or None,
        harness=harness_filter or None,
        since=since_ts,
    )
    return {"traces": traces}


async def trace_detail_report(db_path: Any, trace_id: str) -> Optional[Dict[str, Any]]:
    harness, session_id = split_trace_id(trace_id)
    if not session_id:
        return None
    events, _ = await read_trace_events(
        db_path, session_id=session_id, limit=MAX_EVENT_SCAN
    )
    if not events:
        return None
    trace = build_trace(events)
    if trace is None:
        return None
    if trace["trace"]["trace_id"] != trace_id:
        return None
    return trace


__all__ = [
    "DEFAULT_TRACE_LIMIT",
    "MAX_TRACE_LIMIT",
    "TraceQueryError",
    "build_trace",
    "build_traces",
    "normalize_trace_limit",
    "normalize_trace_since",
    "read_trace_events",
    "split_trace_id",
    "summarize_trace",
    "trace_detail_report",
    "trace_list_report",
]
