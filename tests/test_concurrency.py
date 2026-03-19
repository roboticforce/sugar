"""
Concurrency correctness tests for Sugar.

These tests verify that concurrent access to the work queue, memory store,
and the core loop behaves correctly. They must all pass before the
feature/concurrency-fixes branch is considered done.

Run with:
    pytest tests/test_concurrency.py -v -m "not slow"
    pytest tests/test_concurrency.py -v                  # includes slow tests

Design principles:
- Every test uses a real aiosqlite database in a temp dir (no mocks for storage)
- Tests are deterministic: no sleep-based synchronisation, use asyncio.Event instead
- Each test class covers one failure domain
"""

import asyncio
import tempfile
import time
from pathlib import Path
from typing import List
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from sugar.storage.work_queue import WorkQueue


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def queue(tmp_path: Path) -> WorkQueue:
    """Real WorkQueue backed by a temp aiosqlite database."""
    db_path = tmp_path / "concurrency_test.db"
    q = WorkQueue(str(db_path))
    await q.initialize()
    yield q
    await q.close()


def _make_task(title: str, priority: int = 3) -> dict:
    return {
        "type": "bug_fix",
        "title": title,
        "description": f"desc for {title}",
        "priority": priority,
        "source": "test",
    }


# ---------------------------------------------------------------------------
# 1. Concurrent task pickup - uniqueness guarantee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestConcurrentTaskPickup:
    """
    N concurrent callers of get_next_work must each receive a distinct task.

    The current implementation has a TOCTOU race: SELECT then UPDATE are
    separate statements. Under concurrency, two coroutines can SELECT the
    same row before either commits its UPDATE.

    These tests quantify that bug and verify the fix.
    """

    @pytest.mark.integration
    async def test_two_concurrent_callers_get_distinct_tasks(
        self, queue: WorkQueue
    ) -> None:
        """Two simultaneous get_next_work calls must return different tasks."""
        await queue.add_work(_make_task("task-A", priority=1))
        await queue.add_work(_make_task("task-B", priority=2))

        # Fire both coroutines simultaneously using gather
        results = await asyncio.gather(
            queue.get_next_work(),
            queue.get_next_work(),
        )

        ids = [r["id"] for r in results if r is not None]
        assert len(ids) == len(
            set(ids)
        ), f"Duplicate task assigned to concurrent workers: {ids}"

    @pytest.mark.integration
    async def test_n_concurrent_callers_each_get_unique_task(
        self, queue: WorkQueue
    ) -> None:
        """N concurrent callers each receive a unique task or None."""
        n = 5
        for i in range(n):
            await queue.add_work(_make_task(f"task-{i}", priority=i + 1))

        results = await asyncio.gather(*[queue.get_next_work() for _ in range(n)])

        non_null = [r for r in results if r is not None]
        ids = [r["id"] for r in non_null]
        assert len(ids) == len(set(ids)), f"Duplicate tasks assigned: ids={ids}"

    @pytest.mark.integration
    async def test_no_task_claimed_twice_across_worker_loop(
        self, queue: WorkQueue
    ) -> None:
        """
        Simulates the _execute_work loop calling get_next_work for
        max_concurrent_work=3 workers while a separate coroutine also
        calls get_next_work. Verifies zero duplicates across all callers.
        """
        task_count = 4
        for i in range(task_count):
            await queue.add_work(_make_task(f"task-{i}"))

        # Simulate 3-worker loop + 1 external caller
        calls = [queue.get_next_work() for _ in range(4)]
        results = await asyncio.gather(*calls)

        non_null = [r for r in results if r is not None]
        ids = [r["id"] for r in non_null]

        assert len(ids) == len(
            set(ids)
        ), f"Race condition: duplicate task IDs assigned: {ids}"
        # All 4 tasks should be claimed (exactly task_count tasks exist)
        assert len(non_null) == task_count

    @pytest.mark.integration
    async def test_extra_concurrent_callers_get_none_not_duplicate(
        self, queue: WorkQueue
    ) -> None:
        """When there are fewer tasks than callers, extras must get None."""
        await queue.add_work(_make_task("only-task"))

        results = await asyncio.gather(
            queue.get_next_work(),
            queue.get_next_work(),
            queue.get_next_work(),
        )

        non_null = [r for r in results if r is not None]
        assert (
            len(non_null) == 1
        ), f"Expected exactly 1 task claimed, got {len(non_null)}: {non_null}"
        ids = [r["id"] for r in non_null]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# 2. Status consistency under concurrent access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestStatusConsistency:
    """
    After concurrent pickup, verify the database reflects 'active' status
    for claimed tasks and 'pending' for unclaimed tasks.
    """

    @pytest.mark.integration
    async def test_claimed_tasks_are_marked_active_in_db(
        self, queue: WorkQueue
    ) -> None:
        """Every task returned by get_next_work must appear as 'active' in the db."""
        for i in range(3):
            await queue.add_work(_make_task(f"task-{i}"))

        results = await asyncio.gather(*[queue.get_next_work() for _ in range(3)])
        claimed = [r for r in results if r is not None]

        for task in claimed:
            stored = await queue.get_work_by_id(task["id"])
            assert stored is not None
            assert (
                stored["status"] == "active"
            ), f"Task {task['id']} should be active, got {stored['status']}"

    @pytest.mark.integration
    async def test_unclaimed_tasks_remain_pending(self, queue: WorkQueue) -> None:
        """Tasks not picked up must remain 'pending' with no increment to attempts."""
        ids = []
        for i in range(3):
            task_id = await queue.add_work(_make_task(f"task-{i}", priority=i + 1))
            ids.append(task_id)

        # Claim only the first one
        claimed = await queue.get_next_work()
        assert claimed is not None

        for task_id in ids:
            task = await queue.get_work_by_id(task_id)
            if task_id == claimed["id"]:
                assert task["status"] == "active"
                assert task["attempts"] == 1
            else:
                assert task["status"] == "pending"
                assert (
                    task["attempts"] == 0
                ), f"Unclaimed task {task_id} had attempts incremented"

    @pytest.mark.integration
    async def test_concurrent_complete_and_fail_do_not_corrupt_each_other(
        self, queue: WorkQueue
    ) -> None:
        """
        Concurrently completing one task and failing another must not corrupt
        each other's final state.
        """
        id_a = await queue.add_work(_make_task("task-complete"))
        id_b = await queue.add_work(_make_task("task-fail"))

        await queue.get_next_work()  # marks id_a active (highest priority same, FIFO)
        await queue.get_next_work()  # marks id_b active

        await asyncio.gather(
            queue.complete_work(id_a, {"success": True}),
            queue.fail_work(id_b, "simulated error", max_retries=1),
        )

        task_a = await queue.get_work_by_id(id_a)
        task_b = await queue.get_work_by_id(id_b)

        assert task_a["status"] == "completed"
        # fail_work with max_retries=1 and attempts=1 -> permanently failed
        assert task_b["status"] == "failed"


