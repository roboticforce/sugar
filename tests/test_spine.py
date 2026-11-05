"""Tests for the event spine (docs/local-dashboard.md §11).

Every test uses throwaway SQLite files; nothing touches ``~/.sugar/events.db``.
"""

import asyncio

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app
from sugar.dashboard.events import DashboardState, EventBroadcaster
from sugar.dashboard.observers import KanbanObserver, map_kanban_event
from sugar.dashboard.spine import (
    EventSpine,
    SpineError,
    build_event,
    default_events_db_path,
    retention_days_from_env,
)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# spine storage
# ---------------------------------------------------------------------------


class TestSpineStore:
    def _spine(self, tmp_path, **kwargs):
        return EventSpine(str(tmp_path / "events.db"), **kwargs)

    def test_append_and_list(self, tmp_path):
        async def go():
            spine = self._spine(tmp_path)
            await spine.start()
            first = await spine.append(
                {
                    "kind": "run.started",
                    "actor": "dsh",
                    "actor_kind": "agent",
                    "task_id": "t_1",
                    "payload": {"go": "x"},
                }
            )
            second = await spine.append({"kind": "task.created", "actor": "steve"})
            events, last = await spine.list_events()
            await spine.close()
            return first, second, events, last

        first, second, events, last = run(go())
        assert first["id"] == 1 and second["id"] == 2
        assert first["payload"] == {"go": "x"}
        assert [e["kind"] for e in events] == ["run.started", "task.created"]
        assert last == 2
        assert first["source"] == "ingest"
        assert first["machine"]  # filled from the host

    def test_list_filters_and_cursor(self, tmp_path):
        async def go():
            spine = self._spine(tmp_path)
            await spine.start()
            for kind, actor in (
                ("task.created", "steve"),
                ("task.created", "dsh"),
                ("run.started", "dsh"),
            ):
                await spine.append({"kind": kind, "actor": actor, "task_id": "t_9"})
            by_actor, _ = await spine.list_events(actor="dsh")
            by_kind, _ = await spine.list_events(kind="task.created")
            by_task, _ = await spine.list_events(task_id="t_9")
            page, cursor = await spine.list_events(since_id=1, limit=10)
            await spine.close()
            return by_actor, by_kind, by_task, page, cursor

        by_actor, by_kind, by_task, page, cursor = run(go())
        assert len(by_actor) == 2
        assert len(by_kind) == 2
        assert len(by_task) == 3
        assert [e["id"] for e in page] == [2, 3]
        assert cursor == 3

    def test_prune_removes_old_events(self, tmp_path):
        async def go():
            spine = self._spine(tmp_path, retention_days=1)
            await spine.start()
            await spine.append({"kind": "task.created", "ts": "2020-01-01T00:00:00Z"})
            await spine.append({"kind": "task.created"})
            removed = await spine.prune()
            remaining, _ = await spine.list_events()
            await spine.close()
            return removed, remaining

        removed, remaining = run(go())
        assert removed == 1
        assert len(remaining) == 1

    def test_retention_zero_disables_prune(self, tmp_path):
        async def go():
            spine = self._spine(tmp_path, retention_days=0)
            await spine.start()
            await spine.append({"kind": "x", "ts": "2020-01-01T00:00:00Z"})
            removed = await spine.prune()
            await spine.close()
            return removed

        assert run(go()) == 0

    def test_retention_env(self, monkeypatch):
        monkeypatch.delenv("SUGAR_EVENTS_RETENTION_DAYS", raising=False)
        assert retention_days_from_env() == 30
        monkeypatch.setenv("SUGAR_EVENTS_RETENTION_DAYS", "7")
        assert retention_days_from_env() == 7
        monkeypatch.setenv("SUGAR_EVENTS_RETENTION_DAYS", "bogus")
        assert retention_days_from_env() == 30

    def test_default_path_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SUGAR_EVENTS_DB", str(tmp_path / "custom.db"))
        assert default_events_db_path() == tmp_path / "custom.db"
        monkeypatch.delenv("SUGAR_EVENTS_DB")
        assert str(default_events_db_path()).endswith(".sugar/events.db")


