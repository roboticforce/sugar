"""OpenAI Agents SDK tracing processor for the Sugar event spine.

The processor is a duck-typed implementation of the SDK's
``TracingProcessor`` interface (``on_trace_start``, ``on_trace_end``,
``on_span_start``, ``on_span_end``, ``shutdown``, ``force_flush``). It does not
import the optional ``openai-agents`` package at module import time and is
tolerant of missing or renamed span fields.

Mapping:

- ``FunctionSpanData`` -> ``tool.started`` / ``tool.completed`` / ``tool.failed``
- ``GenerationSpanData`` -> ``usage.reported`` (model + token usage, no content)
- ``AgentSpanData`` -> ``turn.started`` / ``turn.ended``
- trace start/end -> ``run.started`` / ``run.ended``

Install::

    from sugar.integrations.openai_agents import install
    install()  # add_trace_processor (keeps the SDK's default processors)
    # or, for no OpenAI egress at all:
    install(replace=True)  # set_trace_processors([sugar_processor])

Delivery is fail-open: callbacks never raise and a stopped board never breaks
an agent run.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from .client import SugarTelemetryClient

HARNESS = "openai-agents"
INSTALL_HINT = (
    "SugarTraceProcessor requires the optional 'openai-agents' package. "
    "Install it with: pip install openai-agents"
)
USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens", "requests")
FUNCTION_SPAN = "FunctionSpanData"
GENERATION_SPAN = "GenerationSpanData"
AGENT_SPAN = "AgentSpanData"


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    """First non-None attribute from *names* (tolerant of renamed fields)."""
    if obj is None:
        return default
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _as_timestamp(value: Any) -> Any:
    """Pass datetimes/ISO strings through; convert unix seconds to UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return value


def _usage_dict(usage: Any) -> Dict[str, int]:
    """Normalize a token-usage dict/object to known integer counters."""
    if usage is None:
        return {}
    if isinstance(usage, Mapping):
        source = usage
    else:
        source = {key: getattr(usage, key, None) for key in USAGE_KEYS}
    result: Dict[str, int] = {}
    for key, value in source.items():
        if key not in USAGE_KEYS or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            result[key] = int(value)
    return result


def _metadata_value(metadata: Any, *keys: str) -> Optional[str]:
    getter = getattr(metadata, "get", None)
    if getter is None:
        return None
    for key in keys:
        try:
            value = getter(key)
        except Exception:  # pragma: no cover - exotic metadata mappings
            continue
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _span_identity(span: Any) -> Optional[str]:
    return _first_text(
        getattr(span, "span_id", None),
        getattr(span, "id", None),
    )


