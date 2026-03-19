"""
Concurrency benchmark script for Sugar.

Measures concrete numbers for:
  1. Event loop blocking time from MemoryStore sync sqlite3 calls
  2. Concurrent task pickup uniqueness and latency
  3. Task throughput (adds + gets per second) at various concurrency levels
  4. Shutdown latency under concurrent task spawn

Run this BEFORE and AFTER the concurrency fixes to get comparison numbers:

    python tests/benchmarks/bench_concurrency.py

Output format is plain text, easy to copy into a PR description.
"""

import asyncio
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

# Allow running from project root without installing
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sugar.storage.work_queue import WorkQueue

try:
    from sugar.memory.store import MemoryStore
    from sugar.memory.types import MemoryEntry, MemoryQuery, MemoryType

    HAS_MEMORY = True
except ImportError:
    HAS_MEMORY = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hr(label: str = "") -> None:
    width = 60
    if label:
        print(f"\n{'=' * 4} {label} {'=' * (width - len(label) - 6)}")
    else:
        print("=" * width)


def _result(label: str, value: str) -> None:
    print(f"  {label:<40} {value}")


async def _make_queue(tmp: Path, name: str) -> WorkQueue:
    q = WorkQueue(str(tmp / f"{name}.db"))
    await q.initialize()
    return q


def _make_task(title: str, priority: int = 3) -> dict:
    return {
        "type": "bug_fix",
        "title": title,
        "description": f"desc for {title}",
        "priority": priority,
        "source": "bench",
    }


# ---------------------------------------------------------------------------
# Benchmark 1: Event loop blocking from MemoryStore
# ---------------------------------------------------------------------------


async def bench_event_loop_blocking(tmp: Path) -> dict:
    """
    Measure how long the event loop is stalled when MemoryStore.store()
    runs synchronously.

    Method: run a high-frequency ticker coroutine alongside the store
    operation. The maximum gap between ticker ticks equals the blocking time.
    """
    if not HAS_MEMORY:
        return {"skipped": "sugar.memory not installed"}

    store = MemoryStore(str(tmp / "blocking_bench.db"))
    results = []

    for trial in range(5):
        max_gap_ms = 0.0
        stop = asyncio.Event()

        async def ticker():
            nonlocal max_gap_ms
            last = asyncio.get_event_loop().time()
            while not stop.is_set():
                await asyncio.sleep(0.001)
                now = asyncio.get_event_loop().time()
                gap = (now - last) * 1000
                if gap > max_gap_ms:
                    max_gap_ms = gap
                last = now

        ticker_task = asyncio.create_task(ticker())

        entry = MemoryEntry(
            id="",
            memory_type=MemoryType.DECISION,
            content="benchmark content for blocking measurement " * 10,
            summary="benchmark summary",
        )
        # Synchronous call - blocks the loop
        store.store(entry)

        stop.set()
        await ticker_task
        results.append(max_gap_ms)

    store.close()
    return {
        "trials": results,
        "mean_ms": statistics.mean(results),
        "max_ms": max(results),
        "p95_ms": sorted(results)[int(len(results) * 0.95)],
    }


async def bench_event_loop_blocking_fixed(tmp: Path) -> dict:
    """
    Same measurement but using a per-thread MemoryStore (one fix approach).

    The fix requires that MemoryStore either:
      (a) uses aiosqlite (async) instead of sync sqlite3, OR
      (b) opens a fresh connection per thread in a thread pool, OR
      (c) serialises all writes through a single background thread.

    This benchmark simulates approach (b): a fresh MemoryStore per executor call.
    This tells you what the event loop stall looks like AFTER a correct fix.
    """
    if not HAS_MEMORY:
        return {"skipped": "sugar.memory not installed"}

    db_path = str(tmp / "blocking_fixed_bench.db")
    loop = asyncio.get_event_loop()
    results = []

    def store_in_thread(content: str) -> None:
        """Creates its own connection - safe to call from any thread."""
        store = MemoryStore(db_path)
        entry = MemoryEntry(
            id="",
            memory_type=MemoryType.DECISION,
            content=content,
            summary="benchmark summary",
        )
        store.store(entry)
        store.close()

    for trial in range(5):
        max_gap_ms = 0.0
        stop = asyncio.Event()

        async def ticker():
            nonlocal max_gap_ms
            last = asyncio.get_event_loop().time()
            while not stop.is_set():
                await asyncio.sleep(0.001)
                now = asyncio.get_event_loop().time()
                gap = (now - last) * 1000
                if gap > max_gap_ms:
                    max_gap_ms = gap
                last = now

        ticker_task = asyncio.create_task(ticker())

        content = "benchmark content for blocking measurement " * 10
        # Non-blocking: runs in thread pool, main loop stays free
        await loop.run_in_executor(None, store_in_thread, content)

        stop.set()
        await ticker_task
        results.append(max_gap_ms)

    return {
        "trials": results,
        "mean_ms": statistics.mean(results),
        "max_ms": max(results),
        "p95_ms": sorted(results)[int(len(results) * 0.95)],
    }


