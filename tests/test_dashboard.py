"""Tests for the local dashboard backend (docs/local-dashboard.md).

Every test uses a throwaway SQLite database under ``tmp_path``; no test ever
touches the repository's ``.sugar/sugar.db``.
"""

import asyncio
import contextlib
import json
import re
import sqlite3

import pytest
from starlette.testclient import TestClient

from sugar.dashboard import create_app
from sugar.dashboard.serializer import iso_utc_z, serialize_task
from sugar.identity import resolve_actor
from sugar.storage.work_queue import WorkQueue

ISO_Z_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    """A throwaway database path (never the project's .sugar/sugar.db)."""
    return str(tmp_path / "sugar.db")


@pytest.fixture
def client(db_path):
    app = create_app(db_path)
    with TestClient(app) as test_client:
        yield test_client


def seed(db_path: str, **overrides):
    """Insert a task through WorkQueue from a separate connection.

    This simulates another process (``sugar add`` / MCP server / ``sugar run``)
    writing to the same database while the dashboard is running.
    """

    async def _seed():
        queue = WorkQueue(db_path)
        await queue.initialize()
        payload = {
            "type": "feature",
            "title": "seeded task",
            "priority": 3,
            "status": "pending",
            "source": "test",
        }
        payload.update(overrides)
        return await queue.add_work(payload)

    return asyncio.run(_seed())


def assert_iso_z(value):
    assert value is not None, "timestamp should not be null"
    assert ISO_Z_PATTERN.match(value), f"not ISO-8601 UTC with Z: {value!r}"


def create_task(client, **overrides):
    body = {"title": "dashboard test task", "type": "feature"}
    body.update(overrides)
    response = client.post("/api/tasks", json=body)
    assert response.status_code == 201, response.text
    return response.json()["task"]


def parse_sse_events(text: str):
    events = []
    for block in text.split("\n\n"):
        name = None
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: ") :]  # noqa: E203 (black style)
            elif line.startswith("data: "):
                data_lines.append(line[len("data: ") :])  # noqa: E203
        if name:
            payload = json.loads("\n".join(data_lines)) if data_lines else None
            events.append((name, payload))
    return events


class SSEClient:
    """Drive the ASGI app directly so SSE chunks arrive without buffering."""

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
        await self._wait_for_chunk(timeout=5.0)

    def text(self):
        return b"".join(self.chunks).decode("utf-8")

    async def _wait_for_chunk(self, timeout):
        async def _poll():
            while not self.chunks:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_poll(), timeout=timeout)

    async def wait_for(self, needle: str, timeout: float = 5.0) -> str:
        async def _poll():
            while needle not in self.text():
                await asyncio.sleep(0.01)
            return self.text()

        return await asyncio.wait_for(_poll(), timeout=timeout)

    async def close(self):
        if self._disconnect is not None:
            self._disconnect.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._task = None


@contextlib.asynccontextmanager
async def running_sse_app(tmp_path, read_only=False):
    db_path = str(tmp_path / "sse.db")
    app = create_app(db_path, read_only=read_only)
    async with app.router.lifespan_context(app):
        client = SSEClient(app)
        await client.start()
        try:
            yield app, db_path, client
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# Health / task types / stats
# ---------------------------------------------------------------------------


def test_health(client, db_path):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["db"].endswith("sugar.db")
    assert data["read_only"] is False
    assert isinstance(data["uptime_s"], (int, float))
    assert data["version"]


def test_task_types_endpoint(client):
    response = client.get("/api/task-types")
    assert response.status_code == 200
    types = response.json()["types"]
    assert types, "fresh databases get default task types"
    ids = {task_type["id"] for task_type in types}
    assert "feature" in ids
    for task_type in types:
        assert set(task_type) == {"id", "name", "description"}


