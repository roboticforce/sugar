"""``sugar evidence`` CLI and its dependency-free board client.

Commands (frozen Phase 1c contract):

* ``sugar evidence record`` — POST canonical evidence to ``/api/evidence``.
* ``sugar evidence list`` — table/JSON of ``GET /api/evidence``.
* ``sugar evidence summary`` — totals/by-status/by-kind.

Board configuration: ``--url`` or ``$SUGAR_BOARD_URL`` (default
``http://127.0.0.1:7331``); bearer ``$SUGAR_BOARD_TOKEN``. Uses only the stdlib
(``urllib``) and never prints the token or request body in errors.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import click

from ..activity import (
    ActivityConfigError,
    ActivityError,
    resolve_board_token,
    resolve_board_url,
)
from ..telemetry.schema import EVIDENCE_KINDS, normalize_iso_timestamp
from .query import EVIDENCE_STATUSES, event_status, event_summary_text

EVIDENCE_PATH = "/api/evidence"
DEFAULT_TIMEOUT = 5.0
MAX_RESPONSE_BYTES = 2_000_000


# ---------------------------------------------------------------------------
# stdlib board client
# ---------------------------------------------------------------------------


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """Extract a short, safe server error detail (never the token/request body)."""
    try:
        raw = exc.read(65536)
    except Exception:  # pragma: no cover - best effort
        raw = b""
    detail = ""
    if raw:
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            data = None
        if isinstance(data, Mapping):
            detail = str(data.get("detail") or data.get("error") or "")
    return detail[:300]


def _query_string(query: Optional[Mapping[str, Any]]) -> str:
    if not query:
        return ""
    filtered = {
        key: value
        for key, value in query.items()
        if value is not None and str(value).strip() != ""
    }
    if not filtered:
        return ""
    return "?" + urllib.parse.urlencode(filtered)


def _request_json(
    method: str,
    path: str,
    *,
    query: Optional[Mapping[str, Any]] = None,
    body: Optional[Mapping[str, Any]] = None,
    server: Optional[str] = None,
    token: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Call the board and return the decoded JSON object.

    Raises :class:`ActivityConfigError` for bad local configuration/input and
    :class:`ActivityError` for transport/HTTP failures.
    """
    base = resolve_board_url(server)
    url = base + path + _query_string(query)
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        try:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ActivityConfigError(
                f"request body is not JSON-serializable: {exc}"
            ) from None
        headers["Content-Type"] = "application/json"
    bearer = resolve_board_token(token)
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or 200)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = _error_detail(exc)
        message = f"board rejected the request (HTTP {exc.code})"
        if detail:
            message += f": {detail}"
        raise ActivityError(message) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise ActivityError(
                f"timed out after {timeout:.1f}s contacting the board at {base}"
            ) from None
        raise ActivityError(
            f"cannot reach the Sugar board at {base}: {reason}"
        ) from None
    except (socket.timeout, TimeoutError):
        raise ActivityError(
            f"timed out after {timeout:.1f}s contacting the board at {base}"
        ) from None
    except OSError as exc:
        raise ActivityError(f"cannot reach the Sugar board at {base}: {exc}") from None
    if status < 200 or status >= 300:
        raise ActivityError(f"board rejected the request (HTTP {status})")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ActivityError("board response is too large")
    try:
        decoded = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        raise ActivityError(
            f"unexpected response from the board at {base} (invalid JSON)"
        ) from None
    if not isinstance(decoded, dict):
        raise ActivityError(f"unexpected response from the board at {base}")
    return decoded


