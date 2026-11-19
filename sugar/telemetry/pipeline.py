"""Shared telemetry ingest pipeline: normalize -> redact -> dedupe -> spine.

Used by the HTTP hook/evidence endpoints and the Unix-socket fast path so both
transports behave identically. Hook ingestion is fail-open: callers get a 200
even when an event is ignored, deduped or storage fails.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from . import redact
from .dedupe import Deduper, dedupe_key
from .normalize import NormalizedEvent, normalize_hook
from .schema import TelemetryValidationError, build_envelope, build_evidence_event

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    stored: bool
    duplicate: bool = False
    dropped: bool = False
    event: Optional[Dict[str, Any]] = None
    reason: str = ""


class TelemetryPipeline:
    """Normalize + redact + dedupe + append telemetry to the event spine."""

    def __init__(
        self,
        spine: Any,
        broadcaster: Any = None,
        *,
        deduper: Optional[Deduper] = None,
        redaction_level: Optional[str] = None,
        max_bytes: Optional[int] = None,
    ):
        self.spine = spine
        self.broadcaster = broadcaster
        self.deduper = deduper or Deduper()
        self.redaction_level = redaction_level
        self.max_bytes = max_bytes

    # -- hook path ---------------------------------------------------------

    async def ingest_hook(
        self,
        harness: str,
        body: Mapping[str, Any],
        *,
        source: str = "telemetry:http-hook",
    ) -> IngestResult:
        """Normalize one raw hook payload; never raises for known harnesses."""
        normalized = normalize_hook(harness, body)
        if normalized is None:
            return IngestResult(stored=False, dropped=True, reason="unsupported hook")
        if self._is_duplicate(normalized):
            return IngestResult(stored=False, duplicate=True, dropped=True)
        try:
            envelope = self._envelope_for(normalized)
            event = await self.spine.append(envelope, source=source)
        except Exception:
            logger.warning("telemetry: hook ingest failed", exc_info=True)
            return IngestResult(stored=False, dropped=True, reason="spine unavailable")
        self._publish(event)
        return IngestResult(stored=True, event=event)

    def _is_duplicate(self, normalized: NormalizedEvent) -> bool:
        key = dedupe_key(
            normalized.harness,
            normalized.session_id,
            normalized.tool_use_id,
            normalized.kind,
        )
        if key is None:
            return False
        return self.deduper.is_duplicate(
            normalized.harness,
            normalized.session_id,
            normalized.tool_use_id,
            normalized.kind,
        )

    def _envelope_for(self, normalized: NormalizedEvent) -> Dict[str, Any]:
        summary = redact.redact_payload(
            normalized.summary,
            level=self.redaction_level,
            max_bytes=self.max_bytes,
        )
        return build_envelope(
            kind=normalized.kind,
            harness=normalized.harness,
            summary=summary,
            actor=normalized.actor,
            actor_kind=normalized.actor_kind,
            session_id=normalized.session_id,
            task_id=normalized.task_id,
            run_id=normalized.run_id,
            project=normalized.project,
            trace_id=normalized.trace_id,
            turn_id=normalized.turn_id,
            span_id=normalized.span_id,
            parent_span_id=normalized.parent_span_id,
            tool_use_id=normalized.tool_use_id,
            tool_name=normalized.tool_name,
            status=normalized.status,
            duration_ms=normalized.duration_ms,
            cwd=normalized.cwd,
            model=normalized.model,
            fidelity=normalized.fidelity,
            ts=normalized.ts,
            source="telemetry",
        )

    # -- evidence path -----------------------------------------------------

    async def ingest_evidence(
        self, body: Mapping[str, Any], *, source: str = "telemetry:evidence"
    ) -> IngestResult:
        """Validate + redact + append a canonical evidence event.

        Raises :class:`TelemetryValidationError` for a malformed body; storage
        failures are logged and reported as ``dropped`` (callers can fail-open).
        """
        envelope = build_evidence_event(body)
        summary = redact.redact_payload(
            envelope["payload"].get("payload"),
            level=self.redaction_level,
            max_bytes=self.max_bytes,
        )
        envelope["payload"]["payload"] = summary
        try:
            event = await self.spine.append(envelope, source=source)
        except Exception:
            logger.warning("telemetry: evidence ingest failed", exc_info=True)
            return IngestResult(stored=False, dropped=True, reason="spine unavailable")
        self._publish(event)
        return IngestResult(stored=True, event=event)

    # -- socket path -------------------------------------------------------

    async def ingest_socket_message(self, message: Any) -> IngestResult:
        """Handle one decoded socket line ``{"harness": ..., "body": {...}}``."""
        if not isinstance(message, Mapping):
            return IngestResult(stored=False, dropped=True, reason="not an object")
        harness = message.get("harness")
        body = message.get("body")
        if not isinstance(body, Mapping):
            return IngestResult(
                stored=False, dropped=True, reason="body must be an object"
            )
        try:
            return await self.ingest_hook(
                str(harness or ""), body, source="telemetry:socket"
            )
        except TelemetryValidationError:
            return IngestResult(stored=False, dropped=True, reason="invalid message")

    def _publish(self, event: Optional[Dict[str, Any]]) -> None:
        if event is not None and self.broadcaster is not None:
            try:
                self.broadcaster.publish("activity", event)
            except Exception:  # pragma: no cover - broadcaster must never break ingest
                logger.debug("telemetry: activity broadcast failed", exc_info=True)
