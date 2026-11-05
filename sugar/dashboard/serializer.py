"""JSON serialization helpers for the local dashboard.

The dashboard speaks a frozen contract (``docs/local-dashboard.md``).  SQLite
stores timestamps as ``YYYY-MM-DD HH:MM:SS`` in UTC and JSON columns as text,
so every row read from ``work_items`` is normalized here before it is handed
to REST clients or SSE subscribers.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# SQLite's CURRENT_TIMESTAMP format (UTC, second precision).
SQLITE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

_FALLBACK_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)


def utc_now_sqlite() -> str:
    """Return the current UTC time in SQLite ``CURRENT_TIMESTAMP`` format."""
    return datetime.now(timezone.utc).strftime(SQLITE_TIMESTAMP_FORMAT)


def iso_utc_z(value: Any) -> Optional[str]:
    """Convert a SQLite timestamp to ISO-8601 UTC with a trailing ``Z``.

    Returns ``None`` for empty/unparseable values.  The contract forbids ever
    returning the raw SQLite string, so unknown formats degrade to ``None``
    rather than leaking through.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            dt = None
            for fmt in _FALLBACK_FORMATS:
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_json_value(value: Any, default: Any) -> Any:
    """Parse a JSON text column, falling back to ``default`` on bad data."""
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return default if parsed is None else parsed


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def serialize_task(row: Any) -> Dict[str, Any]:
    """Serialize a ``work_items`` row into the contract's Task object."""
    data = dict(row)

    source_file = data.get("source_file")
    error_message = data.get("error_message")
    description = data.get("description")

    return {
        "id": data.get("id"),
        "type": data.get("type"),
        "title": data.get("title"),
        "description": description if description is not None else "",
        "priority": _as_int(data.get("priority"), 3),
        "status": data.get("status") or "pending",
        "source": data.get("source"),
        "source_file": source_file or None,
        "assigned_agent": data.get("assigned_agent"),
        "parent_task_id": data.get("parent_task_id"),
        "stage": data.get("stage"),
        "blocked_by": parse_json_value(data.get("blocked_by"), []),
        "context": parse_json_value(data.get("context"), {}),
        "acceptance_criteria": parse_json_value(data.get("acceptance_criteria"), None),
        "attempts": _as_int(data.get("attempts"), 0),
        "created_at": iso_utc_z(data.get("created_at")),
        "updated_at": iso_utc_z(data.get("updated_at")),
        "started_at": iso_utc_z(data.get("started_at")),
        "completed_at": iso_utc_z(data.get("completed_at")),
        "last_attempt_at": iso_utc_z(data.get("last_attempt_at")),
        "error_message": error_message or None,
        "result": parse_json_value(data.get("result"), {}),
        "commit_sha": data.get("commit_sha") or None,
        "total_execution_time": _as_float(data.get("total_execution_time")),
        "verification_status": data.get("verification_status"),
        "thinking_summary": data.get("thinking_summary"),
        # Provenance: who asked for the work vs who is doing it now. Legacy
        # rows have no value and stay null (unknown, never guessed).
        "created_by": data.get("created_by"),
        "created_by_kind": data.get("created_by_kind"),
        "created_by_session": data.get("created_by_session"),
        "claimed_by": data.get("claimed_by"),
        "lease_expires_at": iso_utc_z(data.get("lease_expires_at")),
    }
