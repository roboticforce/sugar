"""Seed a realistic Sugar memory database for the sugar-memory skill eval.

Creates a .sugar/memory.db in the eval workspace with project context that an
agent needs to succeed on the eval cases: a stored decision, an error pattern,
a preference, a guideline, and a file-context entry.

Run from the skill directory:
    python3 seed_memory.py

Requires: sugarai installed with memory extra (or the repo on PYTHONPATH).
"""

import os
import sys
import uuid
from pathlib import Path

# Allow running from repo root when not installed
REPO_ROOT = Path(__file__).resolve().parents[3]
if (REPO_ROOT / "sugar").exists():
    sys.path.insert(0, str(REPO_ROOT))

from sugar.memory import MemoryEntry, MemoryType, MemoryStore  # noqa: E402
from sugar.memory.types import MemoryScope  # noqa: E402


def main():
    db_path = Path(__file__).resolve().parent / ".sugar" / "memory.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    store = MemoryStore(str(db_path))

    entries = [
        MemoryEntry(
            id=str(uuid.uuid4()),
            memory_type=MemoryType.DECISION,
            content=(
                "API layer uses the repository pattern; all list endpoints must use "
                "cursor pagination (cursor + limit params), never offset pagination. "
                "Chosen to keep query performance stable as the users table grows. "
                "See the UserRepository in app/repositories."
            ),
            summary="List endpoints use cursor pagination via repository pattern",
            metadata={"tags": ["api", "pagination", "repository"]},
            importance=0.9,
        ),
        MemoryEntry(
            id=str(uuid.uuid4()),
            memory_type=MemoryType.ERROR_PATTERN,
            content=(
                "SQLite 'database is locked' errors in CI happen because tests run "
                "in parallel on a single shared connection. Fix: serialize test "
                "database access with a global lock, or use a separate DB file per "
                "test worker. Recurring issue since March 2026."
            ),
            summary="SQLite 'database is locked' in CI -> serialize test DB access",
            metadata={"tags": ["ci", "sqlite", "testing"]},
            importance=0.95,
        ),
        MemoryEntry(
            id=str(uuid.uuid4()),
            memory_type=MemoryType.PREFERENCE,
            content=(
                "Code style: black + isort for formatting, mypy strict for typing. "
                "Use pathlib.Path for all path operations, never os.path. "
                "Async functions for I/O. No em dashes in any user-facing copy."
            ),
            summary="black, isort, mypy strict; pathlib; async I/O",
            metadata={"tags": ["style", "conventions"]},
            importance=0.85,
        ),
        MemoryEntry(
            id=str(uuid.uuid4()),
            memory_type=MemoryType.GUIDELINE,
            content=(
                "Dependency pinning policy: pin every dependency to an exact version "
                "in every project (npm save-exact, Ruby '= X.Y.Z', Python exact in "
                "requirements.txt). Never use range specifiers. Upgrades are "
                "intentional and go through PRs."
            ),
            summary="Pin all dependencies to exact versions, no ranges",
            metadata={"tags": ["dependencies", "policy"]},
            importance=0.9,
        ),
        MemoryEntry(
            id=str(uuid.uuid4()),
            memory_type=MemoryType.FILE_CONTEXT,
            content=(
                "app/services/notifications.py is the notification service. It sends "
                "email via SMTP and webhooks via the outbox pattern. The outbox table "
                "is drained by a background worker every 5 seconds. Tests for it live "
                "in tests/services/test_notifications.py."
            ),
            summary="notifications service uses outbox pattern, drained by worker",
            metadata={"tags": ["notifications", "file-context"]},
            importance=0.8,
        ),
        MemoryEntry(
            id=str(uuid.uuid4()),
            memory_type=MemoryType.OUTCOME,
            content=(
                "Switching from offset to cursor pagination on the users endpoint "
                "cut p95 latency from 400ms to 90ms at 50k rows and removed the "
                "page-skew bug on concurrent inserts. No regressions observed in "
                "the following release."
            ),
            summary="Cursor pagination cut p95 from 400ms to 90ms on users endpoint",
            metadata={"tags": ["performance", "pagination", "outcome"]},
            importance=0.8,
        ),
    ]

    for entry in entries:
        # Store under project scope in this project store
        entry_id = store.store(entry)
        print(f"Stored {entry.memory_type.value}: {entry_id[:8]}")

    # Also store a couple under global scope so get_project_context shows guidelines
    global_manager_path = Path(__file__).resolve().parent / ".sugar" / "global_memory.db"
    gstore = MemoryStore(str(global_manager_path))
    for entry in entries[:1]:
        gstore.store(entry)
    gstore.close()

    print(f"\nSeeded {len(entries)} project memories in {db_path}")
    print(f"Verification: {store.count()} entries total")
    store.close()


if __name__ == "__main__":
    main()