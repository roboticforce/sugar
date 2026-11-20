"""Discover DSH session logs on disk.

Layout (verified against real ``~/.dsh/sessions``)::

    ~/.dsh/sessions/<encoded-workspace>/<session-uuid>/session.v4.jsonl.zstd

The directory name encodes the workspace path (``--Users-me-Dev-app--``); the
session id is normally the UUID directory, but the log header is authoritative.
Discovery is read-only and tolerates missing/unreadable directories.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

SESSION_FILENAME = "session.v4.jsonl.zstd"
ENV_SESSIONS = "SUGAR_DSH_SESSIONS"
DEFAULT_SESSIONS = "~/.dsh/sessions"
FALLBACK_SESSIONS = "~/.deepseek/sessions"


@dataclass(frozen=True)
class DiscoveredSession:
    """One session log found on disk."""

    path: Path
    workspace: str
    workspace_dir: str
    session_id: str


def _home(home: Optional[Path] = None) -> Path:
    return Path(home).expanduser() if home is not None else Path.home()


def default_sessions_dir(home: Optional[Path] = None) -> Path:
    """Resolve the sessions directory (env override, else DSH, else fallback)."""
    override = os.environ.get(ENV_SESSIONS, "").strip()
    if override:
        return Path(override).expanduser()
    base = _home(home)
    primary = base / ".dsh" / "sessions"
    if primary.exists():
        return primary
    fallback = base / ".deepseek" / "sessions"
    if fallback.exists():
        return fallback
    return primary


def decode_workspace(encoded: str) -> str:
    """Best-effort decode of DSH's ``--Users-me-Dev-app--`` workspace encoding."""
    text = str(encoded or "").strip()
    if not text:
        return ""
    if text.startswith("--") and text.endswith("--"):
        inner = text[2:-2]
        # "/" and "-" are both encoded as "-"; this cannot be reversed exactly,
        # so return a readable label. The session header's `cwd` is authoritative.
        return "/" + inner.replace("-", "/")
    return text


def _session_dirs(sessions_dir: Path) -> List[Path]:
    """Return candidate ``*/<session>/`` directories, tolerating depth variants."""
    if not sessions_dir.exists() or not sessions_dir.is_dir():
        return []
    try:
        entries = list(sessions_dir.iterdir())
    except OSError:
        return []
    found: List[Path] = []
    for entry in entries:
        if not entry.is_dir():
            continue
        if (entry / SESSION_FILENAME).is_file():
            found.append(entry)
            continue
        try:
            children = list(entry.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and (child / SESSION_FILENAME).is_file():
                found.append(child)
    return found


def discover_sessions(
    sessions_dir: Optional[Path] = None, *, home: Optional[Path] = None
) -> List[DiscoveredSession]:
    """Return every session log under *sessions_dir* (sorted by path)."""
    directory = (
        Path(sessions_dir).expanduser()
        if sessions_dir is not None
        else default_sessions_dir(home=home)
    )
    discovered: List[DiscoveredSession] = []
    for session_dir in sorted(_session_dirs(directory)):
        path = session_dir / SESSION_FILENAME
        workspace_dir = session_dir.parent.name
        discovered.append(
            DiscoveredSession(
                path=path,
                workspace=decode_workspace(workspace_dir),
                workspace_dir=workspace_dir,
                session_id=session_dir.name,
            )
        )
    return discovered
