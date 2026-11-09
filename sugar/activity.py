"""Tiny stdlib client for Sugar's live telemetry ingest endpoint.

Harnesses and scripts use this to post activity events (``tool.invoked``,
``run.started``, ``run.ended``, ``agent.activity``, ``usage.reported``, ...) to a
running dashboard::

    POST <board>/api/events   (docs/local-dashboard.md §11.3)

The helper is deliberately dependency-free (``urllib.request`` only), never
prints/logs the bearer token or the request body, and keeps error messages safe
to show to a user. ``sugar emit`` and the ``sugar_emit_event`` MCP tool both use
it.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_BOARD_URL = "http://127.0.0.1:7331"
DEFAULT_TIMEOUT = 5.0
INGEST_PATH = "/api/events"
MAX_RESPONSE_BYTES = 1_000_000


class ActivityError(Exception):
    """A telemetry emit failed. ``str(exc)`` is safe to show to a user."""


class ActivityConfigError(ActivityError):
    """Bad board URL / event body / token configuration (caller input error)."""


def resolve_board_url(explicit: Optional[str] = None) -> str:
    """Resolve and validate the board base URL (no trailing slash).

    Precedence: *explicit* → ``$SUGAR_BOARD_URL`` → ``http://127.0.0.1:7331``.
    """
    raw = explicit
    if raw is None or not str(raw).strip():
        raw = os.environ.get("SUGAR_BOARD_URL") or DEFAULT_BOARD_URL
    text = str(raw).strip()
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ActivityConfigError(
            f"invalid board URL {text!r}: expected http(s)://host[:port]"
        )
    if parts.username or parts.password:
        raise ActivityConfigError(
            "board URL must not embed credentials; pass --token or "
            "set $SUGAR_BOARD_TOKEN"
        )
    if parts.query or parts.fragment:
        raise ActivityConfigError(
            "board URL must not contain a query string or fragment"
        )
    return text.rstrip("/")


def ingest_url(server: Optional[str] = None) -> str:
    """Return the full ``/api/events`` ingest URL for *server*."""
    return resolve_board_url(server) + INGEST_PATH


def resolve_board_token(explicit: Optional[str] = None) -> Optional[str]:
    """Resolve the optional bearer token (``--token`` or ``$SUGAR_BOARD_TOKEN``)."""
    raw = explicit if explicit is not None else os.environ.get("SUGAR_BOARD_TOKEN")
    token = str(raw).strip() if raw is not None else ""
    return token or None


def build_event_body(
    kind: str,
    *,
    actor: Optional[str] = None,
    actor_kind: Optional[str] = None,
    harness: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    project: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the exact ``POST /api/events`` body, omitting unset fields."""
    kind_text = str(kind or "").strip()
    if not kind_text:
        raise ActivityConfigError("event kind must not be empty")
    body: Dict[str, Any] = {"kind": kind_text}
    for key, value in (
        ("actor", actor),
        ("actor_kind", actor_kind),
        ("harness", harness),
        ("session_id", session_id),
        ("task_id", task_id),
        ("project", project),
    ):
        if value is None:
            continue
        text = str(value).strip()
        if text:
            body[key] = text
    if payload is not None:
        if not isinstance(payload, dict):
            raise ActivityConfigError("payload must be a JSON object")
        body["payload"] = payload
    return body


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """Extract a short, safe server error detail (never the request/response body)."""
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
        if isinstance(data, dict):
            detail = str(data.get("detail") or data.get("error") or "")
    return detail[:300]


def post_event(
    body: Dict[str, Any],
    *,
    server: Optional[str] = None,
    token: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """POST one event to ``<server>/api/events`` and return the stored event.

    Raises :class:`ActivityConfigError` for bad input and :class:`ActivityError`
    for transport/HTTP failures. Messages never include the token or body.
    """
    if not isinstance(body, dict):
        raise ActivityConfigError("event body must be a JSON object")
    try:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ActivityConfigError(
            f"event body is not JSON-serializable: {exc}"
        ) from None

    url = ingest_url(server)
    bearer = resolve_board_token(token)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, data=encoded, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or 201)
            raw = response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        detail = _error_detail(exc)
        message = f"board rejected the event (HTTP {exc.code})"
        if detail:
            message += f": {detail}"
        raise ActivityError(message) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise ActivityError(
                f"timed out after {timeout:.1f}s contacting the board at {url}"
            ) from None
        raise ActivityError(
            f"cannot reach the Sugar board at {url}: {reason}"
        ) from None
    except (socket.timeout, TimeoutError):
        raise ActivityError(
            f"timed out after {timeout:.1f}s contacting the board at {url}"
        ) from None
    except OSError as exc:
        raise ActivityError(f"cannot reach the Sugar board at {url}: {exc}") from None

    if status < 200 or status >= 300:
        raise ActivityError(f"board rejected the event (HTTP {status})")
    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        raise ActivityError(
            f"unexpected response from the board at {url} (invalid JSON)"
        ) from None
    if not isinstance(data, dict):
        raise ActivityError(f"unexpected response from the board at {url}")
    event = data.get("event")
    return event if isinstance(event, dict) else data


def emit_activity(
    kind: str,
    *,
    actor: Optional[str] = None,
    actor_kind: Optional[str] = None,
    harness: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    project: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    server: Optional[str] = None,
    token: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Build and post one activity event; returns the stored event dict."""
    body = build_event_body(
        kind,
        actor=actor,
        actor_kind=actor_kind,
        harness=harness,
        session_id=session_id,
        task_id=task_id,
        project=project,
        payload=payload,
    )
    return post_event(body, server=server, token=token, timeout=timeout)


async def emit_activity_async(
    kind: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Async variant of :func:`emit_activity` (runs the sync client in a thread)."""
    return await asyncio.to_thread(emit_activity, kind, **kwargs)
