"""Tests for the ephemeral presence SSE heartbeat.

Contract: ``docs/local-dashboard.md`` §3 (SSE protocol) and §11.5. Presence is
derived from the in-memory snapshot only, is never persisted to the spine, and is
only emitted while there is at least one SSE subscriber.
"""

import asyncio
import contextlib
import json
import re
import sqlite3
import time

import pytest

from sugar.dashboard.app import create_app
from sugar.dashboard.events import (
    DEFAULT_PRESENCE_INTERVAL,
    DashboardState,
    EventBroadcaster,
    format_sse,
    presence_interval_from_env,
    validate_presence_interval,
)
from sugar.dashboard.spine import EventSpine
from sugar.storage.work_queue import WorkQueue

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeStore:
    """Minimal TaskStore stand-in for DashboardState unit tests."""

    def __init__(self, tasks=None, stats=None, version=1):
        self.tasks = tasks or []
        self.stats = stats or {"by_status": {}, "total": 0}
        self.version = version

    async def start(self):
        pass

    async def close(self):
        pass

    async def data_version(self):
        return self.version

    async def fetch_all(self):
        return list(self.tasks)

    async def fetch_stats(self):
        return dict(self.stats)


def make_state(presence_interval=DEFAULT_PRESENCE_INTERVAL, **store_kwargs):
    broadcast = EventBroadcaster()
    state = DashboardState(
        _FakeStore(**store_kwargs), broadcast, presence_interval=presence_interval
    )
    return state


def active_task(
    task_id,
    *,
    claimed_by="claude-code",
    created_by=None,
    lease="2026-11-09T20:10:00Z",
    started="2026-11-09T19:55:00Z",
    attempts=1,
    status="active",
    title="A task",
):
    return {
        "id": task_id,
        "title": title,
        "status": status,
        "claimed_by": claimed_by,
        "created_by": created_by,
        "lease_expires_at": lease,
        "started_at": started,
        "attempts": attempts,
    }


def parse_sse_events(text: str):
    events = []
    event_id = None
    for block in text.split("\n\n"):
        name = None
        data_lines = []
        block_id = None
        for line in block.split("\n"):
            if line.startswith("id: "):
                block_id = line[len("id: ") :]
            elif line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: ") :])
        if name:
            payload = json.loads("\n".join(data_lines)) if data_lines else None
            events.append((name, payload, block_id))
    return events


class SSEClient:
    """Drive the ASGI app directly so SSE chunks arrive without buffering.

    (Starlette's TestClient buffers complete responses, so an endless SSE stream
    cannot be read through it; this mirrors the existing dashboard tests.)
    """

    def __init__(self, app):
        self.app = app
        self.chunks = []
        self._disconnect = None
        self._task = None

    async def start(self):
        self._disconnect = asyncio.Event()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/events",
            "raw_path": b"/api/events",
            "query_string": b"",
            "headers": [(b"accept", b"text/event-stream")],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
        }

        async def receive():
            await self._disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                self.chunks.append(message.get("body", b""))

        self._task = asyncio.create_task(self.app(scope, receive, send))
        await self._wait_for(lambda: bool(self.chunks), timeout=5.0)

    def text(self):
        return b"".join(self.chunks).decode("utf-8")

    async def _wait_for(self, predicate, timeout=5.0):
        async def _poll():
            while not predicate():
                await asyncio.sleep(0.01)

        return await asyncio.wait_for(_poll(), timeout=timeout)

    async def wait_for_text(self, needle: str, timeout: float = 5.0) -> str:
        await self._wait_for(lambda: needle in self.text(), timeout=timeout)
        return self.text()

    async def close(self):
        if self._disconnect is not None:
            self._disconnect.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def seed_active_task(db_path: str) -> str:
    """Create a real active/claimed task row for integration tests."""
    queue = WorkQueue(db_path)
    await queue.initialize()
    task_id = await queue.add_work(
        {
            "type": "feature",
            "title": "Fix the flaky test",
            "priority": 2,
            "status": "pending",
            "source": "test",
            "context": {},
        }
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE work_items SET status = ?, claimed_by = ?, "
            "lease_expires_at = ?, started_at = ?, attempts = ? WHERE id = ?",
            (
                "active",
                "claude-code",
                "2026-11-09 20:10:00",
                "2026-11-09 19:55:00",
                2,
                task_id,
            ),
        )
        connection.commit()
    return task_id


# ---------------------------------------------------------------------------
# build_presence()
# ---------------------------------------------------------------------------


