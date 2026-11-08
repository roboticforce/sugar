"""Dashboard lease-reaper tests.

The dashboard owns a writable queue and an event spine, so it is also the place
that reclaims tasks whose workers stopped heartbeating — and the place that
records *why* they came back.
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

from sugar.dashboard.service import DashboardService
from sugar.dashboard.spine import EventSpine
from sugar.storage.work_queue import WorkQueue


def _force_expiry(db_path, task_id: str) -> None:
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE work_items SET status = 'active', lease_expires_at = ? "
            "WHERE id = ?",
            (past, task_id),
        )
        conn.commit()


def _seed_claimed_task(db_path) -> tuple:
    async def seed():
        queue = WorkQueue(str(db_path))
        await queue.initialize()
        task_id = await queue.add_work(
            {
                "type": "feature",
                "title": "crashed worker",
                "priority": 2,
                "status": "pending",
                "source": "test",
            }
        )
        claimed = await queue.get_next_work(worker_id="worker-1", lease_seconds=60)
        await queue.close()
        return task_id, claimed["lease_expires_at"]

    return asyncio.run(seed())


def test_reaper_requeues_expired_lease_and_emits_activity(tmp_path):
    db_path = tmp_path / "sugar.db"
    events_path = tmp_path / "events.db"
    task_id, _lease = _seed_claimed_task(db_path)
    _force_expiry(db_path, task_id)

    async def go():
        service = DashboardService(str(db_path), poll_interval=0.05)
        spine = EventSpine(events_path)
        await spine.start()
        service.spine = spine
        service.state.spine = spine
        await service.queue.initialize()
        await service.state.start()
        try:
            reaped = await service._reap_once()
            assert reaped == [task_id]

            queue = WorkQueue(str(db_path))
            await queue.initialize()
            task = await queue.get_work_item(task_id)
            await queue.close()
            assert task["status"] == "pending"
            assert task["claimed_by"] is None
            assert task["lease_expires_at"] is None

            events, _last_id = await spine.list_events(limit=20)
            reaper_events = [
                event for event in events if event["kind"] == "task.lease_expired"
            ]
            assert len(reaper_events) == 1
            event = reaper_events[0]
            assert event["task_id"] == task_id
            assert event["actor"] == "sugar-reaper"
            assert event["actor_kind"] == "system"
            assert event["payload"]["previous_status"] == "active"
            # The generic status-diff observer must not duplicate the event.
            assert not [event for event in events if event["kind"] == "task.updated"]
        finally:
            await service.state.stop()
            await spine.close()

    asyncio.run(go())


def test_reaper_does_not_run_on_read_only_services(tmp_path):
    db_path = tmp_path / "sugar.db"
    task_id, _lease = _seed_claimed_task(db_path)
    _force_expiry(db_path, task_id)

    async def go():
        service = DashboardService(str(db_path), read_only=True, poll_interval=0.05)
        await service.queue.initialize()
        assert await service._reap_once() == []
        await service.queue.close()

    asyncio.run(go())


def test_reaper_is_a_noop_when_nothing_expired(tmp_path):
    db_path = tmp_path / "sugar.db"
    _seed_claimed_task(db_path)  # claimed with a live 60s lease

    async def go():
        service = DashboardService(str(db_path), poll_interval=0.05)
        await service.queue.initialize()
        assert await service._reap_once() == []
        await service.queue.close()

    asyncio.run(go())
