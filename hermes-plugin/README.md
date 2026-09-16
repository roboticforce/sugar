# Sugar - Hermes memory provider plugin

[Sugar](https://github.com/roboticforce/sugar) as a first-class Hermes memory
provider: durable, local-first agent memory in SQLite on your machine, with
project and global scopes and guideline-aware search. No cloud, no API key,
nothing leaves the machine.

## What it gives Hermes

- **Automatic recall** - each turn, Sugar searches memory and injects relevant
  decisions, error patterns, and guidelines into context (cached and refreshed
  in the background between turns).
- **Tools** the agent can call:
  - `sugar_store` - save a decision, preference, error pattern, research
    finding, or outcome (scope `project` or `global`)
  - `sugar_search` - semantic + keyword search across both scopes
  - `sugar_list_recent` - browse recent memories by type
- **7 memory types** - decision, preference, error_pattern, research, outcome,
  guideline, file_context - the same vocabulary Sugar uses in Claude Code,
  OpenCode, and via the `sugar mcp memory` MCP server.

## Install

```bash
hermes plugins install sugar
```

Requires the `sugarai` package in the same Python environment as Hermes:

```bash
pip install sugarai==3.10.1
```

Then activate it as the memory provider:

```bash
hermes memory setup   # choose "sugar"
# or set in config.yaml:
#   memory:
#     provider: sugar
```

Restart the gateway afterwards (`hermes gateway restart`).

## Configuration

Settings live in `$HERMES_HOME/sugar.json` (created via `hermes memory setup`
or `save_config`), with env-var fallbacks:

| Key | Env var | Default | Meaning |
|-----|---------|---------|---------|
| `project_dir` | `SUGAR_PROJECT_DIR` | auto-detect | Directory whose `.sugar/memory.db` is the project store; empty = walk up from the working directory like Sugar's own tooling |
| `sync_turns` | `SUGAR_SYNC_TURNS` | `false` | Opt-in compact per-turn notes (deliberate `sugar_store` calls are preferred) |
| `search_limit` | `SUGAR_SEARCH_LIMIT` | `5` | Memories recalled per turn |

## Where data lives

- Global store: `~/.sugar/memory.db` (cross-project guidelines and knowledge)
- Project store: nearest `.sugar/memory.db` at or above the working directory
  (or `project_dir` if configured)
- Memory is yours and local. Sugar is AGPL-3.0-or-later.