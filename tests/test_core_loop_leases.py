"""Lease keeper tests for the core loop.

The loop renews a task's lease while the executor/orchestrator is running and
reaps tasks whose workers died without heartbeating. These are the runtime half
of the lease contract; the storage half lives in
``tests/test_task_store_conformance.py``.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from sugar.core.loop import SugarLoop


def _bare_loop(heartbeat_interval: float = 0.01, lease_seconds: int = 60):
    """A SugarLoop with only the attributes ``_run_with_lease`` needs."""
    loop = SugarLoop.__new__(SugarLoop)
    loop.work_queue = AsyncMock()
    loop.work_queue.heartbeat = AsyncMock(return_value=True)
    loop.worker_id = "worker-1"
    loop.lease_seconds = lease_seconds
    loop.heartbeat_interval = heartbeat_interval
    return loop


def test_run_with_lease_renews_while_work_runs():
    loop = _bare_loop()

    async def work():
        await asyncio.sleep(0.06)
        return {"success": True}

    result = asyncio.run(loop._run_with_lease({"id": "task-1"}, work()))

    assert result == {"success": True}
    loop.work_queue.heartbeat.assert_awaited()
    _args, kwargs = loop.work_queue.heartbeat.await_args
    assert kwargs["worker_id"] == "worker-1"
    assert kwargs["lease_seconds"] == 60


def test_run_with_lease_stops_heartbeating_after_work_finishes():
    loop = _bare_loop()

    async def work():
        return {"success": True}

    asyncio.run(loop._run_with_lease({"id": "task-1"}, work()))
    calls_after_finish = loop.work_queue.heartbeat.await_count
    asyncio.run(asyncio.sleep(0.05))
    assert loop.work_queue.heartbeat.await_count == calls_after_finish


def test_run_with_lease_propagates_work_errors():
    loop = _bare_loop()

    async def failing_work():
        raise RuntimeError("executor exploded")

    with pytest.raises(RuntimeError, match="executor exploded"):
        asyncio.run(loop._run_with_lease({"id": "task-1"}, failing_work()))


def test_run_with_lease_stops_when_lease_is_lost():
    loop = _bare_loop(heartbeat_interval=0.01)
    loop.work_queue.heartbeat = AsyncMock(return_value=False)

    async def work():
        await asyncio.sleep(0.08)
        return {"success": True}

    # The keeper gives up after the first failed renewal; the work still finishes.
    assert asyncio.run(loop._run_with_lease({"id": "task-1"}, work())) == {
        "success": True
    }
    assert loop.work_queue.heartbeat.await_count == 1


def test_run_with_lease_survives_heartbeat_exceptions():
    loop = _bare_loop(heartbeat_interval=0.01)
    loop.work_queue.heartbeat = AsyncMock(side_effect=RuntimeError("db gone"))

    async def work():
        await asyncio.sleep(0.05)
        return {"success": True}

    # A broken heartbeat must not kill in-flight work.
    assert asyncio.run(loop._run_with_lease({"id": "task-1"}, work())) == {
        "success": True
    }


def test_requeue_expired_leases_returns_reaped_ids():
    loop = _bare_loop()
    loop.work_queue.requeue_expired_leases = AsyncMock(return_value=["task-1"])

    assert asyncio.run(loop._requeue_expired_leases()) == ["task-1"]
    loop.work_queue.requeue_expired_leases.assert_awaited_once()


def test_requeue_expired_leases_is_best_effort():
    loop = _bare_loop()
    loop.work_queue.requeue_expired_leases = AsyncMock(
        side_effect=RuntimeError("db locked")
    )

    assert asyncio.run(loop._requeue_expired_leases()) == []


def test_requeue_expired_leases_tolerates_stores_without_reaper():
    loop = _bare_loop()
    del loop.work_queue.requeue_expired_leases  # legacy/custom store
    assert asyncio.run(loop._requeue_expired_leases()) == []
