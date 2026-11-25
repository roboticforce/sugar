"""One-click audit package download support for the local dashboard.

Builds a normal :mod:`sugar.audit.package` directory in a temporary directory,
zips it in memory, and returns the archive plus the small amount of metadata the
HTTP handler exposes in headers. No database writes; only temporary files.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from ..audit.export import AuditConfigError, parse_timestamp

DEFAULT_PACKAGE_SIGN_KEY_ENV = "SUGAR_AUDIT_SIGN_KEY"
MAX_PROJECT_LENGTH = 200
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


async def build_audit_package(*args, **kwargs):
    """Indirection that keeps package testing/monkeypatching straightforward."""
    from ..audit.package import build_audit_package as builder

    return await builder(*args, **kwargs)


@dataclass
class AuditDownload:
    archive: bytes
    filenames: List[str] = field(default_factory=list)
    signed: bool = False
    project: Optional[str] = None
    generated_at: str = ""
    sha256: str = ""

    @property
    def filename(self) -> str:
        project = _safe_project(self.project)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"sugar-audit-{project}-{stamp}.zip"


def _safe_project(project: Optional[str]) -> str:
    value = (project or "").strip()
    if not value:
        return "all"
    cleaned = _UNSAFE_FILENAME.sub("-", value).strip(".-_")
    return (cleaned or "all")[:80]


def normalize_scope(
    project: Optional[str], since: Optional[str], until: Optional[str]
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Validate/normalize the download query scope (raises AuditConfigError)."""
    project_value = None
    if project is not None:
        candidate = str(project).strip()
        if len(candidate) > MAX_PROJECT_LENGTH:
            raise AuditConfigError(
                f"project must be <= {MAX_PROJECT_LENGTH} characters"
            )
        project_value = candidate or None
    since_ts = parse_timestamp(since, field_name="since") if since else None
    until_ts = parse_timestamp(until, field_name="until") if until else None
    if since_ts and until_ts and since_ts > until_ts:
        raise AuditConfigError("since must be earlier than or equal to until")
    return project_value, since_ts, until_ts


def _zip_package(package_dir: Path) -> AuditDownload:
    buffer = io.BytesIO()
    filenames: List[str] = []
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(package_dir).as_posix()
            archive.write(path, relative)
            filenames.append(relative)
    data = buffer.getvalue()
    return AuditDownload(
        archive=data,
        filenames=filenames,
        sha256=hashlib.sha256(data).hexdigest(),
    )


async def build_audit_download(
    *,
    events_db: Any,
    board_db: Any = None,
    project: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    sign_key_env: Optional[str] = None,
) -> AuditDownload:
    """Build + zip a scoped audit package; temporary files are always cleaned."""
    project_value, since_ts, until_ts = normalize_scope(project, since, until)
    key_env = sign_key_env
    if key_env is None and os.environ.get(DEFAULT_PACKAGE_SIGN_KEY_ENV, "").strip():
        key_env = DEFAULT_PACKAGE_SIGN_KEY_ENV
    with tempfile.TemporaryDirectory(prefix="sugar-audit-download-") as tmp:
        out_dir = Path(tmp) / "package"
        result = await build_audit_package(
            out_dir,
            events_db=events_db,
            board_db=board_db,
            project=project_value,
            since=since_ts,
            until=until_ts,
            sign_key_env=key_env,
            force=True,
        )
        download = _zip_package(out_dir)
        download.signed = bool(result.signed)
        download.project = project_value
        download.generated_at = str(result.generated_at)
        return download


__all__ = [
    "AuditDownload",
    "build_audit_download",
    "normalize_scope",
]
