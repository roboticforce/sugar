"""Security guardrails and optional bearer-token auth for the dashboard.

The server binds to loopback and has no authentication by default. That is a
deliberate single-user choice, but "loopback" alone does not stop a **web page
the user has open** from POSTing to `127.0.0.1` (a simple cross-origin request
with a `text/plain` body is not preflighted). :class:`DashboardGuardMiddleware`
rejects state-changing requests whose ``Origin`` is not a loopback origin (or,
in token mode, not the request's own host), which blocks that class of
cross-site request forgery (and DNS-rebinding writes) without adding auth for
local tools and agents (they send no ``Origin`` header at all). It also adds
conservative response headers and caps request bodies.

Token mode (``sugar dashboard --allow-remote --token-file ...``, see
``docs/board-server.md``) is opt-in and layered on top:

* every ``/api/*`` request except ``/api/health`` needs a bearer token
  (``Authorization: Bearer``) or the browser session cookie;
* tokens are scoped ``read`` (GET/HEAD) or ``write`` (everything);
* ``GET /?token=...`` validates a token, sets an HttpOnly SameSite=Strict
  cookie named ``sugar_board`` and redirects to ``/`` without the token;
* token comparisons are constant-time and secrets are never logged.

The default loopback/no-auth behaviour is unchanged when no tokens are
configured.
"""

from __future__ import annotations

import hmac
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit

from starlette.responses import JSONResponse, Response

SAFE_METHODS: Set[str] = {"GET", "HEAD", "OPTIONS"}
LOOPBACK_HOSTS: Set[str] = {"127.0.0.1", "localhost", "::1"}
MAX_BODY_BYTES = 1_000_000  # 1 MB: task text and telemetry are small

AUTH_COOKIE_NAME = "sugar_board"
AUTH_COOKIE_MAX_AGE = 24 * 60 * 60  # one day
READ_SCOPE = "read"
WRITE_SCOPE = "write"
TOKEN_SCOPES = (READ_SCOPE, WRITE_SCOPE)
READ_METHODS: Set[str] = {"GET", "HEAD"}

SECURITY_HEADERS: Iterable[Tuple[bytes, bytes]] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)


class TokenConfigError(ValueError):
    """A token file / token CLI option is unusable.

    Messages are shown to the operator and must never contain a token secret.
    """


@dataclass(frozen=True)
class BoardToken:
    """One scoped bearer token. ``write`` implies read access."""

    name: str
    scope: str
    secret: str

    def allows(self, method: str) -> bool:
        if self.scope == WRITE_SCOPE:
            return True
        return method.upper() in READ_METHODS


class TokenRegistry:
    """Immutable set of board tokens with constant-time lookup."""

    def __init__(self, tokens: Sequence[BoardToken]):
        self._tokens: Tuple[BoardToken, ...] = tuple(tokens)

    def __len__(self) -> int:
        return len(self._tokens)

    @property
    def is_empty(self) -> bool:
        return not self._tokens

    def describe(self) -> str:
        """Human summary for CLI output (names + scopes; never secrets)."""
        if not self._tokens:
            return "disabled"
        entries = ", ".join(f"{token.name}={token.scope}" for token in self._tokens)
        return f"{len(self._tokens)} token(s): {entries}"

    def find(self, candidate: Optional[str]) -> Optional[BoardToken]:
        """Return the matching token, comparing every secret in constant time."""
        if not candidate:
            return None
        try:
            candidate_bytes = candidate.encode("utf-8")
        except UnicodeEncodeError:  # pragma: no cover - defensive
            return None
        match: Optional[BoardToken] = None
        for token in self._tokens:
            secret_bytes = token.secret.encode("utf-8")
            if hmac.compare_digest(candidate_bytes, secret_bytes) and match is None:
                match = token
        return match


# ---------------------------------------------------------------------------
# Token sources (CLI: --token-file / --token)
# ---------------------------------------------------------------------------


