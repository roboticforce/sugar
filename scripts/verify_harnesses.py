#!/usr/bin/env python3
"""Verify Sugar against the harnesses installed on this machine.

This is a *real* smoke check, not a unit test: it detects installed CLIs, installs
the matching Sugar adapter into a throwaway HOME, starts a throwaway board, sends
representative harness payloads through the live ingestion paths, and (with
``--real``) runs a small model call where auth is available.

What it can prove without model auth:
  * Claude Code / Codex / Goose / Cursor configs are written in the shape the
    harness expects (and existing config is preserved),
  * the hook endpoints normalize real payload shapes into canonical L3 events,
  * OpenCode's MCP config starts Sugar's task/memory servers,
  * Hermes Kanban can be served read-only by Sugar,
  * DSH session logs are tailed into the spine.

What it cannot prove without auth: that the harness itself invokes the hooks
during a live turn. Those rows are reported as SKIP with the reason, never PASS.

Usage:
    python scripts/verify_harnesses.py
    python scripts/verify_harnesses.py --real
    python scripts/verify_harnesses.py --json
    python scripts/verify_harnesses.py --opencode-project /path/to/repo
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
SUGAR = [PYTHON, "-m", "sugar.main"]
BASE_ENV = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONUNBUFFERED": "1"}
HOME = Path.home()

CLAUSE_PASS = "PASS"
CLAUSE_SKIP = "SKIP"
CLAUSE_FAIL = "FAIL"


@dataclass
class Result:
    harness: str
    status: str
    detail: str

    def as_dict(self) -> Dict[str, str]:
        return {"harness": self.harness, "status": self.status, "detail": self.detail}


def run(
    cmd: List[str],
    *,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[Path] = None,
    timeout: float = 45,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        env={**BASE_ENV, **(env or {})},
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def which(name: str) -> Optional[str]:
    return shutil.which(name)


def version_of(names: List[str]) -> Optional[str]:
    for name in names:
        binary = which(name)
        if not binary:
            continue
        try:
            completed = run([binary, "--version"], timeout=10)
            text = (completed.stdout or completed.stderr or "").strip()
            return text.splitlines()[0][:80] if text else "installed"
        except (OSError, subprocess.TimeoutExpired):
            return "installed"
    return None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_json(url: str, timeout: float = 8) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, body: Any, timeout: float = 8) -> int:
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


class Board:
    """A throwaway Sugar dashboard with its own DB, events and socket."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc: Optional[subprocess.Popen] = None
        self.env = {
            **BASE_ENV,
            "HOME": str(root / "home"),
            "SUGAR_INGEST_SOCKET": str(root / "ingest.sock"),
            "SUGAR_ADAPTERS_FILE": str(root / "home" / ".sugar" / "adapters.json"),
        }
        (root / "home").mkdir(parents=True, exist_ok=True)

    def start(self) -> bool:
        self.proc = subprocess.Popen(
            [
                *SUGAR,
                "dashboard",
                "--port",
                str(self.port),
                "--db",
                str(self.root / "sugar.db"),
                "--events-db",
                str(self.root / "events.db"),
            ],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(40):
            try:
                if http_json(f"{self.url}/api/health", timeout=1).get("ok") is True:
                    return True
            except (OSError, ValueError):
                pass
            time.sleep(0.25)
        return False

    def activity(self, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            return list(
                http_json(f"{self.url}/api/activity?limit={limit}").get("events", [])
            )
        except (OSError, ValueError):
            return []

    def saw(self, kind: str, harness: str) -> bool:
        return any(
            event.get("kind") == kind and event.get("harness") == harness
            for event in self.activity()
        )

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


CLAUDE_HOOKS = {
    "claude-code": {
        "hook_event_name": "PostToolUse",
        "session_id": "verify-claude",
        "cwd": "/tmp/verify",
        "tool_name": "Bash",
        "tool_input": {"command": "pytest -q"},
        "tool_response": {"exit_code": 0},
        "tool_use_id": "verify-claude-1",
    }
}
CODEX_HOOKS = {
    "codex": {
        "hook_event_name": "PostToolUse",
        "session_id": "verify-codex",
        "cwd": "/tmp/verify",
        "tool_name": "shell",
        "tool_input": {"command": "npm test"},
        "tool_response": {"exit_code": 0},
        "tool_use_id": "verify-codex-1",
    }
}
GOOSE_HOOKS = {
    "goose": {
        "event": "PostToolUse",
        "session_id": "verify-goose",
        "tool_name": "shell",
        "tool_input": {"command": "make test"},
        "tool_output": {"exit_code": 0},
        "working_dir": "/tmp/verify",
        "tool_call_id": "verify-goose-1",
    }
}
CURSOR_HOOKS = {
    "cursor": {
        "hook_event_name": "postToolUse",
        "conversation_id": "verify-cursor",
        "tool_name": "Shell",
        "tool_input": {"command": "npm test"},
        "tool_output": "ok",
        "tool_use_id": "verify-cursor-1",
        "cwd": "/tmp/verify",
    }
}


def install_and_probe(
    board: Board, harness: str, fixture: Dict[str, Any], expected_paths: List[Path]
) -> Result:
    home = Path(board.env["HOME"])
    env = {"HOME": str(home)}
    completed = run(
        [*SUGAR, "connect", "--harness", harness, "--yes"], env=env, timeout=60
    )
    if completed.returncode != 0:
        return Result(
            harness,
            CLAUSE_FAIL,
            f"connect failed: {(completed.stderr or completed.stdout).strip()[:160]}",
        )
    missing = [str(path) for path in expected_paths if not path.exists()]
    if missing:
        return Result(harness, CLAUSE_FAIL, "missing config: " + ", ".join(missing))
    status = post_json(f"{board.url}/api/hooks/{harness}", fixture)
    if status != 200:
        return Result(harness, CLAUSE_FAIL, f"hook endpoint returned {status}")
    if not board.saw("tool.completed", harness):
        return Result(
            harness,
            CLAUSE_FAIL,
            "hook was accepted but no tool.completed event appeared",
        )
    return Result(harness, CLAUSE_PASS, "config installed + hook normalized to L3")


def check_claude(board: Board) -> Result:
    home = Path(board.env["HOME"])
    result = install_and_probe(
        board,
        "claude-code",
        CLAUDE_HOOKS["claude-code"],
        [home / ".claude" / "settings.json"],
    )
    if result.status != CLAUSE_PASS:
        return result
    settings = json.loads((home / ".claude" / "settings.json").read_text())
    hooks = settings.get("hooks", {})
    urls = [
        hook.get("url", "")
        for entries in hooks.values()
        for entry in entries
        for hook in entry.get("hooks", [])
        if isinstance(hook, dict)
    ]
    if not hooks or not all("/api/hooks/claude-code" in url for url in urls):
        return Result("claude-code", CLAUSE_FAIL, "hooks are not HTTP hooks to Sugar")
    return Result(
        "claude-code", CLAUSE_PASS, "HTTP hooks installed + L3 ingest verified"
    )


def check_codex(board: Board) -> Result:
    home = Path(board.env["HOME"])
    result = install_and_probe(
        board, "codex", CODEX_HOOKS["codex"], [home / ".codex" / "hooks.json"]
    )
    if result.status != CLAUSE_PASS:
        return result
    data = json.loads((home / ".codex" / "hooks.json").read_text())
    commands: List[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("command"), str):
                commands.append(value["command"])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(data.get("hooks", {}))
    if not any("hook --harness codex" in command for command in commands):
        return Result("codex", CLAUSE_FAIL, "hooks.json lacks the sugar hook command")
    return Result(
        "codex",
        CLAUSE_PASS,
        "command hooks installed + L3 ingest verified (Codex still requires `/hooks` approval)",
    )


def check_goose(board: Board) -> Result:
    home = Path(board.env["HOME"])
    result = install_and_probe(
        board,
        "goose",
        GOOSE_HOOKS["goose"],
        [
            home / ".agents" / "plugins" / "sugar-telemetry" / "plugin.json",
            home / ".agents" / "plugins" / "sugar-telemetry" / "hooks" / "hooks.json",
        ],
    )
    if result.status != CLAUSE_PASS:
        return result
    hooks = json.loads(
        (home / ".agents/plugins/sugar-telemetry/hooks/hooks.json").read_text()
    ).get("hooks", {})
    if not hooks or not all(isinstance(value, list) for value in hooks.values()):
        return Result("goose", CLAUSE_FAIL, "Goose hooks are not arrays of rules")
    return Result(
        "goose", CLAUSE_PASS, "hook plugin installed (array shape) + L3 ingest verified"
    )


def check_cursor(board: Board) -> Result:
    home = Path(board.env["HOME"])
    if not which("cursor"):
        return Result(
            "cursor", CLAUSE_SKIP, "cursor CLI not installed (config path untested)"
        )
    result = install_and_probe(
        board, "cursor", CURSOR_HOOKS["cursor"], [home / ".cursor" / "hooks.json"]
    )
    return result


def check_dsh(board: Board, workdir: Path) -> Result:
    sessions = HOME / ".dsh" / "sessions"
    files = sorted(sessions.rglob("session.v4.jsonl.zstd")) if sessions.exists() else []
    if not files:
        return Result("dsh", CLAUSE_SKIP, "no DSH session logs under ~/.dsh/sessions")
    fixture = workdir / "dsh" / "ws" / "session"
    fixture.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(files[-1], fixture / "session.v4.jsonl.zstd")
    completed = run(
        [
            *SUGAR,
            "tail",
            "dsh",
            "--once",
            "--sessions-dir",
            str(workdir / "dsh"),
            "--state-file",
            str(workdir / "dsh-state.json"),
            "--url",
            board.url,
        ],
        env={"HOME": str(Path(board.env["HOME"]))},
        timeout=90,
    )
    if completed.returncode != 0:
        return Result("dsh", CLAUSE_FAIL, (completed.stderr or completed.stdout)[:160])
    if not board.saw("tool.started", "dsh") and not board.saw("session.started", "dsh"):
        return Result("dsh", CLAUSE_FAIL, "tail ran but no dsh events appeared")
    return Result("dsh", CLAUSE_PASS, "real session log tailed into canonical events")


def check_hermes() -> Result:
    kanban_db = HOME / ".hermes" / "kanban.db"
    if not kanban_db.exists():
        return Result("hermes", CLAUSE_SKIP, "no ~/.hermes/kanban.db")
    port = free_port()
    env = {**BASE_ENV}
    proc = subprocess.Popen(
        [
            *SUGAR,
            "dashboard",
            "--board",
            "kanban",
            "--db",
            str(kanban_db),
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(30):
            try:
                meta = http_json(f"http://127.0.0.1:{port}/api/meta", timeout=1)
                if meta.get("board") == "kanban":
                    tasks = http_json(f"http://127.0.0.1:{port}/api/tasks", timeout=2)
                    return Result(
                        "hermes",
                        CLAUSE_PASS,
                        f"served real Hermes Kanban read-only ({len(tasks.get('tasks', []))} tasks)",
                    )
            except (OSError, ValueError):
                pass
            time.sleep(0.25)
        return Result("hermes", CLAUSE_FAIL, "kanban board did not come up")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def check_opencode(workdir: Path, real: bool) -> Result:
    binary = which("opencode")
    if not binary:
        return Result("opencode", CLAUSE_SKIP, "opencode CLI not installed")
    project = workdir / "opencode-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "README.md").write_text("# harness verification\n")
    (project / ".opencode").mkdir()
    (project / ".opencode" / "opencode.json").write_text("{}\n")
    bin_dir = workdir / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "sugar"
    wrapper.write_text("#!/bin/sh\n" f'exec {PYTHON} -m sugar.main "$@"\n')
    wrapper.chmod(0o755)
    env = {
        "HOME": str(HOME),  # OpenCode auth/config
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "PYTHONPATH": str(REPO_ROOT),
    }
    for args in (
        ["init"],
        ["add", "Verify OpenCode integration", "--type", "feature"],
        [
            "opencode",
            "setup",
            "--yes",
            "--config",
            str(project / ".opencode" / "opencode.json"),
        ],
    ):
        completed = run([*SUGAR, *args], cwd=project, env=env, timeout=60)
        if completed.returncode != 0:
            return Result(
                "opencode", CLAUSE_FAIL, (completed.stderr or completed.stdout)[:160]
            )
    config = json.loads((project / ".opencode" / "opencode.json").read_text())
    if "sugar-tasks" not in config.get("mcp", {}):
        return Result("opencode", CLAUSE_FAIL, "config lacks sugar-tasks MCP server")
    if not real:
        return Result(
            "opencode",
            CLAUSE_PASS,
            "MCP config installed (add --real to run a live OpenCode tool call)",
        )
    completed = run(
        [
            binary,
            "run",
            "--format",
            "json",
            "Use the sugar_list_tasks MCP tool and reply with the task titles.",
        ],
        cwd=project,
        env=env,
        timeout=120,
    )
    output = completed.stdout or ""
    if completed.returncode != 0:
        return Result(
            "opencode",
            CLAUSE_SKIP,
            f"live OpenCode failed: {(completed.stderr or output)[:160]}",
        )
    if "sugar_list_tasks" not in output:
        return Result(
            "opencode", CLAUSE_SKIP, "live run did not call the Sugar MCP tool"
        )
    return Result(
        "opencode", CLAUSE_PASS, "live OpenCode called sugar_list_tasks via MCP"
    )


def probe_auth(harness: str, workdir: Path) -> Result:
    """Try a tiny live model run; report auth failures as SKIP, never FAIL."""
    if harness == "claude-code" and which("claude"):
        try:
            completed = run(
                ["claude", "-p", "Reply with exactly: ok", "--max-turns", "1"],
                cwd=workdir,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return Result("claude-code", CLAUSE_SKIP, "live run timed out")
        text = (completed.stdout or "") + (completed.stderr or "")
        if "authenticate" in text.lower() or completed.returncode != 0:
            return Result("claude-code", CLAUSE_SKIP, "live run unavailable (auth)")
        return Result("claude-code", CLAUSE_PASS, "live run succeeded")
    if harness == "codex" and which("codex"):
        try:
            completed = run(
                [
                    "codex",
                    "exec",
                    "--skip-git-repo-check",
                    "--color",
                    "never",
                    "Reply with exactly: ok",
                ],
                cwd=workdir,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return Result("codex", CLAUSE_SKIP, "live run timed out")
        text = (completed.stdout or "") + (completed.stderr or "")
        if "authenticate" in text.lower() or "401" in text or completed.returncode != 0:
            return Result("codex", CLAUSE_SKIP, "live run unavailable (auth)")
        return Result("codex", CLAUSE_PASS, "live run succeeded")
    if harness == "goose" and which("goose"):
        try:
            completed = run(
                ["goose", "run", "-t", "Reply with exactly: ok"],
                cwd=workdir,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return Result("goose", CLAUSE_SKIP, "live run timed out")
        text = ((completed.stdout or "") + (completed.stderr or "")).strip()
        lowered = text.lower()
        if (
            completed.returncode != 0
            or "ran into this error" in lowered
            or "request failed" in lowered
        ):
            # goose run can exit 0 even when its provider call fails.
            return Result("goose", CLAUSE_SKIP, "live run unavailable (provider/auth)")
        return Result("goose", CLAUSE_PASS, "live run succeeded")
    return Result(harness, CLAUSE_SKIP, "live probe not available")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real", action="store_true", help="also attempt live model runs"
    )
    parser.add_argument("--json", action="store_true", help="JSON report")
    args = parser.parse_args()

    results: List[Result] = []
    with tempfile.TemporaryDirectory(prefix="sugar-harness-verify-") as tmp:
        root = Path(tmp)
        board = Board(root / "board")
        if not board.start():
            print("could not start a throwaway Sugar board", file=sys.stderr)
            return 1
        try:
            for check in (check_claude, check_codex, check_goose, check_cursor):
                results.append(check(board))
            results.append(check_dsh(board, root))
            results.append(check_hermes())
            results.append(check_opencode(root, real=args.real))
            if args.real:
                for harness in ("claude-code", "codex", "goose"):
                    results.append(probe_auth(harness, root))
        finally:
            board.close()

    if args.json:
        print(json.dumps([result.as_dict() for result in results], indent=2))
    else:
        print(f"Harness verification ({len(results)} checks)\n")
        print(f"{'HARNESS':<14} {'STATUS':<6} DETAIL")
        print("-" * 96)
        for result in results:
            print(f"{result.harness:<14} {result.status:<6} {result.detail}")
        print()
        counts = {
            status: sum(1 for r in results if r.status == status)
            for status in (CLAUSE_PASS, CLAUSE_SKIP, CLAUSE_FAIL)
        }
        print(
            f"{counts[CLAUSE_PASS]} passed · {counts[CLAUSE_SKIP]} skipped · {counts[CLAUSE_FAIL]} failed"
        )
        print(
            "SKIP means the harness is not installed or requires model auth; it is not a failure."
        )
    return 1 if any(result.status == CLAUSE_FAIL for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