class SugarTraceProcessor:
    """Tracing processor that forwards SDK spans to ``POST /api/events``.

    ``client`` may be a :class:`~sugar.integrations.client.SugarTelemetryClient`
    or any object with a compatible ``emit`` method (tests use a recorder).
    """

    def __init__(
        self,
        client: Optional[SugarTelemetryClient] = None,
        *,
        client_options: Optional[Mapping[str, Any]] = None,
        max_traces: int = 128,
        max_spans: int = 1024,
    ) -> None:
        if client is None:
            options = dict(client_options or {})
            options.setdefault("harness", HARNESS)
            options.setdefault("actor", HARNESS)
            client = SugarTelemetryClient(**options)
        self.client = client
        self.max_traces = max(1, int(max_traces))
        self.max_spans = max(1, int(max_spans))
        self._traces: Dict[str, Dict[str, Any]] = {}
        self._spans: Dict[str, float] = {}
        self._lock = threading.Lock()

    # -- trace lifecycle ---------------------------------------------------

    def on_trace_start(self, trace: Any) -> None:
        """Emit ``run.started`` for the SDK trace."""
        try:
            trace_id = _first_text(
                getattr(trace, "trace_id", None), getattr(trace, "id", None)
            )
            if trace_id is None:
                return
            metadata = _attr(trace, "metadata", default={})
            fields = {
                "trace_id": trace_id,
                "run_id": trace_id,
                "session_id": _first_text(getattr(trace, "group_id", None)),
                "task_id": _metadata_value(metadata, "sugar_task_id", "task_id"),
                "project": _metadata_value(metadata, "sugar_project", "project"),
            }
            self._remember_trace(trace_id, fields)
            self._emit(
                "run.started",
                status="started",
                fidelity="L1",
                ts=_as_timestamp(_attr(trace, "started_at", "start_time")),
                summary={"name": _first_text(getattr(trace, "name", None))},
                **fields,
            )
        except Exception:  # noqa: BLE001 - tracing must never break the agent
            pass

    def on_trace_end(self, trace: Any) -> None:
        """Emit ``run.ended`` (status failed when the trace carries an error)."""
        try:
            trace_id = _first_text(
                getattr(trace, "trace_id", None), getattr(trace, "id", None)
            )
            if trace_id is None:
                return
            metadata = _attr(trace, "metadata", default={})
            fields = {
                "trace_id": trace_id,
                "run_id": trace_id,
                "session_id": _first_text(getattr(trace, "group_id", None)),
                "task_id": _metadata_value(metadata, "sugar_task_id", "task_id"),
                "project": _metadata_value(metadata, "sugar_project", "project"),
            }
            duration = self._pop_trace_duration(trace)
            error = _attr(trace, "error")
            self._emit(
                "run.ended",
                status="failed" if error else "completed",
                duration_ms=duration,
                fidelity="L1",
                ts=_as_timestamp(_attr(trace, "ended_at", "end_time")),
                summary={
                    "name": _first_text(getattr(trace, "name", None)),
                    "error": _first_text(error),
                },
                **fields,
            )
        except Exception:  # noqa: BLE001 - tracing must never break the agent
            pass

    # -- span lifecycle ----------------------------------------------------

    def on_span_start(self, span: Any) -> None:
        """Emit started events for function/agent spans; record generation timing."""
        try:
            data = getattr(span, "span_data", None)
            if data is None:
                return
            span_type = type(data).__name__
            if span_type not in (FUNCTION_SPAN, AGENT_SPAN, GENERATION_SPAN):
                return
            fields = self._span_fields(span)
            self._record_span_start(span)
            if span_type == FUNCTION_SPAN:
                self._emit(
                    "tool.started",
                    tool_name=self._tool_name(data),
                    tool_use_id=_span_identity(span),
                    status="started",
                    fidelity="L3",
                    summary={"input": _attr(data, "input", "arguments", "args")},
                    ts=_as_timestamp(_attr(span, "started_at", "start_time")),
                    **fields,
                )
            elif span_type == AGENT_SPAN:
                self._emit(
                    "turn.started",
                    status="started",
                    fidelity="L1",
                    summary={"agent": _first_text(_attr(data, "name", "agent_name"))},
                    ts=_as_timestamp(_attr(span, "started_at", "start_time")),
                    **fields,
                )
        except Exception:  # noqa: BLE001 - tracing must never break the agent
            pass

    def on_span_end(self, span: Any) -> None:
        """Emit completion/failure/usage events for supported spans."""
        try:
            data = getattr(span, "span_data", None)
            if data is None:
                return
            span_type = type(data).__name__
            if span_type not in (FUNCTION_SPAN, AGENT_SPAN, GENERATION_SPAN):
                return
            fields = self._span_fields(span)
            duration = self._pop_span_duration(span)
            error = _attr(span, "error")
            status = "failed" if error else "completed"
            ts = _as_timestamp(_attr(span, "ended_at", "end_time"))
            if span_type == FUNCTION_SPAN:
                summary: Dict[str, Any] = {
                    "input": _attr(data, "input", "arguments", "args"),
                    "output": _attr(data, "output", "result", "response"),
                }
                if error:
                    summary["error"] = _first_text(error)
                self._emit(
                    "tool.failed" if error else "tool.completed",
                    tool_name=self._tool_name(data),
                    tool_use_id=_span_identity(span),
                    status=status,
                    duration_ms=duration,
                    fidelity="L3",
                    summary=summary,
                    ts=ts,
                    **fields,
                )
            elif span_type == GENERATION_SPAN:
                model = _first_text(_attr(data, "model", "model_name"))
                self._emit(
                    "usage.reported",
                    status=status,
                    duration_ms=duration,
                    fidelity="L1",
                    model=model,
                    summary={
                        "model": model,
                        "usage": _usage_dict(_attr(data, "usage", "token_usage")),
                    },
                    ts=ts,
                    **fields,
                )
            else:  # AGENT_SPAN
                self._emit(
                    "turn.ended",
                    status=status,
                    duration_ms=duration,
                    fidelity="L1",
                    summary={
                        "agent": _first_text(_attr(data, "name", "agent_name")),
                        "error": _first_text(error),
                    },
                    ts=ts,
                    **fields,
                )
        except Exception:  # noqa: BLE001 - tracing must never break the agent
            pass

    # -- processor protocol ------------------------------------------------

    def shutdown(self) -> None:
        """No-op: the client is stateless (required by ``TracingProcessor``)."""

    def force_flush(self) -> None:
        """No-op: delivery is synchronous per event (required by the protocol)."""

    # -- helpers -----------------------------------------------------------

    def _tool_name(self, data: Any) -> Optional[str]:
        return _first_text(_attr(data, "name", "tool_name"))

    def _span_fields(self, span: Any) -> Dict[str, Any]:
        trace_id = _first_text(getattr(span, "trace_id", None))
        trace_info: Dict[str, Any] = {}
        if trace_id is not None:
            trace_info = self._traces.get(trace_id, {})
        return {
            "trace_id": trace_id,
            "run_id": trace_id,
            "span_id": _span_identity(span),
            "parent_span_id": _first_text(
                getattr(span, "parent_id", None),
                getattr(span, "parent_span_id", None),
            ),
            "session_id": trace_info.get("session_id"),
            "task_id": trace_info.get("task_id"),
            "project": trace_info.get("project"),
        }

    def _remember_trace(self, trace_id: Optional[str], fields: Dict[str, Any]) -> None:
        if trace_id is None:
            return
        record = dict(fields)
        record["_started"] = time.monotonic()
        with self._lock:
            self._traces[trace_id] = record
            while len(self._traces) > self.max_traces:
                oldest = next(iter(self._traces))
                self._traces.pop(oldest, None)

    def _record_span_start(self, span: Any) -> None:
        key = self._span_key(span)
        with self._lock:
            self._spans[key] = time.monotonic()
            while len(self._spans) > self.max_spans:
                oldest = next(iter(self._spans))
                self._spans.pop(oldest, None)

    def _pop_span_duration(self, span: Any) -> Optional[int]:
        key = self._span_key(span)
        with self._lock:
            started = self._spans.pop(key, None)
        if started is None:
            return None
        return max(0, int(round((time.monotonic() - started) * 1000)))

    def _pop_trace_duration(self, trace: Any) -> Optional[int]:
        trace_id = _first_text(
            getattr(trace, "trace_id", None), getattr(trace, "id", None)
        )
        if trace_id is None:
            return None
        with self._lock:
            info = self._traces.pop(trace_id, None)
        if not info or "_started" not in info:
            return None
        return max(0, int(round((time.monotonic() - info["_started"]) * 1000)))

    def _span_key(self, span: Any) -> str:
        identity = _span_identity(span)
        return identity if identity is not None else f"obj:{id(span)}"

    def _emit(self, kind: str, **fields: Any) -> None:
        try:
            self.client.emit(kind, **fields)
        except Exception:  # noqa: BLE001 - telemetry is best-effort
            pass


