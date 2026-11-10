"""Append-only event spine: the dashboard's activity log.

The spine is deliberately *not* a second task store. Tasks stay on the board (or
Sugar's own worker queue); this module only records transitions and harness
telemetry so the dashboard can answer "what happened, from where, for what".
See ``docs/local-dashboard.md`` §11.

Design rules:
- best-effort: a spine failure must never break the dashboard or a task write;
- append-only: no updates, no deletes except the retention prune;
- one file for the whole machine (``~/.sugar/events.db``) so it can be the
  central nervous system rather than a per-project log.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from ..identity import ACTOR_KINDS, normalize_actor_id, normalize_kind

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30
MAX_KIND_LENGTH = 64
MAX_FIELD_LENGTH = 200
MAX_PAYLOAD_BYTES = 32 * 1024
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT,
    actor_kind TEXT,
    harness TEXT,
    machine TEXT,
    session_id TEXT,
    task_id TEXT,
    run_id TEXT,
    project TEXT,
    trace_id TEXT,
    source TEXT NOT NULL,
    payload TEXT,
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events (kind);
CREATE INDEX IF NOT EXISTS idx_events_actor ON events (actor);
CREATE INDEX IF NOT EXISTS idx_events_task ON events (task_id);
"""


class SpineError(ValueError):
    """Invalid event payload (surfaced to ingest callers as a 400)."""


def default_events_db_path() -> Path:
    override = os.environ.get("SUGAR_EVENTS_DB", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".sugar" / "events.db"


def retention_days_from_env(default: int = DEFAULT_RETENTION_DAYS) -> int:
    raw = os.environ.get("SUGAR_EVENTS_RETENTION_DAYS")
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0, value)


def machine_name() -> str:
    try:
        return socket.gethostname().split(".")[0] or "localhost"
    except OSError:  # pragma: no cover - hostname is available in practice
        return "localhost"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(value: Any, limit: int = MAX_FIELD_LENGTH) -> Optional[str]:
    text = normalize_actor_id(value) if value is not None else None
    if text is None:
        return None
    return text[:limit]


def _clean_kind(value: Any) -> str:
    text = _clean(value, MAX_KIND_LENGTH)
    if not text:
        raise SpineError("kind is required and must be a non-empty string")
    return text


def _clean_time(value: Any) -> str:
    text = _clean(value, 40)
    if not text:
        return now_iso()
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return now_iso()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_payload(value: Any) -> Optional[str]:
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict):
        raise SpineError("payload must be a JSON object")
    try:
        encoded = json.dumps(value, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise SpineError(f"payload is not JSON-serializable: {exc}") from exc
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise SpineError("payload is too large (32 KiB max)")
    return encoded


def build_event(
    body: Dict[str, Any],
    *,
    source: str = "ingest",
    default_actor_kind: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate/normalize an event dict for insertion (no I/O)."""
    if not isinstance(body, dict):
        raise SpineError("event must be a JSON object")
    actor_kind = normalize_kind(body.get("actor_kind")) or normalize_kind(
        default_actor_kind
    )
    if body.get("actor_kind") not in (None, "") and actor_kind is None:
        raise SpineError("actor_kind must be one of: " + ", ".join(ACTOR_KINDS))
    return {
        "ts": _clean_time(body.get("ts")),
        "kind": _clean_kind(body.get("kind")),
        "actor": _clean(body.get("actor")),
        "actor_kind": actor_kind,
        "harness": _clean(body.get("harness")),
        "machine": _clean(body.get("machine")) or machine_name(),
        "session_id": _clean(body.get("session_id")),
        "task_id": _clean(body.get("task_id")),
        "run_id": _clean(body.get("run_id")),
        "project": _clean(body.get("project")),
        "trace_id": _clean(body.get("trace_id")),
        "source": _clean(body.get("source"), 40) or source,
        "payload": _clean_payload(body.get("payload")),
        "ingested_at": now_iso(),
    }


class EventSpine:
    """Append-only activity log on its own SQLite file (WAL, best-effort)."""

    def __init__(
        self,
        db_path: Any = None,
        *,
        retention_days: Optional[int] = None,
    ):
        self.db_path = str(
            Path(db_path).expanduser() if db_path else default_events_db_path()
        )
        self.retention_days = (
            retention_days if retention_days is not None else retention_days_from_env()
        )
        self._db: Optional[aiosqlite.Connection] = None

    async def start(self) -> None:
        if self._db is not None:
            return
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.db_path, isolation_level=None)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA synchronous = NORMAL")
        await db.execute("PRAGMA busy_timeout = 5000")
        await db.executescript(SCHEMA)
        _restrict_permissions(self.db_path)
        self._db = db
        await self.prune()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("EventSpine is not started")
        return self._db

    async def append(
        self, body: Dict[str, Any], *, source: str = "ingest"
    ) -> Dict[str, Any]:
        """Validate + insert one event; returns the stored event (with id)."""
        event = build_event(body, source=source)
        cursor = await self._conn().execute(
            """
            INSERT INTO events
                (ts, kind, actor, actor_kind, harness, machine, session_id,
                 task_id, run_id, project, trace_id, source, payload, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["ts"],
                event["kind"],
                event["actor"],
                event["actor_kind"],
                event["harness"],
                event["machine"],
                event["session_id"],
                event["task_id"],
                event["run_id"],
                event["project"],
                event["trace_id"],
                event["source"],
                event["payload"],
                event["ingested_at"],
            ),
        )
        event["id"] = cursor.lastrowid
        if event["payload"]:
            try:
                event["payload"] = json.loads(event["payload"])
            except ValueError:  # pragma: no cover - we wrote valid JSON
                event["payload"] = {}
        else:
            event["payload"] = {}
        return event

    async def list_events(
        self,
        *,
        since_id: int = 0,
        limit: int = DEFAULT_LIMIT,
        kind: Optional[str] = None,
        actor: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Return events with ``id > since_id`` in ascending order plus last id."""
        clauses = ["id > ?"]
        params: List[Any] = [max(0, int(since_id))]
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        limit = max(1, min(int(limit), MAX_LIMIT))
        sql = (
            "SELECT * FROM events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY id ASC LIMIT ?"
        )
        cursor = await self._conn().execute(sql, [*params, limit])
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        events = [_decode_row(dict(row)) for row in rows]
        last_id = events[-1]["id"] if events else max(0, int(since_id))
        return events, last_id

    async def count(self) -> int:
        cursor = await self._conn().execute("SELECT COUNT(*) FROM events")
        try:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0
        finally:
            await cursor.close()

    async def prune(self) -> int:
        """Delete events older than the retention window. Returns rows removed."""
        if self.retention_days <= 0:
            return 0
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        cursor = await self._conn().execute(
            "DELETE FROM events WHERE ts < ?", (cutoff,)
        )
        removed = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        await cursor.close()
        if removed:
            logger.info("event spine: pruned %s events older than %s", removed, cutoff)
        return removed


def _restrict_permissions(db_path: str) -> None:
    """Keep activity history private to the owner (best effort, POSIX only)."""
    try:
        os.chmod(db_path, 0o600)
        for suffix in ("-wal", "-shm"):
            sidecar = db_path + suffix
            if Path(sidecar).exists():
                os.chmod(sidecar, 0o600)
    except OSError:  # pragma: no cover - Windows / exotic filesystems
        logger.debug("event spine: could not restrict db permissions")


def _decode_row(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("payload")
    if isinstance(payload, str) and payload:
        try:
            row["payload"] = json.loads(payload)
        except ValueError:
            row["payload"] = {"raw": payload}
    elif payload is None:
        row["payload"] = {}
    return row
