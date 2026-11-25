"""Scoped, self-verifying audit packages (Phase 1a audit foundation).

A package is one directory containing the tamper-evident event export plus the
board task slice, verification evidence, trace summaries/span details, observed
AIBOM inventory, adapter coverage snapshot, a claims/limitations matrix, a human
README and a package-level SHA-256/HMAC manifest.

Everything is read-only against the source SQLite files. Package files are built
in a sibling temporary directory and the whole directory is swapped into place,
so a failed build never leaves a partial package and the previous package (with
``force=True``) is left intact.

No schema changes, no file contents, no commercial or planning content. Event
payloads are exported as stored by the spine (already redacted at ingestion for
telemetry transports); callers should treat the package as sensitive metadata.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

import aiosqlite

from ..__version__ import __version__ as SUGAR_VERSION
from ..dashboard import aibom as aibom_module
from ..dashboard import traces as traces_module
from ..dashboard.serializer import serialize_task
from ..evidence.query import build_summary as build_evidence_summary
from ..telemetry import redact
from ..telemetry.coverage import coverage_report
from ..telemetry.schema import EVIDENCE_KINDS
from .export import (
    AuditConfigError,
    AuditError,
    AuditFileError,
    canonical_json,
    export_events,
    now_iso,
    parse_timestamp,
    resolve_signing_key,
    verify_export,
)

logger = logging.getLogger(__name__)

PACKAGE_FORMAT = "sugar-audit-package-v1"
CLAIMS_FORMAT = "sugar-audit-claims-v1"
PACKAGE_MANIFEST = "package.manifest.json"
EVENTS_FILE = "events.jsonl"
EVENTS_MANIFEST = "events.manifest.json"
TASKS_FILE = "tasks.json"
EVIDENCE_FILE = "evidence.json"
TRACES_FILE = "traces.json"
AIBOM_FILE = "aibom.json"
COVERAGE_FILE = "coverage.json"
CLAIMS_FILE = "claims.json"
README_FILE = "README.md"
REQUIRED_FILES: Tuple[str, ...] = (
    EVENTS_FILE,
    EVENTS_MANIFEST,
    TASKS_FILE,
    EVIDENCE_FILE,
    TRACES_FILE,
    AIBOM_FILE,
    COVERAGE_FILE,
    CLAIMS_FILE,
    README_FILE,
)
_HASHED_FILES: Tuple[str, ...] = REQUIRED_FILES
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

DEFAULT_MAX_EVENTS = 50_000
MAX_TASKS = 5_000
MAX_EVIDENCE_EVENTS = 5_000
MAX_TRACES = 100
MAX_COVERAGE_EVENTS = 5_000


@dataclass
class PackageResult:
    out_dir: str
    manifest_path: str
    generated_at: str
    events_count: int
    tasks_count: int
    evidence_count: int
    traces_count: int
    aibom_components: int
    claims_summary: Dict[str, int]
    signed: bool
    key_id: Optional[str]
    files: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    limitations: List[str] = field(default_factory=list)

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "format": PACKAGE_FORMAT,
            "out_dir": self.out_dir,
            "manifest": self.manifest_path,
            "generated_at": self.generated_at,
            "events": self.events_count,
            "tasks": self.tasks_count,
            "evidence": self.evidence_count,
            "traces": self.traces_count,
            "aibom_components": self.aibom_components,
            "claims": dict(self.claims_summary),
            "signed": self.signed,
            "key_id": self.key_id,
            "files": dict(self.files),
            "limitations": list(self.limitations),
        }


@dataclass
class PackageVerifyResult:
    ok: bool
    errors: List[str]
    package_path: str
    files_checked: int = 0
    signed: bool = False
    key_id: Optional[str] = None
    manifest: Optional[Dict[str, Any]] = None

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "package": self.package_path,
            "files_checked": self.files_checked,
            "signed": self.signed,
            "key_id": self.key_id,
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# Small file / manifest helpers
# ---------------------------------------------------------------------------


def _max_events() -> int:
    raw = os.environ.get("SUGAR_AUDIT_PACKAGE_MAX_EVENTS", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return min(value, 1_000_000)
    return DEFAULT_MAX_EVENTS


def _write_atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _write_json(path: Path, value: Any) -> None:
    _write_atomic(
        path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )


def _hash_file(path: Path) -> Tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _package_manifest_core(manifest: Mapping[str, Any]) -> bytes:
    core = {key: value for key, value in manifest.items() if key != "signed"}
    return canonical_json(core).encode("utf-8")


def _sign_package_manifest(manifest: Dict[str, Any], key: bytes) -> Dict[str, Any]:
    signature = hmac.new(
        key, _package_manifest_core(manifest), hashlib.sha256
    ).hexdigest()
    key_id = hashlib.sha256(key).hexdigest()[:16]
    return {"alg": "hmac-sha256", "key_id": key_id, "signature": signature}


def _install_directory(tmp: Path, out: Path, force: bool) -> None:
    """Atomically move *tmp* into *out*, preserving *out* if the swap fails."""
    if not out.exists():
        os.replace(tmp, out)
        return
    if not force:
        raise AuditConfigError(
            f"package directory already exists: {out} (pass --force to overwrite)"
        )
    if not out.is_dir():
        raise AuditConfigError(f"output path exists and is not a directory: {out}")
    backup = out.with_name(f".{out.name}.backup-{os.getpid()}")
    suffix = 1
    while backup.exists():
        backup = out.with_name(f".{out.name}.backup-{os.getpid()}-{suffix}")
        suffix += 1
    os.replace(out, backup)
    try:
        os.replace(tmp, out)
    except BaseException:
        try:
            os.replace(backup, out)
        except OSError:  # pragma: no cover - best effort restoration
            logger.error("audit package: could not restore previous package %s", out)
        raise
    shutil.rmtree(backup, ignore_errors=True)


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------


def _compare_ts(value: Any, since: Optional[str], until: Optional[str]) -> bool:
    if not isinstance(value, str) or not value:
        return True
    text = value.strip()
    if since and text < since:
        return False
    if until and text > until:
        return False
    return True


def _task_project_context(
    task: Mapping[str, Any],
) -> Tuple[Optional[str], Optional[str]]:
    context = task.get("context")
    if not isinstance(context, Mapping):
        context = {}
    project = context.get("project")
    cwd = context.get("cwd")
    return (
        str(project).strip() if isinstance(project, str) and project.strip() else None,
        str(cwd).strip() if isinstance(cwd, str) and cwd.strip() else None,
    )


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return None
    if isinstance(value, str):
        return value[:2_000]
    if isinstance(value, Mapping):
        return {
            str(key)[:120]: _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:20]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_value(item, depth=depth + 1) for item in value[:20]]
    return value


def _bounded_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """Keep auditor-relevant task metadata bounded and redacted; no file contents."""
    bounded = dict(task)
    for field in ("title", "description", "error_message"):
        if bounded.get(field):
            bounded[field] = redact.redact_text(str(bounded[field]), level="full")[
                :2_000
            ]
    if "result" in bounded:
        bounded["result"] = redact.redact_payload(
            bounded.get("result") or {}, level="full"
        )
    if "context" in bounded:
        bounded["context"] = redact.redact_payload(
            bounded.get("context") or {}, level="full"
        )
    # Reasoning summaries are not audit evidence and can be large/sensitive.
    bounded.pop("thinking_summary", None)
    return bounded


async def _read_tasks(
    board_db: Any,
    *,
    project: Optional[str],
    since: Optional[str],
    until: Optional[str],
) -> Tuple[List[Dict[str, Any]], bool, List[str]]:
    limitations: List[str] = []
    if not board_db:
        return (
            [],
            False,
            ["Board task database was not available; tasks.json is empty."],
        )
    path = Path(str(board_db)).expanduser()
    if not path.exists() or not path.is_file():
        return [], False, ["Board task database was not found; tasks.json is empty."]
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    try:
        async with aiosqlite.connect(
            uri, uri=True, isolation_level=None, timeout=5
        ) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA query_only = 1")
            await db.execute("PRAGMA busy_timeout = 5000")
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name = 'work_items'"
            )
            try:
                found = await cursor.fetchone() is not None
            finally:
                await cursor.close()
            if not found:
                return (
                    [],
                    False,
                    ["Board database has no work_items table; tasks.json is empty."],
                )
            cursor = await db.execute(
                "SELECT * FROM work_items ORDER BY created_at ASC LIMIT ?",
                (MAX_TASKS + 1,),
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
    except (sqlite3.Error, OSError) as exc:
        return (
            [],
            False,
            [f"Board task database was unreadable; tasks.json is empty ({exc})."],
        )

    truncated = len(rows) > MAX_TASKS
    tasks: List[Dict[str, Any]] = []
    for row in rows[:MAX_TASKS]:
        try:
            task = _bounded_task(serialize_task(dict(row)))
        except Exception:  # pragma: no cover - legacy/corrupt row
            continue
        task_context_project, _cwd = _task_project_context(task)
        if project and (task_context_project or "") != project:
            continue
        if not _compare_ts(task.get("created_at"), since, until):
            continue
        tasks.append(task)
    if truncated:
        limitations.append(f"tasks.json truncated at {MAX_TASKS} board tasks.")
    return tasks, truncated, limitations


def _read_exported_events(events_path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with events_path.open("rb") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            try:
                record = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(record, dict):
                events.append(record)
    return events


def _group_events_by_session(
    events: Sequence[Mapping[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        session_id = event.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            continue
        grouped.setdefault(session_id.strip(), []).append(dict(event))
    return grouped


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _evidence_payload(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    evidence = [
        dict(event)
        for event in events
        if str(event.get("kind") or "") in EVIDENCE_KINDS
    ]
    truncated = len(evidence) > MAX_EVIDENCE_EVENTS
    evidence = evidence[:MAX_EVIDENCE_EVENTS]
    limitations: List[str] = []
    if truncated:
        limitations.append(
            f"evidence.json truncated at {MAX_EVIDENCE_EVENTS} evidence events."
        )
    return {
        "summary": build_evidence_summary(evidence),
        "events": evidence,
        "count": len(evidence),
        "limitations": limitations,
    }


def _traces_payload(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    grouped = _group_events_by_session(events)
    summaries = traces_module.build_traces(events, limit=MAX_TRACES)
    traces: List[Dict[str, Any]] = []
    for summary in summaries:
        trace_id = str(summary.get("trace_id") or "")
        _harness, session_id = traces_module.split_trace_id(trace_id)
        session_events = grouped.get(session_id or "", [])
        detail = traces_module.build_trace(session_events) if session_events else None
        if detail is None:
            continue
        trace_summary = detail.get("trace", summary)
        traces.append(
            {
                **trace_summary,
                "summary": trace_summary,
                "spans": detail.get("spans", []),
            }
        )
    limitations: List[str] = []
    if len(grouped) > MAX_TRACES:
        limitations.append(
            f"traces.json includes at most {MAX_TRACES} traces; "
            "remaining sessions are omitted."
        )
    return {"traces": traces, "count": len(traces), "limitations": limitations}


def _aibom_payload(
    events: Sequence[Mapping[str, Any]],
    *,
    project: Optional[str],
    since: Optional[str],
    until: Optional[str],
) -> Dict[str, Any]:
    report = aibom_module.aggregate_aibom(events, project=project, since=since)
    scope = report.get("scope")
    if isinstance(scope, dict):
        scope["until"] = until
    return report


async def _coverage_payload(events_db: Any) -> Tuple[Dict[str, Any], List[str]]:
    try:
        report = await coverage_report(events_db)
        return report, [
            "Coverage is a 24h adapter snapshot; it is not narrowed by the package "
            "project/since/until scope and may extend beyond it."
        ]
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("audit package: coverage unavailable: %s", exc)
        return {
            "generated_at": now_iso(),
            "summary": {"L0": [], "L1": [], "L2": [], "L3": []},
            "harnesses": [],
        }, ["Adapter coverage snapshot was unavailable."]


def _claims_payload(
    *,
    export_result: Any,
    tasks: Sequence[Mapping[str, Any]],
    tasks_available: bool,
    tasks_truncated: bool,
    evidence_count: int,
    traces_count: int,
    aibom_components: int,
    coverage_harnesses: int,
    generated_at: str,
    scope: Mapping[str, Any],
    limitations: Sequence[str],
) -> Dict[str, Any]:
    event_limitations = []
    if getattr(export_result, "truncated", False):
        event_limitations.append(
            "Event export was truncated by the package safety limit."
        )
    task_limitations = []
    if not tasks_available:
        task_limitations.append("Board task database was unavailable.")
    if tasks_truncated:
        task_limitations.append("Board task list was truncated by the package limit.")
    claims = [
        {
            "claim": "Scoped event log was exported with a hash manifest",
            "status": (
                "partial" if getattr(export_result, "truncated", False) else "proven"
            ),
            "evidence": [EVENTS_FILE, EVENTS_MANIFEST],
            "limitations": event_limitations,
        },
        {
            "claim": "Board tasks for the requested scope are included",
            "status": (
                "partial" if (not tasks_available or tasks_truncated) else "proven"
            ),
            "evidence": [TASKS_FILE],
            "limitations": task_limitations,
        },
        {
            "claim": "Verification evidence is present for the scope",
            "status": "proven" if evidence_count > 0 else "not_proven",
            "evidence": [EVIDENCE_FILE],
            "limitations": (
                []
                if evidence_count > 0
                else ["No verification events matched the scope."]
            ),
        },
        {
            "claim": "Trace structure for the scope is reconstructable",
            "status": "proven" if traces_count > 0 else "not_proven",
            "evidence": [TRACES_FILE],
            "limitations": (
                [] if traces_count > 0 else ["No scoped traces had event data."]
            ),
        },
        {
            "claim": "Observed AI/model/tool inventory is included",
            "status": "proven" if aibom_components > 0 else "not_proven",
            "evidence": [AIBOM_FILE],
            "limitations": (
                [] if aibom_components > 0 else ["No AIBOM components were observed."]
            ),
        },
        {
            "claim": "Adapter coverage snapshot is included",
            "status": "proven" if coverage_harnesses > 0 else "partial",
            "evidence": [COVERAGE_FILE],
            "limitations": (
                [] if coverage_harnesses > 0 else ["Coverage snapshot is empty."]
            ),
        },
        {
            "claim": "Package hashes and optional HMAC are self-verifying",
            "status": "proven",
            "evidence": [PACKAGE_MANIFEST],
            "limitations": (
                []
                if getattr(export_result, "signed", False)
                else [
                    "Package is hash-verified only; no HMAC signing key was supplied."
                ]
            ),
        },
    ]
    return {
        "format": CLAIMS_FORMAT,
        "generated_at": generated_at,
        "scope": dict(scope),
        "claims": claims,
        "limitations": list(limitations),
    }


def _readme_text(
    *,
    generated_at: str,
    scope: Mapping[str, Any],
    event_count: int,
    task_count: int,
    evidence_count: int,
    trace_count: int,
    aibom_count: int,
    signed: bool,
    limitations: Sequence[str],
) -> str:
    scope_lines = [
        f"- Project: {scope.get('project') or 'all projects'}",
        f"- Since: {scope.get('since') or 'unbounded'}",
        f"- Until: {scope.get('until') or 'unbounded'}",
    ]
    limitation_lines = (
        "\n".join(f"- {item}" for item in limitations) or "- None recorded."
    )
    return f"""# Sugar audit package