# ---------------------------------------------------------------------------
# Benchmark 2: Concurrent task pickup uniqueness
# ---------------------------------------------------------------------------


async def bench_concurrent_pickup(tmp: Path, n_workers: int = 10) -> dict:
    """
    Add N tasks, then fire N concurrent get_next_work calls.

    Reports:
    - duplicate_count: number of IDs returned more than once (want: 0)
    - total_claimed: unique tasks claimed
    - elapsed_ms: wall time for all N pickups
    """
    q = await _make_queue(tmp, f"pickup_{n_workers}")

    for i in range(n_workers):
        await q.add_work(_make_task(f"pickup-task-{i}", priority=i + 1))

    start = time.perf_counter()
    results = await asyncio.gather(*[q.get_next_work() for _ in range(n_workers)])
    elapsed_ms = (time.perf_counter() - start) * 1000

    await q.close()

    non_null = [r for r in results if r is not None]
    ids = [r["id"] for r in non_null]
    duplicate_count = len(ids) - len(set(ids))

    return {
        "n_workers": n_workers,
        "total_claimed": len(non_null),
        "duplicate_count": duplicate_count,
        "elapsed_ms": elapsed_ms,
        "pickup_rate_per_sec": n_workers / (elapsed_ms / 1000),
    }


# ---------------------------------------------------------------------------
# Benchmark 3: Task throughput
# ---------------------------------------------------------------------------


async def bench_throughput_sequential(tmp: Path, n: int = 100) -> dict:
    """Sequential add + get_next_work - baseline."""
    q = await _make_queue(tmp, "throughput_seq")
    start = time.perf_counter()

    for i in range(n):
        await q.add_work(_make_task(f"seq-{i}"))

    add_done = time.perf_counter()

    for _ in range(n):
        await q.get_next_work()

    get_done = time.perf_counter()
    await q.close()

    add_elapsed = add_done - start
    get_elapsed = get_done - add_done
    total_elapsed = get_done - start

    return {
        "n": n,
        "add_elapsed_s": add_elapsed,
        "get_elapsed_s": get_elapsed,
        "total_elapsed_s": total_elapsed,
        "add_rate_per_sec": n / add_elapsed,
        "get_rate_per_sec": n / get_elapsed,
        "total_ops_per_sec": (n * 2) / total_elapsed,
    }


async def bench_throughput_concurrent(tmp: Path, n: int = 100) -> dict:
    """Concurrent adds, then sequential gets."""
    q = await _make_queue(tmp, "throughput_con")
    start = time.perf_counter()

    await asyncio.gather(*[q.add_work(_make_task(f"con-{i}")) for i in range(n)])
    add_done = time.perf_counter()

    for _ in range(n):
        await q.get_next_work()

    get_done = time.perf_counter()
    await q.close()

    add_elapsed = add_done - start
    get_elapsed = get_done - add_done
    total_elapsed = get_done - start

    return {
        "n": n,
        "add_elapsed_s": add_elapsed,
        "get_elapsed_s": get_elapsed,
        "total_elapsed_s": total_elapsed,
        "add_rate_per_sec": n / add_elapsed,
        "get_rate_per_sec": n / get_elapsed,
        "total_ops_per_sec": (n * 2) / total_elapsed,
    }


async def bench_throughput_all_concurrent(tmp: Path, n: int = 50) -> dict:
    """Fully concurrent adds and gets - stress test."""
    q = await _make_queue(tmp, "throughput_all_con")

    # Pre-add tasks
    for i in range(n):
        await q.add_work(_make_task(f"stress-{i}"))

    start = time.perf_counter()

    # Concurrent adds + concurrent gets
    add_tasks = [q.add_work(_make_task(f"new-{i}")) for i in range(n)]
    get_tasks = [q.get_next_work() for _ in range(n)]

    results = await asyncio.gather(*add_tasks, *get_tasks, return_exceptions=True)
    elapsed = time.perf_counter() - start

    await q.close()

    errors = [r for r in results if isinstance(r, Exception)]
    gets = [r for r in results[n:] if r is not None and not isinstance(r, Exception)]
    ids = [r["id"] for r in gets]
    duplicates = len(ids) - len(set(ids))

    return {
        "n": n,
        "elapsed_s": elapsed,
        "errors": len(errors),
        "duplicate_pickups": duplicates,
        "total_ops_per_sec": (n * 2) / elapsed,
    }


