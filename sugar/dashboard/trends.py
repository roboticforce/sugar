"""Time-bucketed work analytics over the event spine (``docs/trends.md``).

Pure computation over a list of event dicts plus a bounded read-only reader over
``events.db``. No new datastore and no pricing table: task counts come from the
``task.*`` events, usage numbers are whatever harnesses reported, and cycle time
is paired within the scanned window.

API contract: ``GET /api/trends`` (see ``docs/trends.md``), CLI: ``sugar trends``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import aiosqlite

from .usage import UNKNOWN_GROUP, normalize_usage_payload

logger = logging.getLogger(__name__)

BUCKETS: Tuple[str, ...] = ("hour", "day", "week")
GROUP_BY_DIMENSIONS: Tuple[str, ...] = ("actor", "harness", "project")
DEFAULT_BUCKET = "day"
DEFAULT_GROUP_BY = "actor"
DEFAULT_WINDOW_DAYS = 7

METRIC_KEYS: Tuple[str, ...] = (
    "tasks_created",
    "tasks_claimed",
    "tasks_completed",
    "tasks_failed",
    "events",
    "tokens_in",
    "tokens_out",
    "cost_usd",
)

TASK_KIND_METRICS = {
    "task.created": "tasks_created",
    "task.claimed": "tasks_claimed",
    "task.completed": "tasks_completed",
    "task.failed": "tasks_failed",
}
USAGE_KINDS = ("usage.reported", "run.ended")

MAX_GROUP_KEY_LENGTH = 200
COST_PRECISION = 6
CYCLE_PRECISION = 1

#: Bounded scan: SQL filters by ts, then at most this many rows are read.
DEFAULT_EVENT_LIMIT = 50_000
MAX_EVENT_LIMIT = 200_000
#: Safety valve for absurd bucket ranges (hour buckets for years).
MAX_BUCKETS = 10_000


class TrendsQueryError(ValueError):
    """Invalid trends query parameter (surfaced as HTTP 400 / CLI error)."""


# ---------------------------------------------------------------------------
# Validation / timestamps / buckets
# ---------------------------------------------------------------------------


def validate_bucket(value: Any) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_BUCKET
    text = str(value).strip().lower()
    if text not in BUCKETS:
        raise TrendsQueryError("bucket must be one of: " + ", ".join(BUCKETS))
    return text


def validate_group_by(value: Any) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_GROUP_BY
    text = str(value).strip().lower()
    if text not in GROUP_BY_DIMENSIONS:
        raise TrendsQueryError(
            "group_by must be one of: " + ", ".join(GROUP_BY_DIMENSIONS)
        )
    return text


def normalize_timestamp(value: Any, field_name: str = "timestamp") -> str:
    """Normalize an ISO-8601 input to the spine's ``YYYY-MM-DDTHH:MM:SSZ`` form."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise TrendsQueryError(f"{field_name} must be an ISO-8601 timestamp")
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise TrendsQueryError(
                f"{field_name} is not a valid ISO-8601 timestamp: {value!r}"
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Tolerant timestamp parse for event rows; ``None`` when unusable."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bucket_start_datetime(moment: datetime, bucket: str) -> datetime:
    if bucket == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    if bucket == "day":
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    # Week: ISO week starts on Monday, 00:00 UTC.
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return start - timedelta(days=start.weekday())


def bucket_start(ts: Any, bucket: str = DEFAULT_BUCKET) -> str:
    """UTC-aligned bucket start for *ts* (hour/day/week; week starts Monday)."""
    bucket_name = validate_bucket(bucket)
    if isinstance(ts, str) and not ts.strip():
        raise TrendsQueryError("timestamp must not be empty")
    parsed = _parse_timestamp(ts)
    if parsed is None:
        raise TrendsQueryError(f"invalid timestamp: {ts!r}")
    return _bucket_start_datetime(parsed, bucket_name).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bucket_step(bucket: str) -> timedelta:
    if bucket == "hour":
        return timedelta(hours=1)
    if bucket == "week":
        return timedelta(days=7)
    return timedelta(days=1)


def _bucket_starts(since: datetime, until: datetime, bucket: str) -> List[str]:
    start = _bucket_start_datetime(since, bucket)
    last = _bucket_start_datetime(until, bucket)
    step = _bucket_step(bucket)
    buckets: List[str] = []
    current = start
    while current <= last:
        buckets.append(current.strftime("%Y-%m-%dT%H:%M:%SZ"))
        if len(buckets) > MAX_BUCKETS:
            raise TrendsQueryError(
                "requested range is too large for this bucket "
                f"(max {MAX_BUCKETS} buckets); use a coarser bucket or narrower range"
            )
        current += step
    return buckets


# ---------------------------------------------------------------------------
# Pure computation
# ---------------------------------------------------------------------------


def _empty_metrics() -> Dict[str, Any]:
    return {
        "tasks_created": 0,
        "tasks_claimed": 0,
        "tasks_completed": 0,
        "tasks_failed": 0,
        "events": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
    }


def _add_usage(metrics: Dict[str, Any], usage: Mapping[str, Any]) -> None:
    metrics["tokens_in"] += int(usage["tokens_in"])
    metrics["tokens_out"] += int(usage["tokens_out"])
    metrics["cost_usd"] += float(usage["cost_usd"])


def _finalize(metrics: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "tasks_created": int(metrics["tasks_created"]),
        "tasks_claimed": int(metrics["tasks_claimed"]),
        "tasks_completed": int(metrics["tasks_completed"]),
        "tasks_failed": int(metrics["tasks_failed"]),
        "events": int(metrics["events"]),
        "tokens_in": int(metrics["tokens_in"]),
        "tokens_out": int(metrics["tokens_out"]),
        "cost_usd": round(float(metrics["cost_usd"]), COST_PRECISION),
    }


def _group_key(event: Mapping[str, Any], dimension: str) -> str:
    field = {
        "actor": "actor",
        "harness": "harness",
        "project": "project",
    }[dimension]
    value = event.get(field)
    if value is None:
        return UNKNOWN_GROUP
    text = str(value).strip()
    return text[:MAX_GROUP_KEY_LENGTH] if text else UNKNOWN_GROUP


def _find_claim(claims: List[datetime], completed_at: datetime) -> Optional[datetime]:
    """Latest claimed timestamp at/before *completed_at* (claims sorted asc)."""
    best: Optional[datetime] = None
    for claimed_at in claims:
        if claimed_at <= completed_at:
            best = claimed_at
        else:
            break
    return best


def compute_trends(
    events: Iterable[Mapping[str, Any]],
    since: Any,
    until: Any,
    bucket: str = DEFAULT_BUCKET,
    group_by: str = DEFAULT_GROUP_BY,
    *,
    truncated: bool = False,
) -> Dict[str, Any]:
    """Compute the ``/api/trends`` response body from a list of events.

    Pure/no I/O. ``events`` is normally the spine row list (payload already
    decoded) pre-filtered to ``[since, until]``; events with unusable ``ts`` are
    ignored for metrics. Series are sorted by group key ascending.
    """
    bucket_name = validate_bucket(bucket)
    dimension = validate_group_by(group_by)
    since_ts = normalize_timestamp(since, "since")
    until_ts = normalize_timestamp(until, "until")
    since_dt = _parse_timestamp(since_ts)
    until_dt = _parse_timestamp(until_ts)
    assert since_dt is not None and until_dt is not None  # normalized above
    if since_dt > until_dt:
        raise TrendsQueryError("since must be earlier than or equal to until")
    buckets = _bucket_starts(since_dt, until_dt, bucket_name)

    series: Dict[str, Dict[str, Any]] = {}
    totals = _empty_metrics()
    event_list = list(events or ())
    event_count = 0
    claims: Dict[str, List[datetime]] = {}
    completed: List[Tuple[str, datetime, Optional[str]]] = []

    for event in event_list:
        if not isinstance(event, Mapping):
            continue
        event_at = _parse_timestamp(event.get("ts"))
        if event_at is None or event_at < since_dt or event_at > until_dt:
            continue
        event_count += 1
        bucket_iso = _bucket_start_datetime(event_at, bucket_name).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        key = _group_key(event, dimension)

        group = series.get(key)
        if group is None:
            group = {
                "points": {name: _empty_metrics() for name in buckets},
                "cycle_sum": 0.0,
                "cycle_count": 0,
            }
            series[key] = group
        point = group["points"].get(bucket_iso)
        if point is None:  # pragma: no cover - bucket list always covers range
            continue

        point["events"] += 1
        totals["events"] += 1
        kind = event.get("kind")
        metric = TASK_KIND_METRICS.get(kind)
        if metric is not None:
            point[metric] += 1
            totals[metric] += 1
        if kind in USAGE_KINDS:
            usage = normalize_usage_payload(event.get("payload"))
            if usage is not None:
                _add_usage(point, usage)
                _add_usage(totals, usage)

        task_id = event.get("task_id")
        if kind == "task.claimed" and task_id:
            claims.setdefault(str(task_id), []).append(event_at)
        elif kind == "task.completed" and task_id:
            completed.append((str(task_id), event_at, key))

    for task_id, completed_at, key in completed:
        task_claims = claims.get(task_id)
        if not task_claims:
            continue
        task_claims.sort()
        claimed_at = _find_claim(task_claims, completed_at)
        if claimed_at is None:
            continue
        delta = (completed_at - claimed_at).total_seconds()
        if delta < 0:  # pragma: no cover - guarded by _find_claim
            continue
        group = series.get(key)
        if group is None:  # pragma: no cover - completed event created it
            continue
        group["cycle_sum"] += delta
        group["cycle_count"] += 1

    series_out = []
    for key in sorted(series):
        group = series[key]
        points = [
            {"bucket": name, **_finalize(group["points"][name])} for name in buckets
        ]
        group_totals = _empty_metrics()
        for point in points:
            for metric in METRIC_KEYS:
                group_totals[metric] += point[metric]
        series_out.append(
            {
                "key": key,
                "points": points,
                "totals": _finalize(group_totals),
                "avg_cycle_time_s": (
                    round(group["cycle_sum"] / group["cycle_count"], CYCLE_PRECISION)
                    if group["cycle_count"]
                    else None
                ),
            }
        )

    return {
        "since": since_ts,
        "until": until_ts,
        "bucket": bucket_name,
        "group_by": dimension,
        "buckets": buckets,
        "series": series_out,
        "totals": _finalize(totals),
        "event_count": event_count,
        "truncated": bool(truncated),
    }


# ---------------------------------------------------------------------------
# Bounded read-only spine access
# ---------------------------------------------------------------------------


def normalize_limit(value: Any, default: int = DEFAULT_EVENT_LIMIT) -> int:
    """Parse the event-scan bound (1..``MAX_EVENT_LIMIT``)."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise TrendsQueryError("limit must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise TrendsQueryError("limit must be an integer") from exc
    if number < 1:
        raise TrendsQueryError("limit must be >= 1")
    return min(number, MAX_EVENT_LIMIT)


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


async def read_trend_events(
    db_path: Any,
    *,
    since: Any = None,
    until: Any = None,
    limit: Any = DEFAULT_EVENT_LIMIT,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Read events from the spine with SQL-side time filters, newest last.

    Returns ``(events, truncated)``; the scan is bounded by ``limit`` (clamped
    to ``MAX_EVENT_LIMIT``). A missing DB/table is an empty range, not an error.
    """
    bound = normalize_limit(limit)
    since_ts = normalize_timestamp(since, "since") if since is not None else None
    until_ts = normalize_timestamp(until, "until") if until is not None else None

    path = Path(db_path).expanduser()
    if not path.exists():
        return [], False

    clauses: List[str] = []
    params: List[Any] = []
    if since_ts:
        clauses.append("ts >= ?")
        params.append(since_ts)
    if until_ts:
        clauses.append("ts <= ?")
        params.append(until_ts)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM events{where} ORDER BY id ASC LIMIT ?"
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
            # Empty/foreign SQLite file: an empty range, not a crash.
            return [], False
        raise TrendsQueryError(f"cannot read trend events: {exc}") from exc

    truncated = len(rows) > bound
    events = [_decode_event_row(dict(row)) for row in rows[:bound]]
    return events, truncated


async def trends_report(
    db_path: Any,
    *,
    since: Any = None,
    until: Any = None,
    bucket: Any = DEFAULT_BUCKET,
    group_by: Any = DEFAULT_GROUP_BY,
    limit: Any = DEFAULT_EVENT_LIMIT,
) -> Dict[str, Any]:
    """Read + compute the ``/api/trends`` response; shared by API and CLI.

    Defaults: ``until = now``, ``since = until - 7 days``, day buckets, grouped
    by actor. All timestamps are UTC and inclusive.
    """
    bucket_name = validate_bucket(bucket)
    dimension = validate_group_by(group_by)
    bound = normalize_limit(limit)

    # Blank query-parameter values mean "use the default", not "invalid".
    since_value = since if since is not None and str(since).strip() else None
    until_value = until if until is not None and str(until).strip() else None

    now = datetime.now(timezone.utc)
    until_dt = (
        _parse_timestamp(normalize_timestamp(until_value, "until"))
        if until_value is not None
        else now
    )
    assert until_dt is not None
    if since_value is not None:
        since_dt = _parse_timestamp(normalize_timestamp(since_value, "since"))
    else:
        since_dt = until_dt - timedelta(days=DEFAULT_WINDOW_DAYS)
    assert since_dt is not None
    if since_dt > until_dt:
        raise TrendsQueryError("since must be earlier than or equal to until")

    since_ts = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    until_ts = until_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    events, truncated = await read_trend_events(
        db_path, since=since_ts, until=until_ts, limit=bound
    )
    return compute_trends(
        events,
        since_ts,
        until_ts,
        bucket_name,
        dimension,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# CLI table rendering
# ---------------------------------------------------------------------------


def _format_cost(value: float) -> str:
    return f"{value:.2f}" if abs(value) >= 1 else f"{value:.4f}"


def _format_cycle(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.1f}s"


def _pad(text: str, width: int, left: bool) -> str:
    return text.ljust(width) if left else text.rjust(width)


def format_trends_table(report: Mapping[str, Any]) -> str:
    """Render a trends report as a compact aligned table."""
    series = list(report.get("series") or [])
    totals = report.get("totals") or _empty_metrics()
    headers = ["group", "completed", "failed", "avg_cycle", "events", "cost_usd"]
    rows: List[List[str]] = [
        [
            "TOTAL",
            str(int(totals.get("tasks_completed", 0))),
            str(int(totals.get("tasks_failed", 0))),
            "—",
            str(int(totals.get("events", 0))),
            _format_cost(float(totals.get("cost_usd", 0.0))),
        ]
    ]
    for item in series:
        item_totals = item.get("totals") or {}
        rows.append(
            [
                str(item.get("key", UNKNOWN_GROUP)),
                str(int(item_totals.get("tasks_completed", 0))),
                str(int(item_totals.get("tasks_failed", 0))),
                _format_cycle(item.get("avg_cycle_time_s")),
                str(int(item_totals.get("events", 0))),
                _format_cost(float(item_totals.get("cost_usd", 0.0))),
            ]
        )

    widths = [
        max([len(headers[index])] + [len(row[index]) for row in rows])
        for index in range(len(headers))
    ]

    header = (
        f"Trends {report.get('since')} → {report.get('until')}  "
        f"bucket={report.get('bucket')} group_by={report.get('group_by')}  "
        f"events={report.get('event_count', 0)}"
        + ("  (truncated)" if report.get("truncated") else "")
    )
    lines = [header, ""]
    lines.append(
        "  ".join(_pad(headers[i], widths[i], i == 0) for i in range(len(headers)))
    )
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        lines.append(
            "  ".join(_pad(row[i], widths[i], i == 0) for i in range(len(headers)))
        )

    if not series:
        lines.append("")
        lines.append("(no events in range)")
    lines.append("")
    lines.append("avg_cycle = mean(completed - latest prior claim) per completed task")
    if report.get("truncated"):
        lines.append(
            "scan truncated at the --limit bound; narrow the range or raise --limit"
        )
    return "\n".join(lines)
