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

import hashlib
import hmac
import json
import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote

import aiosqlite

from ..__version__ import __version__ as SUGAR_VERSION
from ..identity import ACTOR_KINDS, normalize_actor_id, normalize_kind

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30
MAX_KIND_LENGTH = 64
MAX_FIELD_LENGTH = 200
MAX_PAYLOAD_BYTES = 32 * 1024
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000

CHAIN_ALGORITHM = "sha256-v1"
CHAIN_GENESIS = "0" * 64
CHECKPOINT_FORMAT = "sugar-merkle-checkpoint-v1"
CHECKPOINT_ALGORITHM = "sha256-merkle-v1"
DEFAULT_CHECKPOINT_SIGN_KEY_ENV = "SUGAR_AUDIT_SIGN_KEY"
DEFAULT_CHECKPOINT_LIMIT = 100
MAX_CHECKPOINT_LIMIT = 1000

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

CREATE TABLE IF NOT EXISTS event_chain (
    event_id INTEGER PRIMARY KEY,
    algorithm TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_chain_hash ON event_chain (hash);

CREATE TABLE IF NOT EXISTS event_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id INTEGER NOT NULL,
    to_id INTEGER NOT NULL,
    count INTEGER NOT NULL,
    root_hash TEXT NOT NULL,
    algorithm TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    sugar_version TEXT NOT NULL,
    signature TEXT,
    key_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_checkpoints_range
    ON event_checkpoints (from_id, to_id);
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


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _payload_for_hash(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            decoded = json.loads(value)
        except ValueError:
            return {"raw": value}
        return decoded if isinstance(decoded, dict) else {"value": decoded}
    return {}


def event_chain_material(
    event_id: int,
    event: Dict[str, Any],
    prev_hash: str,
) -> Dict[str, Any]:
    """Canonical, versioned material covered by an event-chain hash."""
    return {
        "algorithm": CHAIN_ALGORITHM,
        "event_id": int(event_id),
        "kind": event.get("kind"),
        "harness": event.get("harness"),
        "actor": event.get("actor"),
        "session_id": event.get("session_id"),
        "task_id": event.get("task_id"),
        "project": event.get("project"),
        "ts": event.get("ts"),
        "ingested_at": event.get("ingested_at"),
        "payload": _payload_for_hash(event.get("payload")),
        "prev_hash": prev_hash,
    }


def compute_event_hash(event_id: int, event: Dict[str, Any], prev_hash: str) -> str:
    return hashlib.sha256(
        _canonical_bytes(event_chain_material(event_id, event, prev_hash))
    ).hexdigest()


def _merkle_root(hashes: Sequence[str]) -> str:
    if not hashes:
        return CHAIN_GENESIS
    level = [bytes.fromhex(value) for value in hashes]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            hashlib.sha256(level[index] + level[index + 1]).digest()
            for index in range(0, len(level), 2)
        ]
    return level[0].hex()


def _checkpoint_key(
    sign_key_env: Optional[str], *, allow_auto: bool = True
) -> Tuple[Any, Optional[str]]:
    """Resolve an explicit key env, or the default env for checkpoints."""
    env_name = sign_key_env
    if env_name is None and allow_auto:
        if os.environ.get(DEFAULT_CHECKPOINT_SIGN_KEY_ENV, "").strip():
            env_name = DEFAULT_CHECKPOINT_SIGN_KEY_ENV
    if env_name is None:
        return None, None
    from ..audit.export import resolve_signing_key

    return resolve_signing_key(env_name), env_name


def checkpoint_core(checkpoint: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in checkpoint.items()
        if key not in ("signature", "key_id")
    }


def sign_checkpoint(checkpoint: Dict[str, Any], key: bytes) -> Dict[str, Any]:
    return {
        "alg": "hmac-sha256",
        "key_id": hashlib.sha256(key).hexdigest()[:16],
        "signature": hmac.new(
            key, _canonical_bytes(checkpoint_core(checkpoint)), hashlib.sha256
        ).hexdigest(),
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
        await self._backfill_chain()

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
        events = await self.append_batch([body], source=source)
        return events[0]

    async def append_batch(
        self, bodies: Iterable[Dict[str, Any]], *, source: str = "ingest"
    ) -> List[Dict[str, Any]]:
        """Insert events in one transaction, extending the hash chain for each."""
        normalized = [build_event(body, source=source) for body in bodies]
        if not normalized:
            return []
        db = self._conn()
        await db.execute("BEGIN IMMEDIATE")
        try:
            prev_hash = await self._chain_head()
            events: List[Dict[str, Any]] = []
            for event in normalized:
                cursor = await db.execute(
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
                event["id"] = int(cursor.lastrowid)
                event_hash = compute_event_hash(event["id"], event, prev_hash)
                await db.execute(
                    """
                    INSERT INTO event_chain
                        (event_id, algorithm, prev_hash, hash, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event["id"],
                        CHAIN_ALGORITHM,
                        prev_hash,
                        event_hash,
                        now_iso(),
                    ),
                )
                prev_hash = event_hash
                event["payload"] = _payload_for_hash(event["payload"])
                events.append(event)
            await db.execute("COMMIT")
        except BaseException:
            try:
                await db.execute("ROLLBACK")
            except Exception:  # pragma: no cover - original error wins
                pass
            raise
        return events

    async def _chain_head(self) -> str:
        cursor = await self._conn().execute(
            "SELECT hash FROM event_chain ORDER BY event_id DESC LIMIT 1"
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return str(row[0]) if row and row[0] else CHAIN_GENESIS

    async def _backfill_chain(self) -> int:
        """Backfill chain rows for legacy events/tail after a crash (idempotent)."""
        db = self._conn()
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) AS event_count, MAX(id) AS event_max FROM events"
            )
            try:
                event_stats = await cursor.fetchone()
            finally:
                await cursor.close()
            cursor = await db.execute(
                "SELECT COUNT(*) AS chain_count, MAX(event_id) AS chain_max, "
                "MIN(event_id) AS chain_min FROM event_chain"
            )
            try:
                chain_stats = await cursor.fetchone()
            finally:
                await cursor.close()
            if (
                event_stats is not None
                and chain_stats is not None
                and int(event_stats["event_count"] or 0)
                == int(chain_stats["chain_count"] or 0)
                and int(event_stats["event_max"] or 0)
                == int(chain_stats["chain_max"] or 0)
            ):
                await db.execute("COMMIT")
                return 0
            cursor = await db.execute("SELECT * FROM events ORDER BY id ASC")
            try:
                event_rows = [dict(row) for row in await cursor.fetchall()]
            finally:
                await cursor.close()
            cursor = await db.execute(
                "SELECT event_id, hash FROM event_chain ORDER BY event_id ASC"
            )
            try:
                chain_rows = await cursor.fetchall()
            finally:
                await cursor.close()
            existing = {int(row[0]): str(row[1]) for row in chain_rows}
            max_chained = max(existing) if existing else 0
            prev_hash = existing[max_chained] if max_chained else CHAIN_GENESIS
            created = 0
            for event in event_rows:
                event_id = int(event["id"])
                if event_id <= max_chained:
                    continue
                event_hash = compute_event_hash(event_id, event, prev_hash)
                cursor = await db.execute(
                    """
                    INSERT OR IGNORE INTO event_chain
                        (event_id, algorithm, prev_hash, hash, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        CHAIN_ALGORITHM,
                        prev_hash,
                        event_hash,
                        now_iso(),
                    ),
                )
                if cursor.rowcount and cursor.rowcount > 0:
                    created += 1
                await cursor.close()
                prev_hash = event_hash
            await db.execute("COMMIT")
            return created
        except BaseException:
            try:
                await db.execute("ROLLBACK")
            except Exception:  # pragma: no cover
                pass
            raise

    async def verify_chain(self) -> Dict[str, Any]:
        """Convenience wrapper for :func:`verify_event_chain`."""
        return await verify_event_chain(self.db_path)

    async def checkpoint(
        self,
        *,
        from_id: Optional[int] = None,
        to_id: Optional[int] = None,
        sign_key_env: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build + persist a Merkle checkpoint; convenience wrapper."""
        return await build_checkpoint(
            self.db_path,
            from_id=from_id,
            to_id=to_id,
            sign_key_env=sign_key_env,
        )

    async def list_checkpoints(
        self, *, limit: int = DEFAULT_CHECKPOINT_LIMIT
    ) -> List[Dict[str, Any]]:
        return await list_checkpoints(self.db_path, limit=limit)

    async def build_checkpoint(
        self,
        *,
        from_id: Optional[int] = None,
        to_id: Optional[int] = None,
        sign_key_env: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.checkpoint(
            from_id=from_id, to_id=to_id, sign_key_env=sign_key_env
        )

    async def create_checkpoint(
        self,
        *,
        from_id: Optional[int] = None,
        to_id: Optional[int] = None,
        sign_key_env: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.checkpoint(
            from_id=from_id, to_id=to_id, sign_key_env=sign_key_env
        )

    async def verify_event_chain(self) -> Dict[str, Any]:
        return await verify_event_chain(self.db_path)

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
        """Delete events older than the retention window. Returns rows removed.

        Chain rows for pruned events are removed in the same transaction so the
        live chain and events tables stay aligned. A preserved tail may then
        begin after a pruned prefix; verification reports that prefix as pruned
        rather than as tampering.
        """
        if self.retention_days <= 0:
            return 0
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        db = self._conn()
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                "DELETE FROM event_chain WHERE event_id IN "
                "(SELECT id FROM events WHERE ts < ?)",
                (cutoff,),
            )
            await cursor.close()
            cursor = await db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            removed = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
            await cursor.close()
            await db.execute("COMMIT")
        except BaseException:
            try:
                await db.execute("ROLLBACK")
            except Exception:  # pragma: no cover
                pass
            raise
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


class ChainError(ValueError):
    """Invalid chain/checkpoint request or database state."""


def _resolve_db_path(events_db: Any) -> str:
    if hasattr(events_db, "db_path"):
        return str(events_db.db_path)
    return str(Path(events_db).expanduser())


def _ro_uri(path: str) -> str:
    return (
        "file:" + quote(str(Path(path).expanduser().resolve()), safe="/") + "?mode=ro"
    )


async def _table_names(db: aiosqlite.Connection) -> set:
    cursor = await db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    try:
        return {str(row[0]) for row in await cursor.fetchall()}
    finally:
        await cursor.close()


def _checkpoint_row_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "format": CHECKPOINT_FORMAT,
        "from_id": int(row["from_id"]),
        "to_id": int(row["to_id"]),
        "count": int(row["count"]),
        "root_hash": str(row["root_hash"]),
        "generated_at": str(row["generated_at"]),
        "sugar_version": str(row["sugar_version"]),
        "algorithm": str(row["algorithm"]),
        "signature": row["signature"],
        "key_id": row["key_id"],
        "id": int(row["id"]),
    }


async def verify_event_chain(events_db: Any) -> Dict[str, Any]:
    """Verify the per-event hash chain without trusting live event payloads.

    Detects rewritten/deleted events (where the chain row remains), missing or
    reordered chain rows, broken links and altered hashes. A pruned prefix (from
    the retention policy) is accepted; this proves tamper evidence, not
    immutability. The newest checkpoint (if any) is returned for convenience.
    """
    path = _resolve_db_path(events_db)
    result: Dict[str, Any] = {
        "ok": False,
        "checked": 0,
        "first_bad_id": None,
        "errors": [],
        "head_hash": None,
        "checkpoint": None,
    }
    if not Path(path).exists():
        result["errors"].append(f"event database not found: {path}")
        return result
    try:
        db = await aiosqlite.connect(_ro_uri(path), uri=True, isolation_level=None)
    except (aiosqlite.Error, OSError) as exc:
        result["errors"].append(f"cannot open event database: {exc}")
        return result
    try:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout = 5000")
        tables = await _table_names(db)
        if "events" not in tables:
            result["errors"].append("events table is missing")
            return result
        if "event_chain" not in tables:
            result["errors"].append(
                "event_chain table is missing; start the spine to initialize it"
            )
            return result
        cursor = await db.execute("SELECT * FROM events ORDER BY id ASC")
        try:
            event_rows = [dict(row) for row in await cursor.fetchall()]
        finally:
            await cursor.close()
        cursor = await db.execute("SELECT * FROM event_chain ORDER BY event_id ASC")
        try:
            chain_rows = [dict(row) for row in await cursor.fetchall()]
        finally:
            await cursor.close()
        checkpoint = None
        if "event_checkpoints" in tables:
            cursor = await db.execute(
                "SELECT * FROM event_checkpoints ORDER BY id DESC LIMIT 1"
            )
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
            if row is not None:
                checkpoint = _checkpoint_row_to_dict(row)
    except (aiosqlite.Error, OSError) as exc:
        result["errors"].append(f"cannot read event database: {exc}")
        return result
    finally:
        await db.close()

    if chain_rows:
        result["head_hash"] = str(chain_rows[-1]["hash"])
    result["checkpoint"] = checkpoint
    errors: List[str] = result["errors"]
    first_bad: Optional[int] = None

    def fail(event_id: Optional[int], message: str) -> None:
        nonlocal first_bad
        errors.append(message)
        if event_id is not None and first_bad is None:
            first_bad = int(event_id)

    event_by_id = {int(row["id"]): row for row in event_rows}
    chain_by_id = {int(row["event_id"]): row for row in chain_rows}

    for event_id in sorted(chain_by_id):
        if event_id not in event_by_id:
            fail(event_id, f"chain row has no event (event {event_id} deleted?)")
    for event_id in sorted(event_by_id):
        if event_id not in chain_by_id:
            fail(event_id, f"event {event_id} has no chain row")

    expected_prev: Optional[Dict[str, Any]] = None
    for row in chain_rows:
        event_id = int(row["event_id"])
        result["checked"] += 1
        event = event_by_id.get(event_id)
        if event is None:
            expected_prev = row
            continue
        if str(row.get("algorithm") or "") != CHAIN_ALGORITHM:
            fail(
                event_id,
                f"event {event_id} uses unsupported chain algorithm "
                f"{row.get('algorithm')!r}",
            )
        if expected_prev is None:
            if event_id == 1 and str(row.get("prev_hash")) != CHAIN_GENESIS:
                fail(event_id, "genesis chain row does not use the all-zero prev_hash")
            # A first chained id > 1 is a pruned prefix, not a tamper finding;
            # a missing chain row for an existing earlier event is caught above.
        else:
            prev_id = int(expected_prev["event_id"])
            if event_id != prev_id + 1:
                fail(event_id, f"chain gap between events {prev_id} and {event_id}")
            elif str(row.get("prev_hash")) != str(expected_prev.get("hash")):
                fail(event_id, f"chain link broken at event {event_id}")
        recomputed = compute_event_hash(event_id, event, str(row.get("prev_hash")))
        if recomputed != str(row.get("hash")):
            fail(event_id, f"event {event_id} hash mismatch (payload/fields changed?)")
        expected_prev = row

    result["first_bad_id"] = first_bad
    result["ok"] = not errors
    return result


async def build_checkpoint(
    events_db: Any,
    *,
    from_id: Optional[int] = None,
    to_id: Optional[int] = None,
    sign_key_env: Optional[str] = None,
) -> Dict[str, Any]:
    """Build + persist a deterministic Merkle checkpoint over an id range."""
    path = _resolve_db_path(events_db)
    if not Path(path).exists():
        raise ChainError(f"event database not found: {path}")
    key, _env_name = _checkpoint_key(sign_key_env)
    try:
        db = await aiosqlite.connect(path, isolation_level=None)
    except (aiosqlite.Error, OSError) as exc:
        raise ChainError(f"cannot open event database: {exc}") from None
    try:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout = 5000")
        await db.executescript(SCHEMA)
        cursor = await db.execute(
            "SELECT MIN(event_id) AS lo, MAX(event_id) AS hi FROM event_chain"
        )
        try:
            bounds = await cursor.fetchone()
        finally:
            await cursor.close()
        if bounds is None or bounds["lo"] is None:
            raise ChainError("no chained events to checkpoint")
        lo = int(bounds["lo"])
        hi = int(bounds["hi"])
        try:
            start = int(from_id) if from_id is not None else lo
            end = int(to_id) if to_id is not None else hi
        except (TypeError, ValueError):
            raise ChainError("from_id/to_id must be integers") from None
        if start > end:
            raise ChainError("from_id must be <= to_id")
        cursor = await db.execute(
            "SELECT event_id, hash FROM event_chain "
            "WHERE event_id >= ? AND event_id <= ? ORDER BY event_id ASC",
            (start, end),
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        leaves = [str(row["hash"]) for row in rows]
        if not leaves:
            raise ChainError("no chained events in the requested id range")
        root = _merkle_root(leaves)
        checkpoint: Dict[str, Any] = {
            "format": CHECKPOINT_FORMAT,
            "from_id": start,
            "to_id": end,
            "count": len(leaves),
            "root_hash": root,
            "generated_at": now_iso(),
            "sugar_version": SUGAR_VERSION,
            "algorithm": CHECKPOINT_ALGORITHM,
        }
        if key is not None:
            signed = sign_checkpoint(checkpoint, key)
            checkpoint["key_id"] = signed["key_id"]
            checkpoint["signature"] = signed["signature"]
        cursor = await db.execute(
            """
            INSERT INTO event_checkpoints
                (from_id, to_id, count, root_hash, algorithm, generated_at,
                 sugar_version, signature, key_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint["from_id"],
                checkpoint["to_id"],
                checkpoint["count"],
                checkpoint["root_hash"],
                checkpoint["algorithm"],
                checkpoint["generated_at"],
                checkpoint["sugar_version"],
                checkpoint.get("signature"),
                checkpoint.get("key_id"),
            ),
        )
        await cursor.close()
        await db.commit()
        return checkpoint
    finally:
        await db.close()


def verify_checkpoint(
    checkpoint: Dict[str, Any], sign_key_env: Optional[str] = None
) -> Dict[str, Any]:
    """Verify a checkpoint's structure and optional HMAC signature."""
    errors: List[str] = []
    if not isinstance(checkpoint, dict):
        return {
            "ok": False,
            "signed": False,
            "key_id": None,
            "errors": ["not a checkpoint"],
        }
    required = ("format", "from_id", "to_id", "count", "root_hash", "generated_at")
    for key in required:
        if key not in checkpoint:
            errors.append(f"checkpoint is missing {key}")
    if checkpoint.get("format") != CHECKPOINT_FORMAT:
        errors.append(f"unsupported checkpoint format: {checkpoint.get('format')!r}")
    signature = checkpoint.get("signature")
    signed = bool(signature)
    key_id = checkpoint.get("key_id")
    if signed:
        key, _env = _checkpoint_key(sign_key_env)
        if key is None:
            errors.append(
                "checkpoint is signed but no signing key was provided "
                "(pass --sign-key-env or set $SUGAR_AUDIT_SIGN_KEY)"
            )
        else:
            expected = hmac.new(
                key,
                _canonical_bytes(checkpoint_core(checkpoint)),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(expected, str(signature)):
                errors.append(
                    "checkpoint HMAC signature mismatch (wrong key or tampered)"
                )
            expected_key_id = hashlib.sha256(key).hexdigest()[:16]
            if key_id != expected_key_id:
                errors.append("checkpoint key_id mismatch (wrong key?)")
    elif sign_key_env is not None:
        errors.append("checkpoint is not signed but --sign-key-env was provided")
    return {
        "ok": not errors,
        "signed": signed,
        "key_id": key_id if isinstance(key_id, str) else None,
        "errors": errors,
    }


async def verify_checkpoint_against_db(
    events_db: Any,
    checkpoint: Dict[str, Any],
    sign_key_env: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify signature + recompute the checkpoint Merkle root from the DB."""
    result = verify_checkpoint(checkpoint, sign_key_env=sign_key_env)
    errors: List[str] = result["errors"]
    path = _resolve_db_path(events_db)
    chain_result = await verify_event_chain(path)
    if not chain_result.get("ok"):
        errors.append(
            "event hash chain failed verification"
            + (
                f" (first bad id {chain_result.get('first_bad_id')})"
                if chain_result.get("first_bad_id") is not None
                else ""
            )
        )
    try:
        db = await aiosqlite.connect(_ro_uri(path), uri=True, isolation_level=None)
    except (aiosqlite.Error, OSError) as exc:
        errors.append(f"cannot open event database: {exc}")
        result["ok"] = False
        return result
    try:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT hash FROM event_chain WHERE event_id >= ? AND event_id <= ? "
            "ORDER BY event_id ASC",
            (int(checkpoint.get("from_id") or 0), int(checkpoint.get("to_id") or 0)),
        )
        try:
            leaves = [str(row["hash"]) for row in await cursor.fetchall()]
        finally:
            await cursor.close()
    except (aiosqlite.Error, OSError, TypeError, ValueError) as exc:
        errors.append(f"cannot read checkpoint range: {exc}")
        leaves = []
    finally:
        await db.close()
    if len(leaves) != int(checkpoint.get("count") or -1):
        errors.append("checkpoint covered-event count does not match the current chain")
    elif _merkle_root(leaves) != checkpoint.get("root_hash"):
        errors.append("checkpoint Merkle root does not match the current chain")
    result["ok"] = not errors
    return result


async def list_checkpoints(
    events_db: Any, *, limit: int = DEFAULT_CHECKPOINT_LIMIT
) -> List[Dict[str, Any]]:
    """Return checkpoints newest-first (read-only; empty when uninitialized)."""
    path = _resolve_db_path(events_db)
    if not Path(path).exists():
        return []
    limit = max(1, min(int(limit), MAX_CHECKPOINT_LIMIT))
    try:
        db = await aiosqlite.connect(_ro_uri(path), uri=True, isolation_level=None)
    except (aiosqlite.Error, OSError):
        return []
    try:
        db.row_factory = aiosqlite.Row
        tables = await _table_names(db)
        if "event_checkpoints" not in tables:
            return []
        cursor = await db.execute(
            "SELECT * FROM event_checkpoints ORDER BY id DESC LIMIT ?", (limit,)
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        return [_checkpoint_row_to_dict(row) for row in rows]
    except (aiosqlite.Error, OSError):
        return []
    finally:
        await db.close()


async def backfill_event_chain(events_db: Any) -> int:
    """Backfill chain rows for legacy events; returns the number created."""
    path = _resolve_db_path(events_db)
    before = 0
    if Path(path).exists():
        try:
            db = await aiosqlite.connect(_ro_uri(path), uri=True, isolation_level=None)
            try:
                tables = await _table_names(db)
                if "event_chain" in tables:
                    cursor = await db.execute("SELECT COUNT(*) FROM event_chain")
                    try:
                        row = await cursor.fetchone()
                    finally:
                        await cursor.close()
                    before = int(row[0]) if row else 0
            finally:
                await db.close()
        except (aiosqlite.Error, OSError):
            before = 0
    spine = EventSpine(path, retention_days=0)
    await spine.start()
    try:
        db = spine._conn()
        cursor = await db.execute("SELECT COUNT(*) FROM event_chain")
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        total = int(row[0]) if row else 0
        return max(0, total - before)
    finally:
        await spine.close()


# Naming aliases for callers that prefer create/check semantics.
create_checkpoint = build_checkpoint
verify_chain = verify_event_chain


__all__ = [
    "CHAIN_ALGORITHM",
    "CHAIN_GENESIS",
    "CHECKPOINT_ALGORITHM",
    "CHECKPOINT_FORMAT",
    "ChainError",
    "EventSpine",
    "SpineError",
    "build_checkpoint",
    "build_event",
    "compute_event_hash",
    "event_chain_material",
    "list_checkpoints",
    "verify_checkpoint",
    "verify_checkpoint_against_db",
    "verify_event_chain",
    "verify_chain",
    "create_checkpoint",
    "backfill_event_chain",
]