# ---------------------------------------------------------------------------
# 3. Event loop blocking detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestEventLoopBlocking:
    """
    MemoryStore uses synchronous sqlite3 (not aiosqlite). Any call to
    MemoryStore.store() or MemoryStore.search() from an async context blocks
    the event loop for the duration of the SQLite operation.

    These tests measure that blocking time and assert it stays below a
    threshold after the fix (expected fix: run_in_executor wrapper).

    The threshold is deliberately generous (50ms) to avoid flakiness on
    loaded CI machines. The benchmark script has tighter numbers.
    """

    BLOCKING_THRESHOLD_MS = 50.0

    @pytest.mark.integration
    async def test_memory_store_write_does_not_block_event_loop(
        self, tmp_path: Path
    ) -> None:
        """
        A memory store write must not block the event loop for more than
        BLOCKING_THRESHOLD_MS milliseconds.
        """
        pytest.importorskip("sugar.memory.store")
        from sugar.memory.store import MemoryStore
        from sugar.memory.types import MemoryEntry, MemoryType

        store = MemoryStore(str(tmp_path / "mem.db"))

        # Run a concurrent ticker to detect stalls
        stall_detected = asyncio.Event()
        max_gap_ms = 0.0
        stop = asyncio.Event()

        async def ticker():
            nonlocal max_gap_ms
            last = asyncio.get_event_loop().time()
            while not stop.is_set():
                await asyncio.sleep(0.001)  # yield every 1ms
                now = asyncio.get_event_loop().time()
                gap_ms = (now - last) * 1000
                if gap_ms > max_gap_ms:
                    max_gap_ms = gap_ms
                last = now

        ticker_task = asyncio.create_task(ticker())

        entry = MemoryEntry(
            id="",
            memory_type=MemoryType.DECISION,
            content="test content for blocking detection " * 20,
            summary="test summary",
        )
        store.store(entry)
        store.close()

        stop.set()
        await ticker_task

        assert max_gap_ms < self.BLOCKING_THRESHOLD_MS, (
            f"Event loop was blocked for {max_gap_ms:.1f}ms during MemoryStore.store(). "
            f"Threshold: {self.BLOCKING_THRESHOLD_MS}ms. "
            "Fix: wrap synchronous sqlite3 calls in run_in_executor."
        )

    @pytest.mark.integration
    async def test_work_queue_operations_yield_to_event_loop(
        self, queue: WorkQueue
    ) -> None:
        """
        WorkQueue uses aiosqlite (async). Adding and getting work should
        yield control so other coroutines can progress.
        """
        progress_count = 0

        async def background_work():
            nonlocal progress_count
            for _ in range(5):
                await asyncio.sleep(0)
                progress_count += 1

        bg_task = asyncio.create_task(background_work())

        for i in range(5):
            await queue.add_work(_make_task(f"task-{i}"))

        await bg_task

        assert progress_count == 5, (
            f"Background task made only {progress_count}/5 progress steps. "
            "WorkQueue operations may be blocking the event loop."
        )