# ---------------------------------------------------------------------------
# Benchmark 4: Memory store concurrent write latency
# ---------------------------------------------------------------------------


async def bench_memory_concurrent_writes(tmp: Path, n: int = 20) -> dict:
    """
    Fire N concurrent MemoryStore writes via run_in_executor and measure
    total elapsed time and per-write latency.
    """
    if not HAS_MEMORY:
        return {"skipped": "sugar.memory not installed"}

    db_path = str(tmp / "mem_concurrent.db")
    loop = asyncio.get_event_loop()
    write_times: List[float] = []
    errors: List[Exception] = []

    def do_write(i: int) -> float:
        """Per-thread write - creates its own connection (fix pattern b)."""
        store = MemoryStore(db_path)
        entry = MemoryEntry(
            id="",
            memory_type=MemoryType.DECISION,
            content=f"concurrent benchmark write {i} " * 5,
            summary=f"summary {i}",
        )
        t0 = time.perf_counter()
        store.store(entry)
        store.close()
        return (time.perf_counter() - t0) * 1000

    async def timed_write(i: int) -> None:
        try:
            elapsed_ms = await loop.run_in_executor(None, do_write, i)
            write_times.append(elapsed_ms)
        except Exception as e:
            errors.append(e)

    start = time.perf_counter()
    await asyncio.gather(*[timed_write(i) for i in range(n)])
    total_elapsed = (time.perf_counter() - start) * 1000

    if not write_times:
        return {"errors": len(errors), "skipped": "all writes failed"}

    return {
        "n": n,
        "errors": len(errors),
        "total_elapsed_ms": total_elapsed,
        "mean_write_ms": statistics.mean(write_times),
        "p95_write_ms": sorted(write_times)[int(len(write_times) * 0.95)],
        "max_write_ms": max(write_times),
        "throughput_writes_per_sec": n / (total_elapsed / 1000),
    }


# ---------------------------------------------------------------------------
# Benchmark 5: Shutdown latency
# ---------------------------------------------------------------------------


