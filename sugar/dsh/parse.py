"""Parse DSH v4 session logs into canonical telemetry envelopes.

The log is zstd-compressed JSONL. Decompression prefers the system ``zstd -dc``
(it handles concatenated frames, including logs appended by a running DSH) and
falls back to the optional ``zstandard`` Python package. Parsing is tolerant:
malformed JSON lines and unknown event types are skipped, and a truncated final
frame yields the events that were decodable.

Event shapes seen in real logs (v4):

- header: ``{"type":"session","version":4,"id":...,"createdAt":ms,"cwd":...,
  "parentSession":...,"origin":"subagent|...","agentPreset":...}``
- event: ``{"type":"...","seq":N,"time":ms,"data":{...}}`` (``seq``/``time``
  may be strings in older writers — both are tolerated)
- ``tool/result`` has ``data.toolCallId`` or nests it in
  ``data.message.{toolCallId,source.callId}`` and reports failure via
  ``data.isError`` / ``data.message.isError`` / ``data.error``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..telemetry import redact
from ..telemetry.schema import build_envelope, normalize_iso_timestamp

HARNESS = "dsh"
ACTOR = "dsh"
SOURCE = "dsh:session-tail"
ZSTD_TIMEOUT_SECONDS = 30.0
MAX_RESULT_TEXT = 4000

_MODEL_PATTERN = re.compile(r"['\"]model['\"]\s*:\s*['\"]([^'\"]{1,200})['\"]")
_PROVIDER_PATTERN = re.compile(r"['\"]provider['\"]\s*:\s*['\"]([^'\"]{1,200})['\"]")

_IGNORED_TYPES = frozenset(
    {
        "session/title",
        "subagent/descriptor",
        "sandbox/mode",
        "agent/inbox/spliced",
        "system/message",
        "user/message",
        "assistant/message",
        "todo/write",
        "request/context",
        "job/registered",
        "job/settled",
    }
)


class DshParseError(RuntimeError):
    """A session log could not be decompressed/parsed (caller skips it)."""


class DshConfigError(RuntimeError):
    """Invalid tail configuration (bad --since, no decompressor, ...)."""


@dataclass
class ParsedEvent:
    """One canonical envelope plus the DSH sequence number it came from."""

    seq: int
    kind: str
    envelope: Dict[str, Any]


@dataclass
class SessionParseResult:
    path: Path
    session_id: str
    header_event: Optional[Dict[str, Any]]
    events: List[ParsedEvent] = field(default_factory=list)
    last_seq: int = 0
    malformed_lines: int = 0

    @property
    def count(self) -> int:
        return len(self.events)


# ---------------------------------------------------------------------------
# Decompression / JSONL
# ---------------------------------------------------------------------------


def zstd_available() -> bool:
    """True when a decompressor is usable (system CLI or ``zstandard``)."""
    if shutil.which("zstd"):
        return True
    try:  # pragma: no cover - optional dependency
        import importlib.util

        return importlib.util.find_spec("zstandard") is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


def _decompress_with_zstandard(path: Path) -> str:  # pragma: no cover - optional
    import zstandard  # type: ignore[import-not-found]

    try:
        with path.open("rb") as handle:
            reader = zstandard.ZstdDecompressor().stream_reader(handle)
            return reader.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - optional decoder
        raise DshParseError(f"cannot decompress {path}: {exc}") from exc


def decompress_session(path: Path, *, timeout: float = ZSTD_TIMEOUT_SECONDS) -> str:
    """Return the decompressed JSONL text for one session log."""
    binary = shutil.which("zstd")
    if binary:
        try:
            completed = subprocess.run(
                [binary, "-dc", str(path)],
                capture_output=True,
                timeout=max(0.1, timeout),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DshParseError(f"cannot run zstd for {path}: {exc}") from exc
        stdout = completed.stdout or b""
        if stdout:
            # A truncated/corrupt final frame can make zstd exit non-zero after
            # emitting all complete frames; keep what we have.
            return stdout.decode("utf-8", "replace")
        stderr = (completed.stderr or b"").decode("utf-8", "replace").strip()
        raise DshParseError(
            f"cannot decompress {path}: {stderr[:300] or 'zstd produced no output'}"
        )

    if zstd_available():
        return _decompress_with_zstandard(Path(path))

    raise DshConfigError(
        "DSH session logs are zstd-compressed but no decompressor is available; "
        "install `zstd` (brew install zstd / apt install zstd) or the "
        "`zstandard` Python package"
    )


def parse_json_lines(text: str) -> Tuple[List[Dict[str, Any]], int]:
    """Parse JSONL text; returns ``(objects, malformed_line_count)``."""
    objects: List[Dict[str, Any]] = []
    malformed = 0
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except ValueError:
            malformed += 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        else:
            malformed += 1
    return objects, malformed


def read_session_events(
    path: Path, *, timeout: float = ZSTD_TIMEOUT_SECONDS
) -> Tuple[List[Dict[str, Any]], int]:
    """Decompress + parse one log; ``(events, malformed_lines)``."""
    return parse_json_lines(decompress_session(path, timeout=timeout))


# ---------------------------------------------------------------------------
# Small coercion helpers
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            try:
                number = float(text)
            except ValueError:
                return None
            return int(number) if number == number else None
    return None


def ms_to_iso(value: Any) -> Optional[str]:
    """Epoch milliseconds -> ISO-8601 UTC ``...Z``; None when unusable."""
    millis = _as_int(value)
    if millis is None or millis <= 0:
        return None
    try:
        moment = datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_since(value: Any) -> Optional[str]:
    """Validate a ``--since`` value (ISO string, epoch seconds or ms)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 10_000_000_000:  # milliseconds
            seconds /= 1000.0
        try:
            moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise DshConfigError(f"invalid --since value: {value!r}") from exc
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(value).strip()
    if text.isdigit():
        return normalize_since(int(text))
    normalized = normalize_iso_timestamp(text)
    if normalized is None:
        raise DshConfigError(f"invalid --since timestamp: {value!r}")
    return normalized


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