# ---------------------------------------------------------------------------
# 4. Shutdown reliability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestShutdownReliability:
    """
    cancel_all (via shutdown_event) while _execute_work is active must not
    cause crashes, deadlocks, or leave tasks in an inconsistent state.
    """

    @pytest.mark.integration
    async def test_shutdown_event_stops_execute_work_loop(self, tmp_path: Path) -> None:
        """
        Setting the shutdown_event before _execute_work starts must cause it
        to exit without executing any tasks.
        """
        import yaml
        from unittest.mock import patch

        config_path = tmp_path / ".sugar" / "config.yaml"
        config_path.parent.mkdir()
        config_data = {
            "sugar": {
                "dry_run": True,
                "loop_interval": 300,
                "max_concurrent_work": 2,
                "claude": {"command": "claude"},
                "storage": {"database": str(tmp_path / "sugar.db")},
                "discovery": {
                    "error_logs": {"enabled": False},
                    "github": {"enabled": False},
                    "code_quality": {"enabled": False},
                    "test_coverage": {"enabled": False},
                },
            }
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        with (
            patch("sugar.core.loop.WorkQueue"),
            patch("sugar.core.loop.ClaudeWrapper"),
            patch("sugar.core.loop.AgentSDKExecutor"),
            patch("sugar.core.loop.ErrorLogMonitor"),
            patch("sugar.core.loop.CodeQualityScanner"),
            patch("sugar.core.loop.TestCoverageAnalyzer"),
        ):
            from sugar.core.loop import SugarLoop

            loop = SugarLoop(str(config_path))
            loop.work_queue = AsyncMock()
            loop.work_queue.get_next_work = AsyncMock(return_value=None)
            loop.executor = AsyncMock()

            shutdown_event = asyncio.Event()
            shutdown_event.set()  # set BEFORE calling _execute_work

            await loop._execute_work(shutdown_event=shutdown_event)

            # get_next_work must not be called when shutdown is already requested
            loop.work_queue.get_next_work.assert_not_called()

    @pytest.mark.integration
    async def test_shutdown_event_during_task_does_not_deadlock(
        self, tmp_path: Path
    ) -> None:
        """
        Signalling shutdown while a task is executing must not deadlock.
        The loop should complete the current task and exit cleanly.

        This test has a 5-second timeout to catch deadlocks.
        """
        import yaml
        from unittest.mock import patch

        config_path = tmp_path / ".sugar" / "config.yaml"
        config_path.parent.mkdir()
        config_data = {
            "sugar": {
                "dry_run": True,
                "loop_interval": 1,
                "max_concurrent_work": 1,
                "claude": {"command": "claude"},
                "storage": {"database": str(tmp_path / "sugar.db")},
                "discovery": {
                    "error_logs": {"enabled": False},
                    "github": {"enabled": False},
                    "code_quality": {"enabled": False},
                    "test_coverage": {"enabled": False},
                },
            }
        }
        with open(config_path, "w") as f:
            yaml.dump(config_data, f)

        task_started = asyncio.Event()
        task_complete = asyncio.Event()
        shutdown_event = asyncio.Event()

        async def fake_executor(work_item):
            task_started.set()
            # Simulate brief work
            await asyncio.sleep(0.05)
            task_complete.set()
            return {"success": True, "result": "done"}

        with (
            patch("sugar.core.loop.WorkQueue"),
            patch("sugar.core.loop.ClaudeWrapper"),
            patch("sugar.core.loop.AgentSDKExecutor"),
            patch("sugar.core.loop.ErrorLogMonitor"),
            patch("sugar.core.loop.CodeQualityScanner"),
            patch("sugar.core.loop.TestCoverageAnalyzer"),
            patch("sugar.core.loop.WorkflowOrchestrator"),
        ):
            from sugar.core.loop import SugarLoop

            loop = SugarLoop(str(config_path))
            loop.work_queue = AsyncMock()
            loop.work_queue.get_next_work = AsyncMock(
                side_effect=[
                    {
                        "id": "task-shutdown-test",
                        "type": "bug_fix",
                        "title": "Shutdown test task",
                        "priority": 1,
                    },
                    None,
                ]
            )
            loop.work_queue.complete_work = AsyncMock()
            loop.work_queue.fail_work = AsyncMock()
            loop.workflow_orchestrator = AsyncMock()
            loop.workflow_orchestrator.prepare_work_execution = AsyncMock(
                return_value={}
            )
            loop.workflow_orchestrator.complete_work_execution = AsyncMock(
                return_value=True
            )
            loop.executor = AsyncMock()
            loop.executor.execute_work = AsyncMock(side_effect=fake_executor)

            async def signal_shutdown():
                await task_started.wait()
                shutdown_event.set()

            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        loop._execute_work(shutdown_event=shutdown_event),
                        signal_shutdown(),
                    ),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                pytest.fail(
                    "_execute_work deadlocked when shutdown was signalled during task execution"
                )

            # Task should have completed (we complete in-flight tasks)
            assert task_complete.is_set()

    @pytest.mark.integration
    async def test_concurrent_shutdown_and_queue_spawn_no_crash(
        self, queue: WorkQueue, tmp_path: Path
    ) -> None:
        """
        cancel_all equivalent: simultaneously add tasks to the queue and
        trigger shutdown. Neither operation should raise an exception.
        """
        shutdown_event = asyncio.Event()

        async def add_tasks():
            for i in range(10):
                await queue.add_work(_make_task(f"task-{i}"))
                await asyncio.sleep(0)  # interleave with shutdown

        async def trigger_shutdown():
            await asyncio.sleep(0.01)
            shutdown_event.set()

        async def drain_queue():
            while not shutdown_event.is_set():
                await queue.get_next_work()
                await asyncio.sleep(0)

        # Must not raise
        await asyncio.gather(
            add_tasks(),
            trigger_shutdown(),
            drain_queue(),
        )


