"""Observed AIBOM inventory v0 over the event spine (read-only).

This is an **observed runtime inventory**, not a standards-complete AIBOM. It
answers "what did Sugar actually see running?" from the append-only event spine:
models named by usage/run telemetry, harnesses, tools (and MCP servers that are
classifiably named), and artifacts that were created. It does not scan the repo,
does not read model weights/training data, and does not claim CycloneDX/SPDX
compliance. The schema tag is ``sugar.aibom.v0`` and every response says so in
its ``limitations``.

Design rules:

- read-only, bounded scan (SQL-side project/since filters, clamped ``limit``);
- tolerant payload parsing (flat hook payloads and canonical
  ``sugar.telemetry.v1`` envelopes with a nested ``payload.payload``);
- no secrets, prompts, file contents or raw tool arguments — names/paths only;
- MCP servers are only reported when explicitly present (an ``mcp_server``
  field) or when a tool name follows the ``mcp__<server>__`` convention; they are
  never guessed from arbitrary tool names;
- deterministic ordering/dedupe so the same events produce the same inventory.

Both ``GET /api/aibom`` and ``sugar aibom`` use :func:`aibom_report`; there is no
second aggregation path.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import aiosqlite

SCHEMA = "sugar.aibom.v0"

DEFAULT_EVENT_LIMIT = 5000
MAX_EVENT_LIMIT = 50000
DEFAULT_COMPONENT_LIMIT = 200
MAX_EVIDENCE_LINKS = 5
MAX_NAME_LENGTH = 500

#: Stable component ordering (matches the documented sample order).
COMPONENT_TYPES: Tuple[str, ...] = (
    "model",
    "harness",
    "tool",
    "mcp_server",
    "artifact",
)
_TYPE_ORDER = {name: index for index, name in enumerate(COMPONENT_TYPES)}

#: Only names that unambiguously encode an MCP server are classified.
_MCP_TOOL_PATTERN = re.compile(r"^mcp__([A-Za-z0-9][A-Za-z0-9_.-]*)__(.+)$")

_LIMITATIONS: Tuple[str, ...] = (
    "Observed runtime inventory only; not a complete declared AIBOM",
    "No model weights, training data, licenses, datasets or suppliers",
    "MCP provenance is partial: explicit mcp_server fields or mcp__<server>__ tool names only",
    "Only telemetry that reached the event spine is represented (adapters may be absent)",
    "No file contents, prompts, tool arguments, secrets or hashes beyond reported names/paths",
    "v0 is not CycloneDX or SPDX compliant and makes no standards-conformance claim",
)

_TRACE_UNKNOWN = "unknown"
_FIDELITIES = frozenset({"L0", "L1", "L2", "L3"})


class AibomQueryError(ValueError):
    """Invalid AIBOM query parameter (surfaced as HTTP 400 / CLI error)."""


# ---------------------------------------------------------------------------
# Tolerant payload helpers
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
    """Canonical envelope summary (``payload.payload``), when present."""
    return _mapping(_payload(event).get("payload"))


def _first_text(*values: Any, limit: int = MAX_NAME_LENGTH) -> Optional[str]:
    for value in values:
        if value is None or isinstance(value, (Mapping, list, tuple)):
            continue
        text = str(value).strip()
        if text:
            return text[:limit]
    return None


def _field(event: Mapping[str, Any], *keys: str) -> Optional[str]:
    """Look up a field in the canonical summary, payload, then the event."""
    payload = _payload(event)
    nested = _mapping(payload.get("payload"))
    for source in (nested, payload, event):
        for key in keys:
            text = _first_text(source.get(key))
            if text:
                return text
    return None


def _event_ts(event: Mapping[str, Any]) -> Optional[str]:
    text = _first_text(event.get("ts"))
    return text


def _harness(event: Mapping[str, Any]) -> Optional[str]:
    return _field(event, "harness")


def _model(event: Mapping[str, Any]) -> Optional[str]:
    return _field(event, "model", "model_name", "model_id")


def _tool_name(event: Mapping[str, Any]) -> Optional[str]:
    return _field(event, "tool_name", "tool", "name")


def _artifact_path(event: Mapping[str, Any]) -> Optional[str]:
    return _field(event, "path", "artifact_path", "file_path")


def _fidelity(event: Mapping[str, Any]) -> Optional[str]:
    value = _field(event, "fidelity")
    if value and value.upper() in _FIDELITIES:
        return value.upper()
    return None


def _event_id(event: Mapping[str, Any]) -> Optional[int]:
    value = event.get("id")
    return value if isinstance(value, int) else None


def _event_key(event: Mapping[str, Any]) -> Tuple[int, int]:
    event_id = _event_id(event)
    return (0, event_id) if event_id is not None else (1, 0)


def _trace_id(event: Mapping[str, Any]) -> Optional[str]:
    explicit = _first_text(event.get("trace_id"))
    if explicit:
        return explicit
    harness = _harness(event)
    session = _first_text(event.get("session_id"))
    if harness and session:
        return f"{harness}:{session}"
    return None


def _span_id(event: Mapping[str, Any]) -> Optional[str]:
    explicit = _field(event, "span_id")
    if explicit:
        return explicit
    tool_use = _field(event, "tool_use_id", "tool_call_id", "call_id")
    if tool_use:
        return tool_use
    event_id = _event_id(event)
    if event_id is not None:
        return f"event:{event_id}"
    return None


def _evidence_link(event: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    trace_id = _trace_id(event)
    if not trace_id:
        return None
    return {"trace_id": trace_id, "span_id": _span_id(event) or _TRACE_UNKNOWN}


# ---------------------------------------------------------------------------
# Query validation
# ---------------------------------------------------------------------------


def normalize_since(value: Any) -> Optional[str]:
    """Normalize an ISO-8601 ``since`` bound; ``None`` means "no lower bound"."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = str(value).strip()
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise AibomQueryError("since must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_limit(value: Any) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_EVENT_LIMIT
    if isinstance(value, bool):
        raise AibomQueryError("limit must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise AibomQueryError("limit must be a positive integer") from exc
    if number <= 0:
        raise AibomQueryError("limit must be a positive integer")
    return min(number, MAX_EVENT_LIMIT)


def normalize_project(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# Bounded spine read
# ---------------------------------------------------------------------------


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


async def read_aibom_events(
    db_path: Any,
    *,
    project: Any = None,
    since: Any = None,
    limit: Any = DEFAULT_EVENT_LIMIT,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Read candidate events from the spine, oldest first, bounded.

    Filters are applied in SQL. A missing database or missing ``events`` table is
    an empty range (not an error), matching the other dashboard reports.
    Returns ``(events, truncated)``.
    """
    path = Path(db_path).expanduser()
    if not path.exists():
        return [], False
    bound = normalize_limit(limit)
    since_ts = normalize_since(since)
    project_name = normalize_project(project)

    clauses: List[str] = []
    params: List[Any] = []
    if since_ts:
        clauses.append("ts >= ?")
        params.append(since_ts)
    if project_name:
        clauses.append("project = ?")
        params.append(project_name)
    sql = "SELECT * FROM events"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id ASC LIMIT ?"

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
        raise AibomQueryError(f"cannot read AIBOM events: {exc}") from exc

    truncated = len(rows) > bound
    return [_decode_row(dict(row)) for row in rows[:bound]], truncated


# ---------------------------------------------------------------------------
# Aggregation (pure)
# ---------------------------------------------------------------------------


class _Component:
    __slots__ = (
        "type",
        "name",
        "events",
        "projects",
        "first_seen",
        "last_seen",
        "links",
        "link_keys",
    )

    def __init__(self, component_type: str, name: str) -> None:
        self.type = component_type
        self.name = name
        self.events = 0
        self.projects: set = set()
        self.first_seen: Optional[str] = None
        self.last_seen: Optional[str] = None
        self.links: List[Dict[str, str]] = []
        self.link_keys: set = set()

    def observe(self, event: Mapping[str, Any]) -> None:
        self.events += 1
        project = _first_text(event.get("project"))
        if project:
            self.projects.add(project)
        ts = _event_ts(event)
        if ts:
            if self.first_seen is None or ts < self.first_seen:
                self.first_seen = ts
            if self.last_seen is None or ts > self.last_seen:
                self.last_seen = ts
        link = _evidence_link(event)
        if link is not None and len(self.links) < MAX_EVIDENCE_LINKS:
            key = (link["trace_id"], link["span_id"])
            if key not in self.link_keys:
                self.link_keys.add(key)
                self.links.append(link)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "events": self.events,
            "projects": sorted(self.projects),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "evidence_links": self.links,
        }


def _component_key(component_type: str, name: str) -> Tuple[str, str]:
    return (component_type, name)


def _tool_execution_key(event: Mapping[str, Any]) -> Tuple[str, str]:
    harness = _harness(event) or ""
    session = _first_text(event.get("session_id")) or ""
    tool_use = _field(event, "tool_use_id", "tool_call_id", "call_id") or ""
    if not tool_use:
        event_id = _event_id(event)
        tool_use = f"event:{event_id}" if event_id is not None else ""
    return (f"{harness}:{session}", tool_use)


def _mcp_server_for_tool(tool_name: str) -> Optional[str]:
    match = _MCP_TOOL_PATTERN.match(tool_name)
    return match.group(1) if match else None


def _explicit_mcp_server(event: Mapping[str, Any]) -> Optional[str]:
    """Only an explicitly reported server field counts; never infer it."""
    return _field(event, "mcp_server", "mcpServer", "server_name")


def aggregate_aibom(
    events: Iterable[Mapping[str, Any]],
    *,
    project: Any = None,
    since: Any = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Pure aggregation over already-fetched spine events (oldest first)."""
    ordered = [dict(event) for event in events]
    # The reader orders by spine id; tolerate unsorted test input without
    # changing the relative order of events that have no spine id.
    ordered.sort(key=lambda event: _event_key(event))

    components: Dict[Tuple[str, str], _Component] = {}
    by_harness: Dict[str, int] = {}
    by_fidelity: Dict[str, int] = {}
    started_calls: set = set()

    def component(component_type: str, name: str) -> _Component:
        key = _component_key(component_type, name)
        existing = components.get(key)
        if existing is None:
            existing = _Component(component_type, name)
            components[key] = existing
        return existing

    for event in ordered:
        harness = _harness(event)
        if harness:
            by_harness[harness] = by_harness.get(harness, 0) + 1
        fidelity = _fidelity(event)
        if fidelity:
            by_fidelity[fidelity] = by_fidelity.get(fidelity, 0) + 1

        model = _model(event)
        if model:
            component("model", model).observe(event)

        if harness:
            component("harness", harness).observe(event)

        kind = str(event.get("kind") or "")
        if kind == "tool.started":
            tool = _tool_name(event)
            if tool:
                key = _tool_execution_key(event)
                started_calls.add(key)
                component("tool", tool).observe(event)
                mcp_server = _explicit_mcp_server(event) or _mcp_server_for_tool(tool)
                if mcp_server:
                    component("mcp_server", mcp_server).observe(event)
        elif kind in ("tool.completed", "tool.failed"):
            tool = _tool_name(event)
            if tool:
                key = _tool_execution_key(event)
                if key not in started_calls:
                    started_calls.add(key)
                    component("tool", tool).observe(event)
                    mcp_server = _explicit_mcp_server(event) or _mcp_server_for_tool(
                        tool
                    )
                    if mcp_server:
                        component("mcp_server", mcp_server).observe(event)

        if kind == "artifact.created":
            path = _artifact_path(event)
            if path:
                component("artifact", path).observe(event)

    rendered = sorted(
        (item.as_dict() for item in components.values()),
        key=lambda item: (
            _TYPE_ORDER.get(str(item["type"]), len(COMPONENT_TYPES)),
            str(item["name"]),
        ),
    )
    component_limit = DEFAULT_COMPONENT_LIMIT
    truncated = len(rendered) > component_limit
    report_components = rendered[:component_limit]

    return {
        "schema": SCHEMA,
        "generated_at": generated_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": {
            "project": normalize_project(project),
            "since": normalize_since(since),
        },
        "components": report_components,
        "coverage": {
            "by_harness": dict(sorted(by_harness.items())),
            "by_fidelity": dict(sorted(by_fidelity.items())),
        },
        "limitations": list(_LIMITATIONS),
        "events_scanned": len(ordered),
        "component_limit": component_limit,
        "truncated": truncated,
    }


async def aibom_report(
    db_path: Any,
    *,
    project: Any = None,
    since: Any = None,
    limit: Any = DEFAULT_EVENT_LIMIT,
) -> Dict[str, Any]:
    """Read + aggregate the observed AIBOM inventory (API and CLI entry point)."""
    events, scan_truncated = await read_aibom_events(
        db_path, project=project, since=since, limit=limit
    )
    report = aggregate_aibom(
        events,
        project=project,
        since=since,
    )
    if scan_truncated:
        report["truncated"] = True
    report["events_scanned"] = len(events)
    report["limit"] = normalize_limit(limit)
    return report


# ---------------------------------------------------------------------------
# CLI table
# ---------------------------------------------------------------------------


def _cell(value: Any, width: int) -> str:
    text = "" if value is None else str(value)
    if len(text) > width:
        text = text[: max(0, width - 3)] + "..."
    return text.ljust(width)


def format_aibom_table(report: Mapping[str, Any]) -> str:
    """Render the observed inventory as a compact aligned table + summary."""
    components = list(report.get("components") or [])
    headers = ["TYPE", "NAME", "EVENTS", "PROJECTS", "FIRST_SEEN", "LAST_SEEN"]
    rows: List[List[str]] = []
    widths = [len(header) for header in headers]
    for component in components:
        row = [
            str(component.get("type") or ""),
            str(component.get("name") or ""),
            str(int(component.get("events") or 0)),
            ", ".join(component.get("projects") or []),
            str(component.get("first_seen") or ""),
            str(component.get("last_seen") or ""),
        ]
        rows.append(row)
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    widths[1] = min(widths[1], 48)

    lines = ["  ".join(_cell(header, widths[i]) for i, header in enumerate(headers))]
    lines.append("  ".join("-" * width for width in widths))
    if not rows:
        lines.append("(no observed components)")
    for row in rows:
        lines.append("  ".join(_cell(cell, widths[i]) for i, cell in enumerate(row)))

    counts: Dict[str, int] = {}
    for component in components:
        component_type = str(component.get("type") or "")
        counts[component_type] = counts.get(component_type, 0) + 1
    coverage = report.get("coverage") or {}
    by_harness = coverage.get("by_harness") or {}
    by_fidelity = coverage.get("by_fidelity") or {}
    lines.append("")
    lines.append(
        "Components: "
        + str(len(components))
        + (
            " (" + ", ".join(f"{key} {counts[key]}" for key in sorted(counts)) + ")"
            if counts
            else ""
        )
    )
    lines.append(
        "Coverage: harnesses "
        + (
            ", ".join(f"{key}={value}" for key, value in sorted(by_harness.items()))
            or "none"
        )
        + "; fidelity "
        + (
            ", ".join(f"{key}={value}" for key, value in sorted(by_fidelity.items()))
            or "none"
        )
    )
    lines.append(f"Events scanned: {int(report.get('events_scanned') or 0)}")
    if report.get("truncated"):
        lines.append("(scan or component list truncated at the --limit bound)")
    lines.append("Limitations:")
    for limitation in report.get("limitations") or []:
        lines.append(f"- {limitation}")
    return "\n".join(lines)