def test_stats_shape(client, db_path):
    seed(db_path, title="one", type="feature", status="pending")
    seed(db_path, title="two", type="chore", status="completed")

    response = client.get("/api/stats")
    assert response.status_code == 200
    stats = response.json()
    assert stats["total"] == 2
    assert stats["by_status"]["pending"] == 1
    assert stats["by_status"]["completed"] == 1
    assert stats["by_status"]["failed"] == 0
    assert stats["by_type"]["feature"] == 1
    assert stats["by_type"]["chore"] == 1
    assert stats["recent_24h"] == 2


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_get_list_update_delete(client):
    task = create_task(
        client,
        title="Add retry to the GitHub client",
        description="Long form text.",
        type="feature",
        priority=2,
        assigned_agent="tech-lead",
        acceptance_criteria=[{"description": "tests pass"}],
        blocked_by=[],
    )

    assert task["status"] == "pending"
    assert task["priority"] == 2
    assert task["source"] == "dashboard"
    assert task["type"] == "feature"
    assert task["assigned_agent"] == "tech-lead"
    assert task["acceptance_criteria"] == [{"description": "tests pass"}]
    assert task["blocked_by"] == []
    assert task["attempts"] == 0
    assert isinstance(task["context"], dict)
    assert task["result"] == {}
    assert_iso_z(task["created_at"])
    assert_iso_z(task["updated_at"])

    fetched = client.get(f"/api/tasks/{task['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["task"]["id"] == task["id"]

    listing = client.get("/api/tasks").json()
    assert listing["count"] == 1
    assert [t["id"] for t in listing["tasks"]] == [task["id"]]

    patched = client.patch(
        f"/api/tasks/{task['id']}",
        json={
            "title": "Add retry with backoff",
            "description": "Updated",
            "type": "bug_fix",
            "priority": 1,
            "assigned_agent": None,
            "context": {"note": "edited"},
            "acceptance_criteria": [{"description": "retries twice"}],
        },
    )
    assert patched.status_code == 200
    updated = patched.json()["task"]
    assert updated["title"] == "Add retry with backoff"
    assert updated["type"] == "bug_fix"
    assert updated["priority"] == 1
    assert updated["assigned_agent"] is None
    assert updated["context"] == {"note": "edited"}
    assert updated["acceptance_criteria"] == [{"description": "retries twice"}]
    assert_iso_z(updated["updated_at"])

    deleted = client.delete(f"/api/tasks/{task['id']}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": task["id"]}
    assert client.get(f"/api/tasks/{task['id']}").status_code == 404
    assert client.get("/api/stats").json()["total"] == 0


def test_status_transitions(client, db_path):
    task = create_task(client)
    task_id = task["id"]

    hold = client.patch(
        f"/api/tasks/{task_id}", json={"status": "hold", "reason": "waiting"}
    )
    assert hold.status_code == 200
    held = hold.json()["task"]
    assert held["status"] == "hold"
    assert held["context"]["hold_reason"] == "waiting"

    release = client.patch(f"/api/tasks/{task_id}", json={"status": "pending"})
    assert release.status_code == 200
    released = release.json()["task"]
    assert released["status"] == "pending"
    assert "hold_reason" not in released["context"]
    assert "released_at" in released["context"]

    active = client.patch(f"/api/tasks/{task_id}", json={"status": "active"})
    assert active.status_code == 200
    assert active.json()["task"]["status"] == "active"

    completed = client.patch(
        f"/api/tasks/{task_id}",
        json={"status": "completed", "result": {"output": "done"}},
    )
    assert completed.status_code == 200
    done = completed.json()["task"]
    assert done["status"] == "completed"
    assert done["result"] == {"output": "done"}
    assert_iso_z(done["completed_at"])

    # WorkQueue.fail_work retries tasks that have not exhausted max_retries,
    # so an early failure comes back as 'pending' with the error recorded.
    retried = client.patch(
        f"/api/tasks/{task_id}",
        json={"status": "failed", "error_message": "boom"},
    )
    assert retried.status_code == 200
    retried_task = retried.json()["task"]
    assert retried_task["status"] == "pending"
    assert retried_task["error_message"] == "boom"

    # Once attempts are exhausted the same transition becomes a terminal failure.
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE work_items SET status = 'active', attempts = 3 WHERE id = ?",
            (task_id,),
        )
        connection.commit()
    failed = client.patch(
        f"/api/tasks/{task_id}",
        json={"status": "failed", "error_message": "boom"},
    )
    assert failed.status_code == 200
    failed_task = failed.json()["task"]
    assert failed_task["status"] == "failed"
    assert failed_task["error_message"] == "boom"


