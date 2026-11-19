"""Redaction for telemetry payload summaries.

Hook payloads can contain commands, file contents, prompts and model output.
Before anything is written to the spine it is redacted according to a level:

- ``metadata``: no payload content at all (kind/tool/status/cwd live in the
  canonical envelope, not here);
- ``summary`` (default): content is kept but secret-like strings are redacted and
  strings/containers are truncated;
- ``full``: content is kept, secrets are still redacted; only the total byte cap
  truncates.

Configuration (per-invocation, defensive):

- ``$SUGAR_TELEMETRY_REDACTION``: ``metadata`` | ``summary`` | ``full``
- ``$SUGAR_TELEMETRY_MAX_BYTES``: max JSON bytes of the redacted summary
  (default 8192, clamped to 28000 so the whole spine payload stays under 32 KiB)
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Mapping, Optional

LEVELS = ("metadata", "summary", "full")
DEFAULT_LEVEL = "summary"
ENV_LEVEL = "SUGAR_TELEMETRY_REDACTION"
ENV_MAX_BYTES = "SUGAR_TELEMETRY_MAX_BYTES"
DEFAULT_MAX_BYTES = 8192
MAX_ALLOWED_BYTES = 28000
SUMMARY_STRING_LIMIT = 500
SUMMARY_LIST_LIMIT = 20
SUMMARY_DICT_LIMIT = 50
SHRUNK_STRING_LIMIT = 120
REDACTED = "[REDACTED]"

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_SENSITIVE_KEY = re.compile(
    r"(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd|"
    r"authorization|client[_-]?secret|^token$)",
    re.IGNORECASE,
)
_SECRET_PATTERNS = (
    _PRIVATE_KEY,
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|"
        r"passwd|authorization|client[_-]?secret)\b\s*[:=]\s*['\"]?[^\s'\",}]{4,}"
    ),
)


def resolve_level(value: Any = None) -> str:
    """Resolve a redaction level (explicit takes precedence over the env)."""
    candidate = value if value is not None else os.environ.get(ENV_LEVEL)
    text = str(candidate).strip().lower() if candidate is not None else ""
    return text if text in LEVELS else DEFAULT_LEVEL


def resolve_max_bytes(value: Any = None) -> int:
    """Resolve the summary byte cap; invalid values fall back to the default."""
    candidate = value if value is not None else os.environ.get(ENV_MAX_BYTES)
    if candidate is not None and str(candidate).strip():
        try:
            number = int(str(candidate).strip())
        except (TypeError, ValueError):
            number = DEFAULT_MAX_BYTES
        if number > 0:
            return min(number, MAX_ALLOWED_BYTES)
    return DEFAULT_MAX_BYTES


def redact_text(
    text: Any, *, level: Optional[str] = None, max_chars: Optional[int] = None
) -> str:
    """Redact secrets in a string; ``metadata`` returns an empty string."""
    level_name = resolve_level(level)
    if level_name == "metadata":
        return ""
    rendered = str(text)
    for pattern in _SECRET_PATTERNS:
        rendered = pattern.sub(REDACTED, rendered)
    if level_name == "summary":
        limit = (
            SUMMARY_STRING_LIMIT
            if max_chars is None
            else min(SUMMARY_STRING_LIMIT, max_chars)
        )
        if len(rendered) > limit:
            rendered = rendered[: max(0, limit - 1)] + "…"
    elif max_chars is not None and len(rendered) > max_chars:
        rendered = rendered[: max(0, max_chars - 1)] + "…"
    return rendered


def _redact_value(value: Any, level: str, depth: int = 0) -> Any:
    if depth > 6:
        return redact_text(value, level=level, max_chars=SHRUNK_STRING_LIMIT)
    if isinstance(value, str):
        return redact_text(value, level=level)
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= SUMMARY_DICT_LIMIT:
                break
            key_text = str(key)[:120]
            if _SENSITIVE_KEY.search(key_text):
                result[key_text] = REDACTED
            else:
                result[key_text] = _redact_value(item, level, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [
            _redact_value(item, level, depth + 1)
            for item in list(value)[:SUMMARY_LIST_LIMIT]
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value, level=level, max_chars=SHRUNK_STRING_LIMIT)


def _encoded_size(value: Any) -> int:
    try:
        return len(
            json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        )
    except (TypeError, ValueError):  # pragma: no cover - redacted values are JSONable
        return 0


def _shrink(value: Any) -> Any:
    """Aggressively shrink a redacted structure to fit the byte cap."""
    if isinstance(value, str):
        return value[:SHRUNK_STRING_LIMIT]
    if isinstance(value, Mapping):
        return {
            str(key)[:80]: _shrink(item)
            for index, (key, item) in enumerate(value.items())
            if index < 20
        }
    if isinstance(value, (list, tuple)):
        return [_shrink(item) for item in list(value)[:10]]
    return value


def redact_payload(
    payload: Any,
    *,
    level: Optional[str] = None,
    max_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    """Return the redacted, byte-capped summary dict for a telemetry event."""
    level_name = resolve_level(level)
    if level_name == "metadata" or not payload:
        return {}
    if not isinstance(payload, Mapping):
        payload = {"value": payload}

    redacted = {
        str(key)[:120]: _redact_value(value, level_name)
        for key, value in list(payload.items())[:SUMMARY_DICT_LIMIT]
    }
    try:
        changed = redacted != dict(payload)
    except Exception:  # pragma: no cover - exotic payload objects
        changed = True
    limit = resolve_max_bytes(max_bytes)

    if _encoded_size(redacted) > limit:
        redacted = _shrink(redacted)
        changed = True
    if _encoded_size(redacted) > limit:
        redacted = {
            "_truncated": True,
            "note": f"payload exceeded {limit} bytes after redaction",
        }
        changed = True
    if changed:
        redacted["_truncated"] = True
    return redacted
