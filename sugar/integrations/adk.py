"""Google ADK plugin reporting tool calls to the Sugar event spine.

Usage (application code)::

    from sugar.integrations.adk import SugarADKPlugin

    app = App(name="my_app", root_agent=agent, plugins=[SugarADKPlugin()])

or with the ADK web launcher::

    adk web --extra_plugins=sugar.integrations.adk.SugarADKPlugin ...

``google-adk`` is an optional dependency. Importing this module never requires
it; instantiating :class:`SugarADKPlugin` does, and raises a clear ImportError
when it is missing. The callback implementations live on
:class:`SugarADKCallbacks` (dependency-free) so they can also be mixed into a
custom ``BasePlugin`` and are directly unit-testable.

The plugin is fail-open: callbacks always return ``None`` (they never override
ADK's tool execution or error handling) and never raise, including when the
Sugar board is unreachable.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Mapping, Optional

from .client import SugarTelemetryClient

HARNESS = "adk"
DEFAULT_NAME = "sugar_telemetry"
INSTALL_HINT = (
    "SugarADKPlugin requires the optional 'google-adk' package. "
    "Install it with: pip install google-adk"
)

try:  # pragma: no cover - depends on the host environment
    from google.adk.plugins import BasePlugin as _ADKBasePlugin  # type: ignore
except Exception:  # ImportError, or a broken/partial ADK install
    _ADKBasePlugin = None

ADK_AVAILABLE = _ADKBasePlugin is not None


def _tool_name(tool: Any) -> Optional[str]:
    for attribute in ("name", "tool_name", "__name__"):
        value = getattr(tool, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if tool is None:
        return None
    try:
        text = str(tool).strip()
    except Exception:  # pragma: no cover - exotic tool objects
        return None
    return text or None


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _session_id(tool_context: Any) -> Optional[str]:
    session = getattr(tool_context, "session", None)
    direct = getattr(tool_context, "session_id", None)
    session_id = getattr(session, "id", None) if session is not None else None
    return _first_text(session_id, direct)


def _turn_id(tool_context: Any) -> Optional[str]:
    """ADK's invocation id (one user turn -> one invocation)."""
    return _first_text(
        getattr(tool_context, "invocation_id", None),
        getattr(tool_context, "turn_id", None),
    )


def _tool_use_id(tool_context: Any, kwargs: Mapping[str, Any]) -> Optional[str]:
    return _first_text(
        getattr(tool_context, "function_call_id", None),
        getattr(tool_context, "tool_use_id", None),
        getattr(tool_context, "call_id", None),
        kwargs.get("tool_use_id"),
        kwargs.get("call_id"),
    )