async def bench_shutdown_latency(tmp: Path, n_pending_tasks: int = 20) -> dict:
    """
    Measure how long it takes for the _main_loop_with_shutdown to fully stop
    after shutdown_event is set, with N tasks in the queue.

    Uses a stub executor that completes instantly.
    """
    # We test the shutdown detection logic in the sleep phase (not task execution)
    # by measuring the time from event.set() to loop exit.

    loop_interval = 0.1  # short cycle for bench
    shutdown_event = asyncio.Event()
    stop_times: List[float] = []

    async def wait_and_signal():
        await asyncio.sleep(0.01)
        t = time.perf_counter()
        shutdown_event.set()
        stop_times.append(t)

    async def sleep_with_shutdown_check(sleep_time: float) -> None:
        """Simulates the shutdown-aware sleep from _main_loop_with_shutdown."""
        remaining = sleep_time
        while remaining > 0 and not shutdown_event.is_set():
            chunk = min(0.01, remaining)
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=chunk)
                return
            except asyncio.TimeoutError:
                remaining -= chunk

    start = time.perf_counter()
    await asyncio.gather(
        sleep_with_shutdown_check(loop_interval),
        wait_and_signal(),
    )
    end = time.perf_counter()

    signal_to_stop_ms = (end - stop_times[0]) * 1000 if stop_times else -1

    return {
        "loop_interval_s": loop_interval,
        "signal_to_stop_ms": signal_to_stop_ms,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)

        _hr("Sugar Concurrency Benchmark")
        print("Run this before and after applying concurrency fixes.")
        print("Compare numbers to quantify improvement.\n")

        # --- Event loop blocking ---
        _hr("1. Event Loop Blocking (MemoryStore)")
        print("  Measures stall time when MemoryStore.store() runs synchronously.")
        print("  Fix target: blocking (before) -> non-blocking (after)\n")

        blocking = await bench_event_loop_blocking(tmp)
        if "skipped" in blocking:
            _result("SKIPPED", blocking["skipped"])
        else:
            _result("BLOCKING (sync) - mean stall", f"{blocking['mean_ms']:.2f} ms")
            _result("BLOCKING (sync) - max stall", f"{blocking['max_ms']:.2f} ms")
            _result("BLOCKING (sync) - p95 stall", f"{blocking['p95_ms']:.2f} ms")

        print()
        fixed = await bench_event_loop_blocking_fixed(tmp)
        if "skipped" in fixed:
            _result("SKIPPED", fixed["skipped"])
        else:
            _result("NON-BLOCKING (executor) - mean stall", f"{fixed['mean_ms']:.2f} ms")
            _result("NON-BLOCKING (executor) - max stall", f"{fixed['max_ms']:.2f} ms")
            _result("NON-BLOCKING (executor) - p95 stall", f"{fixed['p95_ms']:.2f} ms")

        # --- Concurrent pickup ---
        _hr("2. Concurrent Task Pickup (WorkQueue)")
        print("  N concurrent get_next_work calls. duplicate_count must be 0.\n")
        for n in (2, 5, 10):
            pickup = await bench_concurrent_pickup(tmp, n_workers=n)
            status = "OK" if pickup["duplicate_count"] == 0 else f"RACE CONDITION ({pickup['duplicate_count']} duplicates)"
            _result(f"n={n} workers - duplicates", f"{pickup['duplicate_count']} [{status}]")
            _result(f"n={n} workers - elapsed", f"{pickup['elapsed_ms']:.1f} ms")
            _result(f"n={n} workers - rate", f"{pickup['pickup_rate_per_sec']:.0f} pickups/sec")
            print()

        # --- Throughput ---
        _hr("3. Task Throughput (WorkQueue)")
        print("  ops/sec for add + get_next_work at various concurrency levels.\n")

        seq = await bench_throughput_sequential(tmp, n=100)
        _result("Sequential (100 adds + 100 gets)", f"{seq['total_ops_per_sec']:.0f} ops/sec")
        _result("  add rate", f"{seq['add_rate_per_sec']:.0f} ops/sec")
        _result("  get rate", f"{seq['get_rate_per_sec']:.0f} ops/sec")
        print()

        con = await bench_throughput_concurrent(tmp, n=100)
        _result("Concurrent adds (100) + sequential gets", f"{con['total_ops_per_sec']:.0f} ops/sec")
        _result("  concurrent add rate", f"{con['add_rate_per_sec']:.0f} ops/sec")
        _result("  sequential get rate", f"{con['get_rate_per_sec']:.0f} ops/sec")
        print()

        stress = await bench_throughput_all_concurrent(tmp, n=50)
        status = "OK" if stress["duplicate_pickups"] == 0 and stress["errors"] == 0 else "ISSUES"
        _result("Fully concurrent (50 adds + 50 gets)", f"{stress['total_ops_per_sec']:.0f} ops/sec [{status}]")
        _result("  errors", str(stress["errors"]))
        _result("  duplicate pickups", str(stress["duplicate_pickups"]))
        print()

        # --- Memory concurrent writes ---
        _hr("4. Memory Store Concurrent Write Latency")
        print("  20 concurrent writes via run_in_executor.\n")
        mem = await bench_memory_concurrent_writes(tmp, n=20)
        if "skipped" in mem:
            _result("SKIPPED", mem.get("skipped", ""))
        else:
            _result("errors", str(mem["errors"]))
            _result("total elapsed", f"{mem['total_elapsed_ms']:.1f} ms")
            _result("mean write latency", f"{mem['mean_write_ms']:.2f} ms")
            _result("p95 write latency", f"{mem['p95_write_ms']:.2f} ms")
            _result("throughput", f"{mem['throughput_writes_per_sec']:.0f} writes/sec")
        print()

        # --- Shutdown latency ---
        _hr("5. Shutdown Latency")
        print("  Time from shutdown_event.set() to loop exit.\n")
        shutdown = await bench_shutdown_latency(tmp)
        _result("signal-to-stop latency", f"{shutdown['signal_to_stop_ms']:.2f} ms")
        _result("(sleep chunk granularity)", "10 ms")
        print()

        _hr("Summary")
        print(
            "  Copy these numbers into the PR for before/after comparison.\n"
            "  Key metrics:\n"
            "    - Event loop stall should drop from 10-100ms to <2ms\n"
            "    - Duplicate pickup count must be 0 after fix\n"
            "    - Throughput should not regress by more than 20%\n"
            "    - Shutdown latency should be <= 20ms (one 10ms chunk)\n"
        )


if __name__ == "__main__":
    asyncio.run(main())
