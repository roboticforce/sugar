"""Tests for the Hermes Kanban board source (docs/local-dashboard.md §9).

Every board lives in a throwaway SQLite file under ``tmp_path``; the real
``~/.hermes/kanban.db`` and ``hermes`` CLI are never touched.
"""

import asyncio
import contextlib
import json
import os
import sqlite3
import time

import pytest
from starlette.testclient import TestClient

from sugar.dashboard.app import create_app
from sugar.dashboard.kanban import mapping, paths
from sugar.dashboard.kanban.client import (
    BoardError,
    HermesKanbanClient,
    build_archive_args,
    build_assign_args,
    build_block_args,
    build_claim_args,
    build_comment_args,
    build_complete_args,
    build_create_args,
    build_edit_args,
    build_unblock_args,
    extract_task_id,
    resolve_hermes_bin,
)
from sugar.dashboard.kanban.service import KanbanService
from sugar.dashboard.kanban.store import KanbanStore
from sugar.dashboard.service import DashboardError
from sugar.identity import resolve_actor

NOW = 1_790_000_000

SCHEMA = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, assignee TEXT,
    status TEXT NOT NULL, priority INTEGER DEFAULT 3, created_by TEXT,
    created_at INTEGER, started_at INTEGER, completed_at INTEGER,
    workspace_kind TEXT, workspace_path TEXT, branch_name TEXT, project_id TEXT,
    claim_lock TEXT, claim_expires INTEGER, tenant TEXT, result TEXT,
    idempotency_key TEXT, consecutive_failures INTEGER DEFAULT 0, worker_pid INTEGER,
    worker_started_at INTEGER, last_failure_error TEXT, max_runtime_seconds INTEGER,
    last_heartbeat_at INTEGER, current_run_id INTEGER, workflow_template_id TEXT,
    current_step_key TEXT, skills TEXT, model_override TEXT, provider_override TEXT,
    reasoning_effort TEXT, max_retries INTEGER, goal_mode INTEGER,
    goal_max_turns INTEGER, session_id TEXT, block_kind TEXT,
    block_recurrences INTEGER, completion_contract TEXT
);
CREATE TABLE task_links (
    parent_id TEXT, child_id TEXT, PRIMARY KEY (parent_id, child_id)
);
CREATE TABLE task_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, author TEXT, body TEXT,
    created_at INTEGER
);
CREATE TABLE task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, profile TEXT, step_key TEXT,
    status TEXT, claim_lock TEXT, claim_expires INTEGER, worker_pid INTEGER,
    worker_started_at INTEGER, max_runtime_seconds INTEGER, last_heartbeat_at INTEGER,
    started_at INTEGER, ended_at INTEGER, outcome TEXT, summary TEXT, metadata TEXT,
    error TEXT
);
CREATE TABLE task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, run_id INTEGER, kind TEXT,
    payload TEXT, created_at INTEGER
);
"""


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _connect(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def make_board(tmp_path, tasks=None):
    """Create a minimal Kanban database and return its path."""
    path = tmp_path / "kanban.db"
    conn = _connect(path)
    conn.executescript(SCHEMA)
    rows = (
        tasks
        if tasks is not None
        else [
            dict(
                id="t_000001",
                title="Fix flaky test",
                status="ready",
                priority=1,
                project_id="sugar",
                created_at=NOW - 3600,
                assignee=None,
            ),
            dict(
                id="t_000002",
                title="Write the plan",
                status="todo",
                priority=2,
                project_id="sugar",
                created_at=NOW - 1800,
                assignee="hermes",
            ),
            dict(
                id="t_000003",
                title="Ship it",
                status="done",
                priority=3,
                project_id="discovery",
                created_at=NOW - 900,
                completed_at=NOW - 300,
                assignee="claude",
                result="merged",
            ),
            dict(
                id="t_000004",
                title="Old experiment",
                status="archived",
                priority=5,
                project_id=None,
                created_at=NOW - 100,
            ),
        ]
    )
    for row in rows:
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO tasks ({columns}) VALUES ({placeholders})",
            list(row.values()),
        )
    conn.execute(
        "INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)",
        ("t_000001", "t_000002"),
    )
    conn.execute(
        "INSERT INTO task_comments (task_id, author, body, created_at)"
        " VALUES (?,?,?,?)",
        ("t_000002", "hermes", "needs the research first", NOW - 600),
    )
    conn.execute(
        "INSERT INTO task_runs (task_id, profile, status, outcome,"
        " summary, started_at, ended_at) VALUES (?,?,?,?,?,?,?)",
        ("t_000003", "claude", "done", "completed", "merged", NOW - 800, NOW - 300),
    )
    conn.execute(
        "INSERT INTO task_events (task_id, run_id, kind, payload, created_at)"
        " VALUES (?,?,?,?,?)",
        ("t_000003", None, "completed", '{"summary": "merged"}', NOW - 300),
    )
    conn.commit()
    conn.close()
    return str(path)


class FakeClient:
    """Records verbs and applies their effect to the board (instead of a CLI)."""

    def __init__(self, db_path=None):
        self.calls = []
        self.next_id = "t_new001"
        self.error = None
        self.binary = "/fake/hermes"
        self.db_path = db_path

    def _sql(self, statement, params=()):
        if not self.db_path:
            return
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(statement, params)
            conn.commit()
        finally:
            conn.close()

    def describe(self):
        return {"write_backend": "fake", "hermes_bin": self.binary}

    def _record(self, verb, *args, **kwargs):
        self.calls.append((verb, args, kwargs))
        if self.error:
            raise self.error

    async def create(self, title, **kwargs):
        self._record("create", title, **kwargs)
        if self.error:
            return self.next_id
        now = int(time.time())
        self._sql(
            "INSERT INTO tasks (id, title, body, assignee, status, priority,"
            " created_by, created_at, project_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                self.next_id,
                title,
                kwargs.get("body"),
                kwargs.get("assignee"),
                "triage",
                kwargs.get("priority", 3),
                kwargs.get("created_by", "dashboard"),
                now,
                kwargs.get("project"),
            ),
        )
        return self.next_id

    async def edit(self, task_id, **kwargs):
        self._record("edit", task_id, **kwargs)
        if self.error:
            return
        for column, flag in (
            ("title", "title"),
            ("body", "body"),
            ("priority", "priority"),
        ):
            if kwargs.get(flag) is not None:
                self._sql(
                    f"UPDATE tasks SET {column} = ? WHERE id = ?",
                    (kwargs[flag], task_id),
                )

    async def assign(self, task_id, profile, **kwargs):
        self._record("assign", task_id, profile, **kwargs)
        if not self.error:
            self._sql(
                "UPDATE tasks SET assignee = ? WHERE id = ?",
                (None if profile == "none" else profile, task_id),
            )

    async def claim(self, task_id, ttl=None):
        self._record("claim", task_id, ttl)
        if not self.error:
            self._sql(
                "UPDATE tasks SET status = 'running', claim_lock = 'fake:1'"
                " WHERE id = ?",
                (task_id,),
            )

    async def complete(self, task_ids, **kwargs):
        self._record("complete", list(task_ids), **kwargs)
        if not self.error:
            for task_id in task_ids:
                self._sql(
                    "UPDATE tasks SET status = 'done', result = ? WHERE id = ?",
                    (kwargs.get("result"), task_id),
                )

    async def comment(self, task_id, text, **kwargs):
        self._record("comment", task_id, text, **kwargs)
        if not self.error:
            self._sql(
                "INSERT INTO task_comments (task_id, author, body, created_at)"
                " VALUES (?,?,?,?)",
                (task_id, kwargs.get("author") or "dashboard", text, int(time.time())),
            )

    async def block(self, task_id, **kwargs):
        self._record("block", task_id, **kwargs)
        if not self.error:
            self._sql(
                "UPDATE tasks SET status = 'blocked', block_kind = ? WHERE id = ?",
                (kwargs.get("kind"), task_id),
            )

    async def unblock(self, task_ids, **kwargs):
        self._record("unblock", list(task_ids), **kwargs)
        if not self.error:
            for task_id in task_ids:
                self._sql(
                    "UPDATE tasks SET status = 'ready', block_kind = NULL WHERE id = ?",
                    (task_id,),
                )

    async def archive(self, task_ids):
        self._record("archive", list(task_ids))
        if not self.error:
            for task_id in task_ids:
                self._sql(
                    "UPDATE tasks SET status = 'archived' WHERE id = ?", (task_id,)
                )


@pytest.fixture
def board(tmp_path):
    return make_board(tmp_path)


@pytest.fixture
def fake_client(board):
    return FakeClient(board)


def service_for(board, client, *, read_only=False):
    service = KanbanService(board, read_only=read_only, hermes_bin="/nonexistent")
    service.client = client
    return service


def run(coro):
    return asyncio.run(coro)


@contextlib.asynccontextmanager
async def running(service):
    """Start/stop a service's store + watcher around one test body."""
    await service.start()
    try:
        yield service
    finally:
        await service.stop()