def _state_value(tool_context: Any, keys: tuple) -> Optional[str]:
    state = getattr(tool_context, "state", None)
    getter = getattr(state, "get", None)
    if getter is None:
        return None
    for key in keys:
        try:
            value = getter(key)
        except Exception:  # pragma: no cover - exotic state objects
            continue
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _context_cwd(tool_context: Any) -> Optional[str]:
    for attribute in ("cwd", "working_directory"):
        value = getattr(tool_context, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _context_agent(tool_context: Any) -> Optional[str]:
    agent = getattr(tool_context, "agent", None)
    value = getattr(agent, "name", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


class SugarADKCallbacks:
    """ADK tool callbacks that emit ``tool.{started,completed,failed}``.

    Dependency-free and safe to mix into any class that ADK treats as a plugin.
    ``client`` is a :class:`~sugar.integrations.client.SugarTelemetryClient`
    (or any object with a compatible ``emit`` method, which is what tests use).
    """

    name = DEFAULT_NAME

    def __init__(
        self,
        client: Optional[SugarTelemetryClient] = None,
        *,
        name: str = DEFAULT_NAME,
        task_id: Optional[str] = None,
        project: Optional[str] = None,
        cwd: Optional[str] = None,
        client_options: Optional[Mapping[str, Any]] = None,
        max_pending: int = 512,
    ) -> None:
        self.name = name
        if client is None:
            options = dict(client_options or {})
            options.setdefault("harness", HARNESS)
            options.setdefault("actor", HARNESS)
            client = SugarTelemetryClient(**options)
        self.client = client
        self.task_id = task_id
        self.project = project
        self.cwd = cwd
        self.max_pending = max(1, int(max_pending))
        self._started: Dict[str, float] = {}
        self._lock = threading.Lock()

    # -- ADK callback surface ---------------------------------------------

    async def before_tool_callback(
        self,
        tool: Any = None,
        args: Any = None,
        tool_context: Any = None,
        tool_args: Any = None,
        **kwargs: Any,
    ) -> None:
        """Emit ``tool.started``; never raises."""
        try:
            arguments = args if args is not None else tool_args
            context = (
                tool_context if tool_context is not None else kwargs.get("tool_context")
            )
            name = _tool_name(tool if tool is not None else kwargs.get("tool"))
            ids = self._context_fields(context, kwargs)
            self._record_start(ids["tool_use_id"], name, ids["session_id"])
            self._emit(
                "tool.started",
                tool_name=name,
                status="started",
                fidelity="L3",
                summary={"input": arguments},
                **ids,
            )
        except Exception:  # noqa: BLE001 - ADK callbacks must never raise
            pass
        return None

    async def after_tool_callback(
        self,
        tool: Any = None,
        args: Any = None,
        tool_context: Any = None,
        tool_response: Any = None,
        tool_args: Any = None,
        **kwargs: Any,
    ) -> None:
        """Emit ``tool.completed``; never raises."""
        try:
            arguments = args if args is not None else tool_args
            context = (
                tool_context if tool_context is not None else kwargs.get("tool_context")
            )
            response = tool_response
            if response is None:
                response = kwargs.get("tool_response", kwargs.get("response"))
            name = _tool_name(tool if tool is not None else kwargs.get("tool"))
            ids = self._context_fields(context, kwargs)
            duration = self._pop_duration(ids["tool_use_id"], name, ids["session_id"])
            self._emit(
                "tool.completed",
                tool_name=name,
                status="completed",
                duration_ms=duration,
                fidelity="L3",
                summary={"input": arguments, "response": response},
                **ids,
            )
        except Exception:  # noqa: BLE001 - ADK callbacks must never raise
            pass
        return None

    async def on_tool_error_callback(
        self,
        tool: Any = None,
        args: Any = None,
        tool_context: Any = None,
        error: Any = None,
        tool_args: Any = None,
        **kwargs: Any,
    ) -> None:
        """Emit ``tool.failed``; never raises."""
        try:
            arguments = args if args is not None else tool_args
            context = (
                tool_context if tool_context is not None else kwargs.get("tool_context")
            )
            failure = error if error is not None else kwargs.get("error")
            name = _tool_name(tool if tool is not None else kwargs.get("tool"))
            ids = self._context_fields(context, kwargs)
            duration = self._pop_duration(ids["tool_use_id"], name, ids["session_id"])
            self._emit(
                "tool.failed",
                tool_name=name,
                status="failed",
                duration_ms=duration,
                fidelity="L3",
                summary={
                    "input": arguments,
                    "error": _first_text(failure) or type(failure).__name__,
                },
                **ids,
            )
        except Exception:  # noqa: BLE001 - ADK callbacks must never raise
            pass
        return None

    # -- helpers -----------------------------------------------------------

    def _context_fields(
        self, tool_context: Any, kwargs: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Collect the envelope fields ADK's ToolContext exposes."""
        invocation_id = _turn_id(tool_context)
        session_id = _session_id(tool_context) or invocation_id
        return {
            "tool_use_id": _tool_use_id(tool_context, kwargs),
            "session_id": session_id,
            "run_id": invocation_id,
            "turn_id": None,
            "task_id": _state_value(tool_context, ("sugar_task_id", "task_id"))
            or self.task_id,
            "project": _state_value(tool_context, ("sugar_project", "project"))
            or self.project,
            "cwd": _context_cwd(tool_context) or self.cwd or os.getcwd(),
            "actor": _context_agent(tool_context),
        }

    def _pending_key(
        self, tool_use_id: Optional[str], tool_name: Optional[str], session_id: Any
    ) -> str:
        if tool_use_id:
            return "id:" + str(tool_use_id)
        return f"name:{session_id or '-'}:{tool_name or '-'}"

    def _record_start(
        self, tool_use_id: Optional[str], tool_name: Optional[str], session_id: Any
    ) -> None:
        key = self._pending_key(tool_use_id, tool_name, session_id)
        with self._lock:
            self._started[key] = time.monotonic()
            while len(self._started) > self.max_pending:
                oldest = next(iter(self._started))
                self._started.pop(oldest, None)

    def _pop_duration(
        self, tool_use_id: Optional[str], tool_name: Optional[str], session_id: Any
    ) -> Optional[int]:
        key = self._pending_key(tool_use_id, tool_name, session_id)
        with self._lock:
            started = self._started.pop(key, None)
        if started is None:
            return None
        return max(0, int(round((time.monotonic() - started) * 1000)))

    def _emit(self, kind: str, **fields: Any) -> None:
        try:
            self.client.emit(kind, **fields)
        except Exception:  # noqa: BLE001 - telemetry is best-effort
            pass


if _ADKBasePlugin is not None:  # pragma: no cover - depends on google-adk

    class SugarADKPlugin(  # type: ignore[misc, valid-type]
        SugarADKCallbacks, _ADKBasePlugin
    ):
        """ADK ``BasePlugin`` subclass; register via ``App(plugins=[...])``."""

        def __init__(
            self,
            client: Optional[SugarTelemetryClient] = None,
            *,
            name: str = DEFAULT_NAME,
            **kwargs: Any,
        ) -> None:
            SugarADKCallbacks.__init__(self, client, name=name, **kwargs)
            for call in (
                lambda: _ADKBasePlugin.__init__(self, name),
                lambda: _ADKBasePlugin.__init__(self, name=name),
                lambda: _ADKBasePlugin.__init__(self),
            ):
                try:
                    call()
                    break
                except TypeError:
                    continue
            if not getattr(self, "name", None):
                self.name = name

else:

    class SugarADKPlugin(SugarADKCallbacks):
        """Placeholder plugin that fails loudly when ``google-adk`` is absent."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError(INSTALL_HINT)


def install(
    client: Optional[SugarTelemetryClient] = None,
    *,
    name: str = DEFAULT_NAME,
    **kwargs: Any,
) -> SugarADKPlugin:
    """Return a plugin instance to pass to ``App(plugins=[...])``.

    ADK has no process-global plugin registry, so "install" here means building
    the plugin and handing it to the app (or to
    ``adk web --extra_plugins=sugar.integrations.adk.SugarADKPlugin``).
    """
    if not ADK_AVAILABLE:
        raise ImportError(INSTALL_HINT)
    return SugarADKPlugin(client, name=name, **kwargs)


__all__ = [
    "ADK_AVAILABLE",
    "DEFAULT_NAME",
    "HARNESS",
    "INSTALL_HINT",
    "SugarADKCallbacks",
    "SugarADKPlugin",
    "install",
]
