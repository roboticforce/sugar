#!/usr/bin/env python3
"""
Seed a realistic Sugar work queue for demoing `sugar dashboard`.

Creates a throwaway project under /tmp (never touches your real .sugar/), fills
it with a mix of pending / active / hold / completed / failed tasks plus a small
orchestration tree, then optionally launches the dashboard against it.

Usage:
    python scripts/demo_dashboard.py                 # seed only
    python scripts/demo_dashboard.py --serve         # seed + serve on :7331
    python scripts/demo_dashboard.py --serve --open  # seed + serve + open browser
    python scripts/demo_dashboard.py --reset         # wipe and re-seed
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_MARKER = ".sugar-demo"
DEFAULT_DIR = Path("/tmp/sugar-dashboard-demo")

# The seeded rows. `age` is how long ago the row was created (hours).
# Fields not listed are left at their WorkQueue defaults.
TASKS: list[dict] = [
    # --- live work -------------------------------------------------------
    dict(
        key="flaky",
        title="Fix flaky claim test in tests/test_concurrency.py",
        type="bug_fix",
        priority=1,
        status="pending",
        age=0.4,
        description=(
            "Two agents occasionally double-claim the same row when the test "
            "runs against a cold SQLite file. Reproduce, then assert the "
            "BEGIN IMMEDIATE claim holds under 20 parallel workers."
        ),
        source="discovery:test-failure",
        context={"detected_by": "pytest -n 8", "file": "tests/test_concurrency.py"},
        acceptance_criteria=[
            {"description": "test_concurrent_claim passes 50x in a row"},
            {"description": "no 'database is locked' errors in the log"},
        ],
    ),
    dict(
        key="json-status",
        title="Add --json output to `sugar status`",
        type="feature",
        priority=2,
        status="pending",
        age=2.0,
        description=(
            "Scripts and the dashboard want machine-readable queue stats. "
            "Emit the same payload as GET /api/stats when --json is passed."
        ),
        source="human",
        acceptance_criteria=[{"description": "sugar status --json | jq .total"}],
    ),
    dict(
        key="docs-mcp",
        title="Document the MCP task tools in docs/user/cli-reference.md",
        type="documentation",
        priority=3,
        status="pending",
        age=5.0,
        description=(
            "sugar_add_task / list / view / update / status / remove are "
            "undocumented. Add one section with a usage example per tool."
        ),
        source="human",
    ),
    dict(
        key="prune-orch",
        title="Prune stale .sugar/orchestration dirs older than 30 days",
        type="chore",
        priority=4,
        status="pending",
        age=9.0,
        description="287 dirs and growing. Add cleanup to `sugar cleanup`.",
        source="discovery:code-quality",
    ),
    dict(
        key="passkeys",
        title="Migrate auth middleware to WebAuthn passkeys",
        type="refactor",
        priority=2,
        status="active",
        age=6.0,
        started_age=0.2,
        attempts=1,
        assigned_agent="claude-code",
        stage="implement",
        description=(
            "Stage 3 of 4: replace password hashing with WebAuthn credential "
            "verification behind the existing AuthBackend interface."
        ),
        source="orchestration",
        context={
            "worker": "claude-code",
            "heartbeat_note": "no lease in v1 - a crashed agent leaves this row stuck",
        },
    ),
    dict(
        key="rotate-token",
        title="Rotate the PyPI release token",
        type="chore",
        priority=1,
        status="hold",
        age=30.0,
        hold_reason="Waiting for human approval (security-sensitive)",
        description="Token expires in 14 days; rotate and update the release workflow.",
        source="human",
        context={"requires_human": True},
    ),
    dict(
        key="bump-sa",
        title="Bump SQLAlchemy to 2.1",
        type="chore",
        priority=3,
        status="failed",
        age=48.0,
        attempts=2,
        error="2 tests failed: test_storage.py::test_migration, test_mcp_server.py::test_session",
        description="Dependency refresh; migrations need to be re-tested on 2.1.",
        source="discovery:dependency",
        result={"rc": 1, "failed": ["test_migration", "test_session"]},
    ),
    # --- completed history ----------------------------------------------
    dict(
        key="retry-gh",
        title="Add retry/backoff to the GitHub client",
        type="feature",
        priority=2,
        status="completed",
        age=52.0,
        completed_age=48.0,
        attempts=1,
        description="429/5xx responses should back off with jitter, max 3 tries.",
        source="mcp",
        commit_sha="4f3c9a1d2e",
        result={"files_changed": 4, "tests": "128 passed", "duration_s": 412.5},
    ),
    dict(
        key="hold-tests",
        title="Cover hold/release transitions with tests",
        type="test",
        priority=3,
        status="completed",
        age=30.0,
        completed_age=24.0,
        attempts=1,
        description="Raise coverage on WorkQueue.hold_work / release_work.",
        source="mcp",
        result={"tests_added": 6},
    ),
    dict(
        key="mcp-parse",
        title="Fix MCP server JSON-RPC parse error on empty content",
        type="bug_fix",
        priority=1,
        status="completed",
        age=8.0,
        completed_age=6.0,
        attempts=1,
        description="Empty tool content raised KeyError instead of returning a warning.",
        source="discovery:error-log",
        commit_sha="b81e77c0aa",
        result={"tests": "12 passed"},
    ),
    dict(
        key="docs-agent",
        title="Write the AGENTS.md architecture note",
        type="documentation",
        priority=3,
        status="completed",
        age=80.0,
        completed_age=72.0,
        description="Explain queue -> loop -> executor -> quality gates.",
        source="human",
    ),
    # --- orchestration tree ---------------------------------------------
    dict(
        key="orch-parent",
        title="Orchestrate: per-project rate limits for discovery",
        type="feature",
        priority=2,
        status="pending",
        age=3.0,
        orchestrate=True,
        stage="research",
        description=(
            "Discovery hammers the GitHub API on large repos. Decomposed into "
            "research -> plan -> implement -> review; children are gated by "
            "blocked_by."
        ),
        source="human",
    ),
    dict(
        key="orch-research",
        title="Research existing rate-limit usage in discovery/",
        type="feature",
        priority=2,
        status="pending",
        age=3.0,
        parent="orch-parent",
        stage="research",
        assigned_agent="researcher",
        description="Where do we call the GitHub API, and how often?",
    ),
    dict(
        key="orch-plan",
        title="Plan the rate-limiter integration points",
        type="feature",
        priority=2,
        status="pending",
        age=3.0,
        parent="orch-parent",
        stage="plan",
        blocked_by=["orch-research"],
        assigned_agent="planner",
        description="Pick token bucket vs leaky bucket; define the interface.",
    ),
    dict(
        key="orch-impl",
        title="Implement TokenBucket limiter in discovery/",
        type="feature",
        priority=2,
        status="pending",
        age=3.0,
        parent="orch-parent",
        stage="implement",
        blocked_by=["orch-plan"],
        assigned_agent="coder",
        description="Per-project bucket, configurable in .sugar/config.yaml.",
    ),
    dict(
        key="orch-review",
        title="Review the rate limiter implementation",
        type="feature",
        priority=3,
        status="pending",
        age=3.0,
        parent="orch-parent",
        stage="review",
        blocked_by=["orch-impl"],
        assigned_agent="reviewer",
        description="Check backoff math, thread safety, and tests.",
    ),
]

CONFIG_YAML = """\
sugar:
  loop_interval: 300
  max_concurrent_work: 3
  storage:
    database: ".sugar/sugar.db"
  logging:
    file: ".sugar/sugar.log"
