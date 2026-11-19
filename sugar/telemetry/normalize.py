"""Tolerant normalization of raw harness hook payloads to canonical telemetry.

Supports Claude Code, Codex and Cursor hook shapes (see ``docs/telemetry.md`` /
``docs/local-dashboard.md`` §11). The normalizer never imports ``sugar.connect``
and never raises on unexpected optional fields — unknown events are ignored
(``None``) and malformed top-level bodies raise a validation error the HTTP
endpoint turns into a 400.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from .schema import (
    TelemetryValidationError,
    normalize_iso_timestamp,
    now_iso,
    status_for_kind,
)

KNOWN_HARNESSES = ("claude-code", "codex", "cursor")
HARNESS_ALIASES = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "claude_code": "claude-code",
    "claudecode": "claude-code",
    "codex": "codex",
    "openai-codex": "codex",
    "cursor": "cursor",
    "cursor-agent": "cursor",
    "cursor_agent": "cursor",
}

_TOOL_INPUT_KEYS = ("tool_input", "toolInput", "input", "arguments", "tool_args")
_TOOL_RESPONSE_KEYS = (
    "tool_response",
    "toolResponse",
    "tool_result",
    "result",
    "output",
)
_TOOL_USE_ID_KEYS = (
    "tool_use_id",
    "toolUseId",
    "call_id",
    "callId",
    "tool_call_id",
    "toolCallId",
)
_SESSION_ID_KEYS = (
    "session_id",
    "sessionId",
    "conversation_id",
    "conversationId",
)
_CWD_KEYS = ("cwd", "working_directory", "workingDirectory", "workspace_root")
_MESSAGE_KEYS = ("message", "prompt", "text", "note", "summary")
_MODEL_KEYS = ("model", "model_name", "modelName")
_DURATION_KEYS = ("duration_ms", "durationMs", "elapsed_ms", "elapsedMs")
_TIMESTAMP_KEYS = ("ts", "timestamp", "event_time", "eventTime")
_EVENT_NAME_KEYS = ("hook_event_name", "event", "event_name", "type", "name")

_TOOL_NAME_ALIASES = {
    "shell": "Bash",
    "bash": "Bash",
    "exec_command": "Bash",
    "run_command": "Bash",
    "read": "Read",
    "readfile": "Read",
    "read_file": "Read",
    "edit": "Edit",
    "editfile": "Edit",
    "edit_file": "Edit",
    "write": "Write",
    "writefile": "Write",
    "write_file": "Write",
    "grep": "Grep",
    "glob": "Glob",
}

_EVENT_ALIASES = {
    # Claude Code hook names
    "pretooluse": "tool.started",
    "pre_tool_use": "tool.started",
    "posttooluse": "tool.completed",
    "post_tool_use": "tool.completed",
    "posttoolusefailure": "tool.failed",
    "post_tool_use_failure": "tool.failed",
    "sessionstart": "session.started",
    "session_start": "session.started",
    "sessionend": "session.ended",
    "session_end": "session.ended",
    "userpromptsubmit": "turn.started",
    "user_prompt_submit": "turn.started",
    "stop": "turn.ended",
    "subagentstop": "turn.ended",
    "subagent_stop": "turn.ended",
    # Codex / generic aliases
    "tool.started": "tool.started",
    "tool_started": "tool.started",
    "tool_call": "tool.started",
    "toolcall": "tool.started",
    "tool.completed": "tool.completed",
    "tool_completed": "tool.completed",
    "tool_result": "tool.completed",
    "toolresult": "tool.completed",
    "tool.failed": "tool.failed",
    "tool_failed": "tool.failed",
    "session.started": "session.started",
    "session.ended": "session.ended",
    "turn.started": "turn.started",
    "turn_start": "turn.started",
    "turn.ended": "turn.ended",
    "turn_end": "turn.ended",
    "run.started": "run.started",
    "run_started": "run.started",
    "run.ended": "run.ended",
    "run_ended": "run.ended",
    "usage": "usage.reported",
    "usage.reported": "usage.reported",
    "usage_reported": "usage.reported",
    # Cursor hook names
    "beforeshellexecution": "tool.started",
    "before_shell_execution": "tool.started",
    "beforemcpexecution": "tool.started",
    "before_mcp_execution": "tool.started",
    "beforereadfile": "tool.started",
    "before_read_file": "tool.started",
    "beforetabfileread": "tool.started",
    "before_tab_file_read": "tool.started",
    "aftershellexecution": "tool.completed",
    "after_shell_execution": "tool.completed",
    "aftermcpexecution": "tool.completed",
    "after_mcp_execution": "tool.completed",
    "afterfileedit": "tool.completed",
    "after_file_edit": "tool.completed",
    "aftertabfileedit": "tool.completed",
    "after_tab_file_edit": "tool.completed",
    "beforesubmitprompt": "turn.started",
    "before_submit_prompt": "turn.started",
}


@dataclass
class NormalizedEvent:
    """Normalized hook event, before redaction and envelope building."""

    kind: str
    harness: str
    fidelity: str
    summary: Dict[str, Any] = field(default_factory=dict)
    actor: Optional[str] = None
    actor_kind: str = "agent"
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    run_id: Optional[str] = None
    project: Optional[str] = None
    trace_id: Optional[str] = None
    turn_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    tool_use_id: Optional[str] = None
    tool_name: Optional[str] = None
    status: Optional[str] = None
    duration_ms: Optional[int] = None
    cwd: Optional[str] = None
    model: Optional[str] = None
    ts: str = field(default_factory=now_iso)


def normalize_harness_name(value: Any) -> Optional[str]:
    """Map a path/alias to a canonical known harness name (or ``None``)."""
    if value is None:
        return None
    key = str(value).strip().lower().replace(" ", "-")
    return HARNESS_ALIASES.get(key)


def _text(value: Any, limit: int = 1000) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _first_text(mapping: Mapping[str, Any], keys, limit: int = 1000) -> Optional[str]:
    for key in keys:
        value = _text(mapping.get(key), limit)
        if value:
            return value
    return None


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _event_name(body: Mapping[str, Any]) -> str:
    for key in _EVENT_NAME_KEYS:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower().replace("-", "_").replace(" ", "_")
    return ""


def _timestamp(body: Mapping[str, Any]) -> Optional[str]:
    for key in _TIMESTAMP_KEYS:
        value = body.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            seconds = float(value)
            if seconds > 10_000_000_000:  # milliseconds
                seconds /= 1000.0
            return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        normalized = normalize_iso_timestamp(value)
        if normalized:
            return normalized
    return None


def _duration_ms(
    body: Mapping[str, Any], tool_input: Mapping[str, Any]
) -> Optional[int]:
    for source in (body, tool_input):
        for key in _DURATION_KEYS:
            value = source.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number >= 0:
                return int(number)
    return None


def _tool_name(body: Mapping[str, Any]) -> Optional[str]:
    nested = _mapping(body.get("tool"))
    raw = (
        nested.get("name")
        or body.get("tool_name")
        or body.get("toolName")
        or body.get("name")
        or body.get("tool")
    )
    text = _text(raw, 200)
    if not text:
        return None
    alias = _TOOL_NAME_ALIASES.get(text.strip().lower())
    return alias or text


def _tool_use_id(body: Mapping[str, Any]) -> Optional[str]:
    nested = _mapping(body.get("tool"))
    for key in _TOOL_USE_ID_KEYS:
        value = _text(body.get(key), 200)
        if value:
            return value
    return _text(nested.get("id"), 200) or _text(nested.get("call_id"), 200)


def _usage_summary(body: Mapping[str, Any]) -> Dict[str, Any]:
    usage = _mapping(body.get("usage"))
    source = usage or body
    summary: Dict[str, Any] = {}
    for field in ("tokens_in", "tokens_out", "cost_usd", "model"):
        if field in source:
            summary[field] = source.get(field)
    if "model" in summary and not summary["model"]:
        summary.pop("model", None)
    return summary


def _summary_for(
    kind: str,
    body: Mapping[str, Any],
    tool_input: Mapping[str, Any],
    tool_response: Any,
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    message = _first_text(body, _MESSAGE_KEYS, 20000)
    if message:
        summary["message"] = message
    tool_name = _tool_name(body)
    if tool_name:
        summary["tool_name"] = tool_name
    command = _text(
        body.get("command")
        or tool_input.get("command")
        or tool_input.get("cmd")
        or tool_input.get("shell_command"),
        20000,
    )
    if command:
        summary["command"] = command
    file_path = _text(
        body.get("file_path")
        or body.get("filePath")
        or tool_input.get("file_path")
        or tool_input.get("path")
        or tool_input.get("file"),
        2000,
    )
    if file_path:
        summary["file"] = file_path
    description = _text(tool_input.get("description") or body.get("description"), 2000)
    if description:
        summary["description"] = description
    if tool_input:
        summary["input"] = tool_input
    if tool_response is not None:
        summary["result"] = tool_response
    if kind == "usage.reported":
        summary.update(_usage_summary(body))
    if (
        kind.startswith("verification.")
        or kind.startswith("policy.")
        or kind.startswith("approval.")
    ):
        for key in ("check", "rules", "decision", "reason", "artifact", "commit_sha"):
            if key in body:
                summary[key] = body.get(key)
    return summary


def _fidelity(
    harness: str, kind: str, body: Mapping[str, Any], tool_input: Mapping[str, Any]
) -> str:
    if kind in ("session.started", "session.ended", "turn.started", "turn.ended"):
        return "L1"
    if kind == "usage.reported":
        return "L2"
    if kind in ("tool.started", "tool.completed", "tool.failed"):
        # Claude Code hooks carry tool args/results (L3); Codex/Cursor hooks
        # carry tool names/status (L2).
        return "L3" if harness == "claude-code" else "L2"
    if kind.startswith("run."):
        return "L1"
    return "L1"


def normalize_hook(
    harness: Any,
    body: Mapping[str, Any],
    *,
    now: Optional[str] = None,
) -> Optional[NormalizedEvent]:
    """Normalize one raw hook payload; ``None`` means "ignore this event"."""
    harness_name = normalize_harness_name(harness)
    if harness_name is None:
        raise TelemetryValidationError(f"unknown harness: {harness!r}")
    if not isinstance(body, Mapping):
        raise TelemetryValidationError("hook body must be a JSON object")

    name = _event_name(body)
    kind = _EVENT_ALIASES.get(name)
    if kind is None and _mapping(body.get("usage")):
        kind = "usage.reported"
    if kind is None:
        return None

    tool_input = _mapping(
        next(
            (
                body.get(key)
                for key in _TOOL_INPUT_KEYS
                if isinstance(body.get(key), Mapping)
            ),
            {},
        )
    )
    nested_tool = _mapping(body.get("tool"))
    if nested_tool and not tool_input:
        tool_input = _mapping(
            nested_tool.get("input")
            or nested_tool.get("arguments")
            or nested_tool.get("args")
        )
    tool_response = next(
        (body.get(key) for key in _TOOL_RESPONSE_KEYS if body.get(key) is not None),
        None,
    )
    tool_name = _tool_name(body)
    tool_use_id = _tool_use_id(body)
    session_id = _first_text(body, _SESSION_ID_KEYS, 500)
    cwd = _first_text(body, _CWD_KEYS, 1000)
    model = _first_text(body, _MODEL_KEYS, 200)
    turn_id = _first_text(body, ("turn_id", "turnId"), 200)
    span_id = _first_text(body, ("span_id", "spanId"), 200)
    parent_span_id = _first_text(body, ("parent_span_id", "parentSpanId"), 200)
    run_id = _first_text(body, ("run_id", "runId"), 200)
    task_id = _first_text(body, ("task_id", "taskId"), 200)
    project = _first_text(body, ("project", "project_id", "projectId"), 200)
    trace_id = _first_text(body, ("trace_id", "traceId"), 200)
    actor = _first_text(body, ("actor", "agent", "agent_name"), 200) or harness_name
    actor_kind = _first_text(body, ("actor_kind", "actorKind"), 20) or "agent"
    ts = _timestamp(body) or normalize_iso_timestamp(now) or now_iso()

    return NormalizedEvent(
        kind=kind,
        harness=harness_name,
        fidelity=_fidelity(harness_name, kind, body, tool_input),
        summary=_summary_for(kind, body, tool_input, tool_response),
        actor=actor,
        actor_kind=actor_kind,
        session_id=session_id,
        task_id=task_id,
        run_id=run_id,
        project=project,
        trace_id=trace_id,
        turn_id=turn_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        tool_use_id=tool_use_id,
        tool_name=tool_name,
        status=status_for_kind(kind) or _first_text(body, ("status",), 40),
        duration_ms=_duration_ms(body, tool_input),
        cwd=cwd,
        model=model,
        ts=ts,
    )
