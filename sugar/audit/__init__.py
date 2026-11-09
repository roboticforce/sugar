"""Audit evidence exports for the Sugar event spine.

The public surface is :mod:`sugar.audit.export`: stream a canonical JSONL
snapshot plus a hash/HMAC manifest, and verify either offline. See
``docs/audit.md``.
"""

from .export import (
    FORMAT,
    AuditConfigError,
    AuditError,
    AuditFileError,
    ExportResult,
    VerifyResult,
    canonical_json,
    canonical_json_line,
    export_events,
    manifest_path_for,
    verify_export,
)

__all__ = [
    "FORMAT",
    "AuditConfigError",
    "AuditError",
    "AuditFileError",
    "ExportResult",
    "VerifyResult",
    "canonical_json",
    "canonical_json_line",
    "export_events",
    "manifest_path_for",
    "verify_export",
]
