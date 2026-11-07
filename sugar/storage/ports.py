"""Storage ports for the Sugar coordination core.

This module is the seam between *coordination semantics* and a *storage engine*.
The core must not depend on SQLite (or Postgres) specifics: it depends on this
protocol, and each backend implements it.

The first implementation is :class:`sugar.storage.work_queue.WorkQueue` (SQLite,
local-first). The port exists so that:

- the SQLite implementation is exercised by a backend-agnostic conformance
  suite (``tests/test_task_store_conformance.py``), which protects the
  invariants the product depends on: single claimant, lease expiry, dependency
  ordering, provenance round-trip;
- a Postgres implementation can be added for self-hosted teams without
  rewriting the core;
- backend-specific concerns (transactions, change detection, migrations) stay
  on the storage side of the boundary.

Method signatures intentionally mirror ``WorkQueue`` for an incremental
extraction: an adapter type can be dropped in today without touching callers.
A future revision may introduce typed ``Task``/``Claim`` values, but the
invariants — not the dicts — are the contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from ..identity import DEFAULT_LEASE_SECONDS


@runtime_checkable
class TaskStore(Protocol):
    """The coordination core's storage contract.

    Implementations must be safe for concurrent callers *within one process*
    (atomic claim is the critical operation) and must persist:

    - the task state machine (``pending → active → completed/failed``, plus
      ``hold`` and release back to ``pending``);
    - provenance (``created_by*``) and the claim (``claimed_by``,
      ``lease_expires_at``);
    - dependency intent (``blocked_by``), which :meth:`get_next_work` must
      enforce;
    - attempt counts so retries and permanent failures are distinguishable.
    """

    async def initialize(self) -> None:
        """Create/migrate the backing store. Idempotent."""

    async def close(self) -> None:
        """Release resources held by the store."""

    async def add_work(self, work_item: Dict[str, Any]) -> str:
        """Insert a task and return its id. ``status`` defaults to ``pending``."""

    async def get_next_work(
        self,
        worker_id: Optional[str] = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> Optional[Dict[str, Any]]:
        """Atomically claim the highest-priority *claimable* task.

        "Claimable" means ``pending``, not on hold, and every id in
        ``blocked_by`` exists and is ``completed``. The claim records
        ``claimed_by`` and ``lease_expires_at`` in the same transaction and
        increments ``attempts``. Returns the claimed task, or ``None``.
        """

    async def heartbeat(
        self,
        task_id: str,
        worker_id: Optional[str] = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> bool:
        """Extend the lease of a task owned by *worker_id*. False if not owner/active."""

    async def requeue_expired_leases(self, now: Optional[datetime] = None) -> List[str]:
        """Return active tasks whose lease expired to ``pending`` and return their ids.

        Expired tasks keep their ``attempts`` count (each claim increments it),
        so a task that keeps dying is eventually visible as failing.
        """

    async def complete_work(self, work_id: str, result: Dict[str, Any]) -> None:
        """Mark a task completed, store the result and clear the claim."""

    async def fail_work(
        self,
        work_id: str,
        error_message: str,
        max_retries: int = 3,
        execution_time: float = 0.0,
    ) -> None:
        """Mark failed, or return to ``pending`` while under ``max_retries``."""

    async def hold_work(self, work_id: str, reason: Optional[str] = None) -> bool:
        """Park a task (status ``hold``) so it is not claimable. False if missing."""

    async def release_work(self, work_id: str) -> bool:
        """Return a held task to ``pending`` so it can be claimed again."""

    async def remove_work(self, work_id: str) -> bool:
        """Delete a task. False if it did not exist."""

    async def update_work(self, work_id: str, updates: Dict[str, Any]) -> bool:
        """Apply field updates from an allow-list. False if nothing changed."""

    async def get_work_item(self, work_id: str) -> Optional[Dict[str, Any]]:
        """Read one task (``blocked_by``/``context`` parsed as lists/dicts)."""

    async def get_recent_work(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Most recently created tasks, newest first."""

    async def get_stats(self) -> Dict[str, int]:
        """Counts by status plus ``total``."""