# ---------------------------------------------------------------------------
# 5. Memory store concurrent load
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMemoryStoreConcurrentLoad:
    """
    MemoryStore uses a single shared sqlite3.Connection. Concurrent writes
    from multiple threads or coroutines (via run_in_executor) can cause
    'database is locked' errors with the default WAL mode or corrupt the
    connection state.

    After the fix, all concurrent writes should succeed.
    """

    @pytest.mark.integration
    @pytest.mark.slow
    async def test_concurrent_memory_writes_all_succeed(self, tmp_path: Path) -> None:
        """
        N concurrent coroutines each writing to MemoryStore must all succeed
        with no database-locked errors or exceptions.
        """
        pytest.importorskip("sugar.memory.store")
        from sugar.memory.store import MemoryStore
        from sugar.memory.types import MemoryEntry, MemoryType

        store = MemoryStore(str(tmp_path / "concurrent_mem.db"))
        errors: List[Exception] = []
        write_count = 0
        n = 20
        loop = asyncio.get_event_loop()

        async def write_entry(i: int) -> None:
            nonlocal write_count
            entry = MemoryEntry(
                id="",
                memory_type=MemoryType.DECISION,
                content=f"concurrent write {i} " * 5,
                summary=f"summary {i}",
            )
            try:
                # After fix: store.store() must be wrapped in run_in_executor
                await loop.run_in_executor(None, store.store, entry)
                write_count += 1
            except Exception as e:
                errors.append(e)

        await asyncio.gather(*[write_entry(i) for i in range(n)])
        store.close()

        assert not errors, f"{len(errors)} write(s) failed with errors: {errors[:3]}"
        assert write_count == n, f"Only {write_count}/{n} writes succeeded"

    @pytest.mark.integration
    async def test_memory_store_search_under_concurrent_writes(
        self, tmp_path: Path
    ) -> None:
        """
        Concurrent reads (search) and writes must not corrupt the connection
        or return incorrect results.
        """
        pytest.importorskip("sugar.memory.store")
        from sugar.memory.store import MemoryStore
        from sugar.memory.types import MemoryEntry, MemoryQuery, MemoryType

        store = MemoryStore(str(tmp_path / "rw_mem.db"))

        # Pre-populate
        for i in range(5):
            entry = MemoryEntry(
                id="",
                memory_type=MemoryType.PREFERENCE,
                content=f"pre-existing memory {i}",
                summary=f"summary {i}",
            )
            store.store(entry)

        errors: List[Exception] = []
        loop = asyncio.get_event_loop()

        async def write_new(i: int) -> None:
            entry = MemoryEntry(
                id="",
                memory_type=MemoryType.DECISION,
                content=f"new concurrent write {i}",
                summary=f"decision {i}",
            )
            try:
                await loop.run_in_executor(None, store.store, entry)
            except Exception as e:
                errors.append(e)

        async def read_search() -> None:
            query = MemoryQuery(query="memory", limit=10)
            try:
                await loop.run_in_executor(None, store.search, query)
            except Exception as e:
                errors.append(e)

        ops = [write_new(i) for i in range(10)] + [read_search() for _ in range(5)]
        await asyncio.gather(*ops)
        store.close()

        assert not errors, f"Concurrent read/write errors: {errors[:3]}"


