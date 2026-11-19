"""Phase-0 telemetry ingest core.

Canonical telemetry events are stored in the existing event spine; this package
owns schema/validation (``schema``), tolerant harness normalization
(``normalize``), redaction (``redact``), TTL dedupe (``dedupe``), the shared
ingest pipeline (``pipeline``), Unix-socket transport (``socket``) and the
adapter coverage report (``coverage``).

The public HTTP surface lives in ``sugar.dashboard.app``:
``POST /api/hooks/{harness}``, ``POST /api/evidence``, ``GET /api/coverage``.
"""

from .schema import SCHEMA, TelemetryValidationError

__all__ = ["SCHEMA", "TelemetryValidationError"]
