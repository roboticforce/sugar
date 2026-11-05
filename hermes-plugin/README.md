# Sugar - Hermes memory provider plugin

[Sugar](https://github.com/roboticforce/sugar) as a first-class Hermes memory
provider: durable, local-first agent memory in SQLite on your machine, with
project and global scopes and guideline-aware search. No cloud, no API key,
nothing leaves the machine.

## What it gives Hermes

- **Automatic recall** - each turn, Sugar searches memory and injects relevant
  decisions, error patterns, and guidelines into context. Recall is refreshed
  in the background between turns and cached for the next one.
- **Tools** the agent can call:
  - `sugar_store` - save a decision, preference, error pattern, research
    finding, or outcome (scope `project` or `global`)
  - `sugar_search` - semantic + keyword search across both scopes
  - `sugar_list_recent` - browse recent memories by type
- **7 memory types** - decision, preference, error_pattern, research, outcome,
  guideline, file_context - the same vocabulary Sugar uses in Claude Code,
  OpenCode, and via the `sugar mcp memory` MCP server.
- **Project detection follows the session** - the project store is the nearest
  `.sugar/memory.db` at or above the workspace Hermes hands the provider
  (`initialize(cwd=...)`), falling back to the process working directory, so
  parallel Desktop/gateway sessions do not share the wrong project memory.
- **Backup-aware** - `hermes backup` / `hermes import` include both stores.
- **Pre-compress checkpoints (API v2)** - before Hermes compresses a
  conversation, Sugar durably archives the user/assistant evidence to local
  memory, so the detail survives the lossy rewrite. This provider advertises
  checkpoint API v2, the fail-closed contract: a normal return means the
  archive is committed, and any failure raises so the uncompressed transcript
  is kept. Writes are keyed by transcript digest, so retries do not duplicate.
- **Built-in memory mirroring** - writes to Hermes's own `MEMORY.md` / `USER.md`
  are mirrored into Sugar (user profile as `preference`/global, agent notes as
  `research`/project), so the two memory systems stay searchable in one place.
  Removals are not mirrored: Sugar is an append-only archive and the native
  notification carries no stable entry id.
- **Bundled skill** - `sugar:memory` teaches the agent when to recall and what
  is worth storing, shipped with the plugin.

## Install

```bash
hermes plugins install sugar
```

Requires the `sugarai` package in the same Python environment as Hermes:

```bash
pip install "sugarai>=3.10.3,<3.11"
```

Search is keyword-only (SQLite FTS5) unless you also install the vector extra:

```bash
pip install "sugarai[memory]>=3.10.3,<3.11"   # adds sentence-transformers + sqlite-vec
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
| `project_dir` | `SUGAR_PROJECT_DIR` | auto-detect | Directory whose `.sugar/memory.db` is the project store; empty = use the session workspace, walking up for the nearest `.sugar/` |
| `sync_turns` | `SUGAR_SYNC_TURNS` | `false` | Opt-in compact per-turn notes (deliberate `sugar_store` calls are preferred) |
| `search_limit` | `SUGAR_SEARCH_LIMIT` | `5` | Memories recalled per turn (1-20; malformed or out-of-range values fall back to 5) |

## Where data lives

- Global store: `~/.sugar/memory.db` (cross-project guidelines and knowledge)
- Project store: nearest `.sugar/memory.db` at or above the session workspace
  (or `project_dir` if configured)
- Memory is yours and local. Sugar is AGPL-3.0-or-later.