class TestBuildPresence:
    def test_shape_ordering_and_counts(self):
        state = make_state()
        state._tasks = {
            "a": active_task(
                "a",
                lease="2026-11-09T20:10:00Z",
                attempts=2,
                title="Fix the flaky test",
            ),
            "b": active_task(
                "b",
                claimed_by="dsh",
                lease="2026-11-09T20:05:00Z",
                attempts=1,
                title="Ship the feature",
            ),
            "c": active_task(
                "c",
                claimed_by=None,
                created_by="steve",
                lease=None,
                started=None,
                attempts=0,
                title="Claimed by a human",
            ),
            "d": {"id": "d", "title": "pending", "status": "pending"},
            "e": {"id": "e", "title": "held", "status": "hold"},
        }
        state._stats = {
            "by_status": {"pending": 1, "active": 3, "hold": 1},
            "total": 5,
        }
        state.started_at = time.monotonic() - 12.3

        payload = state.build_presence()

        assert set(payload) == {"ts", "agents", "counts", "uptime_s"}
        assert ISO_Z.match(payload["ts"])
        assert payload["counts"] == {
            "pending": 1,
            "active": 3,
            "hold": 1,
            "total": 5,
        }
        assert payload["uptime_s"] == pytest.approx(12.3, abs=0.5)

        agents = payload["agents"]
        # Lease expiry ascending, null last.
        assert [agent["task_id"] for agent in agents] == ["b", "a", "c"]
        for agent in agents:
            assert set(agent) == {
                "actor",
                "task_id",
                "title",
                "status",
                "lease_expires_at",
                "started_at",
                "attempts",
            }
            assert agent["status"] == "active"
        assert agents[0]["actor"] == "dsh"
        assert agents[1]["actor"] == "claude-code"
        assert agents[2]["actor"] == "steve"  # claimed_by null -> created_by
        assert agents[2]["lease_expires_at"] is None
        assert agents[2]["started_at"] is None
        assert agents[1]["lease_expires_at"] == "2026-11-09T20:10:00Z"
        assert agents[1]["started_at"] == "2026-11-09T19:55:00Z"
        assert agents[0]["attempts"] == 1
        assert agents[0]["title"] == "Ship the feature"

    def test_sqlite_timestamps_are_normalized(self):
        state = make_state()
        state._tasks = {
            "a": active_task(
                "a",
                lease="2026-11-09 20:10:00",
                started="2026-11-09 19:55:00",
            ),
            "b": active_task("b", lease="2026-11-09T20:05:00Z"),
        }
        agents = state.build_presence()["agents"]
        # Sorting uses the normalized value even when raw formats are mixed.
        assert [agent["task_id"] for agent in agents] == ["b", "a"]
        assert agents[1]["lease_expires_at"] == "2026-11-09T20:10:00Z"
        assert agents[1]["started_at"] == "2026-11-09T19:55:00Z"

    def test_before_start_is_empty(self):
        state = make_state()
        payload = state.build_presence()
        assert payload["agents"] == []
        assert payload["counts"] == {"total": 0}
        assert payload["uptime_s"] == 0.0
        assert ISO_Z.match(payload["ts"])

    def test_counts_total_falls_back_to_sum(self):
        state = make_state()
        state._stats = {"by_status": {"active": 2, "pending": 3}}
        payload = state.build_presence()
        assert payload["counts"]["total"] == 5

    def test_unknown_counts_fields_are_ignored(self):
        state = make_state()
        state._stats = {"by_status": {"active": 1, "bogus": "many"}, "total": 1}
        payload = state.build_presence()
        assert payload["counts"] == {"active": 1, "total": 1}


# ---------------------------------------------------------------------------
# publish_presence() / loop
# ---------------------------------------------------------------------------


class TestPresencePublishing:
    @pytest.mark.asyncio
    async def test_publish_without_subscribers_is_noop(self):
        state = make_state()
        state._tasks = {"a": active_task("a")}
        assert state.broadcaster.client_count == 0
        assert await state.publish_presence() is False
        assert state._presence_id == 0

    @pytest.mark.asyncio
    async def test_publish_with_subscriber(self):
        state = make_state()
        state._tasks = {"a": active_task("a")}
        state._stats = {"by_status": {"active": 1}, "total": 1}
        queue = state.broadcaster.subscribe()
        assert await state.publish_presence() is True
        event, payload, event_id = queue.get_nowait()
        assert event == "presence"
        assert event_id == 1
        assert payload["agents"][0]["task_id"] == "a"
        assert payload["counts"]["total"] == 1

        assert await state.publish_presence() is True
        _event, _payload, second_id = queue.get_nowait()
        assert second_id == 2

    @pytest.mark.asyncio
    async def test_loop_cadence_and_clean_stop(self):
        state = make_state(presence_interval=0.02)
        queue = state.broadcaster.subscribe()
        await state.start()
        try:
            first = await asyncio.wait_for(queue.get(), timeout=1.0)
            second = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert first[0] == second[0] == "presence"
            assert first[2] < second[2]

            await asyncio.sleep(0.05)
            await state.stop()
            assert state._presence_task is None
            assert state._watcher is None
            while not queue.empty():
                queue.get_nowait()
            await asyncio.sleep(0.06)
            assert queue.empty()  # cancelled loop emits nothing more
        finally:
            if state._presence_task is not None or state._watcher is not None:
                await state.stop()

    @pytest.mark.asyncio
    async def test_no_build_work_without_subscribers(self):
        state = make_state(presence_interval=0.02)
        calls = {"count": 0}
        original = state.build_presence

        def counting():
            calls["count"] += 1
            return original()

        state.build_presence = counting
        await state.start()
        try:
            await asyncio.sleep(0.1)
            assert calls["count"] == 0
            state.broadcaster.subscribe()
            await asyncio.sleep(0.1)
            assert calls["count"] >= 1
        finally:
            await state.stop()


