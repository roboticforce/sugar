#!/usr/bin/env python3
"""Seed a demo event spine with backdated work history for Trends screenshots.

The Trends view is only meaningful with history. This script writes synthetic
events (task.created/claimed/completed/failed, usage.reported, tool.invoked)
across several projects, agents and harnesses over the last N days into a
throwaway event-spine database — never the user's real ``~/.sugar/events.db``
unless you pass it explicitly.

Usage:
    python scripts/seed_demo_trends.py --db /tmp/sugar-dashboard-demo/.sugar/events.db
    python scripts/seed_demo_trends.py --db ... --days 30 --reset
    python scripts/seed_demo_trends.py --db ... --reset   # remove seeded rows

Seeded events use ``source="seed-trends"`` and ``machine="demo"`` so they are
easy to identify/remove. This is a demo tool, not part of Sugar's runtime.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sugar.dashboard.spine import SCHEMA

PROJECTS = ["sugar", "roboticforce-site", "migratekit", "internal-api"]
AGENTS = [
    ("claude-code", "claude-code"),
    ("codex", "codex"),
    ("goose", "goose"),
    ("dsh", "dsh"),
    ("steve@Mac", "human"),
]
SEED_SOURCE = "seed-trends"
ISO = "%Y-%m-%dT%H:%M:%SZ"


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(ISO)


def _insert(conn: sqlite3.Connection, event: dict) -> None:
    conn.execute(
        """
        INSERT INTO events
            (ts, kind, actor, actor_kind, harness, machine, session_id, task_id,
             run_id, project, trace_id, source, payload, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["ts"],
            event["kind"],
            event.get("actor"),
            event.get("actor_kind"),
            event.get("harness"),
            "demo",
            event.get("session_id"),
            event.get("task_id"),
            event.get("run_id"),
            event.get("project"),
            None,
            SEED_SOURCE,
            json.dumps(event.get("payload") or {}),
            _iso(datetime.now(timezone.utc)),
        ),
    )


def seed(db_path: Path, days: int, seed_value: int, scale: float) -> int:
    rng = random.Random(seed_value)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=days - 1)
    connection = sqlite3.connect(str(db_path))
    connection.executescript(SCHEMA)
    total = 0
    for day_index in range(days):
        day = start + timedelta(days=day_index)
        # Weekends are quieter; a little growth over time.
        weekday_factor = 0.35 if day.weekday() >= 5 else 1.0
        growth = 1.0 + day_index / max(days, 1)
        for project in PROJECTS:
            task_count = max(
                1, int(rng.uniform(1.5, 4.5) * weekday_factor * growth * scale)
            )
            for _ in range(task_count):
                actor, harness = rng.choice(AGENTS)
                task_id = (
                    f"seed-{day_index:02d}-{project}-{rng.randint(1000, 9999)}"
                )
                created = day + timedelta(
                    hours=rng.randint(7, 11), minutes=rng.randint(0, 59)
                )
                claimed = created + timedelta(minutes=rng.randint(1, 25))
                duration_min = rng.randint(8, 180)
                completed = claimed + timedelta(minutes=duration_min)
                failed = rng.random() < 0.12
                base = {
                    "actor": actor,
                    "actor_kind": "human" if actor == "steve@Mac" else "agent",
                    "harness": harness,
                    "task_id": task_id,
                    "project": project,
                    "payload": {"title": f"Seeded work in {project}"},
                }
                _insert(connection, {**base, "kind": "task.created", "ts": _iso(created)})
                total += 1
                # A few tool events between claim and completion.
                for _ in range(rng.randint(2, 6)):
                    moment = claimed + timedelta(
                        seconds=rng.randint(30, max(60, duration_min * 60 - 30))
                    )
                    _insert(
                        connection,
                        {
                            **base,
                            "kind": "tool.invoked",
                            "ts": _iso(moment),
                            "payload": {
                                "message": rng.choice(
                                    [
                                        "running pytest -q",
                                        "editing src/{}.py".format(
                                            rng.choice(["api", "storage", "cli"])
                                        ),
                                        "reading docs/",
                                        "rg -n 'claim' src/",
                                    ]
                                )
                            },
                        },
                    )
                    total += 1
                _insert(
                    connection,
                    {**base, "kind": "task.claimed", "ts": _iso(claimed)},
                )
                total += 1
                final_kind = "task.failed" if failed else "task.completed"
                _insert(connection, {**base, "kind": final_kind, "ts": _iso(completed)})
                total += 1
                if not failed:
                    tokens_in = rng.randint(800, 9000)
                    tokens_out = rng.randint(200, 4000)
                    cost = round(
                        tokens_in / 1_000_000 * 3.0 + tokens_out / 1_000_000 * 15.0, 4
                    )
                    _insert(
                        connection,
                        {
                            **base,
                            "kind": "usage.reported",
                            "ts": _iso(completed + timedelta(seconds=5)),
                            "payload": {
                                "tokens_in": tokens_in,
                                "tokens_out": tokens_out,
                                "cost_usd": cost,
                                "model": "demo-model",
                            },
                        },
                    )
                    total += 1
    connection.commit()
    connection.close()
    return total


def reset(db_path: Path) -> int:
    connection = sqlite3.connect(str(db_path))
    try:
        connection.executescript(SCHEMA)
        cursor = connection.execute(
            "DELETE FROM events WHERE source = ?", (SEED_SOURCE,)
        )
        connection.commit()
        return cursor.rowcount
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        required=True,
        help="event-spine database to seed (use a demo path, not ~/.sugar/events.db)",
    )
    parser.add_argument("--days", type=int, default=14, help="days of history")
    parser.add_argument("--seed", type=int, default=7, help="random seed")
    parser.add_argument("--scale", type=float, default=1.0, help="volume multiplier")
    parser.add_argument(
        "--reset", action="store_true", help="remove previously seeded events first"
    )
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    removed = reset(db_path) if args.reset else 0
    if args.reset and args.days == 0:
        print(f"removed {removed} seeded events from {db_path}")
        return 0
    inserted = seed(db_path, max(1, args.days), args.seed, args.scale)
    print(
        f"seeded {inserted} events over {args.days} days into {db_path}"
        + (f" (removed {removed} previous)" if removed else "")
    )
    print("projects:", ", ".join(PROJECTS))
    print("agents:", ", ".join(actor for actor, _ in AGENTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