def test_hold_and_release_endpoints(client):
    task = create_task(client)
    task_id = task["id"]

    held = client.post(f"/api/tasks/{task_id}/hold", json={"reason": "later"})
    assert held.status_code == 200
    assert held.json()["task"]["status"] == "hold"
    assert held.json()["task"]["context"]["hold_reason"] == "later"

    released = client.post(f"/api/tasks/{task_id}/release")
    assert released.status_code == 200
    assert released.json()["task"]["status"] == "pending"

    not_held = client.post(f"/api/tasks/{task_id}/release")
    assert not_held.status_code == 400
    assert not_held.json()["error"] == "validation_error"


def test_patch_bumps_updated_at(client, db_path):
    task = create_task(client)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE work_items SET updated_at = '2020-01-01 00:00:00' WHERE id = ?",
            (task["id"],),
        )
        connection.commit()

    patched = client.patch(f"/api/tasks/{task['id']}", json={"title": "bumped"})
    assert patched.status_code == 200
    new_updated_at = patched.json()["task"]["updated_at"]
    assert_iso_z(new_updated_at)
    assert new_updated_at.startswith("20")
    assert not new_updated_at.startswith("2020")


# ---------------------------------------------------------------------------
# Validation / errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"title": "   "},
        {"title": 42},
        {"title": "x", "priority": 0},
        {"title": "x", "priority": 6},
        {"title": "x", "priority": "high"},
        {"title": "x", "type": "not_a_real_type"},
        {"title": "x", "status": "completed"},
        {"title": "x", "bogus_field": True},
        {"title": "x", "blocked_by": "abc"},
    ],
)
def test_create_validation_errors(client, body):
    response = client.post("/api/tasks", json=body)
    assert response.status_code == 400, response.text
    payload = response.json()
    assert payload["error"] == "validation_error"
    assert payload.get("detail")


