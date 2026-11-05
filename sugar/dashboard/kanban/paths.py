"""Hermes Kanban board location rules.

Mirrors ``hermes_cli.kanban_db.kanban_db_path`` without importing Hermes:

- ``$HERMES_KANBAN_DB`` pins the file.
- ``default`` board -> ``<root>/kanban.db`` (back-compat).
- any other board  -> ``<root>/kanban/boards/<slug>/kanban.db``.
- board selection: explicit argument -> ``$HERMES_KANBAN_BOARD`` ->
  ``<root>/kanban/current`` -> ``default``.
- ``<root>`` is ``$HERMES_KANBAN_HOME`` or ``~/.hermes``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

DEFAULT_BOARD = "default"


def hermes_root() -> Path:
    """Hermes home directory (``HERMES_KANBAN_HOME`` wins)."""
    override = os.environ.get("HERMES_KANBAN_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hermes"


def current_board(root: Optional[Path] = None) -> str:
    """Active board slug, falling back to ``default`` on anything unusable."""
    env = os.environ.get("HERMES_KANBAN_BOARD", "").strip()
    if env and _valid_slug(env):
        return env
    try:
        marker = (root or hermes_root()) / "kanban" / "current"
        if marker.exists():
            candidate = marker.read_text(encoding="utf-8-sig").strip()
            if candidate and _valid_slug(candidate):
                return candidate
    except OSError:
        pass
    return DEFAULT_BOARD


def _valid_slug(value: str) -> bool:
    if not value or len(value) > 64:
        return False
    return all(char.isalnum() or char in "-_" for char in value)


def kanban_db_path(
    board: Optional[str] = None,
    *,
    explicit: Optional[str] = None,
    root: Optional[Path] = None,
) -> Path:
    """Resolve the board's ``kanban.db`` path."""
    if explicit:
        return Path(explicit).expanduser()

    pinned = os.environ.get("HERMES_KANBAN_DB", "").strip()
    if pinned and not board:
        return Path(pinned).expanduser()

    base = root or hermes_root()
    slug = board or current_board(base)
    if slug == DEFAULT_BOARD:
        return base / "kanban.db"
    return base / "kanban" / "boards" / slug / "kanban.db"