# ---------------------------------------------------------------------------
# Event mapping
# ---------------------------------------------------------------------------


def _model_from_header(data: Mapping[str, Any]) -> Optional[str]:
    raw = data.get("header")
    if isinstance(raw, Mapping):
        raw = json.dumps(raw)
    text = str(raw or "")
    match = _MODEL_PATTERN.search(text)
    return match.group(1) if match else None


def _parse_arguments(value: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return ``(parsed_object, raw_text)`` for a tool/call ``arguments`` field."""
    if isinstance(value, Mapping):
        return _json_safe(dict(value)), None
    if not isinstance(value, str):
        return None, (json.dumps(_json_safe(value))[:2000] if value else None)
    text = value.strip()
    if not text:
        return {}, None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None, text[:2000]
    if isinstance(parsed, Mapping):
        return _json_safe(dict(parsed)), None
    return None, text[:2000]


def _tool_call_summary(data: Mapping[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    arguments, raw = _parse_arguments(data.get("arguments"))
    if arguments is not None:
        summary["arguments"] = arguments
    if raw:
        summary["arguments_raw"] = raw
    command = None
    file_path = None
    if arguments:
        command = arguments.get("command") or arguments.get("cmd")
        file_path = (
            arguments.get("file_path")
            or arguments.get("filePath")
            or arguments.get("path")
            or arguments.get("file")
        )
    if isinstance(command, str) and command.strip():
        summary["command"] = command.strip()[:20000]
    if isinstance(file_path, str) and file_path.strip():
        summary["file"] = file_path.strip()[:2000]
    step = _as_int(data.get("step"))
    if step is not None:
        summary["step"] = step
    return summary


def _result_call_id(data: Mapping[str, Any]) -> Optional[str]:
    candidates: List[Any] = [data.get("toolCallId"), data.get("callId")]
    message = data.get("message")
    if isinstance(message, Mapping):
        candidates.extend(
            [
                message.get("toolCallId"),
                message.get("callId"),
            ]
        )
        source = message.get("source")
        if isinstance(source, Mapping):
            candidates.append(source.get("callId"))
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _result_is_error(data: Mapping[str, Any]) -> bool:
    message = data.get("message")
    for candidate in (data, message if isinstance(message, Mapping) else {}):
        if not candidate:
            continue
        if candidate.get("isError") is True:
            return True
        if candidate.get("error") not in (None, "", False):
            return True
    return False


def _result_text(data: Mapping[str, Any]) -> Optional[str]:
    message = data.get("message")
    content: Any = data.get("content")
    if content is None and isinstance(message, Mapping):
        content = message.get("content")
    if isinstance(content, str):
        return content[:MAX_RESULT_TEXT]
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)[:MAX_RESULT_TEXT]
    return None


def _fidelity(kind: str) -> str:
    if kind == "session.started" or kind.startswith("turn."):
        return "L1"
    return "L3"


def _envelope(
    kind: str,
    *,
    summary: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    cwd: Optional[str] = None,
    turn_id: Optional[str] = None,
    tool_use_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    status: Optional[str] = None,
    duration_ms: Optional[int] = None,
    model: Optional[str] = None,
    ts: Optional[str] = None,
) -> Dict[str, Any]:
    safe_summary = redact.redact_payload(
        summary or {},
        level=redact.resolve_level(None),
        max_bytes=redact.resolve_max_bytes(None),
    )
    return build_envelope(
        kind=kind,
        harness=HARNESS,
        summary=safe_summary,
        actor=ACTOR,
        actor_kind="agent",
        session_id=session_id,
        turn_id=turn_id,
        tool_use_id=tool_use_id,
        tool_name=tool_name,
        status=status,
        duration_ms=duration_ms,
        cwd=cwd,
        model=model,
        fidelity=_fidelity(kind),
        ts=ts,
        source=SOURCE,
        project=None,
    )


@dataclass
class _Context:
    session_id: Optional[str] = None
    cwd: Optional[str] = None
    model: Optional[str] = None
    calls: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def _header_event(
    header: Mapping[str, Any], workspace: Optional[str]
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for source_key, summary_key in (
        ("version", "version"),
        ("origin", "origin"),
        ("parentSession", "parent_session"),
        ("agentPreset", "agent_preset"),
        ("delegationDepth", "delegation_depth"),
    ):
        value = header.get(source_key)
        if value not in (None, "", False):
            summary[summary_key] = _json_safe(value)
    if workspace:
        summary["workspace"] = workspace
    return _envelope(
        "session.started",
        summary=summary,
        session_id=str(header.get("id") or "") or None,
        cwd=str(header.get("cwd") or "") or None,
        ts=ms_to_iso(header.get("createdAt")),
    )


def _map_event(
    event: Mapping[str, Any],
    seq: int,
    *,
    ts: Optional[str],
    context: _Context,
) -> Optional[Dict[str, Any]]:
    event_type = event.get("type")
    data = event.get("data")
    if not isinstance(event_type, str) or not isinstance(data, Mapping):
        return None

    if event_type == "turn/start":
        return _envelope(
            "turn.started",
            summary={"turn": _json_safe(data.get("turn"))},
            session_id=context.session_id,
            cwd=context.cwd,
            turn_id=str(data.get("turn") or "") or None,
            model=context.model,
            ts=ts,
        )
    if event_type == "turn/end":
        summary: Dict[str, Any] = {"turn": _json_safe(data.get("turn"))}
        if data.get("reason") not in (None, ""):
            summary["reason"] = _json_safe(data.get("reason"))
        return _envelope(
            "turn.ended",
            summary=summary,
            session_id=context.session_id,
            cwd=context.cwd,
            turn_id=str(data.get("turn") or "") or None,
            model=context.model,
            ts=ts,
        )
    if event_type == "approval/policy":
        return _envelope(
            "policy.decision",
            summary={
                "policy": _json_safe(data.get("policy")),
                "source": _json_safe(data.get("source")),
            },
            session_id=context.session_id,
            cwd=context.cwd,
            model=context.model,
            status="policy",
            ts=ts,
        )
    if event_type == "request/header":
        model = _model_from_header(data)
        if model:
            context.model = model
        return None
    if event_type == "tool/call":
        call_id = data.get("callId")
        call_id_text = str(call_id).strip() if call_id is not None else ""
        context.calls[call_id_text] = {
            "time": _as_int(event.get("time")),
            "turn": data.get("turn"),
            "step": data.get("step"),
            "name": data.get("name"),
        }
        turn_id = str(data.get("turn") or "") or None
        return _envelope(
            "tool.started",
            summary=_tool_call_summary(data),
            session_id=context.session_id,
            cwd=context.cwd,
            turn_id=turn_id,
            tool_use_id=call_id_text or None,
            tool_name=str(data.get("name") or "") or None,
            status="started",
            model=context.model,
            ts=ts,
        )
    if event_type == "tool/result":
        call_id = _result_call_id(data)
        call = context.calls.get(call_id or "")
        failed = _result_is_error(data)
        duration_ms = None
        result_time = _as_int(event.get("time"))
        if call and call.get("time") is not None and result_time is not None:
            delta = result_time - int(call["time"])
            duration_ms = delta if delta >= 0 else None
        summary = {}
        text = _result_text(data)
        if text is not None:
            summary["result"] = text
        summary["is_error"] = failed
        step = _as_int(data.get("step"))
        if step is not None:
            summary["step"] = step
        tool_name = str(call.get("name") or "") if call else None
        return _envelope(
            "tool.failed" if failed else "tool.completed",
            summary=summary,
            session_id=context.session_id,
            cwd=context.cwd,
            turn_id=str(data.get("turn") or (call or {}).get("turn") or "") or None,
            tool_use_id=call_id,
            tool_name=tool_name or None,
            status="failed" if failed else "completed",
            duration_ms=duration_ms,
            model=context.model,
            ts=ts,
        )
    return None


# ---------------------------------------------------------------------------
# Public parse entry point
# ---------------------------------------------------------------------------


def parse_session_file(
    path: Path,
    *,
    since_seq: int = -1,
    since: Optional[str] = None,
    workspace: Optional[str] = None,
    session_id_fallback: Optional[str] = None,
) -> SessionParseResult:
    """Parse one DSH session log into canonical envelopes newer than *since_seq*.

    Stateful event types (tool calls, model) are processed even for events at or
    below ``since_seq`` so a resumed tail can still pair results with calls.
    """
    events, malformed = read_session_events(path)
    context = _Context(session_id=session_id_fallback)
    header: Optional[Mapping[str, Any]] = None
    header_event: Optional[Dict[str, Any]] = None
    parsed: List[ParsedEvent] = []
    last_seq = max(0, int(since_seq))
    since_text = normalize_since(since)

    for raw in events:
        if raw.get("type") == "session" and "seq" not in raw:
            header = raw
            if context.session_id is None and raw.get("id"):
                context.session_id = str(raw.get("id"))
            if raw.get("cwd") and not context.cwd:
                context.cwd = str(raw.get("cwd"))
            continue

        seq = _as_int(raw.get("seq"))
        if seq is None or seq < 0:
            continue
        last_seq = max(last_seq, seq)
        if not isinstance(raw.get("data"), Mapping):
            continue
        ts = ms_to_iso(raw.get("time"))
        envelope = _map_event(raw, seq, ts=ts, context=context)
        if seq <= since_seq or envelope is None:
            continue
        if since_text is not None and ts is not None and ts < since_text:
            continue
        event_ts = str(envelope.get("ts") or "")
        if since_text is not None and event_ts and event_ts < since_text:
            continue
        parsed.append(
            ParsedEvent(
                seq=seq, kind=str(envelope.get("kind") or ""), envelope=envelope
            )
        )

    session_id = (
        str(header.get("id"))
        if isinstance(header, Mapping) and header.get("id")
        else (session_id_fallback or "")
    )
    if isinstance(header, Mapping):
        context.session_id = session_id or context.session_id
        header_event = _header_event(header, workspace)
        if not header_event.get("session_id"):
            header_event["session_id"] = context.session_id or None
        if not header_event.get("cwd"):
            header_event["cwd"] = context.cwd
        if header_event.get("ts") is None:
            header_event["ts"] = normalize_iso_timestamp(None) or ""
            if not header_event["ts"]:
                header_event["ts"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
    return SessionParseResult(
        path=Path(path),
        session_id=session_id,
        header_event=header_event,
        events=parsed,
        last_seq=last_seq,
        malformed_lines=malformed,
    )


def iter_parsed_events(paths: Iterable[Path]) -> Iterable[ParsedEvent]:
    """Convenience: parse several session files (skipping unreadable ones)."""
    for path in paths:
        try:
            result = parse_session_file(path)
        except DshParseError:
            continue
        yield from result.events