def test_invalid_json_body(client):
    response = client.post(
        "/api/tasks", content="{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "validation_error"


def test_patch_validation_errors(client):
    task = create_task(client)
    task_id = task["id"]
    for body in (
        {},
        {"nope": 1},
        {"title": ""},
        {"priority": 9},
        {"status": "exploded"},
        {"result": {"a": 1}},
    ):
        response = client.patch(f"/api/tasks/{task_id}", json=body)
        assert response.status_code == 400, (body, response.text)
        assert response.json()["error"] == "validation_error"


def test_not_found(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/tasks/{missing}").json() == {"error": "not_found"}
    assert client.get(f"/api/tasks/{missing}").status_code == 404
    assert client.patch(f"/api/tasks/{missing}", json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/tasks/{missing}").status_code == 404
    assert client.post(f"/api/tasks/{missing}/hold", json={}).status_code == 404
    assert client.post(f"/api/tasks/{missing}/release").status_code == 404


def test_read_only_forbidden(tmp_path):
    db_path = str(tmp_path / "read-only.db")
    app = create_app(db_path, read_only=True)
    with TestClient(app) as read_only_client:
        health = read_only_client.get("/api/health").json()
        assert health["read_only"] is True
        assert read_only_client.get("/api/tasks").status_code == 200

        created = read_only_client.post("/api/tasks", json={"title": "nope"})
        assert created.status_code == 403
        assert created.json() == {"error": "read_only"}

        seeded_id = seed(db_path, title="existing")
        assert (
            read_only_client.patch(
                f"/api/tasks/{seeded_id}", json={"title": "nope"}
            ).status_code
            == 403
        )
        assert read_only_client.delete(f"/api/tasks/{seeded_id}").status_code == 403
        assert (
            read_only_client.post(f"/api/tasks/{seeded_id}/hold", json={}).status_code
            == 403
        )
        assert read_only_client.get(f"/api/tasks/{seeded_id}").status_code == 200


def test_observed_task_types_are_editable(tmp_path):
    """A WorkQueue-first DB only seeds 5 types; observed row types stay valid."""
    db_path = str(tmp_path / "legacy-types.db")
    task_id = seed(db_path, title="perf work", type="perf")
    app = create_app(db_path)
    with TestClient(app) as legacy_client:
        ids = {
            task_type["id"]
            for task_type in legacy_client.get("/api/task-types").json()["types"]
        }
        assert {"bug_fix", "feature"} <= ids
        assert "perf" in ids
        assert "chore" not in ids

        edited = legacy_client.patch(f"/api/tasks/{task_id}", json={"type": "perf"})
        assert edited.status_code == 200, edited.text

        created = legacy_client.post(
            "/api/tasks", json={"title": "new perf task", "type": "perf"}
        )
        assert created.status_code == 201, created.text

        invalid = legacy_client.post(
            "/api/tasks", json={"title": "nope", "type": "not_a_real_type"}
        )
        assert invalid.status_code == 400


# ---------------------------------------------------------------------------
# Provenance (docs/local-dashboard.md §10)
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_actor_env(monkeypatch):
    """Neutralize actor env vars so the default is deterministic."""
    for name in (
        "SUGAR_ACTOR",
        "SUGAR_ACTOR_KIND",
        "SUGAR_AGENT_ID",
        "SUGAR_SESSION",
    ):
        monkeypatch.delenv(name, raising=False)


def test_create_actor_defaults_to_resolved_actor(client, clean_actor_env):
    expected = resolve_actor()
    task = create_task(client, title="default provenance")
    assert task["created_by"] == expected.id
    assert task["created_by_kind"] == expected.kind
    assert task["created_by_session"] is None
    # No worker has claimed it yet, so lease/claimant stay null.
    assert task["claimed_by"] is None
    assert task["lease_expires_at"] is None


def test_create_actor_headers_win_over_body(client):
    response = client.post(
        "/api/tasks",
        json={
            "title": "header wins",
            "created_by": "body-actor",
            "created_by_kind": "human",
        },
        headers={
            "X-Sugar-Actor": "  header-agent  ",
            "X-Sugar-Actor-Kind": "agent",
        },
    )
    assert response.status_code == 201, response.text
    task = response.json()["task"]
    assert task["created_by"] == "header-agent"
    assert task["created_by_kind"] == "agent"


def test_create_actor_headers_and_body_merge_per_field(client):
    response = client.post(
        "/api/tasks",
        json={
            "title": "merged",
            "created_by": "body-actor",
            "created_by_kind": "human",
        },
        headers={"X-Sugar-Actor-Kind": "system"},
    )
    assert response.status_code == 201, response.text
    task = response.json()["task"]
    # The id falls through to the body; the kind header still wins.
    assert task["created_by"] == "body-actor"
    assert task["created_by_kind"] == "system"


def test_create_actor_body_fallback(client):
    task = create_task(
        client,
        title="body actor",
        created_by="  alice  ",
        created_by_kind=" agent ",
    )
    assert task["created_by"] == "alice"
    assert task["created_by_kind"] == "agent"


def test_create_actor_ids_are_trimmed_and_limited(client):
    long_id = "a" * 200
    response = client.post(
        "/api/tasks",
        json={"title": "long id"},
        headers={"X-Sugar-Actor": long_id},
    )
    assert response.status_code == 201, response.text
    assert response.json()["task"]["created_by"] == "a" * 128

    trimmed = create_task(client, title="trim", created_by="  spaced-actor  ")
    assert trimmed["created_by"] == "spaced-actor"


def test_create_actor_unknown_body_kind_is_400(client):
    response = client.post(
        "/api/tasks", json={"title": "x", "created_by_kind": "robot"}
    )
    assert response.status_code == 400, response.text
    payload = response.json()
    assert payload["error"] == "validation_error"
    assert "created_by_kind" in payload["detail"]


def test_create_actor_unknown_header_kind_is_400(client):
    response = client.post(
        "/api/tasks",
        json={"title": "x"},
        headers={"X-Sugar-Actor-Kind": "robot"},
    )
    assert response.status_code == 400, response.text
    payload = response.json()
    assert payload["error"] == "validation_error"
    assert "X-Sugar-Actor-Kind" in payload["detail"]


def test_created_by_filter(client):
    alice = client.post(
        "/api/tasks", json={"title": "alice one"}, headers={"X-Sugar-Actor": "alice"}
    ).json()["task"]
    client.post(
        "/api/tasks", json={"title": "bob one"}, headers={"X-Sugar-Actor": "bob"}
    )
    alice_body = create_task(client, title="alice two", created_by="alice")

    filtered = client.get("/api/tasks", params={"created_by": "alice"}).json()
    assert filtered["count"] == 2
    assert {task["id"] for task in filtered["tasks"]} == {alice["id"], alice_body["id"]}

    # Query strings are normalized like body/header ids.
    trimmed = client.get("/api/tasks", params={"created_by": "  alice  "}).json()
    assert trimmed["count"] == 2

    assert (
        client.get("/api/tasks", params={"created_by": "nobody"}).json()["count"] == 0
    )
    combined = client.get(
        "/api/tasks", params={"created_by": "alice", "status": "completed"}
    ).json()
    assert combined["count"] == 0


def test_task_json_provenance_fields(client, db_path):
    task = create_task(client)
    for field in (
        "created_by",
        "created_by_kind",
        "created_by_session",
        "claimed_by",
        "lease_expires_at",
    ):
        assert field in task

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE work_items SET claimed_by = ?, lease_expires_at = ? WHERE id = ?",
            ("sugar-worker@host", "2026-11-05 16:12:44", task["id"]),
        )
        connection.commit()

    fetched = client.get(f"/api/tasks/{task['id']}").json()["task"]
    assert fetched["claimed_by"] == "sugar-worker@host"
    assert fetched["lease_expires_at"] == "2026-11-05T16:12:44Z"
    listing = client.get("/api/tasks").json()["tasks"][0]
    assert listing["created_by"] == task["created_by"]
    assert listing["lease_expires_at"] == "2026-11-05T16:12:44Z"


# ---------------------------------------------------------------------------
# Filtering / pagination
# ---------------------------------------------------------------------------


def test_filters_and_pagination(client, db_path):
    feature_id = seed(db_path, title="needle one", type="feature", priority=1)
    seed(db_path, title="needle two", type="bug_fix", priority=2)
    seed(db_path, title="other", type="feature", priority=3, status="completed")

    by_status = client.get("/api/tasks", params={"status": "pending"}).json()
    assert by_status["count"] == 2

    by_type = client.get("/api/tasks", params={"type": "bug_fix"}).json()
    assert by_type["count"] == 1

    by_priority = client.get("/api/tasks", params={"priority": 1}).json()
    assert by_priority["count"] == 1
    assert by_priority["tasks"][0]["id"] == feature_id

    by_query = client.get("/api/tasks", params={"q": "needle"}).json()
    assert by_query["count"] == 2

    page = client.get("/api/tasks", params={"limit": 1, "offset": 1}).json()
    assert page["count"] == 3  # total matching, not page size
    assert len(page["tasks"]) == 1

    ordered = client.get("/api/tasks", params={"order": "priority"}).json()
    assert [task["priority"] for task in ordered["tasks"]] == [1, 2, 3]

    bad_order = client.get("/api/tasks", params={"order": "nope"})
    assert bad_order.status_code == 400
    assert client.get("/api/tasks", params={"limit": 0}).status_code == 400
    assert client.get("/api/tasks", params={"priority": 9}).status_code == 400


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------


def test_iso_utc_conversion():
    assert iso_utc_z("2026-11-05 16:12:44") == "2026-11-05T16:12:44Z"
    assert iso_utc_z("2026-11-05T16:12:44Z") == "2026-11-05T16:12:44Z"
    assert iso_utc_z("2026-11-05T16:12:44.123456+00:00") == "2026-11-05T16:12:44Z"
    assert iso_utc_z(None) is None
    assert iso_utc_z("") is None
    assert iso_utc_z("not a timestamp") is None


def test_serializer_defaults_for_empty_row():
    task = serialize_task({})
    assert task["blocked_by"] == []
    assert task["context"] == {}
    assert task["result"] == {}
    assert task["acceptance_criteria"] is None
    assert task["priority"] == 3
    assert task["attempts"] == 0
    assert task["total_execution_time"] == 0.0
    assert task["created_at"] is None


def test_serializer_json_columns_and_timestamps(client, db_path):
    task_id = seed(
        db_path,
        title="serializer",
        context={"a": 1},
        acceptance_criteria=[{"description": "tests pass"}],
        blocked_by=["00000000-0000-0000-0000-000000000001"],
    )
    task = client.get(f"/api/tasks/{task_id}").json()["task"]
    assert task["context"] == {"a": 1}
    assert task["acceptance_criteria"] == [{"description": "tests pass"}]
    assert task["blocked_by"] == ["00000000-0000-0000-0000-000000000001"]
    for field in ("created_at", "updated_at"):
        assert_iso_z(task[field])
    assert task["started_at"] is None
    assert task["completed_at"] is None


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_hello_and_snapshot(tmp_path):
    async with running_sse_app(tmp_path) as (app, _db_path, sse):
        text = await sse.wait_for("event: snapshot")
        events = parse_sse_events(text)
        names = [name for name, _payload in events]
        assert names[0] == "hello"
        assert names[1] == "snapshot"
        assert events[0][1] == {
            "version": events[0][1]["version"],
            "server": "sugar-dashboard",
        }
        snapshot = events[1][1]
        assert snapshot["tasks"] == []
        assert snapshot["stats"]["total"] == 0
        assert "by_status" in snapshot["stats"]
        assert "by_type" in snapshot["stats"]


@pytest.mark.asyncio
async def test_sse_streams_external_write_and_delete(tmp_path):
    async with running_sse_app(tmp_path) as (app, db_path, sse):
        await sse.wait_for("event: snapshot")

        queue = WorkQueue(db_path)
        await queue.initialize()
        task_id = await queue.add_work(
            {
                "type": "feature",
                "title": "from another process",
                "priority": 2,
                "status": "pending",
                "source": "test",
            }
        )

        text = await sse.wait_for("event: upsert", timeout=5.0)
        events = parse_sse_events(text)
        upserts = [payload for name, payload in events if name == "upsert"]
        assert upserts, "external WorkQueue write must surface as an SSE upsert"
        assert upserts[-1]["task"]["id"] == task_id
        assert upserts[-1]["task"]["title"] == "from another process"
        assert any(name == "stats" for name, _payload in events)

        await queue.remove_work(task_id)
        text = await sse.wait_for("event: delete", timeout=5.0)
        deletes = [
            payload for name, payload in parse_sse_events(text) if name == "delete"
        ]
        assert deletes[-1] == {"id": task_id}


def test_unknown_statuses_pass_through(client, db_path):
    seeded_id = seed(db_path, title="weird", status="quarantined")
    task = client.get(f"/api/tasks/{seeded_id}").json()["task"]
    assert task["status"] == "quarantined"
    stats = client.get("/api/stats").json()
    assert stats["by_status"]["quarantined"] == 1
    assert stats["total"] == 1


def test_broadcaster_drops_oldest_for_slow_clients():
    from sugar.dashboard.events import EventBroadcaster, format_sse

    broadcaster = EventBroadcaster(queue_size=2)
    queue = broadcaster.subscribe()
    assert broadcaster.client_count == 1
    for number in (1, 2, 3):
        broadcaster.publish("upsert", {"n": number})
    assert queue.qsize() == 2
    assert queue.get_nowait()[1] == {"n": 2}
    assert queue.get_nowait()[1] == {"n": 3}

    broadcaster.unsubscribe(queue)
    assert broadcaster.client_count == 0
    broadcaster.publish("upsert", {"n": 4})
    assert queue.empty()

    assert format_sse("stats", {"total": 0}) == 'event: stats\ndata: {"total":0}\n\n'


def test_internal_errors_do_not_leak_tracebacks(tmp_path, monkeypatch):
    db_path = str(tmp_path / "errors.db")
    app = create_app(db_path)
    with TestClient(app, raise_server_exceptions=False) as internal_client:

        async def boom():
            raise RuntimeError("secret internal detail")

        monkeypatch.setattr(
            internal_client.app.state.dashboard.store, "fetch_stats", boom
        )
        response = internal_client.get("/api/stats")
        assert response.status_code == 500
        assert response.json() == {
            "error": "internal",
            "detail": "internal server error",
        }
        assert "secret internal detail" not in response.text
        assert "Traceback" not in response.text


@pytest.mark.asyncio
async def test_sse_keepalive_comment(tmp_path, monkeypatch):
    import sugar.dashboard.app as app_module

    monkeypatch.setattr(app_module, "KEEPALIVE_INTERVAL", 0.05)
    async with running_sse_app(tmp_path) as (app, _db_path, sse):
        await sse.wait_for("event: snapshot")
        await sse.wait_for(": keepalive", timeout=5.0)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def test_loopback_host_detection():
    from sugar.main import _is_loopback_host

    assert _is_loopback_host("127.0.0.1") is True
    assert _is_loopback_host("::1") is True
    assert _is_loopback_host("localhost") is True
    assert _is_loopback_host("0.0.0.0") is False
    assert _is_loopback_host("192.168.1.10") is False
    assert _is_loopback_host("") is False
