"""Actor identity for Sugar work items.

Sugar records *provenance*, not authorization: on a local machine any process
can claim any label. Identity only becomes a security boundary when the queue
is reachable remotely, and then it must be bound to a credential (token →
actor id), not to a self-declared header.

Three identities are kept separate on purpose:

- ``created_by``       who asked for the work (this module)
- ``assigned_agent``   who should do it (routing intent)
- ``claimed_by``       who is doing it right now (lease, set by the claim)
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

ACTOR_KINDS = ("human", "agent", "system")
DEFAULT_LEASE_SECONDS = 900
MAX_ACTOR_LENGTH = 128
#: Store-neutral timestamp format for lease expiry (UTC, lexicographically sortable).
LEASE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class Actor:
    """A stable actor id plus how it came to be."""

    id: str
    kind: str = "human"
    session: Optional[str] = None

    def as_task_fields(self) -> dict:
        return {
            "created_by": self.id,
            "created_by_kind": self.kind,
            "created_by_session": self.session,
        }


def normalize_actor_id(value: object, default: Optional[str] = None) -> Optional[str]:
    """Trim/limit an actor id; blank or non-string values fall back to *default*."""
    if value is None:
        return default
    text = str(value).strip().strip('"').strip("'")
    if not text:
        return default
    return text[:MAX_ACTOR_LENGTH]


def normalize_kind(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text if text in ACTOR_KINDS else None


def _default_user() -> str:
    return (
        os.environ.get("USER")
        or os.environ.get("LOGNAME")
        or os.environ.get("USERNAME")
        or "unknown"
    )


def _default_host() -> str:
    try:
        return socket.gethostname().split(".")[0] or "localhost"
    except OSError:  # pragma: no cover - hostname is always available in practice
        return "localhost"


def resolve_actor(
    explicit: Optional[str] = None,
    *,
    kind: Optional[str] = None,
    session: Optional[str] = None,
) -> Actor:
    """Resolve the acting identity.

    Precedence for the id: explicit argument → ``$SUGAR_ACTOR`` →
    ``$SUGAR_AGENT_ID`` → ``$USER@$host``. Kind defaults to ``agent`` when an
    agent id comes from the environment, otherwise ``human``.
    """
    env_agent = normalize_actor_id(os.environ.get("SUGAR_AGENT_ID"))
    actor_id = (
        normalize_actor_id(explicit)
        or normalize_actor_id(os.environ.get("SUGAR_ACTOR"))
        or env_agent
        or f"{_default_user()}@{_default_host()}"
    )
    if kind is None:
        kind = normalize_kind(os.environ.get("SUGAR_ACTOR_KIND"))
    if kind is None:
        kind = "agent" if (env_agent and not explicit) else "human"
    actor_session = normalize_actor_id(session) or normalize_actor_id(
        os.environ.get("SUGAR_SESSION")
    )
    return Actor(actor_id, kind, actor_session)


def agent_actor(
    name: Optional[str], version: Optional[str] = None, *, default: str = "mcp-client"
) -> Actor:
    """Actor for a self-identifying MCP/client peer (``clientInfo.name``)."""
    return Actor(
        normalize_actor_id(name) or default,
        "agent",
        normalize_actor_id(version),
    )


def worker_actor(explicit: Optional[str] = None) -> Actor:
    """The identity recorded when Sugar's own loop claims a task."""
    return Actor(
        normalize_actor_id(explicit)
        or normalize_actor_id(os.environ.get("SUGAR_WORKER_ID"))
        or f"sugar-worker@{_default_host()}",
        "agent",
        None,
    )


def lease_expiry(seconds: int = DEFAULT_LEASE_SECONDS) -> str:
    """UTC lease timestamp *seconds* from now, in :data:`LEASE_TIMESTAMP_FORMAT`."""
    delta = timedelta(seconds=max(1, int(seconds)))
    return format_lease_timestamp(datetime.now(timezone.utc) + delta)


def format_lease_timestamp(moment: datetime) -> str:
    """Normalize a datetime to the store-neutral lease timestamp format."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime(LEASE_TIMESTAMP_FORMAT)


def lease_seconds_from_env(default: int = DEFAULT_LEASE_SECONDS) -> int:
    raw = os.environ.get("SUGAR_LEASE_SECONDS")
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default
