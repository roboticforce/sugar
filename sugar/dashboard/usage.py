"""Usage/cost attribution over the event spine (``docs/usage.md``).

Sugar does not bill anything: usage is **reported by harnesses**. A harness
posts an event to ``POST /api/events`` with kind ``usage.reported`` and a payload
like ``{"tokens_in": 1200, "tokens_out": 800, "cost_usd": 0.42, "model": "..."}``;
a ``run.ended`` event carrying the same fields is accepted too. This module is
the single normalizer/aggregator for that telemetry:

- normalize tolerantly (missing/null/int-string fields, negative numbers, NaN
  and non-numeric garbage are ignored — bad telemetry never breaks a surface);
- aggregate into totals and by ``actor``/``harness``/``task``/``project``/``day``/
  ``kind`` (pure functions, no I/O, easy to test);
- read the events back from the spine with SQL-side filters and a bounded scan
  (huge ranges need rollups, not a full-file load; see ``read_usage_events``).

The module is deliberately SQLite/spine-internal, like the rest of the dashboard
backend. There is no pricing table: ``cost_usd`` is whatever the harness says.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import aiosqlite

logger = logging.getLogger(__name__)

#: Event kinds that can carry usage payload fields.
USAGE_REPORTED_KIND = "usage.reported"
RUN_ENDED_KIND = "run.ended"
USAGE_EVENT_KINDS: Tuple[str, ...] = (USAGE_REPORTED_KIND, RUN_ENDED_KIND)

#: Grouping dimensions supported by ``aggregate_usage`` / the API / the CLI.
GROUP_BY_DIMENSIONS: Tuple[str, ...] = (
    "actor",
    "harness",
    "task",
    "project",
    "day",
    "kind",
)

UNKNOWN_GROUP = "unknown"
MAX_GROUP_KEY_LENGTH = 200
MAX_KINDS_FILTER = 50
COST_PRECISION = 6

#: Bounded read window: enough for a month of events on a small team, cheap to
#: sort in memory. Beyond this, aggregate in SQL/rollups instead of paging more.
DEFAULT_EVENT_LIMIT = 5000
MAX_EVENT_LIMIT = 50000
DEFAULT_BATCH_SIZE = 1000


class UsageQueryError(ValueError):
    """Invalid usage query parameter (surfaced as a 400 / CLI error)."""


# ---------------------------------------------------------------------------
# Normalization (tolerant on purpose)
# ---------------------------------------------------------------------------


def _as_count(value: Any) -> Optional[int]:
    """Coerce a token count to a non-negative int; ``None`` when unusable."""
    if value is None or isinstance(value, bool):
        return None
    number: Optional[float]
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number)


def _as_cost(value: Any) -> Optional[float]:
    """Coerce a USD cost to a finite non-negative float; ``None`` when unusable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip().lstrip("$")
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _as_model(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()[:MAX_GROUP_KEY_LENGTH]
    return text or None


def normalize_usage_payload(payload: Any) -> Optional[Dict[str, Any]]:
    """Normalize one usage payload; ``None`` if it carries no usable numbers.

    Missing fields are 0; individually invalid values (negative, NaN, ``"abc"``,
    booleans, nulls) are ignored and simply do not contribute. A payload with no
    usable numeric field at all is *not* a usage event — garbage telemetry is
    skipped rather than counted as a zero-cost event.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    if not isinstance(payload, Mapping):
        return None

    tokens_in = _as_count(payload.get("tokens_in"))
    tokens_out = _as_count(payload.get("tokens_out"))
    cost_usd = _as_cost(payload.get("cost_usd"))
    if tokens_in is None and tokens_out is None and cost_usd is None:
        return None

    usage: Dict[str, Any] = {
        "tokens_in": tokens_in or 0,
        "tokens_out": tokens_out or 0,
        "cost_usd": cost_usd or 0.0,
    }
    usage["tokens_total"] = usage["tokens_in"] + usage["tokens_out"]
    model = _as_model(payload.get("model"))
    if model:
        usage["model"] = model
    return usage


def extract_usage(event: Any) -> Optional[Dict[str, Any]]:
    """Return normalized usage for a usage-bearing event, else ``None``."""
    if not isinstance(event, Mapping):
        return None
    if event.get("kind") not in USAGE_EVENT_KINDS:
        return None
    return normalize_usage_payload(event.get("payload"))


def _empty_metrics() -> Dict[str, Any]:
    return {
        "events": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_total": 0,
        "cost_usd": 0.0,
    }


def _add_usage(metrics: Dict[str, Any], usage: Mapping[str, Any]) -> None:
    metrics["events"] += 1
    metrics["tokens_in"] += int(usage["tokens_in"])
    metrics["tokens_out"] += int(usage["tokens_out"])
    metrics["tokens_total"] += int(usage["tokens_total"])
    metrics["cost_usd"] += float(usage["cost_usd"])


def _finalize_metrics(metrics: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "events": int(metrics["events"]),
        "tokens_in": int(metrics["tokens_in"]),
        "tokens_out": int(metrics["tokens_out"]),
        "tokens_total": int(metrics["tokens_total"]),
        "cost_usd": round(float(metrics["cost_usd"]), COST_PRECISION),
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def validate_group_by(value: Any) -> Optional[str]:
    """Validate a group dimension (``None``/blank = totals only)."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text not in GROUP_BY_DIMENSIONS:
        raise UsageQueryError(
            "group_by must be one of: " + ", ".join(GROUP_BY_DIMENSIONS)
        )
    return text


def _group_value(event: Mapping[str, Any], dimension: str) -> str:
    if dimension == "day":
        ts = event.get("ts")
        if isinstance(ts, str) and len(ts) >= 10 and ts[4] == "-" and ts[7] == "-":
            return ts[:10]
        return UNKNOWN_GROUP
    field = {
        "actor": "actor",
        "harness": "harness",
        "task": "task_id",
        "project": "project",
        "kind": "kind",
    }[dimension]
    value = event.get(field)
    if value is None:
        return UNKNOWN_GROUP
    text = str(value).strip()
    return text[:MAX_GROUP_KEY_LENGTH] if text else UNKNOWN_GROUP


def aggregate_usage(
    events: Iterable[Mapping[str, Any]], *, group_by: Optional[str] = None
) -> Dict[str, Any]:
    """Aggregate usage-bearing events into totals (and optional groups).

    Pure function: no DB access. ``events`` may contain any mix of kinds; only
    ``usage.reported`` / ``run.ended`` events with usable payload fields
    contribute. Groups sort by ``cost_usd`` descending, then key ascending, so
    "where is the money going" is the first thing a client sees.
    """
    dimension = validate_group_by(group_by)
    totals = _empty_metrics()
    grouped: Dict[str, Dict[str, Any]] = {}

    for event in events or ():
        usage = extract_usage(event)
        if usage is None:
            continue
        _add_usage(totals, usage)
        if dimension is None:
            continue
        key = _group_value(event, dimension)
        bucket = grouped.get(key)
        if bucket is None:
            bucket = _empty_metrics()
            grouped[key] = bucket
        _add_usage(bucket, usage)

    groups = [
        {"key": key, **_finalize_metrics(bucket)} for key, bucket in grouped.items()
    ]
    groups.sort(key=lambda group: (-group["cost_usd"], group["key"]))
    return {"totals": _finalize_metrics(totals), "groups": groups}


# ---------------------------------------------------------------------------
# Query-parameter helpers
# ---------------------------------------------------------------------------


def normalize_timestamp(value: Any) -> Optional[str]:
    """Normalize an ISO-8601 timestamp to the spine's ``...Z`` form."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise UsageQueryError(f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_kinds(value: Any) -> List[str]:
    """Normalize a ``kinds`` filter (comma string or iterable) to a list."""
    if value is None:
        return []
    if isinstance(value, str):
        raw: Iterable[Any] = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = [value]
    kinds: List[str] = []
    for item in raw:
        text = str(item).strip() if item is not None else ""
        if text and text not in kinds:
            kinds.append(text[:MAX_GROUP_KEY_LENGTH])
        if len(kinds) >= MAX_KINDS_FILTER:
            break
    return kinds


def normalize_limit(value: Any, default: int = DEFAULT_EVENT_LIMIT) -> int:
    """Parse/limit the event-scan bound (1..``MAX_EVENT_LIMIT``)."""
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise UsageQueryError("limit must be an integer") from exc
    if number < 1:
        raise UsageQueryError("limit must be >= 1")
    return min(number, MAX_EVENT_LIMIT)


# ---------------------------------------------------------------------------
# Reading the spine (bounded, SQL-side filters)
# ---------------------------------------------------------------------------


def _decode_event_row(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("payload")
    if isinstance(payload, str) and payload:
        try:
            row["payload"] = json.loads(payload)
        except ValueError:
            row["payload"] = {"raw": payload}
    elif payload is None:
        row["payload"] = {}
    return row


async def read_usage_events(
    db_path: Any,
    *,
    since: Any = None,
    until: Any = None,
    kinds: Any = None,
    actor: Optional[str] = None,
    harness: Optional[str] = None,
    since_id: int = 0,
    limit: Any = DEFAULT_EVENT_LIMIT,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Read usage-candidate events from the spine, newest last.

    Filtering happens in SQL; the scan is bounded by ``limit`` (clamped to
    ``MAX_EVENT_LIMIT``), so a huge events DB is never loaded whole. Returns
    ``(events, truncated)`` where ``truncated`` means more rows matched than the
    bound. A missing database is an empty range, not an error. Callers that need
    the full history must page with ``since_id`` (or add a rollup).
    """
    path = Path(db_path).expanduser()
    if not path.exists():
        return [], False

    bound = normalize_limit(limit)
    since_ts = normalize_timestamp(since)
    until_ts = normalize_timestamp(until)
    kind_list = normalize_kinds(kinds)

    clauses = ["id > ?"]
    params: List[Any] = [max(0, int(since_id))]
    if since_ts:
        clauses.append("ts >= ?")
        params.append(since_ts)
    if until_ts:
        clauses.append("ts <= ?")
        params.append(until_ts)
    if kind_list:
        clauses.append("kind IN (" + ", ".join("?" for _ in kind_list) + ")")
        params.extend(kind_list)
    if actor:
        clauses.append("actor = ?")
        params.append(str(actor).strip()[:MAX_GROUP_KEY_LENGTH])
    if harness:
        clauses.append("harness = ?")
        params.append(str(harness).strip()[:MAX_GROUP_KEY_LENGTH])

    sql = (
        "SELECT * FROM events WHERE "
        + " AND ".join(clauses)
        + " ORDER BY id ASC LIMIT ?"
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
            # An empty/foreign SQLite file is an empty range, not a crash.
            return [], False
        raise UsageQueryError(f"cannot read usage events: {exc}") from exc

    truncated = len(rows) > bound
    events = [_decode_event_row(dict(row)) for row in rows[:bound]]
    return events, truncated


async def usage_report(
    db_path: Any,
    *,
    since: Any = None,
    until: Any = None,
    group_by: Any = None,
    kinds: Any = None,
    actor: Optional[str] = None,
    harness: Optional[str] = None,
    since_id: int = 0,
    limit: Any = DEFAULT_EVENT_LIMIT,
) -> Dict[str, Any]:
    """Read + aggregate in one call; the API and CLI both use this."""
    dimension = validate_group_by(group_by)
    since_ts = normalize_timestamp(since)
    until_ts = normalize_timestamp(until)
    events, truncated = await read_usage_events(
        db_path,
        since=since_ts,
        until=until_ts,
        kinds=kinds,
        actor=actor,
        harness=harness,
        since_id=since_id,
        limit=limit,
    )
    aggregate = aggregate_usage(events, group_by=dimension)
    return {
        "totals": aggregate["totals"],
        "groups": aggregate["groups"],
        "since": since_ts,
        "until": until_ts,
        "group_by": dimension,
        "events_scanned": len(events),
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Text output (CLI; no external deps)
# --------------------------------------------------------------------------


def _format_cost(value: float) -> str:
    # 4dp reads naturally for agent-sized spend ($0.0042), 2dp once it is real money.
    return f"{value:.2f}" if abs(value) >= 1 else f"{value:.4f}"


def format_usage_table(report: Mapping[str, Any]) -> str:
    """Render a usage report as a compact aligned table."""
    totals = report.get("totals") or _empty_metrics()
    headers = ["", "events", "tokens_in", "tokens_out", "tokens", "cost_usd"]
    rows: List[List[str]] = [["TOTAL"] + _metric_cells(totals)]
    for group in report.get("groups") or []:
        rows.append([str(group.get("key", UNKNOWN_GROUP))] + _metric_cells(group))

    widths = []
    for index in range(len(headers)):
        width = max([len(headers[index])] + [len(row[index]) for row in rows])
        widths.append(width)

    lines = [
        "  ".join(_pad(header, widths[i], i == 0) for i, header in enumerate(headers))
    ]
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        lines.append(
            "  ".join(_pad(cell, widths[i], i == 0) for i, cell in enumerate(row))
        )

    if not report.get("groups"):
        lines.append("")
        lines.append(
            "(no group_by; pass --group-by actor|harness|task|project|day|kind)"
        )
    if report.get("truncated"):
        lines.append("")
        lines.append(
            "(scan truncated at the --limit bound; narrow the range or page with "
            "since_id for full history)"
        )
    return "\n".join(lines)


def _metric_cells(metrics: Mapping[str, Any]) -> List[str]:
    return [
        str(int(metrics.get("events", 0))),
        str(int(metrics.get("tokens_in", 0))),
        str(int(metrics.get("tokens_out", 0))),
        str(int(metrics.get("tokens_total", 0))),
        _format_cost(float(metrics.get("cost_usd", 0.0))),
    ]


def _pad(text: str, width: int, left: bool) -> str:
    return text.ljust(width) if left else text.rjust(width)
