"""Read-side queries for evidence events on the dashboard event spine.

Evidence kinds are owned by :mod:`sugar.telemetry.schema`; this module provides a
bounded, defensive query used by ``GET /api/evidence`` and the
``sugar evidence`` CLI. It deliberately does not extend the spine API: the
dashboard may be serving a spine written by older versions, so every row/payload
is decoded tolerantly.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import aiosqlite

from ..telemetry.schema import EVIDENCE_KINDS, normalize_iso_timestamp

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
MAX_SCAN = 10_000
DEFAULT_OFFSET = 0

# Statuses accepted by the frozen contract's ``status=`` filter. Evidence status
# vocabulary is intentionally broad because different harnesses use different
# words (``ok`` from DSH policy events, ``passed`` from CI verifiers, ...).
EVIDENCE_STATUSES = (
    "approved",
    "blocked",
    "canceled",
    "cancelled",
    "completed",
    "created",
    "denied",
    "error",
    "failed",
    "info",
    "linked",
    "ok",
    "passed",
    "pending",
    "policy",
    "rejected",
    "running",
    "skipped",
    "success",
    "timeout",
    "unknown",
    "warning",
)


class EvidenceQueryError(ValueError):
    """Invalid evidence query parameters (surfaced as HTTP 400)."""


def parse_limit(value: Any) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_LIMIT
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise EvidenceQueryError("limit must be an integer") from None
    if parsed < 1 or parsed > MAX_LIMIT:
        raise EvidenceQueryError(f"limit must be between 1 and {MAX_LIMIT}")
    return parsed


def parse_offset(value: Any) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_OFFSET
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise EvidenceQueryError("offset must be an integer") from None
    if parsed < 0:
        raise EvidenceQueryError("offset must be >= 0")
    return parsed


def normalize_filter_timestamp(value: Any, name: str) -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    normalized = normalize_iso_timestamp(value)
    if normalized is None:
        raise EvidenceQueryError(f"{name} must be an ISO-8601 timestamp")
    return normalized


def normalize_kind(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text not in EVIDENCE_KINDS:
        raise EvidenceQueryError(
            "unknown evidence kind; expected one of: " + ", ".join(EVIDENCE_KINDS)
        )
    return text


def normalize_status(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text not in EVIDENCE_STATUSES:
        raise EvidenceQueryError(
            "unknown evidence status; expected one of: " + ", ".join(EVIDENCE_STATUSES)
        )
    return text


def decode_payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return {"raw": value}
        return dict(parsed) if isinstance(parsed, Mapping) else {"value": parsed}
    return {}


def _decode_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    event = dict(row)
    event["payload"] = decode_payload(event.get("payload"))
    return event


def event_status(event: Mapping[str, Any]) -> Optional[str]:
    """Extract the evidence status from ``payload.payload.status`` or ``payload.status``."""
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    nested = payload.get("payload")
    if isinstance(nested, Mapping):
        value = nested.get("status")
        if value not in (None, ""):
            return str(value).strip().lower() or None
    value = payload.get("status")
    if value not in (None, ""):
        return str(value).strip().lower() or None
    return None


def event_summary_text(event: Mapping[str, Any], limit: int = 80) -> str:
    """Short human summary for the evidence table."""
    payload = event.get("payload")
    nested = payload.get("payload") if isinstance(payload, Mapping) else None
    candidates: List[Any] = []
    if isinstance(nested, Mapping):
        candidates.extend(
            [
                nested.get("summary"),
                nested.get("title"),
                nested.get("message"),
                nested.get("check"),
                nested.get("command"),
                nested.get("commit"),
            ]
        )
    if isinstance(payload, Mapping):
        candidates.extend([payload.get("tool_name"), payload.get("status")])
    for value in candidates:
        if value is None or value == "":
            continue
        text = str(value).replace("\n", " ").strip()
        if text:
            return text[:limit]
    return "-"


def build_summary(events: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    by_status: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    total = 0
    for event in events:
        total += 1
        kind = str(event.get("kind") or "")
        if kind:
            by_kind[kind] = by_kind.get(kind, 0) + 1
        status = event_status(event)
        if status:
            by_status[status] = by_status.get(status, 0) + 1
    return {
        "total": total,
        "by_status": dict(sorted(by_status.items())),
        "by_kind": dict(sorted(by_kind.items())),
    }


async def _read_rows(db_path: Any, sql: str, params: List[Any]) -> List[Dict[str, Any]]:
    if db_path is None:
        return []
    path = Path(str(db_path)).expanduser()
    if not path.exists():
        return []
    try:
        async with aiosqlite.connect(str(path), isolation_level=None, timeout=5) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout = 5000")
            await db.execute("PRAGMA query_only = ON")
            cursor = await db.execute(sql, params)
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise
    return [_decode_row(dict(row)) for row in rows]


async def query_evidence(
    db_path: Any,
    *,
    since: Any = None,
    until: Any = None,
    task_id: Any = None,
    kind: Any = None,
    status: Any = None,
    limit: Any = DEFAULT_LIMIT,
    offset: Any = DEFAULT_OFFSET,
) -> Dict[str, Any]:
    """Query evidence events; returns the frozen ``GET /api/evidence`` payload."""
    since_value = normalize_filter_timestamp(since, "since")
    until_value = normalize_filter_timestamp(until, "until")
    kind_value = normalize_kind(kind)
    status_value = normalize_status(status)
    limit_value = parse_limit(limit)
    offset_value = parse_offset(offset)
    task_value = str(task_id).strip() if task_id is not None else ""
    task_value = task_value or None

    clauses = ["kind IN (" + ", ".join("?" for _ in EVIDENCE_KINDS) + ")"]
    params: List[Any] = list(EVIDENCE_KINDS)
    if since_value:
        clauses.append("ts >= ?")
        params.append(since_value)
    if until_value:
        clauses.append("ts <= ?")
        params.append(until_value)
    if task_value:
        clauses.append("task_id = ?")
        params.append(task_value)
    if kind_value:
        clauses.append("kind = ?")
        params.append(kind_value)

    sql = (
        "SELECT * FROM events WHERE "
        + " AND ".join(clauses)
        + " ORDER BY id ASC LIMIT ?"
    )
    params.append(MAX_SCAN)
    events = await _read_rows(db_path, sql, params)
    if status_value:
        events = [event for event in events if event_status(event) == status_value]
    summary = build_summary(events)
    page = events[offset_value : offset_value + limit_value]
    last_id = int(page[-1].get("id") or 0) if page else 0
    return {"events": page, "last_id": last_id, "summary": summary}


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_OFFSET",
    "EVIDENCE_STATUSES",
    "EvidenceQueryError",
    "MAX_LIMIT",
    "MAX_SCAN",
    "build_summary",
    "decode_payload",
    "event_status",
    "event_summary_text",
    "normalize_filter_timestamp",
    "normalize_kind",
    "normalize_status",
    "parse_limit",
    "parse_offset",
    "query_evidence",
]
