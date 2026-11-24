"""Attention/risk signals and trace insights (Human layer backend).

Read-only projections over the existing task board + event spine:

* :func:`attention_report` builds the frozen ``GET /api/attention`` items from
  failed/running traces, failed/held tasks, verification gaps, coverage gaps,
  policy/approval events and conservative spend gaps.
* :func:`trace_insights` adds the additive ``insights`` object to
  ``GET /api/traces/{trace_id}`` (no LLM generation; derived from spans/status,
  verification evidence, coverage and usage).

Everything degrades to an empty/smaller signal set when a source is missing or
unreadable; no item is fabricated.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import aiosqlite

from . import pricing
from .traces import (
    MAX_TRACE_LIMIT,
    TraceQueryError,
    read_trace_events,
    split_trace_id,
    trace_list_report,
)
from .usage import UsageQueryError, aggregate_usage, usage_report
from ..telemetry.coverage import coverage_report

logger = logging.getLogger(__name__)

DEFAULT_ATTENTION_LIMIT = 50
MAX_ATTENTION_LIMIT = 200
LONG_RUNNING_ENV = "SUGAR_ATTENTION_LONG_RUNNING_SECONDS"
DEFAULT_LONG_RUNNING_SECONDS = 2 * 60 * 60
MAX_POLICY_EVENTS = 2_000
MAX_VERIFIED_TASKS = 10_000
MAX_ITEM_TEXT = 500

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
ATTENTION_KINDS = (
    "failed_session",
    "blocked_task",
    "running_too_long",
    "unverified_evidence",
    "coverage_gap",
    "policy_denied",
    "approval_pending",
    "spend_anomaly",
)


class AttentionQueryError(ValueError):
    """Invalid attention query parameter (surfaced as HTTP 400)."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now(now: Any = None) -> datetime:
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if isinstance(now, str) and now.strip():
        parsed = _parse_iso(now)
        if parsed is not None:
            return parsed
    return datetime.now(timezone.utc)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any, fallback: datetime) -> str:
    parsed = _parse_iso(value)
    return (
        parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
        if parsed
        else fallback.strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _text(value: Any, limit: int = MAX_ITEM_TEXT) -> Optional[str]:
    if value is None or isinstance(value, (Mapping, list, tuple)):
        return None
    text = str(value).replace("\n", " ").strip()
    return text[:limit] if text else None


def _payload(event: Mapping[str, Any]) -> Dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _summary(event: Mapping[str, Any]) -> Dict[str, Any]:
    nested = _payload(event).get("payload")
    return dict(nested) if isinstance(nested, Mapping) else {}


def _field(event: Mapping[str, Any], *keys: str) -> Any:
    payload = _payload(event)
    nested = _summary(event)
    for source in (payload, nested):
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return value
    for key in keys:
        value = event.get(key)
        if value not in (None, ""):
            return value
    return None


def _event_status(event: Mapping[str, Any]) -> str:
    value = _field(event, "status", "decision", "outcome")
    text = str(value or "").strip().lower()
    return text


def _event_trace_id(event: Mapping[str, Any]) -> Optional[str]:
    explicit = _text(event.get("trace_id"), 300)
    if explicit:
        return explicit
    session = _text(event.get("session_id"), 300) or _text(
        _field(event, "session_id"), 300
    )
    if not session:
        return None
    harness = _text(event.get("harness"), 300) or _text(_field(event, "harness"), 300)
    return f"{harness or 'unknown'}:{session}"


def _event_project(event: Mapping[str, Any]) -> Optional[str]:
    value = _text(event.get("project"), 200) or _text(_field(event, "project"), 200)
    if value:
        return value
    cwd = _text(_field(event, "cwd"), 1000)
    if cwd:
        cleaned = cwd.rstrip("/\\")
        if cleaned:
            return cleaned.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:200] or None
    return None


def _event_cwd(event: Mapping[str, Any]) -> Optional[str]:
    return _text(_field(event, "cwd"), 1000)