def _validate_secret(secret: str, label: str) -> str:
    if not secret:
        raise TokenConfigError(f"{label} must not be empty")
    if not secret.isascii() or not secret.isprintable():
        raise TokenConfigError(f"{label} must be printable ASCII")
    forbidden = (" ", "\t", "\r", "\n", ";", ",", '"')
    if any(character in secret for character in forbidden):
        raise TokenConfigError(
            f"{label} must not contain whitespace, commas, semicolons or quotes"
        )
    return secret


def parse_token_line(raw: str) -> Optional[BoardToken]:
    """Parse one ``name:scope:token`` line. Blank/comment lines return None."""
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split(":", 2)
    if len(parts) != 3:
        raise TokenConfigError("expected 'name:scope:token'")
    name, scope, secret = parts
    name = name.strip()
    scope = scope.strip().lower()
    secret = secret.strip()
    if not name:
        raise TokenConfigError("token name must not be empty")
    if scope not in TOKEN_SCOPES:
        raise TokenConfigError(
            f"invalid scope {scope!r} (expected one of: {', '.join(TOKEN_SCOPES)})"
        )
    _validate_secret(secret, "token")
    return BoardToken(name=name, scope=scope, secret=secret)


def _check_token_file_permissions(path: Path) -> None:
    """Refuse token files that are group- or world-readable (POSIX only)."""
    if os.name != "posix":
        return
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:  # pragma: no cover - race with deletion
        return
    if mode & 0o077:
        raise TokenConfigError(
            f"token file {path} has mode {oct(mode)}; it must not be readable "
            f"by group or others (run: chmod 600 {path})"
        )


def load_token_file(path: str | os.PathLike) -> list[BoardToken]:
    """Load and validate a token file. Raises :class:`TokenConfigError`."""
    token_path = Path(path).expanduser()
    if not token_path.exists():
        raise TokenConfigError(f"token file not found: {token_path}")
    if not token_path.is_file():
        raise TokenConfigError(f"token file is not a regular file: {token_path}")
    _check_token_file_permissions(token_path)
    try:
        content = token_path.read_text(encoding="utf-8")
    except OSError as exc:
        reason = getattr(exc, "strerror", None) or str(exc)
        raise TokenConfigError(f"could not read token file {token_path}: {reason}")
    tokens: list[BoardToken] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        try:
            token = parse_token_line(line)
        except TokenConfigError as exc:
            raise TokenConfigError(
                f"token file {token_path} line {line_number}: {exc}"
            ) from None
        if token is not None:
            tokens.append(token)
    if not tokens:
        raise TokenConfigError(f"token file {token_path} defines no tokens")
    return tokens


def build_token_registry(
    *,
    token_file: str | os.PathLike | None = None,
    adhoc_token: str | None = None,
) -> TokenRegistry:
    """Build a registry from a token file and/or one ad-hoc write token."""
    tokens: list[BoardToken] = []
    if token_file:
        tokens.extend(load_token_file(token_file))
    if adhoc_token is not None:
        secret = _validate_secret(adhoc_token.strip(), "--token")
        tokens.append(BoardToken(name="adhoc", scope=WRITE_SCOPE, secret=secret))
    if not tokens:
        raise TokenConfigError(
            "no board tokens configured; pass --token-file PATH or --token TOKEN"
        )

    seen_names: dict[str, str] = {}
    seen_secrets: dict[str, str] = {}
    for token in tokens:
        name_key = token.name.lower()
        if name_key in seen_names:
            raise TokenConfigError(f"duplicate token name: {token.name}")
        seen_names[name_key] = token.name
        if token.secret in seen_secrets:
            raise TokenConfigError(
                "duplicate token secret for token names "
                f"{seen_secrets[token.secret]!r} and {token.name!r}"
            )
        seen_secrets[token.secret] = token.name
    return TokenRegistry(tokens)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


