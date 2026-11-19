"""Adapter coverage for the Phase-0 telemetry matrix (``GET /api/coverage``).

Coverage combines the adapter registry written by ``sugar connect``
(``~/.sugar/adapters.json``) with recent telemetry events in the spine. The
registry is read defensively: a missing/corrupt file simply means "no adapters
registered" and coverage still reports harnesses seen in the event stream.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import aiosqlite

from .normalize import KNOWN_HARNESSES, normalize_harness_name
from .schema import FIDELITIES, FIDELITY_RANK

logger = logging.getLogger(__name__)

ADAPTERS_ENV = "SUGAR_ADAPTERS_FILE"
COVERAGE_WINDOW_HOURS = 24
DEFAULT_EVENT_LIMIT = 5000
MAX_EVENT_LIMIT = 50000

DISPLAY_NAMES = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
    "goose": "Goose",
    "dsh": "DeepSeek Harness",
}

TRANSPORT_UNKNOWN = "unknown"


def adapters_path(explicit: Any = None) -> Path:
    if explicit is not None and str(explicit).strip():
        return Path(str(explicit)).expanduser()
    override = os.environ.get(ADAPTERS_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".sugar" / "adapters.json"


def display_name(harness: str) -> str:
    return DISPLAY_NAMES.get(harness, harness.replace("-", " ").title() or harness)


def _canonical_harness(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    return normalize_harness_name(text) or text.lower()


def load_adapters(path: Any = None) -> Dict[str, Dict[str, Any]]:
    """Read ``adapters.json`` defensively; always returns a dict of entries."""
    adapter_file = adapters_path(path)
    try:
        raw = adapter_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        logger.debug("coverage: adapters file is not valid JSON: %s", adapter_file)
        return {}
    if not isinstance(data, Mapping):
        return {}
    entries = data.get("adapters")
    if isinstance(entries, Mapping):
        result: Dict[str, Dict[str, Any]] = {}
        for key, value in entries.items():
            if not isinstance(value, Mapping):
                continue
            canonical = _canonical_harness(key) or str(key)
            result[canonical] = dict(value)
        return result
    if isinstance(entries, list):
        result = {}
        for value in entries:
            if not isinstance(value, Mapping):
                continue
            harness = _canonical_harness(value.get("harness"))
            if harness:
                result[harness] = dict(value)
        return result
    return {}


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


async def read_recent_events(
    db_path: Any,
    *,
    hours: int = COVERAGE_WINDOW_HOURS,
    limit: int = DEFAULT_EVENT_LIMIT,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return events from the last *hours*, newest first, bounded by *limit*."""
    if db_path is None:
        return []
    path = Path(str(db_path)).expanduser()
    if not path.exists():
        return []
    moment = now or datetime.now(timezone.utc)
    since = (moment - timedelta(hours=max(1, int(hours)))).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    bound = max(1, min(int(limit), MAX_EVENT_LIMIT))
    try:
        async with aiosqlite.connect(str(path), isolation_level=None, timeout=5) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout = 5000")
            await db.execute("PRAGMA query_only = ON")
            cursor = await db.execute(
                "SELECT * FROM events WHERE ts >= ? ORDER BY id DESC LIMIT ?",
                (since, bound),
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        logger.debug("coverage: cannot read events: %s", exc)
        return []
    return [_decode_event_row(dict(row)) for row in rows]


def _valid_fidelity(value: Any) -> Optional[str]:
    if isinstance(value, str) and value in FIDELITIES:
        return value
    return None


def _event_fidelity(event: Mapping[str, Any]) -> Optional[str]:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    return _valid_fidelity(payload.get("fidelity"))


def _make_entry(
    harness: str,
    registry_entry: Optional[Mapping[str, Any]],
    *,
    events_24h: int,
    last_event_at: Optional[str],
    derived_fidelity: Optional[str],
) -> Dict[str, Any]:
    entry = dict(registry_entry or {})
    installed = bool(entry.get("installed"))
    saw_events = events_24h > 0
    detected = bool(entry.get("detected", installed or saw_events))
    fidelity = _valid_fidelity(entry.get("fidelity")) or derived_fidelity or "L0"
    transport = str(entry.get("transport") or "").strip()
    if not transport:
        transport = "event-spine" if saw_events else TRANSPORT_UNKNOWN
    notes = str(entry.get("notes") or "").strip()
    if not registry_entry and saw_events:
        notes = notes or "seen in telemetry events; no adapter registry entry"
    adapter_version = str(entry.get("adapter_version") or "").strip()
    if not adapter_version and installed:
        adapter_version = "phase0"
    return {
        "harness": harness,
        "display_name": str(entry.get("display_name") or display_name(harness)),
        "detected": detected,
        "installed": installed,
        "fidelity": fidelity,
        "transport": transport,
        "last_event_at": last_event_at,
        "events_24h": int(events_24h),
        "adapter_version": adapter_version,
        "notes": notes,
    }


async def coverage_report(
    db_path: Any = None,
    *,
    adapters: Any = None,
    now: Optional[datetime] = None,
    event_limit: int = DEFAULT_EVENT_LIMIT,
) -> Dict[str, Any]:
    """Build the frozen ``GET /api/coverage`` response."""
    moment = now or datetime.now(timezone.utc)
    registry = load_adapters(adapters)
    events = await read_recent_events(db_path, limit=event_limit, now=moment)

    events_by_harness: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        harness = _canonical_harness(event.get("harness"))
        if harness is None:
            continue
        events_by_harness.setdefault(harness, []).append(event)

    # Registry entries are authoritative for the Phase-0 adapter matrix; events
    # from known harnesses are also surfaced even before `sugar connect` runs.
    harnesses = sorted(set(registry) | (set(events_by_harness) & set(KNOWN_HARNESSES)))
    entries: List[Dict[str, Any]] = []
    for harness in harnesses:
        harness_events = events_by_harness.get(harness, [])
        last_event_at = None
        derived: Optional[str] = None
        for event in harness_events:
            ts = event.get("ts")
            if isinstance(ts, str) and (last_event_at is None or ts > last_event_at):
                last_event_at = ts
            fidelity = _event_fidelity(event)
            if fidelity is not None and (
                derived is None or FIDELITY_RANK[fidelity] > FIDELITY_RANK[derived]
            ):
                derived = fidelity
        entries.append(
            _make_entry(
                harness,
                registry.get(harness),
                events_24h=len(harness_events),
                last_event_at=last_event_at,
                derived_fidelity=derived,
            )
        )

    summary: Dict[str, List[str]] = {level: [] for level in FIDELITIES}
    for entry in entries:
        summary[entry["fidelity"]].append(entry["harness"])
    for level in summary:
        summary[level].sort()

    return {
        "generated_at": moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": summary,
        "harnesses": entries,
    }