class TestEventValidation:
    def test_kind_required(self):
        with pytest.raises(SpineError):
            build_event({})
        with pytest.raises(SpineError):
            build_event({"kind": "   "})

    def test_bad_actor_kind(self):
        with pytest.raises(SpineError):
            build_event({"kind": "task.created", "actor_kind": "robot"})

    def test_actor_kind_inferred(self):
        event = build_event(
            {"kind": "task.created", "actor": "dsh"}, default_actor_kind="agent"
        )
        assert event["actor_kind"] == "agent"

    def test_payload_must_be_object(self):
        with pytest.raises(SpineError):
            build_event({"kind": "x", "payload": [1, 2]})

    def test_payload_too_large(self):
        with pytest.raises(SpineError):
            build_event({"kind": "x", "payload": {"blob": "a" * 40_000}})

    def test_ts_normalized(self):
        assert build_event({"kind": "x", "ts": "2026-11-05T12:00:00Z"})["ts"] == (
            "2026-11-05T12:00:00Z"
        )
        assert build_event({"kind": "x", "ts": "nonsense"})["ts"].endswith("Z")

    def test_ids_trimmed_and_limited(self):
        event = build_event({"kind": "x", "actor": "  dsh  ", "task_id": "t" * 400})
        assert event["actor"] == "dsh"
        assert len(event["task_id"]) == 128  # identity id cap


# ---------------------------------------------------------------------------
# kanban observer mapping
# ---------------------------------------------------------------------------


class TestKanbanMapping:
    def _row(self, **overrides):
        row = {
            "id": 7,
            "task_id": "t_000001",
            "run_id": None,
            "kind": "created",
            "payload": '{"assignee": null}',
            "created_at": 1_790_000_000,
            "title": "Fix flaky test",
            "project_id": "sugar",
            "created_by": "dsh",
            "assignee": None,
            "status": "ready",
        }
        row.update(overrides)
        return row

    def test_created_maps_provenance(self):
        event = map_kanban_event(self._row())
        assert event["kind"] == "task.created"
        assert event["actor"] == "dsh"
        assert event["harness"] == "dsh"
        assert event["project"] == "sugar"
        assert event["task_id"] == "t_000001"
        assert event["payload"]["title"] == "Fix flaky test"
        assert event["ts"].endswith("Z")
        assert event["source"] == "observer:kanban"

    def test_claimed_uses_lock_machine(self):
        event = map_kanban_event(
            self._row(
                kind="claimed", payload='{"lock": "Mac:64813"}', assignee="claude"
            )
        )
        assert event["kind"] == "task.claimed"
        assert event["machine"] == "Mac"
        assert event["actor"] == "claude"

    def test_commented_uses_author(self):
        event = map_kanban_event(
            self._row(kind="commented", payload='{"author": "reviewer"}')
        )
        assert event["kind"] == "task.commented"
        assert event["actor"] == "reviewer"

    def test_unknown_kind_degrades(self):
        event = map_kanban_event(self._row(kind="promoted"))
        assert event["kind"] == "task.updated"
        assert event["payload"]["native_kind"] == "promoted"


class _FakeStore:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch_task_events_after(self, after_id, *, limit=200, newest_first=False):
        self.calls.append((after_id, newest_first))
        if newest_first:
            return list(reversed(self.rows))[:1]
        return [row for row in self.rows if row["id"] > after_id][:limit]


class TestKanbanObserver:
    def test_tails_new_rows_once(self, tmp_path):
        rows = [
            {
                "id": 1,
                "task_id": "t_1",
                "run_id": None,
                "kind": "created",
                "payload": "{}",
                "created_at": 1_790_000_000,
                "title": "one",
                "project_id": None,
                "created_by": "dsh",
                "assignee": None,
                "status": "ready",
            },
            {
                "id": 2,
                "task_id": "t_1",
                "run_id": None,
                "kind": "completed",
                "payload": "{}",
                "created_at": 1_790_000_010,
                "title": "one",
                "project_id": None,
                "created_by": "dsh",
                "assignee": "claude",
                "status": "done",
            },
        ]
        store = _FakeStore(rows)
        broadcaster = EventBroadcaster()
        queue = broadcaster.subscribe()

        async def go():
            spine = EventSpine(str(tmp_path / "e.db"))
            await spine.start()
            observer = KanbanObserver(store, spine, broadcaster)
            observer._watermark = 0
            await observer._drain()
            second = await observer._drain()  # nothing new
            events, _ = await spine.list_events()
            await spine.close()
            return events, second

        events, second = run(go())
        assert [e["kind"] for e in events] == ["task.created", "task.completed"]
        assert second is None
        assert queue.qsize() == 2  # both published to SSE
        first = queue.get_nowait()
        assert first[0] == "activity"


