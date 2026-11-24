"""Tail DSH session logs and replay new events to a running Sugar board.

State is a small JSON checkpoint file (default ``~/.sugar/dsh-tail-state.json``)::

    {"version": 1, "sessions": {"/abs/path/session.v4.jsonl.zstd":
        {"session_id": "...", "last_seq": 42, "size": 123456, "updated_at": "..."}}}

The checkpoint is written atomically after each cycle. Delivery is fail-open and
at-most-once per (DSH sequence number, canonical event): a board outage never
blocks the tail, and successfully processed events are not replayed (the DSH log
itself is the source of truth if you need to re-ingest; delete the state file to
replay from scratch). One raw DSH event can validly map to several canonical
events (e.g. ``tool.completed`` + ``artifact.created``), so dedupe is by event
identity, not by sequence number alone.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..integrations.client import SugarTelemetryClient
from .discover import DiscoveredSession, default_sessions_dir, discover_sessions
from .parse import (
    DshParseError,
    ParsedEvent,
    SessionParseResult,
    normalize_since,
    parse_session_file,
)

logger = logging.getLogger(__name__)

STATE_VERSION = 1
DEFAULT_STATE = "~/.sugar/dsh-tail-state.json"
ENV_STATE = "SUGAR_DSH_STATE"
DEFAULT_INTERVAL = 2.0
MIN_INTERVAL = 0.2

Emit = Callable[[Dict[str, Any]], Any]


@dataclass
class TailResult:
    """Counters for one poll/cycle."""

    sessions: int = 0
    events: int = 0
    emitted: int = 0
    skipped: int = 0
    failed: int = 0
    cycles: int = 0
    errors: List[str] = field(default_factory=list)
    session_ids: List[str] = field(default_factory=list)

    def add(self, other: "TailResult") -> None:
        self.sessions += other.sessions
        self.events += other.events
        self.emitted += other.emitted
        self.skipped += other.skipped
        self.failed += other.failed
        self.cycles += other.cycles
        self.errors.extend(other.errors)
        self.session_ids.extend(other.session_ids)


def default_state_path(home: Optional[Path] = None) -> Path:
    override = os.environ.get(ENV_STATE, "").strip()
    if override:
        return Path(override).expanduser()
    base = Path(home).expanduser() if home is not None else Path.home()
    return base / ".sugar" / "dsh-tail-state.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_state() -> Dict[str, Any]:
    return {"version": STATE_VERSION, "sessions": {}}


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the checkpoint; missing/corrupt files degrade to an empty state."""
    state_path = Path(path) if path is not None else default_state_path()
    try:
        raw = state_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return empty_state()
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        sessions = {}
    return {"version": STATE_VERSION, "sessions": sessions}


