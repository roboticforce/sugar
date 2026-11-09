"""Tamper-evident audit exports for the Sugar event spine.

An export is two files:

* ``<name>.jsonl`` — one canonical JSON object per line, ordered by event id;
* ``<name>.manifest.json`` — count, first/last id, SHA-256 of the JSONL bytes and
  optional HMAC-SHA256 over the manifest core.

The exporter only reads the spine (SQLite ``mode=ro``) and never uploads anything.
See ``docs/audit.md`` for the provenance limits of a local, self-signed export.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote

import aiosqlite

from ..__version__ import __version__ as SUGAR_VERSION
from ..dashboard.spine import now_iso

logger = logging.getLogger(__name__)

FORMAT = "sugar-audit-v1"
SOURCE = "sugar-event-spine"
HMAC_ALGORITHM = "hmac-sha256"
PAGE_SIZE = 500
MAX_EXPORT_LIMIT = 1_000_000

MANIFEST_SUFFIX = ".manifest.json"
REQUIRED_MANIFEST_KEYS = (
    "format",
    "generated_at",
    "sugar_version",
    "source",
    "first_id",
    "last_id",
    "count",
    "sha256",
    "filters",
)
_ENV_VAR_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AuditError(ValueError):
    """Base class for audit export/verification errors."""


class AuditConfigError(AuditError):
    """Bad CLI input, filters, paths or signing-key configuration."""


class AuditFileError(AuditError):
    """An input file is missing, unreadable or structurally malformed."""


@dataclass
class ExportResult:
    jsonl_path: str
    manifest_path: str
    count: int
    first_id: Optional[int]
    last_id: Optional[int]
    sha256: str
    signed: bool
    key_id: Optional[str]
    truncated: bool
    filters: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "format": FORMAT,
            "jsonl": self.jsonl_path,
            "manifest": self.manifest_path,
            "count": self.count,
            "first_id": self.first_id,
            "last_id": self.last_id,
            "sha256": self.sha256,
            "signed": self.signed,
            "key_id": self.key_id,
            "truncated": self.truncated,
            "filters": dict(self.filters),
        }


@dataclass
class VerifyResult:
    ok: bool
    errors: List[str]
    jsonl_path: str
    manifest_path: str
    count: Optional[int] = None
    sha256: Optional[str] = None
    signed: bool = False
    key_id: Optional[str] = None
    manifest: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "jsonl": self.jsonl_path,
            "manifest": self.manifest_path,
            "count": self.count,
            "sha256": self.sha256,
            "signed": self.signed,
            "key_id": self.key_id,
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# Canonical encoding helpers
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """Canonical JSON: sorted keys, compact separators, UTF-8 (no escapes)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_json_line(value: Any) -> bytes:
    """One canonical JSONL record, including its trailing newline."""
    return (canonical_json(value) + "\n").encode("utf-8")


def manifest_path_for(jsonl_path: str | os.PathLike) -> Path:
    """Return the manifest path that belongs next to *jsonl_path*."""
    path = Path(jsonl_path).expanduser()
    if path.suffix.lower() == ".jsonl":
        return path.with_suffix(MANIFEST_SUFFIX)
    return path.with_name(path.name + MANIFEST_SUFFIX)


def key_id_for(key: bytes) -> str:
    """Public fingerprint of a signing key (first 16 hex of SHA-256(key))."""
    return hashlib.sha256(key).hexdigest()[:16]


def _manifest_core_bytes(manifest: Dict[str, Any]) -> bytes:
    """Canonical bytes of the manifest core: everything except ``signed``."""
    core = {key: value for key, value in manifest.items() if key != "signed"}
    return canonical_json(core).encode("utf-8")


# ---------------------------------------------------------------------------
# Input validation / key handling
# ---------------------------------------------------------------------------