# ---------------------------------------------------------------------------
# sugar queue transitions
# ---------------------------------------------------------------------------


class _DiffStore:
    def __init__(self):
        self.tasks = []
        self.version = 0

    async def start(self):
        pass

    async def close(self):
        pass

    async def data_version(self):
        return self.version

    async def fetch_all(self):
        return [dict(task) for task in self.tasks]

    async def fetch_stats(self):
        return {
            "total": len(self.tasks),
            "by_status": {},
            "by_type": {},
            "recent_24h": 0,
        }


class TestSugarObserver:
    def test_emits_created_claimed_completed(self, tmp_path):
        async def go():
            spine = EventSpine(str(tmp_path / "e.db"))
            await spine.start()
            store = _DiffStore()
            broadcaster = EventBroadcaster()
            state = DashboardState(store, broadcaster, poll_interval=0.05)
            state.spine = spine

            await state.refresh(publish=False)  # initial snapshot: silent
            store.tasks = [
                {
                    "id": "w1",
                    "title": "queued work",
                    "status": "pending",
                    "created_by": "steve",
                    "created_by_kind": "human",
                    "claimed_by": None,
                    "source": "cli",
                }
            ]
            await state.refresh()
            store.tasks[0]["status"] = "active"
            store.tasks[0]["claimed_by"] = "sugar-worker@Mac"
            await state.refresh()
            store.tasks[0]["status"] = "completed"
            store.tasks[0]["claimed_by"] = None
            await state.refresh()
            events, _ = await spine.list_events()
            await spine.close()
            return events

        events = run(go())
        assert [e["kind"] for e in events] == [
            "task.created",
            "task.claimed",
            "task.completed",
        ]
        assert events[0]["actor"] == "steve"
        assert events[0]["actor_kind"] == "human"
        assert events[1]["actor"] == "sugar-worker@Mac"
        assert events[2]["actor"] == "sugar-worker@Mac"  # last claimer
        assert events[1]["source"] == "observer:sugar"
        assert events[1]["payload"]["previous_status"] == "pending"


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


def test_post_event_and_read_activity(tmp_path):
    db = str(tmp_path / "sugar.db")
    events_db = str(tmp_path / "events.db")
    app = create_app(db, events_db=events_db)
    with TestClient(app) as client:
        response = client.post(
            "/api/events",
            json={
                "kind": "run.started",
                "actor": "dsh",
                "actor_kind": "agent",
                "harness": "dsh",
                "task_id": "t_x",
                "payload": {"go": "x"},
            },
        )
        assert response.status_code == 201, response.text
        event = response.json()["event"]
        assert event["id"] == 1 and event["source"] == "ingest"

        listed = client.get("/api/activity").json()
        assert listed["last_id"] == 1
        assert listed["events"][0]["kind"] == "run.started"
        assert listed["events"][0]["payload"] == {"go": "x"}

        empty = client.get("/api/activity?kind=task.created").json()
        assert empty["events"] == []


def test_invalid_ingest_is_400(tmp_path):
    app = create_app(str(tmp_path / "sugar.db"), events_db=str(tmp_path / "e.db"))
    with TestClient(app) as client:
        assert client.post("/api/events", json={}).status_code == 400
        bad = client.post("/api/events", json={"kind": "x", "actor_kind": "robot"})
        assert bad.status_code == 400


def test_spine_disabled_is_503(tmp_path):
    app = create_app(str(tmp_path / "sugar.db"))  # no events_db
    with TestClient(app) as client:
        assert client.get("/api/activity").status_code == 503
        assert client.post("/api/events", json={"kind": "x"}).status_code == 503


def test_task_write_emits_activity(tmp_path):
    """A dashboard-created task becomes a task.created event via the observer."""
    db = str(tmp_path / "sugar.db")
    app = create_app(db, events_db=str(tmp_path / "e.db"))
    with TestClient(app) as client:
        created = client.post(
            "/api/tasks",
            json={"title": "observed task"},
            headers={"X-Sugar-Actor": "dsh", "X-Sugar-Actor-Kind": "agent"},
        )
        assert created.status_code == 201, created.text
        # The observer runs inside the dashboard; poll briefly for the event.
        import time

        for _ in range(40):
            events = client.get("/api/activity").json()["events"]
            if any(e["kind"] == "task.created" for e in events):
                break
            time.sleep(0.05)
        kinds = [e["kind"] for e in client.get("/api/activity").json()["events"]]
        assert "task.created" in kinds