def save_state(state: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Atomically write the checkpoint (temp file + ``os.replace``)."""
    state_path = Path(path) if path is not None else default_state_path()
    payload = {
        "version": STATE_VERSION,
        "sessions": state.get("sessions") if isinstance(state, dict) else {},
    }
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    except OSError as exc:
        raise DshStateError(
            f"cannot prepare state file at {state_path}: {exc}"
        ) from exc
    temp = state_path.with_name(f".{state_path.name}.tmp.{os.getpid()}")
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(encoded)
        try:
            os.chmod(temp, 0o600)
        except OSError:  # pragma: no cover - exotic filesystem
            pass
        os.replace(temp, state_path)
    except OSError as exc:
        raise DshStateError(f"cannot write state file at {state_path}: {exc}") from exc
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:  # pragma: no cover
            pass
    return state_path


class DshStateError(RuntimeError):
    """The checkpoint file could not be written."""


def make_emitter(
    *,
    board_url: Optional[str] = None,
    token: Optional[str] = None,
    debug: bool = False,
) -> Emit:
    """Build the default fail-open emitter (canonical envelope -> POST /api/events)."""
    client = SugarTelemetryClient(
        board_url=board_url,
        token=token,
        harness="dsh",
        actor="dsh",
        actor_kind="agent",
        source="dsh:session-tail",
        debug=debug,
    )

    def emit(envelope: Dict[str, Any]) -> Any:
        return client.post(envelope)

    return emit


def _session_size(session: DiscoveredSession) -> Optional[int]:
    try:
        return int(session.path.stat().st_size)
    except OSError:
        return None


def _event_identity(item: ParsedEvent) -> tuple:
    """Stable dedupe key for one parsed canonical event.

    A single raw DSH event may yield several events with the same DSH sequence
    number (trace enrichment adds e.g. artifacts next to a tool result), so the
    sequence number alone must not collapse them. Identical duplicate log lines
    still produce identical envelopes and are deduped.
    """
    payload = item.envelope.get("payload")
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        )
    except (TypeError, ValueError):  # pragma: no cover - defensive
        encoded = repr(payload)
    return (item.seq, item.kind, encoded)


def _entry_reset_needed(
    entry: Optional[Dict[str, Any]], session: DiscoveredSession, size: int
) -> bool:
    if not entry:
        return False
    previous_size = entry.get("size")
    if isinstance(previous_size, int) and size < previous_size:
        return True  # truncated / rewound file
    return False


def _emit_one(emit: Emit, envelope: Dict[str, Any], result: TailResult) -> None:
    try:
        outcome = emit(envelope)
    except Exception as exc:  # noqa: BLE001 - fail-open by contract
        result.failed += 1
        result.errors.append(f"emit failed: {exc}")
        return
    ok = getattr(outcome, "ok", None)
    if ok is False:
        result.failed += 1
        error = getattr(outcome, "error", None)
        if error:
            result.errors.append(f"emit failed: {error}")
        return
    result.emitted += 1


def tail_once(
    *,
    sessions_dir: Optional[Path] = None,
    state_path: Optional[Path] = None,
    state: Optional[Dict[str, Any]] = None,
    emit: Optional[Emit] = None,
    since: Any = None,
    home: Optional[Path] = None,
) -> TailResult:
    """Scan all known sessions once and replay new events.

    Runs synchronously. Delivery failures are counted (``failed``) but never
    raised; the checkpoint advances so the next cycle does not spin on the same
    events (at-most-once delivery, fail-open).
    """
    directory = (
        Path(sessions_dir).expanduser()
        if sessions_dir is not None
        else default_sessions_dir(home=home)
    )
    checkpoint_path = (
        Path(state_path) if state_path is not None else default_state_path(home=home)
    )
    checkpoint = state if state is not None else load_state(checkpoint_path)
    sessions_state = checkpoint.setdefault("sessions", {})
    emitter = emit or make_emitter()
    since_text = normalize_since(since)
    result = TailResult()
    changed = False

    for session in discover_sessions(directory):
        size = _session_size(session)
        if size is None:
            result.errors.append(f"cannot stat {session.path}")
            continue
        key = str(session.path.resolve())
        entry = sessions_state.get(key)
        reset = _entry_reset_needed(entry, session, size)
        # seq 0 is a real event: a fresh/rewound file starts at -1 so it is
        # emitted; a checkpoint means "everything with seq <= last_seq was sent".
        since_seq = -1 if (entry is None or reset) else int(entry.get("last_seq") or 0)

        try:
            parsed: SessionParseResult = parse_session_file(
                session.path,
                since_seq=since_seq,
                since=since_text,
                workspace=session.workspace,
                session_id_fallback=session.session_id,
            )
        except DshParseError as exc:
            result.errors.append(f"{session.path}: {exc}")
            logger.debug("dsh tail: skipping %s: %s", session.path, exc)
            continue

        session_id = parsed.session_id or session.session_id
        # A discovered folder name is only a fallback id; the header inside the
        # log is authoritative. Compare against the header id, not the path
        # fallback, or every cycle looks like a replaced log and replays it.
        previous_session = (entry or {}).get("session_id")
        if (
            entry is not None
            and isinstance(previous_session, str)
            and previous_session
            and session_id
            and session_id != previous_session
        ):
            reset = True
            since_seq = -1
            try:
                parsed = parse_session_file(
                    session.path,
                    since_seq=since_seq,
                    since=since_text,
                    workspace=session.workspace,
                    session_id_fallback=session.session_id,
                )
            except DshParseError as exc:
                result.errors.append(f"{session.path}: {exc}")
                logger.debug("dsh tail: skipping %s: %s", session.path, exc)
                continue
            session_id = parsed.session_id or session.session_id

        result.sessions += 1
        if session_id and session_id not in result.session_ids:
            result.session_ids.append(session_id)

        header = parsed.header_event
        emit_header = header is not None and (entry is None or reset)
        if emit_header and header is not None:
            _emit_one(emitter, header, result)
            result.events += 1

        seen = set()
        for item in parsed.events:
            identity = _event_identity(item)
            if identity in seen:
                result.skipped += 1
                continue
            seen.add(identity)
            _emit_one(emitter, item.envelope, result)
            result.events += 1

        previous_last = int((entry or {}).get("last_seq") or 0)
        next_last = max(previous_last, parsed.last_seq)
        sessions_state[key] = {
            "session_id": session_id,
            "last_seq": next_last,
            "size": size,
            "updated_at": utc_now_iso(),
        }
        changed = True

    if changed:
        try:
            save_state(checkpoint, checkpoint_path)
        except DshStateError as exc:
            result.errors.append(str(exc))
    return result


def watch(
    *,
    sessions_dir: Optional[Path] = None,
    state_path: Optional[Path] = None,
    since: Any = None,
    interval: float = DEFAULT_INTERVAL,
    emit: Optional[Emit] = None,
    home: Optional[Path] = None,
    stop_event: Optional[threading.Event] = None,
    max_cycles: Optional[int] = None,
    on_cycle: Optional[Callable[[TailResult], None]] = None,
) -> TailResult:
    """Poll forever (or until *stop_event* / *max_cycles*) and replay new events."""
    delay = max(MIN_INTERVAL, float(interval))
    stop = stop_event or threading.Event()
    totals = TailResult()
    checkpoint_path = (
        Path(state_path) if state_path is not None else default_state_path(home=home)
    )
    checkpoint = load_state(checkpoint_path)
    cycles = 0
    while not stop.is_set():
        cycle = tail_once(
            sessions_dir=sessions_dir,
            state_path=checkpoint_path,
            state=checkpoint,
            emit=emit,
            since=since,
            home=home,
        )
        cycles += 1
        cycle.cycles = 1
        totals.add(cycle)
        if on_cycle is not None:
            try:
                on_cycle(cycle)
            except Exception:  # noqa: BLE001 - reporting must not stop the tail
                logger.debug("dsh tail: on_cycle callback failed", exc_info=True)
        if max_cycles is not None and cycles >= int(max_cycles):
            break
        stop.wait(delay)
    totals.cycles = cycles
    return totals


__all__ = [
    "DEFAULT_INTERVAL",
    "DEFAULT_STATE",
    "DshStateError",
    "ENV_STATE",
    "TailResult",
    "default_state_path",
    "empty_state",
    "load_state",
    "make_emitter",
    "save_state",
    "tail_once",
    "watch",
]