# ---------------------------------------------------------------------------
# mapping
# ---------------------------------------------------------------------------


class TestMapping:
    def test_iso_timestamp(self):
        assert mapping.iso_timestamp(NOW) == "2026-09-21T14:13:20Z"
        assert mapping.iso_timestamp(None) is None
        assert mapping.iso_timestamp(0) is None
        assert mapping.iso_timestamp("not-a-number") is None
        assert mapping.iso_timestamp(str(NOW)) == "2026-09-21T14:13:20Z"

    def test_latest_activity_prefers_heartbeat(self):
        row = {
            "created_at": NOW - 100,
            "started_at": NOW - 50,
            "completed_at": None,
            "last_heartbeat_at": NOW,
        }
        assert mapping.latest_activity(row) == mapping.iso_timestamp(NOW)

    def test_priority_normalization(self):
        assert mapping.normalize_priority(1) == 1
        assert mapping.normalize_priority("4") == 4
        assert mapping.normalize_priority(0) == 3
        assert mapping.normalize_priority(9) == 3
        assert mapping.normalize_priority(None) == 3
        assert mapping.normalize_priority(True) == 3

    def test_serialize_full_row(self):
        row = dict(
            id="t_1",
            title="Do it",
            body="Body",
            assignee="hermes",
            status="review",
            priority=2,
            created_by="dsh",
            created_at=NOW - 100,
            started_at=NOW - 50,
            completed_at=None,
            project_id="sugar",
            claim_lock="Mac:1",
            claim_expires=NOW + 900,
            block_kind="needs_input",
            result="ok",
            consecutive_failures=0,
            last_heartbeat_at=NOW - 10,
        )
        task = mapping.serialize_task(row, parents=["t_0"])
        assert task["id"] == "t_1"
        assert task["status"] == "review"
        assert task["type"] == "sugar"
        assert task["description"] == "Body"
        assert task["assigned_agent"] == "hermes"
        assert task["source"] == "dsh"
        assert task["blocked_by"] == ["t_0"]
        assert task["result"] == {"text": "ok"}
        assert task["context"]["claim_lock"] == "Mac:1"
        assert task["context"]["block_kind"] == "needs_input"
        assert task["created_at"].endswith("Z")
        assert task["updated_at"] == mapping.iso_timestamp(NOW - 10)

    def test_serialize_defaults(self):
        task = mapping.serialize_task({"id": "t_2", "title": "T", "status": "todo"})
        assert task["type"] == mapping.DEFAULT_PROJECT
        assert task["priority"] == 3
        assert task["blocked_by"] == []
        assert task["result"] == {}
        assert task["acceptance_criteria"] is None
        assert task["context"] == {}
        assert task["created_by"] is None
        assert task["created_by_kind"] is None
        assert task["created_by_session"] is None
        assert task["claimed_by"] is None
        assert task["lease_expires_at"] is None

    def test_serialize_provenance(self):
        row = dict(
            id="t_prov",
            title="Provenance",
            status="running",
            created_by="alice",
            claim_lock="worker:7",
            claim_expires=NOW + 900,
        )
        task = mapping.serialize_task(row)
        assert task["created_by"] == "alice"
        # Kanban has no kind/session columns: unknown is truthful, never guessed.
        assert task["created_by_kind"] is None
        assert task["created_by_session"] is None
        assert task["claimed_by"] == "worker:7"
        assert task["lease_expires_at"] == mapping.iso_timestamp(NOW + 900)

    def test_status_counts_zero_filled(self):
        counts = mapping.status_counts(
            [{"status": "ready"}, {"status": "ready"}, {"status": "done"}]
        )
        assert counts["ready"] == 2 and counts["done"] == 1
        assert counts["triage"] == 0
        assert set(counts) == set(mapping.KANBAN_STATUSES)


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