def unauthorized_response() -> JSONResponse:
    return JSONResponse(
        {"error": "unauthorized", "detail": "missing or invalid board token"},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


def forbidden_scope_response(scope: str, method: str) -> JSONResponse:
    return JSONResponse(
        {
            "error": "forbidden_scope",
            "detail": f"'{scope}' token may not use {method.upper()}",
        },
        status_code=403,
    )


def set_board_cookie(response: Response, secret: str, *, secure: bool) -> None:
    """Attach the browser session cookie for *secret* (never use for logging)."""
    response.set_cookie(
        AUTH_COOKIE_NAME,
        secret,
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=secure,
        path="/",
    )


# ---------------------------------------------------------------------------
# Origin policy
# ---------------------------------------------------------------------------


def _host_at_parts(request_host: str):
    try:
        return urlsplit("//" + request_host.strip())
    except ValueError:
        return None


def _same_host(origin_parts, request_host: Optional[str]) -> bool:
    """True when Origin matches the request's Host header (token mode only)."""
    if not request_host:
        return False
    host_parts = _host_at_parts(request_host)
    if host_parts is None:
        return False
    origin_host = (origin_parts.hostname or "").lower()
    request_host_name = (host_parts.hostname or "").lower()
    if not origin_host or origin_host != request_host_name:
        return False
    origin_port = origin_parts.port
    if origin_port is None:
        origin_port = 443 if origin_parts.scheme == "https" else 80
    if host_parts.port is None:
        # Reverse proxies often forward the default port implicitly.
        return origin_port == (443 if origin_parts.scheme == "https" else 80)
    return host_parts.port == origin_port


def origin_allowed(origin: str | None, request_host: str | None = None) -> bool:
    """True when *origin* is absent (native client / curl) or trusted.

    Loopback origins are always allowed (default local mode). When
    ``request_host`` is supplied, an origin matching the request's own Host
    header is also allowed; only token mode passes it, so the default
    behaviour is unchanged.
    """
    if origin is None or not origin.strip():
        return True
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    hostname = (parts.hostname or "").lower()
    if hostname in LOOPBACK_HOSTS:
        return True
    return _same_host(parts, request_host)


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


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class DashboardGuardMiddleware:
    """ASGI middleware: origin gate for writes + basic security headers."""

    def __init__(self, app, trust_same_origin: bool = False):
        self.app = app
        self.trust_same_origin = trust_same_origin

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        method = (scope.get("method") or "GET").upper()
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers") or []
        }

        if method not in SAFE_METHODS:
            request_host = headers.get("host") if self.trust_same_origin else None
            if not origin_allowed(headers.get("origin"), request_host):
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


def _bearer_token(authorization: str | None) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _cookie_value(cookie_header: str | None, name: str) -> Optional[str]:
    if not cookie_header:
        return None
    for chunk in cookie_header.split(";"):
        key, separator, value = chunk.strip().partition("=")
        if separator and key == name:
            return value.strip().strip('"') or None
    return None


class BoardAuthMiddleware:
    """ASGI middleware: scoped bearer-token/cookie auth for ``/api/*``.

    ``/api/health`` stays public so clients can probe liveness; ``OPTIONS``
    passes through for CORS preflight. Only active when tokens are configured.
    """

    def __init__(self, app, tokens: TokenRegistry):
        self.app = app
        self.tokens = tokens

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.tokens.is_empty:
            return await self.app(scope, receive, send)

        path = scope.get("path") or ""
        if path == "/api/health" or not path.startswith("/api/"):
            return await self.app(scope, receive, send)

        method = (scope.get("method") or "GET").upper()
        if method == "OPTIONS":
            return await self.app(scope, receive, send)

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers") or []
        }
        authorization = headers.get("authorization")
        if authorization is not None:
            record = self.tokens.find(_bearer_token(authorization))
        else:
            record = self.tokens.find(
                _cookie_value(headers.get("cookie"), AUTH_COOKIE_NAME)
            )
        if record is None:
            return await unauthorized_response()(scope, receive, send)
        if not record.allows(method):
            return await forbidden_scope_response(record.scope, method)(
                scope, receive, send
            )
        return await self.app(scope, receive, send)
