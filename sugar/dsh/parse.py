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
- ``assistant/message`` (and ``assistant/attempt`` when it carries usage)
  includes ``data.usage`` = ``{inputTokens, outputTokens, cacheReadTokens,
  cacheWriteTokens, totalTokens}``. These are **per-response incremental**
  counters for that single model call (input + output + cache read + cache
  write == totalTokens), not cumulative session sums — so one
  ``usage.reported`` is emitted per record (deduped by a usage fingerprint)
  and summing them is correct. ``tokens_in`` is the full prompt side (fresh
  input + cache read + cache write) so ``tokens_in + tokens_out`` equals the
  provider's ``totalTokens``; the fresh slice is kept as ``input_tokens``.
  ``compaction/summary`` usage is a separate compaction call and is
  intentionally not counted here.

Trace-enrichment mappings (frozen contract for the trace backend):

- ``subagent/catalog`` (parent session) -> ``subagent.started`` with
  ``parent_session_id``/``child_session_id``/``label``/``mode``/``created_at``;
  ``childCreatedAt`` is the ms created timestamp. Catalog entries are unique per
  child, so the event is emitted once.
- ``team/task`` -> ``team.task`` with ``team_id``/``team_task_id``/``subject``/
  ``status``/``owner_id``/``owner_name?``/``blocked_by?``/``parent_session_id``
  plus the task ``revision`` for update ordering. One event per task revision
  (deduped by team+task+revision); owner names are resolved from
  ``team/member`` records seen in the same session.
- ``deliverables/presented`` files and successful ``write`` tool results ->
  ``artifact.created`` with ``path``/``description?``/``turn``/
  ``tool_call_id?`` (paths only, never file contents).
- Successful ``edit`` tool results -> optional ``workspace.changed`` with the
  reliable changed ``paths`` (and a diff count, never diff contents).
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

#: DSH usage object keys -> canonical token buckets (camelCase first, snake_case
#: tolerated for generated/future logs).
_USAGE_INPUT_KEYS = ("inputTokens", "input_tokens", "promptTokens", "prompt_tokens")
_USAGE_OUTPUT_KEYS = (
    "outputTokens",
    "output_tokens",
    "completionTokens",
    "completion_tokens",
)
_USAGE_CACHE_READ_KEYS = (
    "cacheReadTokens",
    "cache_read_tokens",
    "cache_read_input_tokens",
)
_USAGE_CACHE_WRITE_KEYS = (
    "cacheWriteTokens",
    "cache_write_tokens",
    "cache_creation_input_tokens",
)
_USAGE_TOTAL_KEYS = ("totalTokens", "total_tokens")

#: Event types that can carry an assistant-response usage object.
_USAGE_EVENT_TYPES = ("assistant/message", "assistant/attempt")

