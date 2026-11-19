"""In-memory TTL/LRU dedupe for telemetry deliveries.

The same hook event can arrive twice (e.g. HTTP hook retry + Unix-socket fast
path, or a harness replaying a hook). Events whose
``(harness, session_id, tool_use_id, kind)`` key was seen within the TTL are
dropped. Events without an explicit ``tool_use_id`` are never deduped (otherwise
two identical tool calls in a session would collapse).
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from typing import Callable, Optional, Tuple

ENV_TTL = "SUGAR_TELEMETRY_DEDUPE_TTL"
ENV_MAX_ENTRIES = "SUGAR_TELEMETRY_DEDUPE_MAX"
DEFAULT_TTL_SECONDS = 60.0
DEFAULT_MAX_ENTRIES = 10_000

DedupeKey = Tuple[str, str, str, str]


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        number = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def dedupe_key(
    harness: str,
    session_id: Optional[str],
    tool_use_id: Optional[str],
    kind: str,
) -> Optional[DedupeKey]:
    """Return the dedupe key, or ``None`` when the event cannot be deduped."""
    tool = (tool_use_id or "").strip()
    if not tool:
        return None
    return (
        (harness or "").strip().lower(),
        (session_id or "").strip(),
        tool,
        (kind or "").strip(),
    )


class Deduper:
    """Small TTL-LRU set of recently delivered telemetry event keys."""

    def __init__(
        self,
        *,
        ttl_seconds: Optional[float] = None,
        max_entries: Optional[int] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        ttl = (
            _env_float(ENV_TTL, DEFAULT_TTL_SECONDS)
            if ttl_seconds is None
            else ttl_seconds
        )
        max_size = (
            _env_int(ENV_MAX_ENTRIES, DEFAULT_MAX_ENTRIES)
            if max_entries is None
            else max_entries
        )
        self.ttl_seconds = max(0.001, float(ttl))
        self.max_entries = max(1, int(max_size))
        self._clock = clock
        self._seen: "OrderedDict[DedupeKey, float]" = OrderedDict()

    def __len__(self) -> int:
        return len(self._seen)

    def _purge(self, now: float) -> None:
        expired = self.ttl_seconds <= 0
        while self._seen:
            key, seen_at = next(iter(self._seen.items()))
            if not expired and now - seen_at < self.ttl_seconds:
                break
            self._seen.popitem(last=False)

    def is_duplicate(
        self,
        harness: str,
        session_id: Optional[str],
        tool_use_id: Optional[str],
        kind: str,
    ) -> bool:
        """True when this key was seen within the TTL; records new keys."""
        key = dedupe_key(harness, session_id, tool_use_id, kind)
        if key is None:
            return False
        now = self._clock()
        self._purge(now)
        if key in self._seen:
            return True
        self._seen[key] = now
        self._seen.move_to_end(key)
        while len(self._seen) > self.max_entries:
            self._seen.popitem(last=False)
        return False


_default_deduper: Optional[Deduper] = None


def default_deduper() -> Deduper:
    """Process-wide deduper shared by the HTTP hook and Unix-socket paths."""
    global _default_deduper
    if _default_deduper is None:
        _default_deduper = Deduper()
    return _default_deduper
