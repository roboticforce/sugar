"""Pure mapping between Hermes Kanban rows and the dashboard task contract.

Kept free of I/O so it can be unit-tested against plain dicts. The contract is
``docs/local-dashboard.md`` §9.2.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

# Board order matches ``hermes_cli.kanban_workflow.DEFAULT_WORKFLOW`` (triage first,
# done last). ``archived`` is a filter, never a column, and is excluded from the
# dashboard's status list.
KANBAN_STATUSES: tuple[str, ...] = (
    "triage",
    "todo",
    "scheduled",
    "ready",
    "running",
    "blocked",
    "review",
    "done",
)
ARCHIVED = "archived"
DEFAULT_PRIORITY = 3
DEFAULT_PROJECT = "task"

# Columns the dashboard renders/compares. Kept explicit so a schema addition in
# Hermes does not accidentally change the wire shape.
_CONTEXT_FIELDS = (
    "claim_lock",
    "claim_expires",
    "block_kind",
    "block_recurrences",
    "workspace_kind",
    "workspace_path",
    "branch_name",
    "project_id",
    "tenant",
    "current_run_id",
    "consecutive_failures",
    "last_heartbeat_at",
)


def iso_timestamp(value: Any) -> Optional[str]:
    """Unix seconds -> ``YYYY-MM-DDTHH:MM:SSZ``; ``None`` when unusable."""
    if value is None or value == "":
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def latest_activity(row: Mapping[str, Any]) -> Optional[str]:
    """Best available ``updated_at``: heartbeat > completed > started > created."""
    values = [
        row.get(key)
        for key in ("last_heartbeat_at", "completed_at", "started_at", "created_at")
    ]
    usable = [value for value in values if value not in (None, "")]
    if not usable:
        return None
    try:
        return iso_timestamp(max(float(value) for value in usable))
    except (TypeError, ValueError):
        return None


def normalize_priority(value: Any) -> int:
    """Coerce to 1..5; anything missing/invalid becomes 3 (the Kanban default)."""
    if isinstance(value, bool):
        return DEFAULT_PRIORITY
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PRIORITY
    return number if 1 <= number <= 5 else DEFAULT_PRIORITY


def facet_value(row: Mapping[str, Any]) -> str:
    """The board's ``type`` facet maps to ``project_id``."""
    project = row.get("project_id")
    return str(project) if project else DEFAULT_PROJECT


def build_context(
    row: Mapping[str, Any], activity: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Kanban-only fields the Sugar task contract has no column for."""
    context: Dict[str, Any] = {}
    for field in _CONTEXT_FIELDS:
        value = row.get(field)
        if value in (None, "", 0) and field not in ("consecutive_failures",):
            continue
        if field == "claim_expires":
            value = iso_timestamp(value)
        if field == "last_heartbeat_at":
            value = iso_timestamp(value)
        if value is None:
            continue
        context[field] = value
    if activity:
        context["activity"] = activity
    return context


def serialize_task(
    row: Mapping[str, Any],
    *,
    parents: Iterable[str] = (),
    activity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Map one Kanban task row onto the dashboard's task JSON shape."""
    parent_ids = [str(parent) for parent in parents]
    result_text = row.get("result")
    return {
        "id": row.get("id"),
        "type": facet_value(row),
        "title": row.get("title") or "",
        "description": row.get("body") or "",
        "priority": normalize_priority(row.get("priority")),
        "status": row.get("status") or "todo",
        "source": row.get("created_by"),
        "source_file": None,
        "assigned_agent": row.get("assignee"),
        "parent_task_id": parent_ids[0] if len(parent_ids) == 1 else None,
        "stage": row.get("current_step_key"),
        "blocked_by": parent_ids,
        "context": build_context(row, activity),
        "acceptance_criteria": None,
        "attempts": int(row.get("attempts") or 0),
        "created_at": iso_timestamp(row.get("created_at")),
        "updated_at": latest_activity(row),
        "started_at": iso_timestamp(row.get("started_at")),
        "completed_at": iso_timestamp(row.get("completed_at")),
        "last_attempt_at": iso_timestamp(row.get("last_heartbeat_at")),
        "error_message": row.get("last_failure_error"),
        "result": {"text": result_text} if result_text else {},
        "commit_sha": None,
        "total_execution_time": 0.0,
        "verification_status": None,
        "thinking_summary": None,
        # Provenance (§10.4): Kanban's ``created_by`` is a plain string with no
        # kind/session columns, so those stay null. The lease maps 1:1.
        "created_by": row.get("created_by") or None,
        "created_by_kind": None,
        "created_by_session": None,
        "claimed_by": row.get("claim_lock") or None,
        "lease_expires_at": iso_timestamp(row.get("claim_expires")),
    }


def status_counts(rows: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    """Counts by status, with every board column present (zero-filled)."""
    counts = {status: 0 for status in KANBAN_STATUSES}
    for row in rows:
        status = row.get("status") or "todo"
        counts[status] = counts.get(status, 0) + 1
    return counts


def validate_status(value: str) -> Optional[str]:
    """Return the status if it is a known board column, else ``None``."""
    return value if value in KANBAN_STATUSES else None


def allowed_task_fields() -> List[str]:
    """Fields a dashboard PATCH may change on a Kanban task."""
    return ["title", "description", "priority", "assigned_agent"]