# ---------------------------------------------------------------------------
# Interval configuration
# ---------------------------------------------------------------------------


class TestPresenceInterval:
    @pytest.mark.parametrize("bad", [0, -1, -0.01, float("nan"), float("inf")])
    def test_invalid_intervals_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_presence_interval(bad)

    @pytest.mark.parametrize("bad", [True, "2", None])
    def test_non_numeric_intervals_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_presence_interval(bad)

    def test_valid_interval(self):
        assert validate_presence_interval(0.05) == 0.05
        assert validate_presence_interval(2) == 2.0

    def test_env_interval(self, monkeypatch):
        monkeypatch.delenv("SUGAR_PRESENCE_INTERVAL", raising=False)
        assert presence_interval_from_env() == DEFAULT_PRESENCE_INTERVAL
        monkeypatch.setenv("SUGAR_PRESENCE_INTERVAL", "0.25")
        assert presence_interval_from_env() == 0.25
        for bad in ("0", "-2", "bogus", "nan", "inf", "  "):
            monkeypatch.setenv("SUGAR_PRESENCE_INTERVAL", bad)
            assert presence_interval_from_env() == DEFAULT_PRESENCE_INTERVAL

    def test_create_app_uses_interval(self, tmp_path, monkeypatch):
        explicit = create_app(str(tmp_path / "explicit.db"), presence_interval=0.25)
        assert explicit.state.dashboard.state.presence_interval == 0.25

        monkeypatch.setenv("SUGAR_PRESENCE_INTERVAL", "0.5")
        from_env = create_app(str(tmp_path / "env.db"))
        assert from_env.state.dashboard.state.presence_interval == 0.5

    def test_create_app_rejects_invalid_interval(self, tmp_path):
        for bad in (0, -1):
            with pytest.raises(ValueError):
                create_app(str(tmp_path / "bad.db"), presence_interval=bad)


# ---------------------------------------------------------------------------
# SSE integration + spine isolation
# ---------------------------------------------------------------------------


class TestPresenceSSE:
    @pytest.mark.asyncio
    async def test_hello_snapshot_then_presence(self, tmp_path):
        db_path = str(tmp_path / "sse.db")
        task_id = await seed_active_task(db_path)
        app = create_app(db_path, presence_interval=0.05)
        client = SSEClient(app)
        async with app.router.lifespan_context(app):
            await client.start()
            try:
                text = await client.wait_for_text("event: presence", timeout=2.0)
            finally:
                await client.close()

        events = parse_sse_events(text)
        names = [name for name, _payload, _event_id in events]
        assert names[:3] == ["hello", "snapshot", "presence"]
        presence_events = [event for event in events if event[0] == "presence"]
        payload = presence_events[0][1]
        assert set(payload) == {"ts", "agents", "counts", "uptime_s"}
        assert ISO_Z.match(payload["ts"])
        assert presence_events[0][2] == "1"  # SSE id: line, first heartbeat
        assert payload["agents"] == [
            {
                "actor": "claude-code",
                "task_id": task_id,
                "title": "Fix the flaky test",
                "status": "active",
                "lease_expires_at": "2026-11-09T20:10:00Z",
                "started_at": "2026-11-09T19:55:00Z",
                "attempts": 2,
            }
        ]
        assert payload["counts"]["active"] == 1
        assert payload["counts"]["total"] == 1
        assert isinstance(payload["uptime_s"], (int, float))
        assert payload["uptime_s"] >= 0

    @pytest.mark.asyncio
    async def test_presence_is_not_written_to_spine(self, tmp_path):
        db_path = str(tmp_path / "board.db")
        events_db = str(tmp_path / "events.db")
        app = create_app(db_path, events_db=events_db, presence_interval=0.02)
        async with app.router.lifespan_context(app):
            queue = app.state.dashboard.broadcaster.subscribe()
            first = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert first[0] == "presence"
            await asyncio.sleep(0.06)

        spine = EventSpine(events_db)
        await spine.start()
        try:
            events, _ = await spine.list_events()
            count = await spine.count()
        finally:
            await spine.close()
        assert events == []
        assert count == 0


def test_format_sse_with_id_prefix():
    assert (
        format_sse("presence", {"total": 0}, event_id=7)
        == 'id: 7\nevent: presence\ndata: {"total":0}\n\n'
    )