_IGNORED_TYPES = frozenset(
    {
        "session/title",
        "subagent/descriptor",
        "sandbox/mode",
        "agent/inbox/spliced",
        "system/message",
        "user/message",
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


def _first_int(data: Mapping[str, Any], keys: Sequence[str]) -> Optional[int]:
    for key in keys:
        value = _as_int(data.get(key))
        if value is not None:
            return value
    return None


def _project_from_cwd(cwd: Optional[str]) -> Optional[str]:
    """Project label for usage grouping: basename of the session cwd."""
    if not isinstance(cwd, str) or not cwd.strip():
        return None
    text = cwd.strip().rstrip("/\\")
    if not text:
        return None
    name = text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    return name or None


def _usage_summary(data: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize one DSH assistant ``usage`` object to canonical token buckets.

    DSH reports ``inputTokens`` (fresh/uncached prompt), ``outputTokens``,
    ``cacheReadTokens``/``cacheWriteTokens`` and ``totalTokens``, where
    ``totalTokens == input + output + cache read + cache write`` for that one
    model call. ``tokens_in`` is the **full prompt side** (fresh input + cache
    read/write) so the aggregator's ``tokens_total`` (in + out) equals DSH's
    own ``totalTokens`` — i.e. /api/usage reports the same token volume the
    provider counted. The fresh-input slice stays available as
    ``input_tokens`` and the cache slices as ``cache_read_tokens`` /
    ``cache_write_tokens`` for future cost mapping.

    Each record is per-response incremental (never cumulative), so no
    delta/cumulative arithmetic is applied; duplicates are deduped by the
    caller's fingerprint.
    """
    usage = data.get("usage")
    if not isinstance(usage, Mapping):
        message = data.get("message")
        if isinstance(message, Mapping) and isinstance(message.get("usage"), Mapping):
            usage = message.get("usage")
        else:
            return None

    input_tokens = _first_int(usage, _USAGE_INPUT_KEYS)
    tokens_out = _first_int(usage, _USAGE_OUTPUT_KEYS)
    cache_read = _first_int(usage, _USAGE_CACHE_READ_KEYS)
    cache_write = _first_int(usage, _USAGE_CACHE_WRITE_KEYS)
    provider_total = _first_int(usage, _USAGE_TOTAL_KEYS)
    cache_total = max(cache_read or 0, 0) + max(cache_write or 0, 0)
    if tokens_out is None and provider_total is not None and input_tokens is not None:
        tokens_out = max(provider_total - input_tokens - cache_total, 0)
    if input_tokens is None and provider_total is not None and tokens_out is not None:
        input_tokens = max(provider_total - tokens_out - cache_total, 0)
    if input_tokens is None and tokens_out is None:
        return None
    input_tokens = max(input_tokens or 0, 0)
    tokens_out = max(tokens_out or 0, 0)
    tokens_in = input_tokens + cache_total

    summary: Dict[str, Any] = {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": tokens_in + tokens_out,
        "input_tokens": input_tokens,
        "usage_source": "assistant/usage",
    }
    if cache_read is not None and cache_read >= 0:
        summary["cache_read_tokens"] = cache_read
    if cache_write is not None and cache_write >= 0:
        summary["cache_write_tokens"] = cache_write
    if provider_total is not None and provider_total >= 0:
        summary["provider_total_tokens"] = provider_total
    return summary


def _usage_event(
    data: Mapping[str, Any],
    context: _Context,
    *,
    ts: Optional[str],
) -> Optional[Dict[str, Any]]:
    summary = _usage_summary(data)
    if summary is None:
        return None
    turn = data.get("turn")
    step = _as_int(data.get("step"))
    if step is not None:
        summary["step"] = step
    project = _project_from_cwd(context.cwd)
    if project:
        summary["project"] = project
    fingerprint = (
        str(turn),
        step,
        summary.get("tokens_in"),
        summary.get("tokens_out"),
        summary.get("cache_read_tokens"),
        summary.get("cache_write_tokens"),
        summary.get("provider_total_tokens"),
    )
    if fingerprint in context.usage_seen:
        return None
    context.usage_seen.add(fingerprint)

    envelope = _envelope(
        "usage.reported",
        summary=summary,
        session_id=context.session_id,
        cwd=context.cwd,
        turn_id=str(turn) if turn not in (None, "") else None,
        model=context.model,
        project=project,
        status="completed",
        ts=ts,
    )
    # Compatibility bridge: /api/usage reads the spine event's top-level
    # ``payload`` object (sugar/dashboard/usage.py), while canonical envelopes
    # keep the summary under payload.payload. Mirror the aggregator-accepted
    # fields at the top level so DSH usage appears in /api/usage too.
    payload = envelope.get("payload")
    if isinstance(payload, dict):
        for key in ("tokens_in", "tokens_out", "tokens_total"):
            if key in summary:
                payload[key] = summary[key]
        if context.model:
            payload["model"] = context.model
    return envelope


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
    if kind in ("usage.reported", "team.task"):
        return "L2"
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
    project: Optional[str] = None,
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
        project=project,
    )


@dataclass
class _Context:
    session_id: Optional[str] = None
    cwd: Optional[str] = None
    model: Optional[str] = None
    calls: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Exact usage records already emitted (assistant/attempt + assistant/message
    #: can mirror the same record; a retry never repeats a successful request).
    usage_seen: set = field(default_factory=set)
    #: team/member id -> display name (used to fill team.task owner_name).
    team_members: Dict[str, str] = field(default_factory=dict)
    #: Stable dedupe keys for trace-enrichment events.
    subagents_seen: set = field(default_factory=set)
    team_tasks_seen: set = field(default_factory=set)
    #: session+path -> emitted artifact.created envelope (one per path, with
    #: description/sha merged in from later presented-file events).
    artifact_by_path: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    workspaces_seen: set = field(default_factory=set)


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
    parent_session = header.get("parentSession")
    if parent_session not in (None, ""):
        summary["parent_session_id"] = _json_safe(parent_session)
    return _envelope(
        "session.started",
        summary=summary,
        session_id=str(header.get("id") or "") or None,
        cwd=str(header.get("cwd") or "") or None,
        ts=ms_to_iso(header.get("createdAt")),
    )


def _as_text(value: Any, *, limit: int = 1000) -> Optional[str]:
    if value is None or isinstance(value, (Mapping, list, tuple)):
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _subagent_started(
    data: Mapping[str, Any], context: _Context, *, ts: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Map one parent-side ``subagent/catalog`` entry to ``subagent.started``."""
    child_id = _as_text(data.get("childId"), limit=300)
    if not child_id:
        return None
    created_at = ms_to_iso(data.get("childCreatedAt"))
    key = (context.session_id or "", child_id, created_at or "")
    if key in context.subagents_seen:
        return None
    context.subagents_seen.add(key)
    summary: Dict[str, Any] = {"child_session_id": child_id}
    if context.session_id:
        summary["parent_session_id"] = context.session_id
    label = _as_text(data.get("label"))
    if label:
        summary["label"] = label
    mode = _as_text(data.get("mode"), limit=100)
    if mode:
        summary["mode"] = mode
    if created_at:
        summary["created_at"] = created_at
    return _envelope(
        "subagent.started",
        summary=summary,
        session_id=context.session_id,
        cwd=context.cwd,
        model=context.model,
        project=_project_from_cwd(context.cwd),
        status="started",
        ts=ts,
    )


def _team_task_event(
    data: Mapping[str, Any], context: _Context, *, ts: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Map one ``team/task`` revision snapshot to ``team.task``."""
    task = data.get("task")
    if not isinstance(task, Mapping):
        return None
    task_id = _as_text(task.get("id"), limit=200)
    if not task_id:
        return None
    team_id = _as_text(data.get("teamId"), limit=300) or context.session_id
    revision = _as_int(task.get("revision"))
    status = _as_text(task.get("status"), limit=60)
    key = (
        team_id or "",
        task_id,
        str(revision) if revision is not None else status or "",
    )
    if key in context.team_tasks_seen:
        return None
    context.team_tasks_seen.add(key)

    owner_id = _as_text(task.get("ownerId"), limit=300)
    owner_name = context.team_members.get(owner_id) if owner_id else None
    summary: Dict[str, Any] = {
        "team_id": team_id,
        "team_task_id": task_id,
        "subject": _as_text(task.get("subject"), limit=500),
        "status": status,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "parent_session_id": team_id or context.session_id,
    }
    if revision is not None:
        summary["revision"] = revision
    blocked_by = task.get("blockedBy")
    if isinstance(blocked_by, list) and blocked_by:
        summary["blocked_by"] = [_json_safe(item) for item in blocked_by[:50]]
    return _envelope(
        "team.task",
        summary={key: value for key, value in summary.items() if value is not None},
        session_id=context.session_id,
        cwd=context.cwd,
        project=_project_from_cwd(context.cwd),
        ts=ts,
    )


def _artifact_envelope(
    path: str,
    *,
    context: _Context,
    description: Optional[str] = None,
    turn: Optional[int] = None,
    call_id: Optional[str] = None,
    sha256: Optional[str] = None,
    operation: Optional[str] = None,
    ts: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build one ``artifact.created`` envelope; a session+path is emitted once.

    A file can be produced by a tool result and later presented (or written
    again). The artifact identity is the session + path, so later sources only
    enrich a missing description/sha256 instead of duplicating the event.
    """
    key = f"{context.session_id or ''}\0{path}"
    existing = context.artifact_by_path.get(key)
    if existing is not None:
        summary = existing.get("payload", {}).get("payload")
        if isinstance(summary, dict):
            if description and not summary.get("description"):
                summary["description"] = description
            if sha256 and not summary.get("sha256"):
                summary["sha256"] = sha256
            if operation and not summary.get("operation"):
                summary["operation"] = operation
        return None

    summary: Dict[str, Any] = {"path": path}
    if description:
        summary["description"] = description
    if operation:
        summary["operation"] = operation
    if turn is not None:
        summary["turn"] = turn
    if call_id:
        summary["tool_call_id"] = call_id
    if sha256:
        summary["sha256"] = sha256
    envelope = _envelope(
        "artifact.created",
        summary=summary,
        session_id=context.session_id,
        cwd=context.cwd,
        turn_id=str(turn) if turn is not None else None,
        tool_use_id=call_id,
        project=_project_from_cwd(context.cwd),
        ts=ts,
    )
    context.artifact_by_path[key] = envelope
    return envelope


def _deliverable_artifacts(
    data: Mapping[str, Any], context: _Context, *, ts: Optional[str]
) -> List[Dict[str, Any]]:
    """Map ``deliverables/presented`` files to ``artifact.created`` events."""
    turn = _as_int(data.get("turn"))
    call_id = _as_text(data.get("callId"), limit=300)
    files = data.get("files")
    if not isinstance(files, list):
        return []
    envelopes: List[Dict[str, Any]] = []
    for item in files:
        if not isinstance(item, Mapping):
            continue
        path = _as_text(item.get("path"), limit=1000)
        if not path:
            continue
        envelope = _artifact_envelope(
            path,
            context=context,
            description=_as_text(item.get("description"), limit=500),
            turn=turn,
            call_id=call_id,
            sha256=_as_text(item.get("sha256"), limit=100),
            ts=ts,
        )
        if envelope is not None:
            envelopes.append(envelope)
    return envelopes


def _tool_result_enrichment(
    data: Mapping[str, Any], context: _Context, *, ts: Optional[str]
) -> List[Dict[str, Any]]:
    """Artifact/workspace events derived from a successful file-producing tool result."""
    if _result_is_error(data):
        return []
    call_id = _result_call_id(data)
    call = context.calls.get(call_id or "")
    if not call:
        return []
    tool_name = str(call.get("name") or "")
    if tool_name not in ("write", "edit"):
        return []
    meta = data.get("meta")
    if not isinstance(meta, Mapping):
        return []
    turn = _as_int(data.get("turn"))
    if turn is None:
        turn = _as_int(call.get("turn"))
    paths: List[str] = []
    meta_path = _as_text(meta.get("path"), limit=1000)
    if meta_path:
        paths.append(meta_path)
    diffs = meta.get("diffs")
    if isinstance(diffs, list):
        for diff in diffs[:50]:
            if not isinstance(diff, Mapping):
                continue
            diff_path = _as_text(diff.get("path"), limit=1000)
            if diff_path and diff_path not in paths:
                paths.append(diff_path)
    if not paths:
        return []

    envelopes: List[Dict[str, Any]] = []
    if tool_name == "write":
        operation = _as_text(meta.get("operation"), limit=40)
        for path in paths:
            envelope = _artifact_envelope(
                path,
                context=context,
                turn=turn,
                call_id=call_id,
                operation=operation,
                ts=ts,
            )
            if envelope is not None:
                envelopes.append(envelope)
    else:  # edit: reliable file paths/diffs exist -> optional workspace.changed
        key = ("edit", tuple(paths), call_id or "")
        if key in context.workspaces_seen:
            return []
        context.workspaces_seen.add(key)
        summary = {"paths": paths, "operation": "edit"}
        if turn is not None:
            summary["turn"] = turn
        if call_id:
            summary["tool_call_id"] = call_id
        if isinstance(diffs, list):
            summary["diff_count"] = len(diffs)
        envelopes.append(
            _envelope(
                "workspace.changed",
                summary=summary,
                session_id=context.session_id,
                cwd=context.cwd,
                turn_id=str(turn) if turn is not None else None,
                tool_use_id=call_id,
                project=_project_from_cwd(context.cwd),
                ts=ts,
            )
        )
    return envelopes


def _map_extra_events(
    event: Mapping[str, Any],
    seq: int,
    *,
    ts: Optional[str],
    context: _Context,
) -> List[Dict[str, Any]]:
    """Secondary envelopes for one raw DSH event (multi-file artifacts, etc.)."""
    event_type = event.get("type")
    data = event.get("data")
    if not isinstance(event_type, str) or not isinstance(data, Mapping):
        return []
    if event_type == "deliverables/presented":
        return _deliverable_artifacts(data, context, ts=ts)
    if event_type == "tool/result":
        return _tool_result_enrichment(data, context, ts=ts)
    return []


def _resolve_team_owner_names(events: List[ParsedEvent], context: _Context) -> None:
    """Fill owner_name on team.task events whose member record arrived later."""
    if not context.team_members:
        return
    for item in events:
        if item.kind != "team.task":
            continue
        payload = item.envelope.get("payload")
        if not isinstance(payload, dict):
            continue
        summary = payload.get("payload")
        if not isinstance(summary, dict) or summary.get("owner_name"):
            continue
        owner_id = summary.get("owner_id")
        name = context.team_members.get(str(owner_id)) if owner_id else None
        if name:
            summary["owner_name"] = name


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

    if event_type == "subagent/catalog":
        return _subagent_started(data, context, ts=ts)
    if event_type == "team/member":
        member = data.get("member")
        if isinstance(member, Mapping):
            member_id = _as_text(member.get("id"), limit=300)
            member_name = _as_text(member.get("name"), limit=200)
            if member_id and member_name:
                context.team_members[member_id] = member_name
        return None
    if event_type == "team/task":
        return _team_task_event(data, context, ts=ts)
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
    if event_type in _USAGE_EVENT_TYPES:
        return _usage_event(data, context, ts=ts)
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
        envelopes: List[Dict[str, Any]] = []
        if envelope is not None:
            envelopes.append(envelope)
        envelopes.extend(_map_extra_events(raw, seq, ts=ts, context=context))
        if seq <= since_seq or not envelopes:
            continue
        for item in envelopes:
            if since_text is not None and ts is not None and ts < since_text:
                continue
            event_ts = str(item.get("ts") or "")
            if since_text is not None and event_ts and event_ts < since_text:
                continue
            parsed.append(
                ParsedEvent(seq=seq, kind=str(item.get("kind") or ""), envelope=item)
            )

    _resolve_team_owner_names(parsed, context)

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
