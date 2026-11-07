"""Backend-agnostic conformance suite for :class:`sugar.storage.ports.TaskStore`.

Every storage backend must pass every test in this module. To add a backend
(e.g. Postgres for self-hosted teams), append a factory to ``BACKENDS``; the
suite then runs against it automatically.

The invariants tested here are the product: a task is claimed by exactly one
worker, workers renew leases and lose ownership when reaped, dependencies gate
claims, provenance round-trips, and the state machine cannot be violated.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from sugar.identity import LEASE_TIMESTAMP_FORMAT, format_lease_timestamp
from sugar.storage.ports import TaskStore
from sugar.storage.work_queue import WorkQueue


def _sqlite_factory(db_path: str) -> TaskStore:
    return WorkQueue(db_path)


#: id -> factory(db_path) -> TaskStore. Add Postgres here when it exists.
BACKENDS = {"sqlite": _sqlite_factory}


@pytest.fixture(params=sorted(BACKENDS), ids=sorted(BACKENDS))
def store(request, tmp_path):
    backend = BACKENDS[request.param]
    queue = backend(str(tmp_path / "sugar.db"))
    asyncio.run(queue.initialize())
    try:
        yield queue
    finally:
        asyncio.run(queue.close())


def run(coro):
    return asyncio.run(coro)


def add(store: TaskStore, **overrides) -> str:
    item = {
        "type": "feature",
        "title": "conformance task",
        "priority": 2,
        "status": "pending",
        "source": "conformance",
        "context": {"k": "v"},
        "blocked_by": [],
        **overrides,
    }
    return run(store.add_work(item))


def parse_lease(value: str) -> datetime:
    return datetime.strptime(value, LEASE_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Contract shape
# --------------------------------------------------------------------------


def test_implements_task_store_protocol(store):
    assert isinstance(store, TaskStore)


# --------------------------------------------------------------------------
# Create / read / provenance
# --------------------------------------------------------------------------


def test_add_and_read_roundtrip(store):
    task_id = add(
        store,
        title="round trip",
        created_by="agent-a",
        created_by_kind="agent",
        created_by_session="session-1",
        context={"branch": "main"},
        blocked_by=[],
    )
    task = run(store.get_work_item(task_id))
    assert task is not None
    assert task["id"] == task_id
    assert task["status"] == "pending"
    assert task["claimed_by"] is None
    assert task["created_by"] == "agent-a"
    assert task["created_by_kind"] == "agent"
    assert task["created_by_session"] == "session-1"
    assert task["context"] == {"branch": "main"}
    assert task["blocked_by"] == []
    assert task["attempts"] == 0


# --------------------------------------------------------------------------
# Claim
# --------------------------------------------------------------------------


def test_claim_records_owner_and_lease(store):
    task_id = add(store, title="claim me")
    claimed = run(store.get_next_work(worker_id="worker-1", lease_seconds=120))
    assert claimed is not None
    assert claimed["id"] == task_id
    assert claimed["status"] == "active"
    assert claimed["claimed_by"] == "worker-1"
    assert claimed["attempts"] == 1
    assert parse_lease(claimed["lease_expires_at"]) > datetime.now(timezone.utc)

    stored = run(store.get_work_item(task_id))
    assert stored["status"] == "active"
    assert stored["claimed_by"] == "worker-1"
    assert stored["lease_expires_at"] == claimed["lease_expires_at"]

    # The only task is now active: a second claim finds nothing.
    assert run(store.get_next_work(worker_id="worker-2")) is None


def test_concurrent_claims_have_exactly_one_winner(store):
    task_id = add(store, title="contended")

    async def race():
        results = await asyncio.gather(
            *[store.get_next_work(worker_id=f"worker-{index}") for index in range(5)]
        )
        return [result for result in results if result is not None]

    winners = run(race())
    assert len(winners) == 1
    assert winners[0]["id"] == task_id
    stored = run(store.get_work_item(task_id))
    assert stored["claimed_by"] in {f"worker-{index}" for index in range(5)}


def test_completed_task_is_not_claimable(store):
    task_id = add(store, title="done")
    run(store.get_next_work(worker_id="worker-1"))
    run(store.complete_work(task_id, {"ok": True}))
    assert run(store.get_next_work(worker_id="worker-2")) is None
    assert run(store.get_work_item(task_id))["status"] == "completed"


# --------------------------------------------------------------------------
# Leases: heartbeat + reaper
# --------------------------------------------------------------------------


def test_heartbeat_extends_lease_only_for_owner(store):
    add(store, title="heartbeat")
    claimed = run(store.get_next_work(worker_id="worker-1", lease_seconds=60))
    first_lease = parse_lease(claimed["lease_expires_at"])

    assert (
        run(store.heartbeat(claimed["id"], worker_id="worker-2", lease_seconds=600))
        is False
    )
    unchanged = run(store.get_work_item(claimed["id"]))
    assert parse_lease(unchanged["lease_expires_at"]) == first_lease

    assert (
        run(store.heartbeat(claimed["id"], worker_id="worker-1", lease_seconds=600))
        is True
    )
    extended = run(store.get_work_item(claimed["id"]))
    assert parse_lease(extended["lease_expires_at"]) > first_lease
    assert extended["status"] == "active"
    assert extended["claimed_by"] == "worker-1"


def test_expired_lease_is_requeued_and_reclaimable(store):
    task_id = add(store, title="crashed worker")
    claimed = run(store.get_next_work(worker_id="worker-1", lease_seconds=60))
    assert claimed["id"] == task_id

    # Reap as if five minutes have passed: the lease is now expired.
    later = datetime.now(timezone.utc) + timedelta(seconds=300)
    requeued = run(store.requeue_expired_leases(now=later))
    assert requeued == [task_id]

    task = run(store.get_work_item(task_id))
    assert task["status"] == "pending"
    assert task["claimed_by"] is None
    assert task["lease_expires_at"] is None
    assert task["attempts"] == 1  # claims increment; the reaper does not reset

    # Reaping again is a no-op.
    assert run(store.requeue_expired_leases(now=later)) == []

    # The task is claimable again, and the attempt count keeps the history.
    reclaimed = run(store.get_next_work(worker_id="worker-2"))
    assert reclaimed["id"] == task_id
    assert reclaimed["attempts"] == 2


def test_heartbeat_keeps_task_out_of_reaper(store):
    add(store, title="alive")
    claimed = run(store.get_next_work(worker_id="worker-1", lease_seconds=60))
    run(store.heartbeat(claimed["id"], worker_id="worker-1", lease_seconds=600))
    assert run(store.requeue_expired_leases(now=datetime.now(timezone.utc))) == []


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------


def test_dependency_blocks_claim_until_blocker_completes(store):
    blocker_id = add(store, title="prerequisite", priority=1)
    dependent_id = add(store, title="dependent", priority=2, blocked_by=[blocker_id])

    first = run(store.get_next_work(worker_id="worker-1"))
    assert first["id"] == blocker_id
    # Only the dependent remains, and it is blocked.
    assert run(store.get_next_work(worker_id="worker-1")) is None
    assert run(store.get_work_item(dependent_id))["status"] == "pending"

    run(store.complete_work(blocker_id, {"ok": True}))
    second = run(store.get_next_work(worker_id="worker-1"))
    assert second["id"] == dependent_id


def test_blocked_task_does_not_starve_claimable_tasks(store):
    blocker_id = add(store, title="blocker", priority=1)
    add(store, title="blocked", priority=1, blocked_by=[blocker_id])
    free_id = add(store, title="free", priority=5)

    claimed = run(store.get_next_work(worker_id="worker-1"))
    assert claimed["id"] == blocker_id  # priority 1, unblocked
    run(store.complete_work(blocker_id, {"ok": True}))

    claimed = run(store.get_next_work(worker_id="worker-1"))
    # The formerly blocked task (priority 1) should now win over priority 5.
    assert claimed["title"] == "blocked"
    run(store.complete_work(claimed["id"], {"ok": True}))

    claimed = run(store.get_next_work(worker_id="worker-1"))
    assert claimed["id"] == free_id


def test_missing_blocker_keeps_task_blocked(store):
    add(store, title="orphan", blocked_by=["00000000-0000-0000-0000-000000000000"])
    assert run(store.get_next_work(worker_id="worker-1")) is None


def test_unreadable_blocker_fails_closed(store):
    add(store, title="bad json", blocked_by="not-json")
    assert run(store.get_next_work(worker_id="worker-1")) is None


# --------------------------------------------------------------------------
# Hold / release / fail state machine
# --------------------------------------------------------------------------


def test_hold_hides_task_and_release_restores_it(store):
    task_id = add(store, title="park me")
    assert run(store.hold_work(task_id, "waiting on design")) is True
    assert run(store.get_next_work(worker_id="worker-1")) is None
    assert run(store.get_work_item(task_id))["status"] == "hold"

    assert run(store.release_work(task_id)) is True
    claimed = run(store.get_next_work(worker_id="worker-1"))
    assert claimed["id"] == task_id


def test_failure_returns_to_pending_until_retry_limit(store):
    task_id = add(store, title="flaky")
    run(store.get_next_work(worker_id="worker-1"))
    run(store.fail_work(task_id, "boom", max_retries=3))
    assert run(store.get_work_item(task_id))["status"] == "pending"

    run(store.get_next_work(worker_id="worker-1"))  # attempt 2
    run(store.fail_work(task_id, "boom again", max_retries=2))
    task = run(store.get_work_item(task_id))
    assert task["status"] == "failed"
    assert task["claimed_by"] is None


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------


def test_stats_reflect_state(store):
    add(store, title="one")
    add(store, title="two")
    claimed = run(store.get_next_work(worker_id="worker-1"))
    run(store.complete_work(claimed["id"], {"ok": True}))

    stats = run(store.get_stats())
    assert stats["total"] == 2
    assert stats["completed"] == 1
    assert stats["pending"] == 1
