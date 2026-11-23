"""Evidence read/query and CLI helpers (Phase 1c).

The dashboard exposes ``GET /api/evidence`` and the ``sugar evidence`` CLI
(record/list/summary) on top of the append-only event spine. Evidence kinds are
owned by :mod:`sugar.telemetry.schema`; this package only queries/renders them.
"""

from .query import (
    DEFAULT_LIMIT,
    EVIDENCE_STATUSES,
    MAX_LIMIT,
    EvidenceQueryError,
    query_evidence,
)

__all__ = [
    "DEFAULT_LIMIT",
    "EVIDENCE_STATUSES",
    "MAX_LIMIT",
    "EvidenceQueryError",
    "query_evidence",
]