def _normalize_project(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text[:200] or None


def normalize_attention_limit(value: Any) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_ATTENTION_LIMIT
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise AttentionQueryError("limit must be an integer") from None
    if number < 1 or number > MAX_ATTENTION_LIMIT:
        raise AttentionQueryError(f"limit must be between 1 and {MAX_ATTENTION_LIMIT}")
    return number


def long_running_seconds() -> int:
    raw = os.environ.get(LONG_RUNNING_ENV, "").strip()
    if raw:
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return DEFAULT_LONG_RUNNING_SECONDS


def _task_context(task: Mapping[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    context = task.get("context")
    if not isinstance(context, Mapping):
        context = {}
    project = _text(context.get("project"), 200)
    cwd = _text(context.get("cwd"), 1000)
    if not project and cwd:
        cleaned = cwd.rstrip("/\\")
        if cleaned:
            project = cleaned.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:200] or None
    return project, cwd


def _make_item(
    *,
    item_id: str,
    kind: str,
    severity: str,
    title: str,
    detail: str,
    project: Optional[str],
    cwd: Optional[str],
    trace_id: Optional[str],
    task_id: Optional[str],
    ts: str,
    href: str,
) -> Dict[str, Any]:
    return {
        "id": item_id[:400],
        "kind": kind,
        "severity": severity,
        "title": (title or kind)[:MAX_ITEM_TEXT],
        "detail": (detail or "")[:MAX_ITEM_TEXT],
        "project": project,
        "cwd": cwd,
        "trace_id": trace_id,
        "task_id": task_id,
        "ts": ts,
        "href": href,
    }


# ---------------------------------------------------------------------------
# Signal builders
# ---------------------------------------------------------------------------


def _failed_trace_items(
    traces: Sequence[Mapping[str, Any]], now: datetime
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for trace in traces:
        if str(trace.get("status") or "") != "failed":
            continue
        trace_id = _text(trace.get("trace_id"), 300)
        if not trace_id:
            continue
        title = _text(trace.get("title"), 200) or trace_id
        items.append(
            _make_item(
                item_id=f"failed_session:{trace_id}",
                kind="failed_session",
                severity="high",
                title=f"Failed session: {title}",
                detail=(
                    f"Trace {trace_id} ended with failures "
                    f"(harness {trace.get('harness') or 'unknown'})."
                ),
                project=_text(trace.get("project"), 200),
                cwd=_text(trace.get("cwd"), 1000),
                trace_id=trace_id,
                task_id=None,
                ts=_iso(trace.get("ended_at") or trace.get("started_at"), now),
                href=f"/traces/{trace_id}",
            )
        )
    return items


def _long_running_items(
    traces: Sequence[Mapping[str, Any]], now: datetime, threshold: int
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for trace in traces:
        if str(trace.get("status") or "") != "running":
            continue
        trace_id = _text(trace.get("trace_id"), 300)
        started = _parse_iso(trace.get("started_at"))
        if not trace_id or started is None:
            continue
        age = (now - started).total_seconds()
        if age < threshold:
            continue
        hours = max(1, int(age // 3600))
        title = _text(trace.get("title"), 200) or trace_id
        items.append(
            _make_item(
                item_id=f"running_too_long:{trace_id}",
                kind="running_too_long",
                severity="medium",
                title=f"Session running too long: {title}",
                detail=f"Trace {trace_id} has been running for about {hours}h.",
                project=_text(trace.get("project"), 200),
                cwd=_text(trace.get("cwd"), 1000),
                trace_id=trace_id,
                task_id=None,
                ts=_iso(trace.get("started_at"), now),
                href=f"/traces/{trace_id}",
            )
        )
    return items


def _task_items(
    tasks: Sequence[Mapping[str, Any]],
    verified_task_ids: Set[str],
    now: datetime,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for task in tasks:
        task_id = _text(task.get("id"), 300)
        if not task_id:
            continue
        status = str(task.get("status") or "").lower()
        project, cwd = _task_context(task)
        title = _text(task.get("title"), 200) or task_id
        updated = (
            task.get("completed_at") or task.get("updated_at") or task.get("created_at")
        )
        if status in ("failed", "hold"):
            severity = "high" if status == "failed" else "medium"
            if status == "failed":
                item_title = f"Task failed: {title}"
                detail = _text(task.get("error_message"), MAX_ITEM_TEXT) or (
                    f"Task {task_id} is marked failed."
                )
            else:
                item_title = f"Task on hold: {title}"
                context = task.get("context")
                reason = (
                    _text(context.get("hold_reason"), MAX_ITEM_TEXT)
                    if isinstance(context, Mapping)
                    else None
                )
                detail = reason or f"Task {task_id} is on hold."
            items.append(
                _make_item(
                    item_id=f"blocked_task:{task_id}",
                    kind="blocked_task",
                    severity=severity,
                    title=item_title,
                    detail=detail,
                    project=project,
                    cwd=cwd,
                    trace_id=None,
                    task_id=task_id,
                    ts=_iso(updated, now),
                    href=f"/tasks/{task_id}",
                )
            )
        elif status == "pending" and _text(task.get("error_message"), MAX_ITEM_TEXT):
            items.append(
                _make_item(
                    item_id=f"blocked_task:{task_id}",
                    kind="blocked_task",
                    severity="high",
                    title=f"Task retrying after failure: {title}",
                    detail=_text(task.get("error_message"), MAX_ITEM_TEXT)
                    or f"Task {task_id} failed and is queued for retry.",
                    project=project,
                    cwd=cwd,
                    trace_id=None,
                    task_id=task_id,
                    ts=_iso(updated, now),
                    href=f"/tasks/{task_id}",
                )
            )
        elif status == "completed":
            if task_id in verified_task_ids:
                continue
            verification = (_text(task.get("verification_status"), 100) or "").lower()
            if verification and verification not in (
                "pending",
                "unknown",
                "none",
                "not_run",
                "not-run",
            ):
                continue
            items.append(
                _make_item(
                    item_id=f"unverified_evidence:{task_id}",
                    kind="unverified_evidence",
                    severity="medium",
                    title=f"Task completed without verification: {title}",
                    detail=(
                        f"Task {task_id} has no verification.check_result / "
                        "verification.test_run evidence."
                    ),
                    project=project,
                    cwd=cwd,
                    trace_id=None,
                    task_id=task_id,
                    ts=_iso(updated, now),
                    href=f"/tasks/{task_id}",
                )
            )
    return items


def _coverage_items(
    coverage: Mapping[str, Any],
    now: datetime,
    active_harnesses: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    generated = coverage.get("generated_at")
    active = active_harnesses or set()
    for entry in coverage.get("harnesses") or []:
        if not isinstance(entry, Mapping):
            continue
        harness = _text(entry.get("harness"), 200)
        fidelity = str(entry.get("fidelity") or "").upper()
        events = int(entry.get("events_24h") or 0)
        if not harness or fidelity not in ("L0", "L1"):
            continue
        if events <= 0 and harness not in active:
            continue
        display = _text(entry.get("display_name"), 200) or harness
        items.append(
            _make_item(
                item_id=f"coverage_gap:{harness}",
                kind="coverage_gap",
                severity="medium",
                title=f"Coverage gap: {display} is {fidelity}",
                detail=(
                    f"{display} produced {events} events in the last 24h but "
                    f"adapter fidelity is {fidelity}; richer telemetry is not wired."
                ),
                project=None,
                cwd=None,
                trace_id=None,
                task_id=None,
                ts=_iso(generated, now),
                href="/coverage",
            )
        )
    return items


def _spend_items(usage: Mapping[str, Any], now: datetime) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    groups = usage.get("groups") or []
    if groups:
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            tokens = int(group.get("tokens_total") or 0)
            if tokens <= 0:
                continue
            basis = str(group.get("cost_basis") or "unavailable")
            reported = float(group.get("cost_usd") or 0.0)
            estimated = float(group.get("estimated_cost_usd") or 0.0)
            if basis != "unavailable" and not (reported == 0 and estimated == 0):
                continue
            key = _text(group.get("key"), 200)
            project = key if key and key != "unknown" else None
            label = project or "all projects"
            items.append(
                _make_item(
                    item_id=f"spend_anomaly:{label}",
                    kind="spend_anomaly",
                    severity="low",
                    title=f"Spend not attributable: {label}",
                    detail=(
                        f"{tokens} tokens have no reported or estimated cost "
                        f"(cost_basis={basis})."
                    ),
                    project=project,
                    cwd=None,
                    trace_id=None,
                    task_id=None,
                    ts=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    href="/usage",
                )
            )
        return items
    totals = usage.get("totals") or {}
    tokens = int(totals.get("tokens_total") or 0)
    if tokens > 0:
        basis = str(totals.get("cost_basis") or "unavailable")
        reported = float(totals.get("cost_usd") or 0.0)
        estimated = float(totals.get("estimated_cost_usd") or 0.0)
        if basis == "unavailable" and reported == 0 and estimated == 0:
            items.append(
                _make_item(
                    item_id="spend_anomaly:all",
                    kind="spend_anomaly",
                    severity="low",
                    title="Spend not attributable",
                    detail=f"{tokens} tokens have no cost attribution.",
                    project=None,
                    cwd=None,
                    trace_id=None,
                    task_id=None,
                    ts=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    href="/usage",
                )
            )
    return items


def _policy_items(
    events: Sequence[Mapping[str, Any]], now: datetime
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for event in events:
        kind = str(event.get("kind") or "")
        status = _event_status(event)
        pending = (
            kind in ("approval.requested", "approval.pending", "approval.asked")
            or kind.startswith("approval.")
            and status in ("pending", "requested", "waiting", "asked")
        )
        denied = kind in ("approval.denied", "approval.rejected") or (
            kind in ("policy.decision", "policy.denied")
            and status in ("denied", "deny", "blocked", "rejected", "reject", "failed")
        )
        if not pending and not denied:
            continue
        name = (
            _text(_field(event, "policy", "approval", "tool_name", "name"), 200)
            or "decision"
        )
        project = _event_project(event)
        cwd = _event_cwd(event)
        trace_id = _event_trace_id(event)
        task_id = _text(event.get("task_id"), 300)
        reason = _text(_field(event, "reason", "description", "message"), 300)
        detail = reason or f"{kind} status={status or 'unknown'}"
        summary = _summary(event)
        payload_map = _payload(event)
        identity = (
            _text(summary.get("id"), 300)
            or _text(summary.get("approval_id"), 300)
            or _text(payload_map.get("approval_id"), 300)
            or _text(payload_map.get("id"), 300)
            or name
        )
        items.append(
            _make_item(
                item_id=(
                    ("approval_pending:" if pending else "policy_denied:")
                    + f"{kind}:{event.get('session_id')}:{identity}:{event.get('ts')}"
                ),
                kind="approval_pending" if pending else "policy_denied",
                severity="high",
                title=(
                    f"Approval pending: {name}" if pending else f"Policy denied: {name}"
                ),
                detail=detail,
                project=project,
                cwd=cwd,
                trace_id=trace_id,
                task_id=task_id,
                ts=_iso(event.get("ts"), now),
                href=f"/traces/{trace_id}" if trace_id else "/evidence",
            )
        )
    return items


def collect_attention_items(
    *,
    traces: Sequence[Mapping[str, Any]] = (),
    tasks: Sequence[Mapping[str, Any]] = (),
    verified_task_ids: Set[str] = frozenset(),
    coverage: Optional[Mapping[str, Any]] = None,
    usage: Optional[Mapping[str, Any]] = None,
    policy_events: Sequence[Mapping[str, Any]] = (),
    now: Any = None,
    project: Optional[str] = None,
    limit: Any = DEFAULT_ATTENTION_LIMIT,
    threshold_seconds: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Build, dedupe, filter and sort attention items (pure)."""
    moment = _now(now)
    bound = normalize_attention_limit(limit)
    threshold = (
        threshold_seconds if threshold_seconds is not None else long_running_seconds()
    )
    items: List[Dict[str, Any]] = []
    items.extend(_failed_trace_items(traces, moment))
    items.extend(_long_running_items(traces, moment, max(1, int(threshold))))
    items.extend(_task_items(tasks, set(verified_task_ids), moment))
    if coverage:
        active_harnesses = {
            value
            for trace in traces
            for value in (_text(trace.get("harness"), 200),)
            if value
        }
        items.extend(_coverage_items(coverage, moment, active_harnesses))
    if usage:
        items.extend(_spend_items(usage, moment))
    if policy_events:
        items.extend(_policy_items(policy_events, moment))

    project_filter = _normalize_project(project)
    deduped: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if project_filter and str(item.get("project") or "") != project_filter:
            continue
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        existing = deduped.get(item_id)
        if existing is None or SEVERITY_RANK.get(
            str(item.get("severity")), 3
        ) < SEVERITY_RANK.get(str(existing.get("severity")), 3):
            deduped[item_id] = dict(item)
    result = list(deduped.values())
    result.sort(key=lambda item: str(item.get("ts") or ""), reverse=True)
    result.sort(key=lambda item: SEVERITY_RANK.get(str(item.get("severity")), 3))
    return result[:bound]


# ---------------------------------------------------------------------------
# Spine readers
# ---------------------------------------------------------------------------


async def _read_events(
    db_path: Any, sql: str, params: Sequence[Any] = ()
) -> List[Dict[str, Any]]:
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
            cursor = await db.execute(sql, list(params))
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        logger.debug("attention: cannot read events: %s", exc)
        return []
    decoded: List[Dict[str, Any]] = []
    for row in rows:
        event = dict(row)
        payload = event.get("payload")
        if isinstance(payload, str) and payload:
            try:
                event["payload"] = json.loads(payload)
            except ValueError:
                event["payload"] = {"raw": payload}
        elif payload is None:
            event["payload"] = {}
        decoded.append(event)
    return decoded


async def _read_verified_task_ids(db_path: Any) -> Set[str]:
    rows = await _read_events(
        db_path,
        "SELECT DISTINCT task_id FROM events WHERE kind LIKE 'verification.%' "
        "AND task_id IS NOT NULL LIMIT ?",
        (MAX_VERIFIED_TASKS,),
    )
    return {
        str(row.get("task_id")) for row in rows if str(row.get("task_id") or "").strip()
    }


async def _read_policy_events(db_path: Any) -> List[Dict[str, Any]]:
    return await _read_events(
        db_path,
        "SELECT * FROM events WHERE kind LIKE 'approval.%' "
        "OR kind = 'policy.decision' OR kind = 'policy.denied' "
        "ORDER BY id DESC LIMIT ?",
        (MAX_POLICY_EVENTS,),
    )


async def attention_report(
    db_path: Any,
    *,
    tasks: Sequence[Mapping[str, Any]] = (),
    project: Any = None,
    limit: Any = DEFAULT_ATTENTION_LIMIT,
    now: Any = None,
) -> Dict[str, Any]:
    """Collect all attention sources and return the frozen API response."""
    moment = _now(now)
    bound = normalize_attention_limit(limit)
    traces: List[Dict[str, Any]] = []
    try:
        report = await trace_list_report(db_path, limit=MAX_TRACE_LIMIT)
        traces = list(report.get("traces") or [])
    except (TraceQueryError, Exception) as exc:  # noqa: B014 - degrade, never 500
        logger.debug("attention: traces unavailable: %s", exc)
    verified = await _read_verified_task_ids(db_path)
    policy_events = await _read_policy_events(db_path)
    coverage: Dict[str, Any] = {}
    try:
        coverage = await coverage_report(db_path, now=moment)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("attention: coverage unavailable: %s", exc)
    usage: Dict[str, Any] = {}
    try:
        usage = await usage_report(db_path, group_by="project", limit=5000)
    except (UsageQueryError, Exception) as exc:  # noqa: B014 - degrade
        logger.debug("attention: usage unavailable: %s", exc)
    items = collect_attention_items(
        traces=traces,
        tasks=tasks,
        verified_task_ids=verified,
        coverage=coverage,
        usage=usage,
        policy_events=policy_events,
        now=moment,
        project=project,
        limit=bound,
    )
    return {"items": items}


# ---------------------------------------------------------------------------
# Trace insights (additive to GET /api/traces/{trace_id})
# ---------------------------------------------------------------------------


def _flag(kind: str, severity: str, detail: str) -> Dict[str, str]:
    return {"kind": kind, "severity": severity, "detail": detail[:MAX_ITEM_TEXT]}


def build_trace_insights(
    trace_report: Mapping[str, Any],
    *,
    events: Sequence[Mapping[str, Any]] = (),
    verified: bool = False,
    coverage_entry: Optional[Mapping[str, Any]] = None,
    usage: Optional[Mapping[str, Any]] = None,
    now: Any = None,
    threshold_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Derive the frozen ``insights`` object from a trace report (pure)."""
    moment = _now(now)
    summary = trace_report.get("trace") if isinstance(trace_report, Mapping) else {}
    if not isinstance(summary, Mapping):
        summary = {}
    spans = trace_report.get("spans") if isinstance(trace_report, Mapping) else []
    if not isinstance(spans, list):
        spans = []
    threshold = (
        threshold_seconds if threshold_seconds is not None else long_running_seconds()
    )
    flags: List[Dict[str, str]] = []
    bullets: List[str] = []

    failed_spans = [
        span
        for span in spans
        if isinstance(span, Mapping)
        and str(span.get("status") or "") == "failed"
        and str(span.get("kind") or "") not in ("approval", "session", "subagent")
    ]
    blocked_spans = [
        span
        for span in spans
        if isinstance(span, Mapping) and str(span.get("status") or "") == "blocked"
    ]
    policy_spans = [
        span
        for span in spans
        if isinstance(span, Mapping)
        and str(span.get("kind") or "") == "approval"
        and str(span.get("status") or "") in ("failed", "blocked")
    ]
    for span in failed_spans[:3]:
        detail = f"{span.get('kind')} {span.get('name')} failed"
        flags.append(_flag("failure", "high", detail))
        bullets.append(detail)
    if str(summary.get("status") or "") == "failed" and not flags:
        flags.append(_flag("failure", "high", "trace ended with failures"))
        bullets.append("trace ended with failures")
    for span in policy_spans[:3]:
        detail = f"approval {span.get('name')} {span.get('status')}"
        flags.append(_flag("policy", "high", detail))
        bullets.append(detail)
    for span in blocked_spans[:3]:
        detail = f"{span.get('kind')} {span.get('name')} blocked"
        if not any(flag["detail"] == detail for flag in flags):
            flags.append(_flag("failure", "high", detail))
            bullets.append(detail)

    ended = str(summary.get("status") or "") != "running"
    if ended and not verified:
        flags.append(
            _flag("unverified", "medium", "no verification evidence for this session")
        )
        bullets.append("no verification evidence")

    harness = _text(summary.get("harness"), 200)
    if coverage_entry:
        fidelity = str(coverage_entry.get("fidelity") or "").upper()
        notes = str(coverage_entry.get("notes") or "").lower()
        event_only = notes.startswith("seen in telemetry events")
        # Event-derived fidelity (no adapter registry entry) is not an adapter
        # gap by itself; otherwise every single event-only trace would be
        # permanently "watch".
        if fidelity in ("L0", "L1") and not (
            event_only and not coverage_entry.get("installed")
        ):
            detail = f"{harness or 'harness'} coverage is {fidelity}"
            flags.append(_flag("coverage", "medium", detail))
            bullets.append(detail)

    tokens = int((summary.get("tokens_total") or 0))
    if usage:
        totals = usage.get("totals") or {}
        tokens = int(totals.get("tokens_total") or tokens)
        basis = str(totals.get("cost_basis") or "unavailable")
        reported = float(totals.get("cost_usd") or 0.0)
        estimated = float(totals.get("estimated_cost_usd") or 0.0)
        if tokens > 0 and basis == "unavailable" and reported == 0 and estimated == 0:
            detail = f"{tokens} tokens have no cost attribution"
            flags.append(_flag("spend", "low", detail))
            bullets.append(detail)

    started = _parse_iso(summary.get("started_at"))
    if str(summary.get("status") or "") == "running" and started is not None:
        age = (moment - started).total_seconds()
        if age >= max(1, int(threshold)):
            hours = max(1, int(age // 3600))
            detail = f"session has been running for about {hours}h"
            flags.append(_flag("long_running", "medium", detail))
            bullets.append(detail)

    if failed_spans:
        risk = "failed"
        headline = "Session failed"
    elif blocked_spans:
        risk = "blocked"
        headline = "Session blocked"
    elif (
        any(str(span.get("status") or "") == "failed" for span in policy_spans)
        or str(summary.get("status") or "") == "failed"
    ):
        risk = "failed"
        headline = "Session failed"
    elif ended and not verified:
        risk = "unverified"
        headline = "Completed without verification evidence"
    elif str(summary.get("status") or "") == "running":
        risk = "watch"
        headline = "Session is running"
    elif flags:
        risk = "watch"
        headline = "Session needs attention"
    else:
        risk = "ok"
        headline = "Session looks healthy"
    if risk in ("ok", "watch") and not bullets:
        bullets.append(
            "verification evidence present"
            if verified
            else "all recorded spans completed"
        )
    return {
        "risk": risk,
        "headline": headline,
        "bullets": bullets[:6],
        "flags": flags[:8],
    }


async def trace_insights(
    db_path: Any,
    trace_id: str,
    trace_report: Mapping[str, Any],
    *,
    now: Any = None,
) -> Dict[str, Any]:
    """Collect trace-local evidence/coverage/usage and build ``insights``."""
    moment = _now(now)
    summary = trace_report.get("trace") if isinstance(trace_report, Mapping) else {}
    if not isinstance(summary, Mapping):
        summary = {}
    events: List[Dict[str, Any]] = []
    _harness, session_id = split_trace_id(str(trace_id or ""))
    if session_id:
        try:
            events, _ = await read_trace_events(
                db_path, session_id=session_id, limit=50_000
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("attention: trace events unavailable: %s", exc)
    verified = any(
        str(event.get("kind") or "").startswith("verification.") for event in events
    )
    if not verified:
        task_ids = {
            str(event.get("task_id"))
            for event in events
            if str(event.get("task_id") or "").strip()
        }
        if task_ids:
            try:
                verified_ids = await _read_verified_task_ids(db_path)
                verified = bool(task_ids & verified_ids)
            except Exception:  # pragma: no cover - defensive
                verified = False
    coverage_entry: Optional[Mapping[str, Any]] = None
    harness = _text(summary.get("harness"), 200)
    if harness:
        try:
            coverage = await coverage_report(db_path, now=moment)
            coverage_entry = next(
                (
                    entry
                    for entry in coverage.get("harnesses") or []
                    if isinstance(entry, Mapping) and entry.get("harness") == harness
                ),
                None,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("attention: coverage unavailable: %s", exc)
    usage: Dict[str, Any] = {}
    if events:
        try:
            usage = aggregate_usage(events, prices=pricing.load_price_table())
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("attention: usage unavailable: %s", exc)
    return build_trace_insights(
        trace_report,
        events=events,
        verified=verified,
        coverage_entry=coverage_entry,
        usage=usage,
        now=moment,
    )


__all__ = [
    "ATTENTION_KINDS",
    "AttentionQueryError",
    "DEFAULT_ATTENTION_LIMIT",
    "MAX_ATTENTION_LIMIT",
    "attention_report",
    "build_trace_insights",
    "collect_attention_items",
    "normalize_attention_limit",
    "trace_insights",
]