"""


def ts(delta_hours: float) -> str:
    """SQLite-style UTC timestamp, N hours ago."""
    moment = datetime.now(timezone.utc) - timedelta(hours=delta_hours)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _rebuild_ok(path: Path) -> bool:
    """Refuse to delete anything that isn't obviously a demo directory."""
    return path.name == DEFAULT_DIR.name or (path / DEMO_MARKER).exists()


def prepare_project(demo_dir: Path, reset: bool) -> Path:
    demo_dir = demo_dir.expanduser().resolve()

    if demo_dir.exists():
        if not reset:
            print(f"ℹ️  Reusing existing demo project: {demo_dir}")
            return demo_dir
        if not _rebuild_ok(demo_dir):
            raise SystemExit(
                f"Refusing to delete {demo_dir}: not a Sugar demo directory.\n"
                f"Use a directory named '{DEFAULT_DIR.name}' or delete it yourself."
            )
        shutil.rmtree(demo_dir)
        print(f"🧹 Removed old demo project: {demo_dir}")

    (demo_dir / ".sugar").mkdir(parents=True)
    (demo_dir / ".sugar" / "config.yaml").write_text(CONFIG_YAML)
    (demo_dir / DEMO_MARKER).write_text("created by scripts/demo_dashboard.py\n")
    print(f"📁 Created demo project: {demo_dir}")
    return demo_dir