Generated at: {generated_at}
Package format: {PACKAGE_FORMAT}

## Scope

{chr(10).join(scope_lines)}
- Events: {event_count}
- Tasks: {task_count}
- Evidence events: {evidence_count}
- Traces: {trace_count}
- Observed AIBOM components: {aibom_count}
- Signed: {"yes (HMAC-SHA256)" if signed else "no (SHA-256 only)"}

## Contents

- `events.jsonl` / `events.manifest.json` — canonical, ordered event-spine export
  with SHA-256 and optional HMAC.
- `tasks.json` — scoped board task metadata (no task file contents).
- `evidence.json` — scoped verification evidence events and summary.
- `traces.json` — bounded trace summaries plus span details.
- `aibom.json` — observed model/harness/tool/artifact inventory.
- `coverage.json` — adapter coverage snapshot.
- `claims.json` — claim matrix with evidence references and limitations.
- `package.manifest.json` — file sizes and hashes, scope, version and optional
  package HMAC.

## Method

The package is assembled from read-only SQLite reads (event spine and board DB)
and existing Sugar projections. No LLM generation is used. Every listed file is
hashed; the package manifest can be signed with HMAC-SHA256 via an environment
variable named on the command line (the key itself is never written).

Verify offline with:

```bash
sugar audit verify-package <this-directory>
```

