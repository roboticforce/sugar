# Sugar - Hermes memory provider plugin

[Sugar](https://github.com/roboticforce/sugar) as a first-class Hermes memory
provider: durable, local-first agent memory in SQLite on your machine, with
project and global scopes and guideline-aware search. No cloud, no API key,
nothing leaves the machine.

## Install (two steps, in order)

**Installing the plugin is not the same as activating it.** Hermes memory
providers are inert until `memory.provider` names one, so if you stop after
step 1 the agent gets no tools and no automatic recall.

### 1. Install the plugin

```bash
hermes plugins install sugar
```

### 2. Activate it as the memory provider

```bash
hermes memory setup sugar      # or: hermes memory setup, then pick "sugar"
hermes memory status           # should name sugar as the active provider
hermes gateway restart         # or restart the Hermes desktop app
```

> **Why the second step?** `plugins.enabled` and `memory.provider` are separate
> settings. A memory plugin can be installed and enabled and still do nothing
> until it is selected. If `memory.provider:` is empty in `$HERMES_HOME/config.yaml`,
> you have not activated it yet.

### Prerequisite: the `sugarai` package

The plugin shells into the `sugar` CLI, so `sugarai` must be installed in the
Python environment Hermes uses, and `sugar` must be on `PATH`:

```bash
pip install "sugarai>=3.10.6,<3.11"
```

Vector search is **not** installed with the plugin, and that is deliberate: the
`[memory]` extra resolves to ~69 packages including `torch`, which has no place
in a host's locked environment by default. Without it, search is SQLite FTS5
keyword search - functional, just not semantic.

Hermes has no supported way for a provider to offer an *optional* extra yet
([NousResearch/hermes-agent#152776](https://github.com/NousResearch/hermes-agent/issues/152776)),
so there is currently no durable opt-in. Installing it by hand into the runtime
venv works, but the runtime environment is reconciled from Hermes's lock, so a
later `hermes pm` install/repair/update can drop it.

If you do enable it, backfill your existing memories - vectors are only created
when a memory is written, so older rows stay keyword-only until you run:

```bash
sugar memory reindex
```

### Verify before you trust it

1. `hermes memory status` names **sugar**.
2. Ask the agent: *"what memory tools do you have?"* - it should list
   `sugar_store`, `sugar_search`, `sugar_list_recent`.
3. Store and recall something:
   ```bash
   sugar recall "deploy"
   ```
4. On a turn with relevant memories, a recall indicator appears:
   **🧠 Sugar — recalled N memories**.

If any of those fail, see Troubleshooting below before assuming the plugin is broken.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Agent has no `sugar_*` tools | Plugin installed but never selected as the memory provider | `hermes memory setup sugar` then `hermes gateway restart` |
| `memory.provider:` is `''` in `config.yaml` | Same - activation missing | Same |
| `hermes memory status` shows the built-in provider | Same | Same |
| Tools work, but no automatic recall between turns | Gateway still running the old config | `hermes gateway restart` (or restart the desktop app) |
| `sugar: command not found` from the plugin | CLI not on the Python env Hermes spawns | Install `sugarai` into that env; check with `hermes memory status` |
| Recall finds nothing from this repository | The session workspace has no `.sugar/` above it, so only the global store is searched | Run `sugar init` in the project, or set `project_dir` |
| A `scope: "project"` write comes back as `global`, or scope labels look wrong | The session workspace is `$HOME` (or above it), so the nearest `.sugar/` **is** the global store - there is no separate project store for that session | Expected. Work from a repo with its own `.sugar/`, or run `sugar init` there. This provider reports the promotion instead of mislabelling the entry |
| Obvious keyword matches are missed | Keyword-only search (no vector extra) | Semantic search is not installed by default; see the vector-extra note above and NousResearch/hermes-agent#152776 |
| Memories exist but recall is empty on the first turn | Recall is cached; the first turn searches, later turns return the cache | Normal - ask something relevant, or run `sugar recall "<text>"` directly |

## What it gives Hermes

- **Automatic recall** - each turn, Sugar searches memory and injects relevant
  decisions, error patterns, and guidelines into context. Recall is refreshed
  in the background between turns and cached for the next one.
- **Tools** the agent can call:
  - `sugar_store` - save a decision, preference, error pattern, research
    finding, or outcome (scope `project` or `global`)
  - `sugar_search` - keyword search by default, semantic when the vector extra
    is installed, across both scopes
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

## What this does not do

- **No semantic search by default.** Plain `sugarai` installs keyword (FTS5)
  search; install the `[memory]` extra for vectors.
- **No cloud sync and no cross-machine sharing** - by design.
- **Removals from built-in memory are not mirrored into Sugar.**
- **Not a DSH-style tool provider** - automatic per-turn recall is specific to
  Hermes's `MemoryProvider` seam. On harnesses without that seam, Sugar is a set
  of tools the model calls.