async def seed(db_path: Path) -> list[dict]:
    # Imported lazily so --help works outside the venv too.
    from sugar.storage.work_queue import WorkQueue

    queue = WorkQueue(str(db_path))
    await queue.initialize()

    # Idempotent-ish: start from an empty queue in the demo DB.
    if (await queue.get_stats())["total"]:
        print("   (queue not empty - adding on top of existing demo data)")

    ids: dict[str, str] = {}
    ordered = TASKS
    for task in ordered:
        payload = {
            "type": task["type"],
            "title": task["title"],
            "description": task.get("description", ""),
            "priority": task["priority"],
            "status": "pending",
            "source": task.get("source", "demo"),
            "context": task.get("context", {}),
            "acceptance_criteria": task.get("acceptance_criteria", []),
            "assigned_agent": task.get("assigned_agent"),
            "stage": task.get("stage"),
            "orchestrate": task.get("orchestrate", False),
        }
        if task.get("parent"):
            payload["parent_task_id"] = ids[task["parent"]]
        if task.get("blocked_by"):
            payload["blocked_by"] = [ids[k] for k in task["blocked_by"]]
        ids[task["key"]] = await queue.add_work(payload)

    # Now stamp the interesting state/timing straight onto the demo DB.
    conn = sqlite3.connect(db_path)
    try:
        for task in ordered:
            created = ts(task["age"])
            updates: dict[str, object] = {
                "created_at": created,
                "updated_at": created,
                "status": task["status"],
                "attempts": task.get("attempts", 0),
            }
            if task.get("started_age") is not None:
                updates["started_at"] = ts(task["started_age"])
                updates["updated_at"] = ts(task["started_age"])
            if task.get("completed_age") is not None:
                updates["completed_at"] = ts(task["completed_age"])
                updates["updated_at"] = ts(task["completed_age"])
            if task.get("error"):
                updates["error_message"] = task["error"]
            if task.get("hold_reason"):
                updates["context"] = _with_hold(task, updates)
            if task.get("commit_sha"):
                updates["commit_sha"] = task["commit_sha"]
            if task.get("result") is not None:
                import json

                updates["result"] = json.dumps(task["result"])
            if task.get("total_execution_time") is not None:
                updates["total_execution_time"] = task["total_execution_time"]

            cols = ", ".join(f"{name} = ?" for name in updates)
            conn.execute(
                f"UPDATE work_items SET {cols} WHERE id = ?",
                [*updates.values(), ids[task["key"]]],
            )
        conn.commit()
    finally:
        conn.close()

    await queue.close()
    return [dict(task, id=ids[task["key"]]) for task in ordered]


def _with_hold(task: dict, updates: dict) -> str:
    import json

    context = dict(task.get("context") or {})
    context["hold_reason"] = task["hold_reason"]
    context["held_at"] = ts(task["age"] / 2)
    return json.dumps(context)


def print_summary(rows: list[dict], demo_dir: Path) -> None:
    icons = {
        "pending": "○",
        "active": "●",
        "hold": "⏸",
        "completed": "✓",
        "failed": "✗",
    }
    print(f"\n🍰 Seeded {len(rows)} tasks in {demo_dir}/.sugar/sugar.db\n")
    for row in rows:
        indent = "    ↳ " if row.get("parent") else "  "
        print(
            f"{indent}{icons.get(row['status'], '?')} [{row['status']:9s}] "
            f"p{row['priority']} {row['type']:13s} {row['title'][:58]}"
        )
    print(
        "\nNext:\n"
        f"  cd {demo_dir}\n"
        "  sugar dashboard --open          # browser + SSE\n"
        "  open apps/macos/dist/SugarDash.app   # native app (after build-app.sh)\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir", type=Path, default=DEFAULT_DIR, help="demo project dir"
    )
    parser.add_argument(
        "--reset", action="store_true", help="delete and re-create the demo dir"
    )
    parser.add_argument(
        "--serve", action="store_true", help="launch `sugar dashboard` after seeding"
    )
    parser.add_argument(
        "--open", action="store_true", help="open the browser (implies --serve)"
    )
    parser.add_argument("--port", type=int, default=7331)
    args = parser.parse_args()

    if args.open:
        args.serve = True

    demo_dir = prepare_project(args.dir, args.reset)
    rows = asyncio.run(seed(demo_dir / ".sugar" / "sugar.db"))
    print_summary(rows, demo_dir)

    if not args.serve:
        return 0

    cmd = [
        sys.executable,
        "-m",
        "sugar.main",
        "--config",
        str(demo_dir / ".sugar" / "config.yaml"),
        "dashboard",
        "--port",
        str(args.port),
    ]
    if args.open:
        cmd.append("--open")

    print(f"🚀 {' '.join(cmd)}\n   (cwd: {demo_dir})")
    try:
        return subprocess.call(cmd, cwd=demo_dir)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
