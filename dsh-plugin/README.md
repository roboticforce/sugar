# Sugar for DeepSeek Harness

[Sugar](https://github.com/roboticforce/sugar) as persistent memory for
[DeepSeek Harness](https://github.com/deepseek-ai): local-first agent memory in
SQLite on your machine, with project and global scopes and guideline-aware
search. No cloud, no API key, nothing leaves the machine.

This package is a **DSH plugin bundle** — a declarative profile patch, no
JavaScript. It wires Sugar's MCP memory server into a DSH profile as one
`dsh-mcp-client` entry.

## What the agent gets

Five tools, namespaced like every other MCP server in DSH:

| Tool | Purpose |
|---|---|
| `mcp__sugar__search_memory` | Semantic + keyword search across project and global memory |
| `mcp__sugar__store_learning` | Save a decision, preference, error pattern, research finding, or outcome |
| `mcp__sugar__get_project_context` | Organized recall: preferences, recent decisions, file context, error patterns, guidelines |
| `mcp__sugar__recall` | One-shot recall formatted for prompt injection |
| `mcp__sugar__list_recent_memories` | Browse newest memories, optionally by type |

Memory lands in `~/.sugar/memory.db` (global) and the nearest `.sugar/memory.db`
at or above the project root (project-scoped).

## Install

### 1. Prerequisites

```bash
pip install "sugarai[mcp]"
```

The MCP extra is **required** — `sugar mcp memory` will not start without it, and
the error message in older releases points at the wrong extra.

Confirm the server works on its own:

```bash
sugar mcp memory     # should block, waiting for MCP input; Ctrl-C to exit
```

`troubleshooting:` if DSH reports the plugin failed to activate, it almost always
means `sugar` is not on DSH's `PATH`. Desktop apps launched from Finder do not
inherit your shell `PATH`. Either install the CLI somewhere on the default path,
or edit the patch below to use an absolute `command:`.

### 2. Install the plugin

**Through the UI:** Plugins → **Add plugin** → paste this directory's absolute
path (for a checkout of the Sugar repo that is `<repo>/dsh-plugin`) → **Install**.

**Or by hand** — append to `~/.dsh/profiles/<profile>/cordis.patch.yml`:

```yaml
- id: mcp-sugar
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: sugar
    transport: stdio
    command: sugar
    args: ['mcp', 'memory']
```

Restart DSH if the profile does not hot-reload, then ask the agent to "search
memory" — the `mcp__sugar__*` tools should be available.

## Skills

DSH discovers skills from `<repo>/.dsh/skills`, `<repo>/.agents/skills`,
`~/.dsh/skills`, and `~/.agents/skills`. It deliberately does **not** scan nested
`**/SKILL.md`, so Sugar's `skills/` directory is invisible to it. To expose
Sugar's memory skill, link it into an `.agents/skills` root:

```bash
mkdir -p ~/.agents/skills
ln -s "$(pwd)/skills/sugar-memory" ~/.agents/skills/sugar-memory
```

DSH resolves symlinked skill directories, and this keeps one source of truth.

## What this plugin does *not* do

Hermes got a native memory-provider plugin in this repo, with automatic per-turn
recall and pre-compress checkpointing. DSH has **no memory-provider seam** (305
`@deepseek-ai/dsh-*` packages, none of them memory), so on DSH Sugar is a set of
tools the model chooses to call, not a layer that acts on its own. Automatic
recall would need a cordis plugin contributing to `ctx.systemPrompt` plus a
`dsh-compaction` hook — a TypeScript package, not a patch.

## Layout

```
dsh-plugin/
├── package.json        # bundle declaration: dsh.bundle.patch -> cordis.patch.yml
├── cordis.patch.yml    # the MCP client entry
└── README.md
```

`package.json` declares `engines.dsh` so DSH's pre-install compatibility check
can reject the plugin on an older runtime instead of failing at activation.
