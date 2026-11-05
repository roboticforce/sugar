"""Security guardrails for the local dashboard.

The server binds to loopback and has no authentication. That is a deliberate
single-user choice, but "loopback" alone does not stop a **web page the user has
open** from POSTing to `127.0.0.1` (a simple cross-origin request with a
`text/plain` body is not preflighted). This middleware rejects state-changing
requests whose `Origin` is not a loopback origin, which blocks that class of
cross-site request forgery (and DNS-rebinding writes) without adding auth for
local tools and agents (they send no `Origin` header at all).

It also adds conservative response headers. See ``docs/local-dashboard.md`` §7.
"""

from __future__ import annotations

from typing import Iterable, Set
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

SAFE_METHODS: Set[str] = {"GET", "HEAD", "OPTIONS"}
LOOPBACK_HOSTS: Set[str] = {"127.0.0.1", "localhost", "::1"}
MAX_BODY_BYTES = 1_000_000  # 1 MB: task text and telemetry are small

SECURITY_HEADERS: Iterable[tuple[bytes, bytes]] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)


def origin_allowed(origin: str | None) -> bool:
    """True when *origin* is absent (native client / curl) or loopback."""
    if origin is None or not origin.strip():
        return True
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    hostname = (parts.hostname or "").lower()
    return hostname in LOOPBACK_HOSTS


def forbidden_response() -> JSONResponse:
    return JSONResponse(
        {"error": "forbidden_origin", "detail": "cross-origin writes are not allowed"},
        status_code=403,
    )


def body_too_large_response() -> JSONResponse:
    return JSONResponse(
        {
            "error": "payload_too_large",
            "detail": f"body exceeds {MAX_BODY_BYTES} bytes",
        },
        status_code=413,
    )


class DashboardGuardMiddleware:
    """ASGI middleware: origin gate for writes + basic security headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        method = (scope.get("method") or "GET").upper()
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers") or []
        }

        if method not in SAFE_METHODS:
            if not origin_allowed(headers.get("origin")):
                return await forbidden_response()(scope, receive, send)
            content_length = headers.get("content-length", "")
            if content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
                return await body_too_large_response()(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                merged = list(message.get("headers") or [])
                existing = {key.lower() for key, _ in merged}
                for key, value in SECURITY_HEADERS:
                    if key not in existing:
                        merged.append((key, value))
                message["headers"] = merged
            await send(message)

        await self.app(scope, receive, send_with_headers)