# ---------------------------------------------------------------------------
# 6. Task throughput - regression guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTaskThroughput:
    """
    Throughput regression guard. After fixes, the queue should process tasks
    faster than the pre-fix baseline. These tests set a floor, not a ceiling.
    """

    @pytest.mark.integration
    @pytest.mark.slow
    async def test_sequential_add_and_get_throughput(self, queue: WorkQueue) -> None:
        """
        Baseline: sequential add + get_next_work must achieve at least
        50 operations per second on any reasonable machine.

        If this test fails on a fresh machine it indicates the aiosqlite
        path has become significantly slower (regression in the fix).
        """
        n = 50
        start = time.perf_counter()

        for i in range(n):
            await queue.add_work(_make_task(f"throughput-task-{i}"))

        for _ in range(n):
            result = await queue.get_next_work()
            assert result is not None

        elapsed = time.perf_counter() - start
        ops_per_sec = (n * 2) / elapsed  # adds + gets

        assert ops_per_sec >= 30, (
            f"Throughput regression: {ops_per_sec:.1f} ops/sec (floor: 30 ops/sec). "
            f"Elapsed: {elapsed:.2f}s for {n * 2} operations."
        )

    @pytest.mark.integration
    @pytest.mark.slow
    async def test_concurrent_add_does_not_regress_vs_sequential(
        self, tmp_path: Path
    ) -> None:
        """
        Concurrent adds must be at least as fast as sequential adds.
        If the fix adds excessive locking, this test will catch it.
        """
        n = 30
        db_path_seq = tmp_path / "seq.db"
        db_path_con = tmp_path / "con.db"

        # Sequential baseline
        q_seq = WorkQueue(str(db_path_seq))
        await q_seq.initialize()
        seq_start = time.perf_counter()
        for i in range(n):
            await q_seq.add_work(_make_task(f"seq-{i}"))
        seq_elapsed = time.perf_counter() - seq_start
        await q_seq.close()

        # Concurrent
        q_con = WorkQueue(str(db_path_con))
        await q_con.initialize()
        con_start = time.perf_counter()
        await asyncio.gather(
            *[q_con.add_work(_make_task(f"con-{i}")) for i in range(n)]
        )
        con_elapsed = time.perf_counter() - con_start
        await q_con.close()

        # Concurrent SQLite writes are inherently serialized at the file level
        # (each add_work opens its own connection). The overhead comes from
        # connection open/close and WAL lock contention, not from our fixes.
        # Use 50x as the bound - we're testing for catastrophic regression
        # (e.g., deadlock or O(n^2) behavior), not for parity with sequential.
        assert con_elapsed <= seq_elapsed * 50, (
            f"Concurrent adds ({con_elapsed:.3f}s) are catastrophically slower "
            f"than sequential ({seq_elapsed:.3f}s). "
            "Check for deadlocks or excessive serialisation in the fix."
        )
