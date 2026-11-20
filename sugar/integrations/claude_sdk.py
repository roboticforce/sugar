"""Claude Agent SDK hooks that report tool calls to the Sugar event spine.

Claude Code users normally install telemetry once with ``sugar connect`` (the
hooks are written to ``~/.claude/settings.json`` and post to the board). This
module is for **programmatic SDK embedding** -- an app that builds
``ClaudeAgentOptions(hooks=...)`` itself -- so Sugar still sees tool calls even
when the Claude Code user-level hooks are absent.

Usage::

    from claude_agent_sdk import ClaudeAgentOptions
    from sugar.integrations.claude_sdk import make_hooks

    options = ClaudeAgentOptions(
        hooks=make_hooks(),  # PreToolUse / PostToolUse / PostToolUseFailure
        ...
    )

``claude-agent-sdk`` is a core Sugar dependency, but this module stays importable
without it; :func:`make_hooks` raises a clear ImportError only when called and
the SDK is missing. Delivery is fail-open: hooks always return ``{}`` (allow the
tool call) and never raise.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Mapping, Optional

from .client import SugarTelemetryClient

HARNESS = "claude-code"
INSTALL_HINT = (
    "make_hooks() requires 'claude-agent-sdk'. For Claude Code CLI telemetry "
    "use `sugar connect` instead; it installs user-level HTTP hooks."
)


def _payload_dict(input_data: Any) -> Mapping[str, Any]:
    return input_data if isinstance(input_data, Mapping) else {}


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


class _HookState:
    """Per-hook-set tool start times (thread-safe, bounded)."""

    def __init__(self, max_pending: int = 512) -> None:
        self.max_pending = max(1, int(max_pending))
        self._started: Dict[str, float] = {}
        self._lock = threading.Lock()

    def start(self, tool_use_id: Optional[str]) -> None:
        if not tool_use_id:
            return
        with self._lock:
            self._started[tool_use_id] = time.monotonic()
            while len(self._started) > self.max_pending:
                oldest = next(iter(self._started))
                self._started.pop(oldest, None)

    def pop_duration(self, tool_use_id: Optional[str]) -> Optional[int]:
        if not tool_use_id:
            return None
        with self._lock:
            started = self._started.pop(tool_use_id, None)
        if started is None:
            return None
        return max(0, int(round((time.monotonic() - started) * 1000)))


def make_callbacks(
    client: Optional[SugarTelemetryClient] = None,
    *,
    client_options: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Callable[..., Any]]:
    """Return ``{event_name: async callback}`` for the Claude Agent SDK.

    The callbacks implement the SDK's
    ``(input_data, tool_use_id, context) -> dict`` hook signature and always
    return ``{}`` so tool execution is never blocked.
    """
    if client is None:
        options = dict(client_options or {})
        options.setdefault("harness", HARNESS)
        options.setdefault("actor", HARNESS)
        client = SugarTelemetryClient(**options)
    state = _HookState()

    def _common(input_data: Any, tool_use_id: Any) -> Dict[str, Any]:
        payload = _payload_dict(input_data)
        return {
            "tool_name": _first_text(payload.get("tool_name")),
            "tool_use_id": _first_text(tool_use_id, payload.get("tool_use_id")),
            "session_id": _first_text(payload.get("session_id")),
            "cwd": _first_text(payload.get("cwd")),
            "task_id": _first_text(
                payload.get("sugar_task_id"), payload.get("task_id")
            ),
        }

    def _emit(kind: str, **fields: Any) -> None:
        try:
            client.emit(kind, **fields)
        except Exception:  # noqa: BLE001 - telemetry is best-effort
            pass

    async def pre_tool_use(
        input_data: Any = None,
        tool_use_id: Any = None,
        context: Any = None,
    ) -> Dict[str, Any]:
        """Emit ``tool.started``; always allows the call."""
        try:
            payload = _payload_dict(input_data)
            fields = _common(input_data, tool_use_id)
            state.start(fields["tool_use_id"])
            _emit(
                "tool.started",
                status="started",
                fidelity="L3",
                summary={"input": payload.get("tool_input")},
                **fields,
            )
        except Exception:  # noqa: BLE001 - hooks must never break a tool call
            pass
        return {}

    async def post_tool_use(
        input_data: Any = None,
        tool_use_id: Any = None,
        context: Any = None,
    ) -> Dict[str, Any]:
        """Emit ``tool.completed``; always allows the call."""
        try:
            payload = _payload_dict(input_data)
            fields = _common(input_data, tool_use_id)
            duration = state.pop_duration(fields["tool_use_id"])
            _emit(
                "tool.completed",
                status="completed",
                duration_ms=duration,
                fidelity="L3",
                summary={
                    "input": payload.get("tool_input"),
                    "response": payload.get("tool_response"),
                },
                **fields,
            )
        except Exception:  # noqa: BLE001 - hooks must never break a tool call
            pass
        return {}

    async def post_tool_use_failure(
        input_data: Any = None,
        tool_use_id: Any = None,
        context: Any = None,
    ) -> Dict[str, Any]:
        """Emit ``tool.failed``; always allows the call."""
        try:
            payload = _payload_dict(input_data)
            fields = _common(input_data, tool_use_id)
            duration = state.pop_duration(fields["tool_use_id"])
            _emit(
                "tool.failed",
                status="failed",
                duration_ms=duration,
                fidelity="L3",
                summary={
                    "input": payload.get("tool_input"),
                    "error": _first_text(
                        payload.get("error"), payload.get("tool_response")
                    ),
                },
                **fields,
            )
        except Exception:  # noqa: BLE001 - hooks must never break a tool call
            pass
        return {}

    return {
        "PreToolUse": pre_tool_use,
        "PostToolUse": post_tool_use,
        "PostToolUseFailure": post_tool_use_failure,
    }


def make_hooks(
    client: Optional[SugarTelemetryClient] = None,
    *,
    client_options: Optional[Mapping[str, Any]] = None,
) -> Dict[str, list]:
    """Return a ``ClaudeAgentOptions(hooks=...)`` mapping.

    Raises :class:`ImportError` when the optional SDK is not installed; run
    ``sugar connect`` for the CLI path instead.
    """
    try:
        from claude_agent_sdk import HookMatcher  # type: ignore
    except Exception as exc:
        raise ImportError(INSTALL_HINT) from exc
    callbacks = make_callbacks(client, client_options=client_options)
    return {
        name: [HookMatcher(matcher=None, hooks=[callback])]
        for name, callback in callbacks.items()
    }


__all__ = ["HARNESS", "INSTALL_HINT", "make_callbacks", "make_hooks"]