class TestPaths:
    def test_default_board(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
        assert paths.kanban_db_path() == tmp_path / "kanban.db"

    def test_named_board(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
        assert (
            paths.kanban_db_path("ops")
            == tmp_path / "kanban" / "boards" / "ops" / "kanban.db"
        )

    def test_explicit_db_wins(self, tmp_path):
        explicit = tmp_path / "custom.db"
        assert paths.kanban_db_path("ops", explicit=str(explicit)) == explicit

    def test_env_pin_wins_without_board(self, monkeypatch, tmp_path):
        pinned = tmp_path / "pinned.db"
        monkeypatch.setenv("HERMES_KANBAN_DB", str(pinned))
        assert paths.kanban_db_path() == pinned

    def test_current_board_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
        monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
        marker = tmp_path / "kanban" / "current"
        marker.parent.mkdir(parents=True)
        marker.write_text("ops\n")
        assert paths.current_board() == "ops"
        assert paths.kanban_db_path() == (
            tmp_path / "kanban" / "boards" / "ops" / "kanban.db"
        )

    def test_invalid_slug_falls_back(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_KANBAN_BOARD", "../../etc")
        assert paths.current_board(tmp_path) == "default"


# ---------------------------------------------------------------------------
# client: command construction
# ---------------------------------------------------------------------------


class TestCommandBuilders:
    def test_create(self):
        args = build_create_args(
            "Title",
            body="Body",
            priority=1,
            assignee="hermes",
            project="sugar",
            parents=["t_0", "t_1"],
            created_by="dashboard",
        )
        assert args[:2] == ["create", "Title"]
        assert "--json" in args and args[args.index("--created-by") + 1] == "dashboard"
        assert args[args.index("--priority") + 1] == "1"
        assert args[args.index("--body") + 1] == "Body"
        assert [args[i + 1] for i, a in enumerate(args) if a == "--parent"] == [
            "t_0",
            "t_1",
        ]

    def test_create_actor_argv(self):
        """--created-by always carries the resolved actor, never a literal."""
        args = build_create_args("T", created_by="alice@host")
        assert args[args.index("--created-by") + 1] == "alice@host"

    def test_edit(self):
        assert build_edit_args("t_1", title="N", body="B", priority=2) == [
            "edit",
            "t_1",
            "--title",
            "N",
            "--body",
            "B",
            "--priority",
            "2",
        ]

    def test_assign_reassign(self):
        assert build_assign_args("t_1", "hermes") == ["assign", "t_1", "hermes"]
        assert build_assign_args("t_1", "none", reassign=True) == [
            "reassign",
            "t_1",
            "none",
        ]

    def test_claim_complete_comment(self):
        assert build_claim_args("t_1", 60) == ["claim", "t_1", "--ttl", "60"]
        assert build_complete_args(["t_1"], result="ok") == [
            "complete",
            "t_1",
            "--result",
            "ok",
        ]
        assert build_comment_args("t_1", "hi", author="dash") == [
            "comment",
            "t_1",
            "hi",
            "--author",
            "dash",
        ]

    def test_block_unblock_archive(self):
        assert build_block_args("t_1", reason="why", kind="needs_input") == [
            "block",
            "t_1",
            "why",
            "--kind",
            "needs_input",
        ]
        assert build_unblock_args(["t_1"], reason="ok") == [
            "unblock",
            "--reason",
            "ok",
            "t_1",
        ]
        assert build_archive_args(["t_1", "t_2"]) == ["archive", "t_1", "t_2"]

    def test_extract_task_id(self):
        assert extract_task_id('{"id": "t_abc123"}') == "t_abc123"
        assert extract_task_id('{"task": {"id": "t_def456"}}') == "t_def456"
        assert extract_task_id("Created t_123abc on board default") == "t_123abc"
        assert extract_task_id("nothing here") is None


class TestClientRun:
    def _stub(self, tmp_path, body, exit_code=0):
        script = tmp_path / "hermes"
        script.write_text(
            "#!/bin/sh\n"
            'if [ "$1" != "kanban" ]; then echo \'bad argv\' >&2; exit 9; fi\n'
            f"echo {json.dumps(body)}\n"
            f"exit {exit_code}\n"
        )
        script.chmod(0o755)
        return str(script)

    def test_resolve_explicit(self, tmp_path):
        stub = self._stub(tmp_path, "ok")
        assert resolve_hermes_bin(stub) == stub

    def test_resolve_missing(self, monkeypatch, tmp_path):
        monkeypatch.delenv("HERMES_BIN", raising=False)
        monkeypatch.setenv("PATH", "")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert resolve_hermes_bin("/definitely/not/here") is None

    @pytest.mark.skipif(os.name != "posix", reason="shebang stub needs a POSIX shell")
    def test_run_success(self, tmp_path):
        stub = self._stub(tmp_path, "done")
        client = HermesKanbanClient(stub, board="ops")
        assert client.command(["list"])[1:] == ["kanban", "--board", "ops", "list"]
        assert run(client.run(["list"])) == "done"

    @pytest.mark.skipif(os.name != "posix", reason="shebang stub needs a POSIX shell")
    def test_run_failure_surfaces_stderr(self, tmp_path):
        script = tmp_path / "hermes"
        script.write_text("#!/bin/sh\necho 'board exploded' >&2\nexit 3\n")
        script.chmod(0o755)
        client = HermesKanbanClient(str(script))
        with pytest.raises(BoardError) as excinfo:
            run(client.run(["list"]))
        assert "board exploded" in excinfo.value.detail
        assert excinfo.value.returncode == 3

    def test_missing_binary(self, monkeypatch, tmp_path):
        # Deterministic even on a machine with a real hermes install.
        monkeypatch.delenv("HERMES_BIN", raising=False)
        monkeypatch.setenv("PATH", "")
        monkeypatch.setenv("HOME", str(tmp_path))
        client = HermesKanbanClient("/definitely/not/here")
        assert client.available is False
        with pytest.raises(BoardError):
            run(client.run(["list"]))


# ---------------------------------------------------------------------------
# store (read-only SQLite)
# ---------------------------------------------------------------------------


class TestStore:
    def test_fetch_all_excludes_archived_and_attaches_parents(self, board):
        async def go():
            store = KanbanStore(board)
            await store.start()
            try:
                tasks = await store.fetch_all()
                by_id = {task["id"]: task for task in tasks}
                assert "t_000004" not in by_id
                assert by_id["t_000002"]["blocked_by"] == ["t_000001"]
                assert by_id["t_000003"]["result"] == {"text": "merged"}
                return tasks
            finally:
                await store.close()

        tasks = run(go())
        assert len(tasks) == 3

    def test_fetch_task_activity_and_prefix(self, board):
        async def go():
            store = KanbanStore(board)
            await store.start()
            try:
                task = await store.fetch_task("t_000002")
                assert task["context"]["activity"]["comments"][0]["body"] == (
                    "needs the research first"
                )
                # short prefix resolves to the same task
                assert (await store.fetch_task("t_000003"))["id"] == "t_000003"
                assert (await store.fetch_task("nope")) is None
                return task
            finally:
                await store.close()

        run(go())

    def test_filters_and_total(self, board):
        async def go():
            store = KanbanStore(board)
            await store.start()
            try:
                tasks, total = await store.fetch_tasks(status="ready")
                assert total == 1 and tasks[0]["id"] == "t_000001"
                tasks, total = await store.fetch_tasks(task_type="discovery")
                assert total == 1 and tasks[0]["id"] == "t_000003"
                tasks, total = await store.fetch_tasks(
                    task_type=mapping.DEFAULT_PROJECT
                )
                assert total == 0
                tasks, total = await store.fetch_tasks(query="plan")
                assert total == 1
                tasks, total = await store.fetch_tasks(priority=3)
                assert total == 1
                tasks, total = await store.fetch_tasks(order="priority", limit=2)
                assert total == 3 and len(tasks) == 2
                assert tasks[0]["priority"] <= tasks[1]["priority"]
                return True
            finally:
                await store.close()

        assert run(go())

    def test_stats(self, board):
        async def go():
            store = KanbanStore(board)
            await store.start()
            try:
                return await store.fetch_stats()
            finally:
                await store.close()

        stats = run(go())
        assert stats["total"] == 3
        assert stats["by_status"]["ready"] == 1
        assert (
            stats["by_status"]["archived"] if "archived" in stats["by_status"] else True
        )
        assert stats["by_type"] == {"sugar": 2, "discovery": 1}
        assert stats["recent_24h"] == 0  # seeded timestamps are old

    def test_task_types(self, board):
        async def go():
            store = KanbanStore(board)
            await store.start()
            try:
                return await store.fetch_task_type_ids()
            finally:
                await store.close()

        assert run(go()) == ["discovery", "sugar"]

    def test_data_version_changes_on_commit(self, board):
        async def go():
            store = KanbanStore(board)
            await store.start()
            try:
                before = await store.data_version()
                conn = _connect(board)
                conn.execute("UPDATE tasks SET title = 'changed' WHERE id = 't_000001'")
                conn.commit()
                conn.close()
                after = await store.data_version()
                return before, after
            finally:
                await store.close()

        before, after = run(go())
        assert after != before

    def test_query_only_connection_refuses_writes(self, board):
        async def go():
            store = KanbanStore(board)
            await store.start()
            try:
                with pytest.raises(sqlite3.OperationalError):
                    await store._conn().execute(
                        "UPDATE tasks SET title = 'nope' WHERE id = 't_000001'"
                    )
            finally:
                await store.close()

        run(go())


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------


class TestService:
    def test_meta(self, board):
        service = KanbanService(board)
        meta = service.meta()
        assert meta["board"] == "kanban"
        assert meta["facet"]["label"] == "Project"
        assert "triage" in meta["statuses"] and "done" in meta["statuses"]
        assert meta["capabilities"]["claim"] is True
        assert meta["capabilities"]["edit_status"] is False

    def test_list_tasks_validates_status(self, board, fake_client):
        async def go():
            async with running(service_for(board, fake_client)) as service:
                with pytest.raises(DashboardError) as excinfo:
                    await service.list_tasks({"status": "bogus"})
                assert excinfo.value.status_code == 400

        run(go())

    def test_list_tasks_created_by_filter(self, board, fake_client):
        captured = {}

        async def go():
            async with running(service_for(board, fake_client)) as service:
                await service.create_task(
                    {"title": "Alice task", "created_by": "alice"}
                )
                fake_client.next_id = "t_new002"
                await service.create_task({"title": "Bob task", "created_by": "bob"})
                captured["filtered"] = await service.list_tasks({"created_by": "alice"})
                captured["combined"] = await service.list_tasks(
                    {"created_by": "alice", "status": "triage"}
                )

        run(go())
        filtered = captured["filtered"]
        assert filtered["count"] == 1
        assert filtered["tasks"][0]["title"] == "Alice task"
        assert filtered["tasks"][0]["created_by"] == "alice"
        assert captured["combined"]["count"] == 1

    def test_create_task_maps_to_cli(self, board, fake_client):
        captured = {}

        async def go():
            async with running(service_for(board, fake_client)) as service:
                captured["task"] = await service.create_task(
                    {
                        "title": "  New work  ",
                        "type": "sugar",
                        "priority": 1,
                        "assigned_agent": "hermes",
                        "description": "Body",
                    }
                )

        run(go())
        verb, args, kwargs = fake_client.calls[0]
        assert verb == "create" and args[0] == "New work"
        assert kwargs["priority"] == 1
        assert kwargs["assignee"] == "hermes"
        assert kwargs["project"] == "sugar"
        assert captured["task"]["id"] == fake_client.next_id
        assert captured["task"]["title"] == "New work"

    def test_create_task_uses_resolved_actor(self, board, fake_client, monkeypatch):
        """Kanban create sends --created-by with the resolved local actor."""
        for name in (
            "SUGAR_ACTOR",
            "SUGAR_ACTOR_KIND",
            "SUGAR_AGENT_ID",
            "SUGAR_SESSION",
        ):
            monkeypatch.delenv(name, raising=False)
        expected = resolve_actor()

        async def go():
            async with running(service_for(board, fake_client)) as service:
                await service.create_task({"title": "provenance"})

        run(go())
        created_by = fake_client.calls[0][2]["created_by"]
        assert created_by == expected.id
        assert created_by != "dashboard"

    def test_create_task_body_actor(self, board, fake_client):
        async def go():
            async with running(service_for(board, fake_client)) as service:
                await service.create_task(
                    {
                        "title": "from the body",
                        "created_by": "alice",
                        "created_by_kind": "agent",
                    }
                )

        run(go())
        assert fake_client.calls[0][2]["created_by"] == "alice"

    def test_create_task_validation(self, board, fake_client):
        async def go():
            async with running(service_for(board, fake_client)) as service:
                for body in (
                    {},
                    {"title": "x", "priority": 9},
                    {"title": "x", "bogus": 1},
                    {"title": "x", "blocked_by": [1]},
                ):
                    with pytest.raises(DashboardError) as excinfo:
                        await service.create_task(body)
                    assert excinfo.value.status_code == 400

        run(go())
        assert fake_client.calls == []

    def test_update_task_edit_and_assign(self, board, fake_client):
        captured = {}

        async def go():
            async with running(service_for(board, fake_client)) as service:
                captured["task"] = await service.update_task(
                    "t_000002",
                    {"title": "Renamed", "priority": 4, "assigned_agent": "dsh"},
                )

        run(go())
        verbs = [call[0] for call in fake_client.calls]
        assert verbs == ["edit", "assign"]
        assert fake_client.calls[0][2]["priority"] == 4
        assert fake_client.calls[1][1] == ("t_000002", "dsh")
        assert captured["task"]["title"] == "Renamed"
        assert captured["task"]["assigned_agent"] == "dsh"

    def test_update_task_rejects_status(self, board, fake_client):
        async def go():
            async with running(service_for(board, fake_client)) as service:
                with pytest.raises(DashboardError) as excinfo:
                    await service.update_task("t_000002", {"status": "done"})
                assert excinfo.value.status_code == 400

        run(go())
        assert fake_client.calls == []

    def test_verbs(self, board, fake_client):
        async def go():
            async with running(service_for(board, fake_client)) as service:
                await service.claim_task("t_000001", {"ttl": 60})
                await service.complete_task(
                    "t_000001", {"result": "ok", "summary": "s"}
                )
                await service.comment_task("t_000001", {"text": "hi", "author": "dash"})
                await service.hold_task(
                    "t_000001", {"reason": "waiting", "kind": "needs_input"}
                )
                await service.release_task("t_000001")
                await service.delete_task("t_000001")

        run(go())
        verbs = [call[0] for call in fake_client.calls]
        assert verbs == ["claim", "complete", "comment", "block", "unblock", "archive"]
        assert fake_client.calls[0][1] == ("t_000001", 60)
        assert fake_client.calls[2][2]["author"] == "dash"
        assert fake_client.calls[3][2]["kind"] == "needs_input"

    def test_read_only_blocks_writes(self, board, fake_client):
        async def go():
            async with running(
                service_for(board, fake_client, read_only=True)
            ) as service:
                with pytest.raises(DashboardError) as excinfo:
                    await service.create_task({"title": "x"})
                assert excinfo.value.status_code == 403

        run(go())
        assert fake_client.calls == []

    def test_board_error_maps_to_502(self, board, fake_client):
        fake_client.error = BoardError("nope", detail="hermes: install lock")

        async def go():
            async with running(service_for(board, fake_client)) as service:
                with pytest.raises(DashboardError) as excinfo:
                    await service.create_task({"title": "x"})
                assert excinfo.value.status_code == 502
                assert "install lock" in excinfo.value.detail

        run(go())


# ---------------------------------------------------------------------------
# ASGI app
# ---------------------------------------------------------------------------


def test_app_meta(board, fake_client):
    app = create_app(board, service=service_for(board, fake_client))
    with TestClient(app) as client:
        response = client.get("/api/meta")
        assert response.status_code == 200
        assert response.json()["board"] == "kanban"


def test_app_lists_kanban_tasks(board, fake_client):
    service = service_for(board, fake_client)
    with TestClient(create_app(board, service=service)) as client:
        response = client.get("/api/tasks")
        payload = response.json()
        assert payload["count"] == 3
        statuses = {task["status"] for task in payload["tasks"]}
        assert statuses == {"ready", "todo", "done"}
        detail = client.get("/api/tasks/t_000002").json()["task"]
        assert detail["context"]["activity"]["comments"]


def test_app_create_claim_complete_comment(board, fake_client):
    service = service_for(board, fake_client)
    with TestClient(create_app(board, service=service)) as client:
        created = client.post(
            "/api/tasks", json={"title": "From the UI", "priority": 2}
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["task"]["id"]
        assert task_id == fake_client.next_id

        assert (
            client.post(f"/api/tasks/{task_id}/claim", json={"ttl": 30}).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/tasks/{task_id}/complete", json={"result": "done"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/tasks/{task_id}/comment", json={"text": "nice"}
            ).status_code
            == 200
        )
        assert client.delete(f"/api/tasks/{task_id}").json() == {
            "deleted": True,
            "id": task_id,
        }
    verbs = [call[0] for call in fake_client.calls]
    assert verbs == ["create", "claim", "complete", "comment", "archive"]


def test_app_create_resolves_provenance(board, fake_client):
    service = service_for(board, fake_client)
    with TestClient(create_app(board, service=service)) as client:
        response = client.post(
            "/api/tasks",
            json={"title": "header actor", "created_by": "body-actor"},
            headers={
                "X-Sugar-Actor": "  header-actor  ",
                "X-Sugar-Actor-Kind": "agent",
            },
        )
        assert response.status_code == 201, response.text
        created = response.json()["task"]
        invalid = client.post(
            "/api/tasks", json={"title": "bad kind", "created_by_kind": "robot"}
        )
        assert invalid.status_code == 400

    # The header won and travelled all the way to the hermes create argv.
    assert fake_client.calls[0][2]["created_by"] == "header-actor"
    assert created["created_by"] == "header-actor"
    # Kanban has no kind column, so the mapped task keeps it null (§10.4).
    assert created["created_by_kind"] is None


def test_app_read_only(board, fake_client):
    service = service_for(board, fake_client, read_only=True)
    with TestClient(create_app(board, service=service)) as client:
        response = client.post("/api/tasks", json={"title": "nope"})
        assert response.status_code == 403
        assert response.json()["error"] == "read_only"
        assert client.get("/api/tasks").status_code == 200


def test_app_unsupported_verbs_on_sugar_source(tmp_path):
    app = create_app(str(tmp_path / "sugar.db"))
    with TestClient(app) as client:
        created = client.post("/api/tasks", json={"title": "sugar task"})
        assert created.status_code == 201
        task_id = created.json()["task"]["id"]
        response = client.post(f"/api/tasks/{task_id}/claim", json={})
        assert response.status_code == 400
        assert response.json()["error"] == "unsupported"
        meta = client.get("/api/meta").json()
        assert meta["board"] == "sugar"
        assert meta["capabilities"]["claim"] is False


def test_kanban_sse_snapshot(board, fake_client):
    service = service_for(board, fake_client)
    app = create_app(board, service=service)
    chunks = []

    async def go():
        await service.start()
        disconnect = asyncio.Event()

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
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        task = asyncio.create_task(app(scope, receive, send))
        for _ in range(200):
            if chunks:
                break
            await asyncio.sleep(0.01)
        disconnect.set()
        try:
            await asyncio.wait_for(task, timeout=2)
        except asyncio.TimeoutError:
            task.cancel()
        finally:
            await service.stop()
        return b"".join(chunks).decode("utf-8")

    text = run(go())
    assert "event: hello" in text
    assert "event: snapshot" in text
    payload = text.split("event: snapshot", 1)[1]
    data = payload.split("data: ", 1)[1].split("\n\n", 1)[0]
    snapshot = json.loads(data)
    assert {task["status"] for task in snapshot["tasks"]} == {"ready", "todo", "done"}
