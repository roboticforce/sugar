"""Fail-open stdlib client for posting canonical telemetry to the event spine.

SDK integrations (Google ADK, OpenAI Agents SDK, Claude Agent SDK) use this
client instead of a harness hook. They build the canonical
``sugar.telemetry.v1`` envelope locally -- including client-side redaction --
and POST the raw spine event to ``POST /api/events``
(``docs/telemetry-adapters.md`` §3.4).

That endpoint stores the body as-is: there is **no server-side normalization or
redaction** on this transport. This module is therefore the schema/redaction
owner for the SDK path. The client is:

- dependency-free (``urllib`` via :mod:`sugar.activity`, stdlib redaction);
- fail-open: :meth:`SugarTelemetryClient.emit` never raises into the agent loop
  and returns an :class:`EmitResult` instead;
- bounded: a 1.5 s default timeout (clamped to 0.2-5 s) so a stopped board can
  never stall a tool call;
- quiet: the request body and token are never printed or included in errors.
  ``$SUGAR_TELEMETRY_DEBUG=1`` prints one-line delivery details to stderr.

Environment:
- ``$SUGAR_BOARD_URL``: board base URL (default ``http://127.0.0.1:7331``)
- ``$SUGAR_BOARD_TOKEN``: optional bearer token (remote/token mode)
- ``$SUGAR_TELEMETRY_REDACTION``: ``metadata`` | ``summary`` | ``full``
- ``$SUGAR_TELEMETRY_MAX_BYTES``: redacted-summary byte cap
- ``$SUGAR_TELEMETRY_DEBUG``: ``1``/``true`` to trace failures on stderr
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from sugar import activity
from sugar.telemetry import redact
from sugar.telemetry.schema import (
    DEFAULT_FIDELITY,
    SCHEMA,
    TelemetryValidationError,
    build_envelope,
    validate_envelope,
)

DEFAULT_TIMEOUT = 1.5
MIN_TIMEOUT = 0.2
MAX_TIMEOUT = 5.0
DEFAULT_HARNESS = "sugar-sdk"
DEFAULT_SOURCE = "telemetry:sdk"
ENV_DEBUG = "SUGAR_TELEMETRY_DEBUG"
_DEBUG_PREFIX = "[sugar-telemetry]"

# Injectable transport for tests/embedders: receives the envelope body and
# returns the server's stored event (or None). Raising from it is fail-open.
Transport = Callable[[Dict[str, Any]], Any]


@dataclass(frozen=True)
class EmitResult:
    """Outcome of one attempted delivery; ``ok`` False never raises."""

    ok: bool
    kind: str
    event: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


def _env_flag(name: str) -> bool:
    value = os.environ.get(name)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _clamp_timeout(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return max(MIN_TIMEOUT, min(number, MAX_TIMEOUT))


def _safe_error(exc: BaseException, limit: int = 300) -> str:
    text = str(exc).replace("\n", " ").strip() or type(exc).__name__
    return text[:limit]


class SugarTelemetryClient:
    """Build and POST canonical telemetry events; never raises on delivery.

    The client is stateless after construction (apart from configuration), so a
    single instance can be shared by concurrent tool callbacks.
    """

    def __init__(
        self,
        *,
        board_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        harness: str = DEFAULT_HARNESS,
        actor: Optional[str] = None,
        actor_kind: str = "agent",
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        run_id: Optional[str] = None,
        project: Optional[str] = None,
        trace_id: Optional[str] = None,
        redaction: Optional[str] = None,
        max_bytes: Optional[int] = None,
        debug: Optional[bool] = None,
        source: str = DEFAULT_SOURCE,
        transport: Optional[Transport] = None,
    ) -> None:
        self.board_url = board_url
        self.token = token
        self.timeout = _clamp_timeout(timeout)
        self.harness = str(harness or DEFAULT_HARNESS)
        self.actor = actor
        self.actor_kind = str(actor_kind or "agent")
        self.session_id = session_id
        self.task_id = task_id
        self.run_id = run_id
        self.project = project
        self.trace_id = trace_id
        self.redaction = redact.resolve_level(redaction)
        self.max_bytes = redact.resolve_max_bytes(max_bytes)
        self.source = str(source or DEFAULT_SOURCE)
        self.debug = _env_flag(ENV_DEBUG) if debug is None else bool(debug)
        self._transport = transport

    # -- configuration -----------------------------------------------------

    def ingest_url(self) -> str:
        """Return the full ``/api/events`` ingest URL for this client.

        Raises :class:`sugar.activity.ActivityConfigError` for an invalid
        ``$SUGAR_BOARD_URL``; :meth:`emit` converts that into a failed result.
        """
        return activity.ingest_url(self.board_url)

    # -- envelope ----------------------------------------------------------

    def build_event(
        self,
        kind: str,
        harness: Optional[str] = None,
        *,
        summary: Optional[Mapping[str, Any]] = None,
        actor: Optional[str] = None,
        actor_kind: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        run_id: Optional[str] = None,
        project: Optional[str] = None,
        trace_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        tool_use_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        status: Optional[str] = None,
        duration_ms: Optional[float] = None,
        cwd: Optional[str] = None,
        model: Optional[str] = None,
        fidelity: str = DEFAULT_FIDELITY,
        ts: Any = None,
    ) -> Dict[str, Any]:
        """Build the canonical envelope (redacted summary, spine-ready).

        Raises :class:`~sugar.telemetry.schema.TelemetryValidationError` for
        invalid input; :meth:`emit` treats that as a dropped, non-fatal event.
        """
        safe_summary = redact.redact_payload(
            summary,
            level=self.redaction,
            max_bytes=self.max_bytes,
        )
        return build_envelope(
            kind=kind,
            harness=harness or self.harness,
            summary=safe_summary,
            actor=actor or self.actor,
            actor_kind=actor_kind or self.actor_kind,
            session_id=session_id or self.session_id,
            task_id=task_id or self.task_id,
            run_id=run_id or self.run_id,
            project=project or self.project,
            trace_id=trace_id or self.trace_id,
            turn_id=turn_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            status=status,
            duration_ms=duration_ms,
            cwd=cwd,
            model=model,
            fidelity=fidelity,
            ts=ts,
            source=self.source,
        )

    # -- delivery ----------------------------------------------------------

    def emit(
        self, kind: str, harness: Optional[str] = None, **fields: Any
    ) -> EmitResult:
        """Build and POST one event; always returns, never raises."""
        try:
            event = self.build_event(kind, harness, **fields)
        except Exception as exc:  # noqa: BLE001 - fail-open by contract
            self._trace("dropped invalid event for kind %r: %s", kind, _safe_error(exc))
            return EmitResult(ok=False, kind=str(kind or ""), error=_safe_error(exc))
        return self.post(event)

    def post(self, event: Dict[str, Any]) -> EmitResult:
        """POST an already-built canonical envelope; always returns."""
        kind = ""
        try:
            if not isinstance(event, dict):
                raise TelemetryValidationError("event must be a JSON object")
            normalized = validate_envelope(event)
            kind = str(normalized.get("kind") or "")
            stored = self._deliver(normalized)
            self._trace("delivered kind=%s id=%s", kind, _event_id(stored))
            return EmitResult(
                ok=True,
                kind=kind,
                event=stored if isinstance(stored, dict) else None,
            )
        except Exception as exc:  # noqa: BLE001 - fail-open by contract
            self._trace("delivery failed kind=%s: %s", kind, _safe_error(exc))
            return EmitResult(ok=False, kind=kind, error=_safe_error(exc))

    def close(self) -> None:
        """No-op; there is no persistent connection to release."""

    # -- internals ---------------------------------------------------------

    def _deliver(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self._transport is not None:
            result = self._transport(event)
            return result if isinstance(result, dict) else None
        return activity.post_event(
            event,
            server=self.board_url,
            token=self.token,
            timeout=self.timeout,
        )

    def _trace(self, template: str, *args: Any) -> None:
        if not self.debug:
            return
        try:
            message = template % args
            print(f"{_DEBUG_PREFIX} {message}", file=sys.stderr)
        except Exception:  # pragma: no cover - debug must never break anything
            pass


def _event_id(event: Any) -> str:
    if isinstance(event, dict):
        value = event.get("id")
        if value is not None:
            return str(value)
    return "-"


__all__ = [
    "DEFAULT_HARNESS",
    "DEFAULT_SOURCE",
    "DEFAULT_TIMEOUT",
    "ENV_DEBUG",
    "SCHEMA",
    "EmitResult",
    "SugarTelemetryClient",
]