def parse_timestamp(value: Any, *, field_name: str = "timestamp") -> str:
    """Normalize an ISO-8601/date input to ``YYYY-MM-DDTHH:MM:SSZ`` (UTC).

    Date-only inputs mean midnight UTC. Raises :class:`AuditConfigError`.
    """
    if not isinstance(value, str) or not value.strip():
        raise AuditConfigError(f"{field_name} must be a non-empty ISO-8601 timestamp")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise AuditConfigError(
            f"{field_name} is not a valid ISO-8601 timestamp: {text!r}"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_kinds(kinds: Optional[Sequence[str]]) -> List[str]:
    """Validate/clean a kind filter: unique, sorted, non-empty strings."""
    if kinds is None:
        return []
    if isinstance(kinds, str):
        raise AuditConfigError("kinds must be a list of event kinds, not a string")
    cleaned = set()
    for kind in kinds:
        if not isinstance(kind, str) or not kind.strip():
            raise AuditConfigError("kinds must contain non-empty strings")
        cleaned.add(kind.strip())
    return sorted(cleaned)


def validate_limit(limit: Optional[int]) -> Optional[int]:
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise AuditConfigError("limit must be an integer")
    if limit < 1:
        raise AuditConfigError("limit must be >= 1")
    if limit > MAX_EXPORT_LIMIT:
        raise AuditConfigError(f"limit must be <= {MAX_EXPORT_LIMIT}")
    return limit


def resolve_signing_key(key_env: Optional[str]) -> Optional[bytes]:
    """Resolve the HMAC key from the *environment variable named* by *key_env*.

    Only the variable name is ever accepted, so keys never appear in argv, the
    manifest, logs or error messages. Raises :class:`AuditConfigError`.
    """
    if key_env is None:
        return None
    name = key_env.strip()
    if not name or not _ENV_VAR_PATTERN.match(name):
        raise AuditConfigError(
            "--sign-key-env must be an environment variable name "
            "(for example SUGAR_AUDIT_HMAC_KEY), never the key itself"
        )
    value = os.environ.get(name)
    if value is None or value == "":
        raise AuditConfigError(f"signing key environment variable {name} is not set")
    return value.encode("utf-8")


def _sign_manifest(manifest: Dict[str, Any], key: bytes) -> Dict[str, Any]:
    signature = hmac.new(
        key, _manifest_core_bytes(manifest), hashlib.sha256
    ).hexdigest()
    return {
        "alg": HMAC_ALGORITHM,
        "key_id": key_id_for(key),
        "signature": signature,
    }


# ---------------------------------------------------------------------------
# Read-only spine access
# ---------------------------------------------------------------------------


async def _open_readonly(db_path: Path) -> aiosqlite.Connection:
    if not db_path.exists():
        raise AuditFileError(f"events database not found: {db_path}")
    if not db_path.is_file():
        raise AuditFileError(f"events database is not a file: {db_path}")
    uri = "file:" + quote(str(db_path.resolve()), safe="/") + "?mode=ro"
    try:
        db = await aiosqlite.connect(uri, uri=True, isolation_level=None)
    except (sqlite3.Error, OSError) as exc:
        raise AuditFileError(
            f"cannot open events database read-only: {db_path} ({exc})"
        ) from None
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("PRAGMA query_only = 1")
        await db.execute("PRAGMA busy_timeout = 5000")
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'events'"
        )
        try:
            has_events = await cursor.fetchone() is not None
        finally:
            await cursor.close()
        if not has_events:
            raise AuditFileError(
                f"{db_path} is not a Sugar event spine database "
                "(missing the 'events' table)"
            )
        # One read transaction = one consistent WAL snapshot for every page of
        # the export, even while the dashboard keeps appending events.
        await db.execute("BEGIN")
    except Exception:
        await db.close()
        raise
    return db


async def _fetch_page(
    db: aiosqlite.Connection,
    *,
    after_id: int,
    since: Optional[str],
    until: Optional[str],
    kinds: Sequence[str],
    limit: int,
) -> List[Dict[str, Any]]:
    clauses = ["id > ?"]
    params: List[Any] = [after_id]
    if since is not None:
        clauses.append("ts >= ?")
        params.append(since)
    if until is not None:
        clauses.append("ts <= ?")
        params.append(until)
    if kinds:
        placeholders = ", ".join("?" for _ in kinds)
        clauses.append(f"kind IN ({placeholders})")
        params.extend(kinds)
    sql = (
        "SELECT * FROM events WHERE "
        + " AND ".join(clauses)
        + " ORDER BY id ASC LIMIT ?"
    )
    try:
        cursor = await db.execute(sql, [*params, limit])
    except sqlite3.Error as exc:  # pragma: no cover - corrupt/locked DB
        raise AuditFileError(f"could not read events: {exc}") from None
    try:
        rows = await cursor.fetchall()
    except sqlite3.Error as exc:  # pragma: no cover - corrupt/locked DB
        raise AuditFileError(f"could not read events: {exc}") from None
    finally:
        await cursor.close()
    return [dict(row) for row in rows]


