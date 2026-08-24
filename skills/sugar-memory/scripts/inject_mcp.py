"""Inject the sugar-memory MCP server into opencode's config.

Harbor's opencode agent writes ~/.config/opencode/opencode.json right before
running opencode, which would clobber any pre-agent config. This script runs
as a background watcher from pre_agent_setup: it waits for that file to be
written, merges the sugar-memory MCP server into it, and exits. opencode then
starts with the memory MCP tools available.

Usage:
    python3 /workspace/scripts/inject_mcp.py --background
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path


CONFIG_PATH = Path.home() / ".config" / "opencode" / "opencode.json"
MCP_SERVER = {
    "name": "sugar-memory",
    "type": "local",
    "command": ["sugar", "mcp", "memory"],
}

POLL_INTERVAL = 0.5
MAX_WAIT = 120.0


def merge(config: dict) -> dict:
    config = dict(config)
    mcp = dict(config.get("mcp") or {})
    mcp[MCP_SERVER["name"]] = {
        "type": MCP_SERVER["type"],
        "command": MCP_SERVER["command"],
    }
    config["mcp"] = mcp
    return config


def inject_once() -> bool:
    if not CONFIG_PATH.exists():
        return False
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    merged = merge(data)
    CONFIG_PATH.write_text(json.dumps(merged, indent=2))
    return True


def background():
    start = time.monotonic()
    while time.monotonic() - start < MAX_WAIT:
        if inject_once():
            print(f"[inject_mcp] merged sugar-memory into {CONFIG_PATH}", flush=True)
            return 0
        time.sleep(POLL_INTERVAL)
    # File may not exist yet if agent setup writes later; retry a few more times
    for _ in range(20):
        time.sleep(POLL_INTERVAL)
        if inject_once():
            print(f"[inject_mcp] merged sugar-memory into {CONFIG_PATH}", flush=True)
            return 0
    print("[inject_mcp] WARN: opencode.json never appeared; memory MCP not injected", flush=True)
    return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true")
    args = parser.parse_args()
    if args.background:
        sys.exit(background())
    sys.exit(0 if inject_once() else 1)


if __name__ == "__main__":
    main()
