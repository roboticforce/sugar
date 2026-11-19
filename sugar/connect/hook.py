"""``sugar hook`` shim: forward one harness hook payload to Sugar, fail-open.

This runs on the hot path (every tool call in some harnesses), so it:

- imports stdlib only and does no logging/setup work;
- prefers the fast Unix-socket ingest path
  (``$SUGAR_INGEST_SOCKET`` / ``~/.sugar/ingest.sock``) and falls back to
  ``POST <board>/api/hooks/<harness>``;
- is bounded by short timeouts and **always succeeds from the harness's point of
  view** — a missing board must never block an agent's tool call;
- never prints anything unless ``$SUGAR_HOOK_DEBUG`` is set (diagnostics to
  stderr only).

Wire protocol (Unix socket): one newline-terminated JSON object
``{"harness": "<id>", "body": {<hook payload>}}``. The HTTP path forwards the
raw hook body to the harness-specific endpoint.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Optional, Union

DEFAULT_BOARD_URL = "http://127.0.0.1:7331"
DEFAULT_SOCKET_PATH = "~/.sugar/ingest.sock"
DEFAULT_SOCKET_TIMEOUT = 0.4
DEFAULT_HTTP_TIMEOUT = 1.0
MAX_STDIN_BYTES = 1_000_000
HOOK_HARNESSES = ("claude-code", "cursor", "codex")


@dataclass
class HookResult:
    delivered: bool
    transport: Optional[str] = None  # "socket" | "http" | None
    detail: str = ""


def default_socket_path() -> Path:
    override = os.environ.get("SUGAR_INGEST_SOCKET", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(DEFAULT_SOCKET_PATH).expanduser()


def default_board_url() -> str:
    return (os.environ.get("SUGAR_BOARD_URL") or DEFAULT_BOARD_URL).strip().rstrip("/")


def hook_endpoint(url: Optional[str], harness: str) -> str:
    """Accept a board base URL or a full hook URL; return the POST endpoint."""
    base = (url or default_board_url()).strip().rstrip("/")
    if "/api/hooks/" in base:
        return base
    return f"{base}/api/hooks/{harness}"


def read_stdin_bytes(
    stream: Union[BinaryIO, Any], limit: int = MAX_STDIN_BYTES
) -> bytes:
    """Read stdin as bytes, bounded; accepts binary or text streams."""
    buffer = getattr(stream, "buffer", None)
    source = buffer if buffer is not None else stream
    data = source.read(limit + 1)
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    return data[:limit]


def _body_object(raw: bytes) -> Any:
    """Parse the hook body for the socket envelope (must stay a JSON object)."""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return {"raw": raw.decode("utf-8", "replace")[:16_000]}
    if isinstance(parsed, dict):
        return parsed
    return {"raw_value": parsed}


def build_socket_message(raw: bytes, harness: str) -> bytes:
    """Encode the newline-delimited socket envelope for one hook payload."""
    envelope = {"harness": harness, "body": _body_object(raw)}
    return (
        json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        + b"\n"
    )


def _forward_socket(message: bytes, socket_path: Path, timeout: float) -> bool:
    if not hasattr(socket, "AF_UNIX"):  # pragma: no cover - Windows without AF_UNIX
        return False
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(max(0.05, timeout))
    try:
        client.connect(str(socket_path))
        client.sendall(message)
        try:
            client.shutdown(socket.SHUT_WR)
        except OSError:  # pragma: no cover - server may close first
            pass
        return True
    except OSError:
        return False
    finally:
        try:
            client.close()
        except OSError:  # pragma: no cover
            pass


def _forward_http(
    raw: bytes,
    harness: str,
    url: Optional[str],
    timeout: float,
    token: Optional[str],
) -> bool:
    endpoint = hook_endpoint(url, harness)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    bearer = token if token is not None else os.environ.get("SUGAR_BOARD_TOKEN", "")
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(
        endpoint, data=raw if raw else b"{}", headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=max(0.05, timeout)) as response:
            status = int(getattr(response, "status", 200) or 200)
            response.read(64_000)
        return 200 <= status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def forward_hook(
    raw: bytes,
    harness: str,
    *,
    socket_path: Optional[Union[str, Path]] = None,
    url: Optional[str] = None,
    socket_timeout: float = DEFAULT_SOCKET_TIMEOUT,
    http_timeout: float = DEFAULT_HTTP_TIMEOUT,
    token: Optional[str] = None,
) -> HookResult:
    """Forward one hook payload: socket first (if available), then HTTP.

    Returns a :class:`HookResult`; callers (the CLI) ignore failures by design.
    """
    if harness not in HOOK_HARNESSES:
        return HookResult(False, detail=f"unknown harness: {harness!r}")
    socket_file = (
        Path(socket_path).expanduser() if socket_path else default_socket_path()
    )
    try:
        if socket_file.exists():
            message = build_socket_message(raw, harness)
            if _forward_socket(message, socket_file, socket_timeout):
                return HookResult(True, "socket")
    except OSError:  # pragma: no cover - defensive
        pass
    if _forward_http(raw, harness, url, http_timeout, token):
        return HookResult(True, "http")
    return HookResult(False, detail="no ingest endpoint accepted the hook")