def _event_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = row.get("payload")
    if isinstance(payload, str) and payload:
        try:
            row["payload"] = json.loads(payload)
        except ValueError:
            row["payload"] = {"raw": payload}
    elif payload is None:
        row["payload"] = {}
    return row


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


async def export_events(
    db_path: str | os.PathLike,
    out_path: str | os.PathLike,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
    kinds: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    sign_key_env: Optional[str] = None,
    force: bool = False,
) -> ExportResult:
    """Stream events from the spine into a canonical JSONL file + manifest.

    Raises :class:`AuditConfigError` for invalid inputs and
    :class:`AuditFileError` for an unreadable/foreign spine database.
    """
    since_ts = parse_timestamp(since, field_name="since") if since else None
    until_ts = parse_timestamp(until, field_name="until") if until else None
    if since_ts and until_ts and since_ts > until_ts:
        raise AuditConfigError("since must be earlier than or equal to until")
    kind_filter = normalize_kinds(kinds)
    limit_value = validate_limit(limit)
    key = resolve_signing_key(sign_key_env)

    db_file = Path(db_path).expanduser()
    out = Path(out_path).expanduser()
    manifest_file = manifest_path_for(out)
    if out.exists() and not force:
        raise AuditConfigError(
            f"output already exists: {out} (pass --force to overwrite)"
        )
    if manifest_file.exists() and not force:
        raise AuditConfigError(
            f"manifest already exists: {manifest_file} (pass --force to overwrite)"
        )
    if out.resolve() == manifest_file.resolve():
        raise AuditConfigError("output and manifest paths must differ")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AuditConfigError(f"cannot create output directory: {exc}") from None

    db = await _open_readonly(db_file)
    jsonl_tmp: Optional[str] = None
    manifest_tmp: Optional[str] = None
    try:
        sha = hashlib.sha256()
        count = 0
        first_id: Optional[int] = None
        last_id: Optional[int] = None
        truncated = False
        after_id = 0

        tmp_fd, jsonl_tmp = tempfile.mkstemp(
            dir=str(out.parent), prefix=f".{out.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "wb") as handle:
                while True:
                    if limit_value is not None:
                        remaining = limit_value + 1 - count
                        if remaining <= 0:
                            break
                        page_size = min(PAGE_SIZE, remaining)
                    else:
                        page_size = PAGE_SIZE
                    rows = await _fetch_page(
                        db,
                        after_id=after_id,
                        since=since_ts,
                        until=until_ts,
                        kinds=kind_filter,
                        limit=page_size,
                    )
                    if not rows:
                        break
                    for row in rows:
                        if limit_value is not None and count >= limit_value:
                            truncated = True
                            break
                        event = _event_from_row(row)
                        line = canonical_json_line(event)
                        handle.write(line)
                        sha.update(line)
                        event_id = event.get("id")
                        if isinstance(event_id, int):
                            if first_id is None:
                                first_id = event_id
                            last_id = event_id
                        count += 1
                    if truncated:
                        break
                    after_id = int(rows[-1]["id"])
                    if len(rows) < page_size:
                        break
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(jsonl_tmp, 0o600)
        except BaseException:
            if jsonl_tmp is not None:
                try:
                    os.unlink(jsonl_tmp)
                except OSError:
                    pass
            raise

        manifest: Dict[str, Any] = {
            "format": FORMAT,
            "generated_at": now_iso(),
            "sugar_version": SUGAR_VERSION,
            "source": SOURCE,
            "first_id": first_id,
            "last_id": last_id,
            "count": count,
            "sha256": sha.hexdigest(),
            "truncated": truncated,
            "filters": {
                "since": since_ts,
                "until": until_ts,
                "kinds": kind_filter,
                "limit": limit_value,
            },
        }
        if key is not None:
            manifest["signed"] = _sign_manifest(manifest, key)

        manifest_fd, manifest_tmp = tempfile.mkstemp(
            dir=str(manifest_file.parent),
            prefix=f".{manifest_file.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(manifest_fd, "wb") as handle:
                handle.write(
                    (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
                        "utf-8"
                    )
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(manifest_tmp, 0o600)
        except BaseException:
            try:
                os.unlink(manifest_tmp)
            except OSError:
                pass
            raise

        os.replace(jsonl_tmp, out)
        jsonl_tmp = None
        os.replace(manifest_tmp, manifest_file)
        manifest_tmp = None
    finally:
        try:
            await db.rollback()
        except sqlite3.Error:  # pragma: no cover - best effort cleanup
            pass
        await db.close()
        for leftover in (jsonl_tmp, manifest_tmp):
            if leftover is not None:
                try:
                    os.unlink(leftover)
                except OSError:
                    pass

    return ExportResult(
        jsonl_path=str(out),
        manifest_path=str(manifest_file),
        count=count,
        first_id=first_id,
        last_id=last_id,
        sha256=sha.hexdigest(),
        signed=key is not None,
        key_id=manifest.get("signed", {}).get("key_id") if key is not None else None,
        truncated=truncated,
        filters=dict(manifest["filters"]),
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _load_manifest(manifest_path: Path) -> Dict[str, Any]:
    if not manifest_path.exists():
        raise AuditFileError(f"manifest not found: {manifest_path}")
    if not manifest_path.is_file():
        raise AuditFileError(f"manifest is not a file: {manifest_path}")
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuditFileError(f"cannot read manifest {manifest_path}: {exc}") from None
    except UnicodeDecodeError as exc:
        raise AuditFileError(
            f"manifest is not valid UTF-8 text: {manifest_path} ({exc})"
        ) from None
    try:
        manifest = json.loads(text)
    except ValueError as exc:
        raise AuditFileError(
            f"manifest is not valid JSON: {manifest_path} ({exc})"
        ) from None
    if not isinstance(manifest, dict):
        raise AuditFileError(f"manifest must be a JSON object: {manifest_path}")
    return manifest


def _verify_structure(manifest: Dict[str, Any], errors: List[str]) -> None:
    if manifest.get("format") != FORMAT:
        errors.append(
            f"unsupported manifest format: {manifest.get('format')!r} "
            f"(expected {FORMAT!r})"
        )
    for key in REQUIRED_MANIFEST_KEYS:
        if key not in manifest:
            errors.append(f"manifest is missing required key: {key}")
    sha = manifest.get("sha256")
    if not isinstance(sha, str) or not _SHA256_HEX_PATTERN.match(sha):
        errors.append("manifest sha256 must be 64 lowercase hex characters")
    count = manifest.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        errors.append("manifest count must be a non-negative integer")
    for key in ("first_id", "last_id"):
        value = manifest.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            errors.append(f"manifest {key} must be an integer or null")
    if not isinstance(manifest.get("filters"), dict):
        errors.append("manifest filters must be an object")


def verify_export(
    jsonl_path: str | os.PathLike,
    manifest_path: str | os.PathLike,
    key_env: Optional[str] = None,
) -> VerifyResult:
    """Verify a JSONL export against its manifest and (optionally) HMAC.

    Tampering returns ``ok=False`` with explanatory ``errors``; only unreadable
    or structurally malformed files raise :class:`AuditFileError`.
    """
    jsonl_file = Path(jsonl_path).expanduser()
    manifest_file = Path(manifest_path).expanduser()
    manifest = _load_manifest(manifest_file)
    if not jsonl_file.exists():
        raise AuditFileError(f"export file not found: {jsonl_file}")
    if not jsonl_file.is_file():
        raise AuditFileError(f"export path is not a file: {jsonl_file}")

    errors: List[str] = []
    _verify_structure(manifest, errors)

    sha = hashlib.sha256()
    count = 0
    first_id: Optional[int] = None
    last_id: Optional[int] = None
    previous_id: Optional[int] = None
    try:
        with jsonl_file.open("rb") as handle:
            for line_number, raw in enumerate(handle, start=1):
                sha.update(raw)
                if not raw.strip():
                    errors.append(f"line {line_number}: blank line in JSONL export")
                    continue
                try:
                    record = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    errors.append(f"line {line_number}: not valid UTF-8 JSON")
                    continue
                if not isinstance(record, dict):
                    errors.append(f"line {line_number}: record is not a JSON object")
                    continue
                if raw != canonical_json_line(record):
                    errors.append(
                        f"line {line_number}: record is not canonical JSON "
                        "(tampered or hand-edited)"
                    )
                event_id = record.get("id")
                if isinstance(event_id, bool) or not isinstance(event_id, int):
                    errors.append(f"line {line_number}: missing integer event id")
                    continue
                if previous_id is not None and event_id <= previous_id:
                    errors.append(
                        f"line {line_number}: event ids are not strictly increasing"
                    )
                previous_id = event_id
                count += 1
                if first_id is None:
                    first_id = event_id
                last_id = event_id
    except OSError as exc:
        raise AuditFileError(f"cannot read export file {jsonl_file}: {exc}") from None

    computed_sha = sha.hexdigest()
    if isinstance(manifest.get("sha256"), str) and manifest["sha256"] != computed_sha:
        errors.append(
            "sha256 mismatch: manifest says "
            f"{manifest['sha256']}, file recomputes to {computed_sha}"
        )
    manifest_count = manifest.get("count")
    if isinstance(manifest_count, int) and not isinstance(manifest_count, bool):
        if manifest_count != count:
            errors.append(
                f"count mismatch: manifest says {manifest_count}, "
                f"export contains {count} record(s)"
            )
    if manifest.get("first_id") != first_id:
        errors.append(
            f"first_id mismatch: manifest says {manifest.get('first_id')!r}, "
            f"export starts at {first_id!r}"
        )
    if manifest.get("last_id") != last_id:
        errors.append(
            f"last_id mismatch: manifest says {manifest.get('last_id')!r}, "
            f"export ends at {last_id!r}"
        )

    signed_block = manifest.get("signed")
    signed = isinstance(signed_block, dict)
    key: Optional[bytes] = None
    if key_env is not None:
        try:
            key = resolve_signing_key(key_env)
        except AuditConfigError as exc:
            errors.append(str(exc))

    if "signed" not in manifest:
        if key_env is not None:
            errors.append("manifest is not signed but --sign-key-env was provided")
    elif not isinstance(signed_block, dict):
        errors.append("manifest signed block must be an object")
    else:
        if signed_block.get("alg") != HMAC_ALGORITHM:
            errors.append(
                f"unsupported signature algorithm: {signed_block.get('alg')!r} "
                f"(expected {HMAC_ALGORITHM!r})"
            )
        signature = signed_block.get("signature")
        if not isinstance(signature, str) or not _SHA256_HEX_PATTERN.match(signature):
            errors.append("manifest signature must be 64 lowercase hex characters")
        if not isinstance(signed_block.get("key_id"), str):
            errors.append("manifest signed.key_id must be a string")
        if key_env is None:
            errors.append(
                "manifest is signed but no signing key was provided "
                "(pass --sign-key-env)"
            )
        elif key is not None:
            expected = hmac.new(
                key, _manifest_core_bytes(manifest), hashlib.sha256
            ).hexdigest()
            if isinstance(signature, str) and not hmac.compare_digest(
                expected, signature
            ):
                errors.append(
                    "HMAC signature mismatch: manifest was modified or the "
                    "signing key is wrong"
                )
            expected_key_id = key_id_for(key)
            if signed_block.get("key_id") != expected_key_id:
                errors.append("key_id mismatch: wrong signing key?")

    return VerifyResult(
        ok=not errors,
        errors=errors,
        jsonl_path=str(jsonl_file),
        manifest_path=str(manifest_file),
        count=count,
        sha256=computed_sha,
        signed=signed,
        key_id=signed_block.get("key_id") if signed else None,
        manifest=manifest,
    )