def record_evidence(
    body: Mapping[str, Any],
    *,
    server: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """POST one evidence event and return the stored spine event."""
    data = _request_json("POST", EVIDENCE_PATH, body=body, server=server, token=token)
    event = data.get("event")
    if not isinstance(event, Mapping):
        raise ActivityError("board response did not include the stored event")
    return dict(event)


def fetch_evidence(
    query: Optional[Mapping[str, Any]] = None,
    *,
    server: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """GET the evidence read API and return the raw response object."""
    return _request_json("GET", EVIDENCE_PATH, query=query, server=server, token=token)


# ---------------------------------------------------------------------------
# Shared CLI helpers
# ---------------------------------------------------------------------------


def _exit_error(message: Exception, code: int) -> None:
    click.echo(f"❌ {message}", err=True)
    raise click.exceptions.Exit(code)


def parse_payload_items(items: Sequence[str]) -> Dict[str, Any]:
    """Parse repeatable ``KEY=VALUE`` options (JSON values when possible)."""
    payload: Dict[str, Any] = {}
    for item in items:
        key, separator, raw = str(item).partition("=")
        key = key.strip()
        if not separator or not key:
            raise click.BadParameter(
                f"invalid --payload {item!r}: expected KEY=VALUE",
                param_hint="--payload",
            )
        try:
            value: Any = json.loads(raw)
        except (TypeError, ValueError):
            value = raw
        payload[key] = value
    return payload


def build_record_body(
    *,
    kind: str,
    status: Optional[str] = None,
    task_id: Optional[str] = None,
    harness: Optional[str] = None,
    check: Optional[str] = None,
    command: Optional[str] = None,
    commit: Optional[str] = None,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    payload_items: Sequence[str] = (),
) -> Dict[str, Any]:
    """Build the canonical ``POST /api/evidence`` body for ``record``."""
    payload = parse_payload_items(payload_items)
    if status is not None:
        payload["status"] = status
    for name, value in (
        ("check", check),
        ("command", command),
        ("commit", commit),
        ("title", title),
        ("summary", summary),
    ):
        if value is not None:
            payload[name] = value
    body: Dict[str, Any] = {"kind": kind, "payload": payload}
    if harness:
        body["harness"] = harness
    if task_id:
        body["task_id"] = task_id
    if status is not None:
        # The evidence builder populates payload.status from the envelope-level
        # status; keep both so readers can use either field.
        body["status"] = status
    return body


def _normalize_time_option(value: Optional[str], name: str) -> Optional[str]:
    if value is None or not str(value).strip():
        return None
    normalized = normalize_iso_timestamp(value)
    if normalized is None:
        raise click.BadParameter(
            f"invalid ISO-8601 timestamp: {value!r}", param_hint=f"--{name}"
        )
    return normalized


def _format_table(events: Sequence[Mapping[str, Any]]) -> str:
    if not events:
        return "No evidence found."
    rows: List[Tuple[str, ...]] = [
        ("TIME", "KIND", "STATUS", "HARNESS", "TASK", "SUMMARY")
    ]
    for event in events:
        rows.append(
            (
                str(event.get("ts") or "-"),
                str(event.get("kind") or "-"),
                event_status(event) or "-",
                str(event.get("harness") or "-"),
                str(event.get("task_id") or "-"),
                event_summary_text(event),
            )
        )
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    lines: List[str] = []
    for row in rows:
        cells = [value.ljust(width) for value, width in zip(row, widths)]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def _format_summary(summary: Mapping[str, Any]) -> str:
    total = summary.get("total", 0)
    by_status = summary.get("by_status") or {}
    by_kind = summary.get("by_kind") or {}
    status_text = (
        " ".join(f"{key}={value}" for key, value in sorted(by_status.items()))
        if by_status
        else "-"
    )
    kind_text = (
        " ".join(f"{key}={value}" for key, value in sorted(by_kind.items()))
        if by_kind
        else "-"
    )
    return (
        "Evidence summary\n"
        f"  total: {total}\n"
        f"  by status: {status_text}\n"
        f"  by kind: {kind_text}"
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group("evidence")
def evidence_group():
    """Record, list and summarize verification evidence.

    Evidence is stored in the Sugar event spine and exposed by a running board
    at ``GET/POST /api/evidence``. Point the CLI at a board with ``--url``,
    ``$SUGAR_BOARD_URL`` or ``$SUGAR_BOARD_TOKEN``.
    """


@evidence_group.command("record")
@click.option(
    "--kind",
    required=True,
    type=click.Choice(EVIDENCE_KINDS, case_sensitive=False),
    help="Evidence kind (verification.*, policy.decision, approval.*, ...)",
)
@click.option(
    "--status", default=None, help="Outcome (passed, failed, blocked, ok, ...)"
)
@click.option("--task-id", default=None, help="Related task id")
@click.option(
    "--harness", default=None, help="Harness/agent that produced the evidence"
)
@click.option("--check", default=None, help="Check name/identifier (e.g. pytest)")
@click.option("--command", default=None, help="Command that produced the evidence")
@click.option("--commit", default=None, help="Related commit SHA")
@click.option("--title", default=None, help="Short title")
@click.option("--summary", default=None, help="Human summary")
@click.option(
    "--payload",
    "payload_items",
    multiple=True,
    metavar="KEY=VALUE",
    help="Extra payload field; VALUE is JSON-decoded when possible. Repeatable.",
)
@click.option(
    "--url",
    default=None,
    help="Board base URL (default: $SUGAR_BOARD_URL or http://127.0.0.1:7331)",
)
@click.option("--json", "json_output", is_flag=True, help="Print a JSON summary")
def record(
    kind,
    status,
    task_id,
    harness,
    check,
    command,
    commit,
    title,
    summary,
    payload_items,
    url,
    json_output,
):
    """Record one evidence event on a running board."""
    body = build_record_body(
        kind=kind,
        status=status,
        task_id=task_id,
        harness=harness,
        check=check,
        command=command,
        commit=commit,
        title=title,
        summary=summary,
        payload_items=payload_items,
    )
    try:
        event = record_evidence(body, server=url)
    except ActivityConfigError as exc:
        _exit_error(exc, 2)
    except ActivityError as exc:
        _exit_error(exc, 1)
    event_kind = str(event.get("kind") or kind)
    event_id = event.get("id")
    stored_status = event_status(event) or status
    if json_output:
        click.echo(
            json.dumps(
                {"id": event_id, "kind": event_kind, "status": stored_status},
                indent=2,
            )
        )
    else:
        click.echo(
            f"✅ recorded evidence {event_id}: {event_kind} "
            f"status={stored_status or '-'}"
        )


@evidence_group.command("list")
@click.option("--since", default=None, help="Minimum event timestamp (ISO-8601)")
@click.option("--until", default=None, help="Maximum event timestamp (ISO-8601)")
@click.option("--task-id", default=None, help="Filter by task id")
@click.option(
    "--kind",
    default=None,
    type=click.Choice(EVIDENCE_KINDS, case_sensitive=False),
    help="Filter by evidence kind",
)
@click.option(
    "--status",
    default=None,
    type=click.Choice(EVIDENCE_STATUSES, case_sensitive=False),
    help="Filter by evidence status",
)
@click.option(
    "--limit",
    default=100,
    show_default=True,
    type=click.IntRange(1, 500),
    help="Maximum events to show (1-500)",
)
@click.option("--json", "json_output", is_flag=True, help="Print the raw JSON response")
@click.option(
    "--url",
    default=None,
    help="Board base URL (default: $SUGAR_BOARD_URL or http://127.0.0.1:7331)",
)
def list_evidence(since, until, task_id, kind, status, limit, json_output, url):
    """List evidence events (oldest first)."""
    query = {
        "since": _normalize_time_option(since, "since"),
        "until": _normalize_time_option(until, "until"),
        "task_id": task_id,
        "kind": kind,
        "status": status,
        "limit": limit,
    }
    try:
        response = fetch_evidence(query, server=url)
    except ActivityConfigError as exc:
        _exit_error(exc, 2)
    except ActivityError as exc:
        _exit_error(exc, 1)
    if json_output:
        click.echo(json.dumps(response, indent=2))
        return
    events = response.get("events")
    if not isinstance(events, list):
        events = []
    click.echo(_format_table(events))


@evidence_group.command("summary")
@click.option("--since", default=None, help="Minimum event timestamp (ISO-8601)")
@click.option("--until", default=None, help="Maximum event timestamp (ISO-8601)")
@click.option("--task-id", default=None, help="Filter by task id")
@click.option("--json", "json_output", is_flag=True, help="Print the raw JSON response")
@click.option(
    "--url",
    default=None,
    help="Board base URL (default: $SUGAR_BOARD_URL or http://127.0.0.1:7331)",
)
def summary(since, until, task_id, json_output, url):
    """Summarize evidence totals by status and kind."""
    query = {
        "since": _normalize_time_option(since, "since"),
        "until": _normalize_time_option(until, "until"),
        "task_id": task_id,
        "limit": 1,
    }
    try:
        response = fetch_evidence(query, server=url)
    except ActivityConfigError as exc:
        _exit_error(exc, 2)
    except ActivityError as exc:
        _exit_error(exc, 1)
    if json_output:
        click.echo(json.dumps(response, indent=2))
        return
    data = response.get("summary")
    click.echo(_format_summary(data if isinstance(data, Mapping) else {}))


__all__ = [
    "DEFAULT_TIMEOUT",
    "EVIDENCE_PATH",
    "build_record_body",
    "evidence_group",
    "fetch_evidence",
    "parse_payload_items",
    "record_evidence",
]