## Limitations

{limitation_lines}

This package contains task/event metadata and should be handled as sensitive.
It contains no file contents or credentials. Event payloads are projected through
Sugar's full redaction profile (secrets and oversized values are removed/
truncated) before hashing. Absolute paths may appear inside task/event metadata
(the package manifest itself uses filenames only).
"""


# ---------------------------------------------------------------------------
# Build entry point
# ---------------------------------------------------------------------------


async def build_audit_package(
    out_dir: str | os.PathLike,
    *,
    events_db: str | os.PathLike,
    board_db: Optional[str | os.PathLike] = None,
    project: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    sign_key_env: Optional[str] = None,
    force: bool = False,
) -> PackageResult:
    """Build an atomic, scoped, self-verifying audit package."""
    since_ts = parse_timestamp(since, field_name="since") if since else None
    until_ts = parse_timestamp(until, field_name="until") if until else None
    if since_ts and until_ts and since_ts > until_ts:
        raise AuditConfigError("since must be earlier than or equal to until")
    project_filter = str(project).strip()[:200] if project else None
    project_filter = project_filter or None
    key = resolve_signing_key(sign_key_env)
    generated_at = now_iso()
    out = Path(out_dir).expanduser()
    if out.exists() and not force:
        raise AuditConfigError(
            f"package directory already exists: {out} (pass --force to overwrite)"
        )
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AuditConfigError(
            f"cannot create output parent directory: {exc}"
        ) from None
    tmp = Path(tempfile.mkdtemp(dir=str(out.parent), prefix=f".{out.name}.tmp-"))
    os.chmod(tmp, 0o700)
    try:
        # 1. Event export (atomic inside the temp directory).
        export_limit = _max_events()
        export_result = await export_events(
            events_db,
            tmp / EVENTS_FILE,
            since=since_ts,
            until=until_ts,
            project=project_filter,
            limit=export_limit,
            sign_key_env=sign_key_env,
            redact_payloads=True,
            force=True,
        )
        events = _read_exported_events(tmp / EVENTS_FILE)
        limitations: List[str] = []
        if export_result.truncated:
            limitations.append(
                f"Event export was truncated at the package limit ({export_limit})."
            )

        # 2. Scoped board tasks.
        tasks, tasks_truncated, task_limitations = await _read_tasks(
            board_db, project=project_filter, since=since_ts, until=until_ts
        )
        limitations.extend(task_limitations)

        # 3. Evidence / traces / inventory from the same scoped event slice.
        evidence = _evidence_payload(events)
        limitations.extend(evidence.get("limitations") or [])
        traces = _traces_payload(events)
        limitations.extend(traces.get("limitations") or [])
        aibom_report = _aibom_payload(
            events,
            project=project_filter,
            since=since_ts,
            until=until_ts,
        )
        coverage, coverage_limitations = await _coverage_payload(events_db)
        limitations.extend(coverage_limitations)

        tasks_payload = {
            "tasks": tasks,
            "count": len(tasks),
            "scope": {
                "project": project_filter,
                "since": since_ts,
                "until": until_ts,
            },
            "limitations": task_limitations,
        }
        scope = {
            "project": project_filter,
            "since": since_ts,
            "until": until_ts,
        }
        claims = _claims_payload(
            export_result=export_result,
            tasks=tasks,
            tasks_available=not task_limitations
            or not any(
                "unavailable" in item.lower() or "not found" in item.lower()
                for item in task_limitations
            ),
            tasks_truncated=tasks_truncated,
            evidence_count=evidence.get("count") or 0,
            traces_count=traces.get("count") or 0,
            aibom_components=len(aibom_report.get("components") or []),
            coverage_harnesses=len(coverage.get("harnesses") or []),
            generated_at=generated_at,
            scope=scope,
            limitations=limitations,
        )
        readme = _readme_text(
            generated_at=generated_at,
            scope=scope,
            event_count=export_result.count,
            task_count=len(tasks),
            evidence_count=evidence.get("count") or 0,
            trace_count=traces.get("count") or 0,
            aibom_count=len(aibom_report.get("components") or []),
            signed=export_result.signed,
            limitations=limitations,
        )

        # 4. Write all files into the temp directory (events already written).
        _write_json(tmp / TASKS_FILE, tasks_payload)
        _write_json(tmp / EVIDENCE_FILE, evidence)
        _write_json(tmp / TRACES_FILE, traces)
        _write_json(tmp / AIBOM_FILE, aibom_report)
        _write_json(tmp / COVERAGE_FILE, coverage)
        _write_json(tmp / CLAIMS_FILE, claims)
        _write_atomic(tmp / README_FILE, readme.encode("utf-8"))

        files: Dict[str, Dict[str, Any]] = {}
        for name in _HASHED_FILES:
            file_path = tmp / name
            if not file_path.exists():
                raise AuditFileError(
                    f"package build did not produce required file: {name}"
                )
            size, digest = _hash_file(file_path)
            files[name] = {"size": size, "sha256": digest}

        manifest: Dict[str, Any] = {
            "format": PACKAGE_FORMAT,
            "generated_at": generated_at,
            "sugar_version": SUGAR_VERSION,
            "scope": scope,
            "counts": {
                "events": export_result.count,
                "tasks": len(tasks),
                "evidence": evidence.get("count") or 0,
                "traces": traces.get("count") or 0,
                "aibom_components": len(aibom_report.get("components") or []),
            },
            "files": files,
            "limitations": limitations,
        }
        if key is not None:
            manifest["signed"] = _sign_package_manifest(manifest, key)
        _write_json(tmp / PACKAGE_MANIFEST, manifest)

        _install_directory(tmp, out, force)
        tmp = None  # type: ignore[assignment]
        claims_summary: Dict[str, int] = {}
        for claim in claims.get("claims") or []:
            status = str(claim.get("status") or "")
            claims_summary[status] = claims_summary.get(status, 0) + 1
        return PackageResult(
            out_dir=str(out),
            manifest_path=str(out / PACKAGE_MANIFEST),
            generated_at=generated_at,
            events_count=export_result.count,
            tasks_count=len(tasks),
            evidence_count=evidence.get("count") or 0,
            traces_count=traces.get("count") or 0,
            aibom_components=len(aibom_report.get("components") or []),
            claims_summary=claims_summary,
            signed=export_result.signed,
            key_id=export_result.key_id,
            files=files,
            limitations=limitations,
        )
    finally:
        if tmp is not None and tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _load_package_manifest(package_dir: Path) -> Dict[str, Any]:
    path = package_dir / PACKAGE_MANIFEST
    if not path.exists() or not path.is_file():
        raise AuditFileError(f"package manifest not found: {path}")
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise AuditFileError(
            f"package manifest is not valid JSON: {path} ({exc})"
        ) from None
    if not isinstance(decoded, dict):
        raise AuditFileError(f"package manifest must be a JSON object: {path}")
    return decoded


def verify_audit_package(
    package_dir: str | os.PathLike, sign_key_env: Optional[str] = None
) -> PackageVerifyResult:
    """Verify package file hashes, required files, events manifest and optional HMAC."""
    path = Path(package_dir).expanduser()
    errors: List[str] = []
    if not path.exists():
        return PackageVerifyResult(
            ok=False,
            errors=[f"package directory not found: {path}"],
            package_path=str(path),
        )
    if not path.is_dir():
        return PackageVerifyResult(
            ok=False,
            errors=[f"package path is not a directory: {path}"],
            package_path=str(path),
        )
    try:
        manifest = _load_package_manifest(path)
    except AuditFileError as exc:
        return PackageVerifyResult(ok=False, errors=[str(exc)], package_path=str(path))

    if manifest.get("format") != PACKAGE_FORMAT:
        errors.append(
            f"unsupported package format: {manifest.get('format')!r} "
            f"(expected {PACKAGE_FORMAT!r})"
        )
    files = manifest.get("files")
    if not isinstance(files, dict):
        errors.append("package manifest files must be an object")
        files = {}
    for required in REQUIRED_FILES:
        if required not in files:
            errors.append(f"package manifest is missing file hash: {required}")
        if not (path / required).exists():
            errors.append(f"required package file is missing: {required}")
    checked = 0
    for name, metadata in files.items():
        if not isinstance(name, str) or Path(name).name != name:
            errors.append(f"manifest file name must be a bare filename: {name!r}")
            continue
        file_path = path / name
        if not file_path.exists():
            errors.append(f"listed package file is missing: {name}")
            continue
        if not isinstance(metadata, dict):
            errors.append(f"manifest metadata for {name} must be an object")
            continue
        try:
            size, digest = _hash_file(file_path)
        except OSError as exc:
            errors.append(f"cannot read package file {name}: {exc}")
            continue
        if metadata.get("size") != size:
            errors.append(
                f"size mismatch for {name}: manifest says {metadata.get('size')!r}, "
                f"file is {size}"
            )
        if metadata.get("sha256") != digest:
            errors.append(f"sha256 mismatch for {name}")
        checked += 1

    # Event export manifest / JSONL integrity (hash + canonical/order checks).
    events_path = path / EVENTS_FILE
    events_manifest_path = path / EVENTS_MANIFEST
    if events_path.exists() and events_manifest_path.exists():
        try:
            export_verify = verify_export(
                events_path, events_manifest_path, key_env=sign_key_env
            )
            if not export_verify.ok:
                errors.extend(f"events export: {item}" for item in export_verify.errors)
        except AuditError as exc:
            errors.append(f"events export verification failed: {exc}")

    # Parse JSON payload files (hash/size alone does not catch malformed JSON
    # produced by a broken build).
    for name in (
        TASKS_FILE,
        EVIDENCE_FILE,
        TRACES_FILE,
        AIBOM_FILE,
        COVERAGE_FILE,
        CLAIMS_FILE,
    ):
        target = path / name
        if not target.exists():
            continue
        try:
            decoded = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            errors.append(f"package file is not valid JSON: {name}")
            continue
        if not isinstance(decoded, dict):
            errors.append(f"package file must contain a JSON object: {name}")

    signed_block = manifest.get("signed")
    signed = isinstance(signed_block, dict)
    key_id: Optional[str] = None
    try:
        key = resolve_signing_key(sign_key_env)
    except AuditConfigError as exc:
        errors.append(str(exc))
        key = None
    if "signed" not in manifest:
        if sign_key_env is not None:
            errors.append(
                "package manifest is not signed but --sign-key-env was provided"
            )
    elif not isinstance(signed_block, dict):
        errors.append("package manifest signed block must be an object")
    else:
        key_id = signed_block.get("key_id")
        if signed_block.get("alg") != "hmac-sha256":
            errors.append(
                f"unsupported package signature algorithm: {signed_block.get('alg')!r}"
            )
        if sign_key_env is None:
            errors.append(
                "package manifest is signed but no signing key was provided "
                "(pass --sign-key-env)"
            )
        elif key is not None:
            expected = hmac.new(
                key, _package_manifest_core(manifest), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(
                expected, str(signed_block.get("signature") or "")
            ):
                errors.append(
                    "package HMAC signature mismatch: manifest was modified or "
                    "the signing key is wrong"
                )
            expected_key_id = hashlib.sha256(key).hexdigest()[:16]
            if key_id != expected_key_id:
                errors.append("package key_id mismatch: wrong signing key?")

    return PackageVerifyResult(
        ok=not errors,
        errors=errors,
        package_path=str(path),
        files_checked=checked,
        signed=signed,
        key_id=key_id if isinstance(key_id, str) else None,
        manifest=manifest,
    )


__all__ = [
    "PACKAGE_FORMAT",
    "PackageResult",
    "PackageVerifyResult",
    "build_audit_package",
    "verify_audit_package",
]
