"""Hermes Kanban board source for the Sugar local dashboard.

The dashboard can point at a shared Kanban board instead of Sugar's own work
queue (``sugar dashboard --board kanban``). Reads use a read-only SQLite
connection; writes shell out to ``hermes kanban`` so Hermes keeps its single
write path. Contract: ``docs/local-dashboard.md`` §9.
"""

from .paths import kanban_db_path

__all__ = ["kanban_db_path"]