def _import_tracing() -> Any:
    try:
        import agents.tracing as tracing  # type: ignore
    except Exception as exc:  # ImportError or a broken install
        raise ImportError(INSTALL_HINT) from exc
    return tracing


def install(
    processor: Optional[SugarTraceProcessor] = None,
    *,
    client: Optional[SugarTelemetryClient] = None,
    replace: bool = False,
    **client_options: Any,
) -> SugarTraceProcessor:
    """Register a :class:`SugarTraceProcessor` with the OpenAI Agents SDK.

    ``replace=True`` uses ``set_trace_processors`` so the SDK's default
    OpenAI-backend processor is removed (no trace egress to OpenAI); the
    default adds Sugar's processor alongside the existing ones.
    """
    tracing = _import_tracing()
    if processor is None:
        processor = SugarTraceProcessor(client=client, client_options=client_options)
    if replace:
        setter = getattr(tracing, "set_trace_processors", None)
        if callable(setter):
            setter([processor])
            return processor
    adder = getattr(tracing, "add_trace_processor", None)
    if not callable(adder):  # pragma: no cover - depends on SDK version
        raise ImportError(INSTALL_HINT)
    adder(processor)
    return processor


__all__ = [
    "AGENT_SPAN",
    "FUNCTION_SPAN",
    "GENERATION_SPAN",
    "HARNESS",
    "INSTALL_HINT",
    "SugarTraceProcessor",
    "install",
]
