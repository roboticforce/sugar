#!/usr/bin/env python3
"""Simulate live agent activity against a running Sugar board.

This exists to *demonstrate* (and load-test) the live telemetry path: it posts
``run.started`` / ``tool.invoked`` / ``agent.activity`` / ``usage.reported``
events to ``POST /api/events`` for tasks that are currently claimed, so the
dashboard and SugarDash's Live floor/console show work happening in real time.

It does not run any AI agent. Real harnesses should call ``sugar emit`` (one
command per tool call) or the MCP ``sugar_emit_event`` tool; this script is the
fake producer for demos and UI development.

Usage:
    python scripts/simulate_agent_activity.py                    # 127.0.0.1:7331
    python scripts/simulate_agent_activity.py --interval 0.5     # faster
    python scripts/simulate_agent_activity.py --duration 30      # stop after 30s
    python scripts/simulate_agent_activity.py --server https://board.internal --token ...

Requires tasks in ``active`` status (claim some first, e.g. with ``sugar run`` or
the dashboard). Exits when the board is unreachable; keeps waiting if there are
no active tasks yet.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

TEMPLATES: Dict[str, List[str]] = {
    "tool.invoked": [
        "running pytest tests/test_{module}.py -q",
        "reading src/{module}.py",
        "editing src/{module}.py",
        "rg -n '{term}' src/",
        "python -m black sugar/{module}.py",
        "git diff --stat",
        "npm test -- --watch=false",
        "cargo check",
    ],
    "agent.activity": [
        "thinking about {theme}",
        "reviewing the diff for {module}.py",
        "planning the next step for {theme}",
        "checking the test output",
    ],
}
MODULES = ["auth", "board", "usage", "audit", "storage", "cli", "dashboard", "memory", "config"]
TERMS = ["claim", "lease", "presence", "token", "event", "export", "schema"]
THEMES = ["lease handling", "the reaper", "token scopes", "activity events", "schema versions"]

#: Extra payload per kind; message is always added by the caller.
EXTRA: Dict[str, Dict[str, Any]] = {
    "tool.invoked": {"tool": "shell"},
    "agent.activity": {"source": "simulator"},
}


def _request(
    server: str,
    path: str,
    *,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
    timeout: float = 5.0,
) -> Any:
    url = server.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def fetch_active(server: str, token: Optional[str]) -> List[Dict[str, Any]]:
    payload = _request(server, "/api/tasks?status=active", token=token)
    tasks = payload.get("tasks", payload) if isinstance(payload, dict) else payload
    return [task for task in tasks if isinstance(task, dict)]


def emit(server: str, token: Optional[str], body: Dict[str, Any]) -> Optional[int]:
    response = _request(server, "/api/events", method="POST", body=body, token=token)
    event = response.get("event", response) if isinstance(response, dict) else {}
    return event.get("id")


def make_message(kind: str, rng: random.Random) -> str:
    template = rng.choice(TEMPLATES[kind])
    return template.format(
        module=rng.choice(MODULES), term=rng.choice(TERMS), theme=rng.choice(THEMES)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server",
        default="http://127.0.0.1:7331",
        help="board URL (default: %(default)s)",
    )
    parser.add_argument("--token", default=None, help="board bearer token (optional)")
    parser.add_argument(
        "--interval", type=float, default=0.9, help="seconds between events"
    )
    parser.add_argument(
        "--duration", type=float, default=0.0, help="stop after N seconds (0 = forever)"
    )
    parser.add_argument(
        "--max-agents", type=int, default=0, help="limit to N active tasks (0 = all)"
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--quiet", action="store_true", help="only print errors")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    server = args.server.rstrip("/")
    started = time.monotonic()
    last_usage = 0.0
    emitted = 0
    waiting = False

    while True:
        if args.duration and time.monotonic() - started >= args.duration:
            break
        try:
            tasks = fetch_active(server, args.token)
        except (urllib.error.URLError, OSError) as exc:
            print(f"error: cannot reach {server}: {exc}", file=sys.stderr)
            return 1
        if args.max_agents:
            tasks = tasks[: args.max_agents]

        if not tasks:
            if not waiting:
                print(
                    "waiting for active tasks — claim some with `sugar run` "
                    "or the dashboard",
                    file=sys.stderr,
                )
                waiting = True
            try:
                time.sleep(max(1.0, args.interval))
            except KeyboardInterrupt:
                break
            continue
        waiting = False

        task = rng.choice(tasks)
        task_id = task.get("id")
        actor = task.get("claimed_by") or "agent"
        kind = rng.choices(
            ["tool.invoked", "agent.activity", "run.started"], weights=[6, 3, 1]
        )[0]
        message = make_message("agent.activity" if kind == "agent.activity" else "tool.invoked", rng)
        payload: Dict[str, Any] = {"message": message, **EXTRA.get(kind, {})}
        if kind == "run.started":
            payload = {"message": f"started work on {task.get('title', 'task')}"}
        body = {
            "kind": kind,
            "actor": actor,
            "actor_kind": "agent",
            "harness": "simulator",
            "task_id": task_id,
            "project": None,
            "payload": payload,
        }
        try:
            event_id = emit(server, args.token, body)
        except urllib.error.HTTPError as exc:
            print(f"error: board rejected event ({exc.code}): {exc.reason}", file=sys.stderr)
            return 1
        except (urllib.error.URLError, OSError) as exc:
            print(f"error: board unreachable: {exc}", file=sys.stderr)
            return 1
        emitted += 1
        if not args.quiet:
            short_task = str(task.get("title", ""))[:28]
            print(
                f"{time.strftime('%H:%M:%S')} {actor:<14} {short_task:<28} {message}"
            )

        # Roughly every ~8 events, report usage so the Usage view has data.
        now = time.monotonic()
        if now - last_usage > args.interval * 8:
            last_usage = now
            tokens_in = rng.randint(200, 3000)
            tokens_out = rng.randint(100, 1500)
            cost = round(tokens_in / 1_000_000 * 3 + tokens_out / 1_000_000 * 15, 4)
            try:
                emit(
                    server,
                    args.token,
                    {
                        "kind": "usage.reported",
                        "actor": actor,
                        "actor_kind": "agent",
                        "harness": "simulator",
                        "task_id": task_id,
                        "payload": {
                            "tokens_in": tokens_in,
                            "tokens_out": tokens_out,
                            "cost_usd": cost,
                            "model": "simulated-model",
                        },
                    },
                )
            except (urllib.error.URLError, OSError):
                pass

        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            break

    if not args.quiet:
        print(f"stopped after {emitted} events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
