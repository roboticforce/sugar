# Sugar

Autonomous issue resolution for AI-assisted development.

<!-- mcp-name: io.github.cdnsteve/sugar -->

Security scanners find vulnerabilities. Dependabot opens issues. Copilot flags problems.
Sugar reads the issue, writes the fix, runs the tests, and opens the PR.

- **Discovers** - watches your GitHub repo for labeled issues (security, bug, dependabot)
- **Resolves** - reads each issue and implements a fix using Claude
- **Verifies** - runs your test suite and quality gates before committing
- **Ships** - opens a PR referencing the original issue, ready for your review

No issue left sitting in a backlog waiting for someone to have time.

## How Sugar Compares

Most AI dev tools stop at the discovery layer:

```
GitHub Copilot CLI  ->  scan  ->  open issues
Snyk                ->  scan  ->  open issues
Dependabot          ->  scan  ->  open issues
```

Sugar is the resolution layer:

```
Labeled issue appears on GitHub
  -> Sugar picks it up (label filter: "security", "dependabot", "bug")
  -> AI agent reads the issue, analyzes the affected code
  -> Fix implemented, tests run locally
  -> PR opened - you review and merge
```

Configure which labels Sugar watches, point it at your repo, and run `sugar run`.

See [workflow examples](docs/workflows/) for security auto-fix, bug triage, test coverage, and more.

## What Sugar Does

Sugar combines persistent memory with autonomous task execution:

- **Project memory** - Decisions, preferences, error patterns, and research stored per-project
- **Global memory** - Standards and guidelines shared across every project you work on
- **GitHub integration** - Watches for labeled issues and resolves them autonomously
- **Semantic search** - Retrieve relevant context by meaning, not just keywords
- **MCP integration** - Your AI agent reads and writes memory directly during sessions
- **Task queue** - Hand off work to run autonomously, powered by the same memory layer

## Quick Start

```bash
# Install once, use in any project
pipx install sugarai

# Initialize in your project
cd ~/dev/my-app
sugar init

# Store what you know
sugar remember "We use async/await everywhere, never callbacks" --type preference
sugar remember "JWT tokens use RS256, expire in 15 min - see auth/tokens.py" --type decision
sugar remember "When tests fail with import errors, check __init__.py exports first" --type error_pattern

# Retrieve it later
sugar recall "authentication"
sugar recall "how do we handle async"
```

Your AI agent can also read and write memory directly - no copy-pasting required.

## MCP Integration

Connect Sugar's memory to your AI agent so it can access project context automatically.

**Claude Code - Memory server (primary):**
```bash
claude mcp add sugar -- sugar mcp memory
```

**Claude Code - Task server (optional):**
```bash
claude mcp add sugar-tasks -- sugar mcp tasks
```

Once connected, Claude can call `store_learning` to save context mid-session and `search_memories` to pull relevant knowledge before starting work. The memory server works from any directory - global memory is always available even outside a Sugar project.

**Other MCP clients (Goose, Claude Desktop):**
```bash
# Goose
goose configure
# Select "Add Extension" -> "Command-line Extension"
# Name: sugar
# Command: sugar mcp memory

# OpenCode - one command setup
sugar opencode setup
```

## Global Memory (New in 3.9)

Some knowledge belongs to you, not just one project. Coding standards, preferred patterns, security practices - these should follow you everywhere.

```bash
# Store a guideline that applies to all your projects
sugar remember "Always validate and sanitize user input before any DB query" \
  --type guideline --global

sugar remember "Use conventional commits: feat/fix/chore/docs/test" \
  --type guideline --global

# View your global guidelines
sugar recall "security" --global
sugar memories --global

# Search works project-first, but guidelines always surface
sugar recall "database queries"
# Returns: project-specific memories + relevant global guidelines
```

Global memory lives at `~/.sugar/memory.db`. Project memory lives at `.sugar/memory.db`. When you search, project context wins - but `guideline` type memories from global always appear in results so your standards stay visible.

**Via MCP**, pass `scope: "global"` to `store_learning` to save cross-project knowledge directly from your AI session.

**Memory types:** `decision`, `preference`, `file_context`, `error_pattern`, `research`, `outcome`, `guideline`

Full docs: [Memory System Guide](docs/user/memory.md)

## How Memory Works

Sugar uses two SQLite databases and a tiered search strategy.

**Two stores:**
- **Project store** (`.sugar/memory.db`) - context specific to one project
- **Global store** (`~/.sugar/memory.db`) - knowledge that applies everywhere

**Seven memory types**, each with different retrieval behavior:

| Type | Purpose | TTL |
|------|---------|-----|
| `decision` | Architecture and implementation choices | Never |
| `preference` | How you like things done | Never |
| `file_context` | What files and modules do | Never |
| `error_pattern` | Bugs and their fixes | 90 days |
| `research` | API docs, library findings | 60 days |
| `outcome` | What worked, what didn't | 30 days |
| `guideline` | Cross-project standards and best practices | Never |

**Search strategy - project-first with reserved guideline slots:**

1. Search the project store first (local context always wins)
2. Reserve slots for global guidelines (cross-project standards always surface)
3. Fill remaining slots with other global results
4. Deduplicate across both stores

This means a mature project's local context dominates results. A new project with no local memory gets global knowledge automatically. And your guidelines are always visible regardless.

**Search engine:** Semantic search via sentence-transformers (all-MiniLM-L6-v2, 384-dim vectors) with sqlite-vec. Falls back to SQLite FTS5 keyword search, then LIKE queries. No external API calls - everything runs locally.

```bash
# Install with semantic search (recommended)
pipx install 'sugarai[memory]'

# Works without it too - just uses keyword matching
pipx install sugarai
```

**MCP tools available to your AI agent:**

| Tool | What it does |
|------|-------------|
| `search_memory` | Search both stores, returns results with scope labels |
| `store_learning` | Save a memory (pass `scope: "global"` for cross-project) |
| `recall` | Get formatted markdown context for a topic |
| `get_project_context` | Full project summary including global guidelines |
| `list_recent_memories` | Browse recent memories by type |

**MCP resources:**
- `sugar://project/context` - project summary
- `sugar://preferences` - coding preferences
- `sugar://global/guidelines` - cross-project standards

## Task Queue

The task queue lets you hand off work and let it run autonomously. It reads from the same memory store, so Sugar already knows your preferences and patterns before it starts.

```bash
# Add tasks
sugar add "Fix authentication timeout" --type bug_fix --urgent
sugar add "Add user profile settings" --type feature

# Start the autonomous loop
sugar run
```

Sugar picks up tasks, executes them with your configured AI agent, runs tests, commits working code, and moves to the next task. It runs until the queue is empty or you stop it.

**Delegate from Claude Code mid-session:**
```
/sugar-task "Fix login timeout" --type bug_fix --urgent
```

**Advanced task options:**
```bash
# Orchestrated execution (research -> plan -> implement -> review)
sugar add "Add OAuth authentication" --type feature --orchestrate

# Iterative mode - loops until tests pass
sugar add "Implement rate limiting" --ralph --max-iterations 10

# Check queue status
sugar list
sugar status
```

Full docs: [Task Orchestration](docs/task_orchestration.md)

## Supported AI Tools

Works with any CLI-based AI coding agent:

| Agent | Memory MCP | Task MCP | Notes |
|-------|-----------|---------|-------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Yes | Yes | Full support |
| [OpenCode](https://github.com/opencode-ai/opencode) | Yes | Yes | `sugar opencode setup` |
| [Goose](https://block.github.io/goose) | Yes | Yes | Via MCP |
| [Aider](https://aider.chat) | Via CLI | Via CLI | Manual recall |

## Installation

**Recommended: pipx** - installs once, available everywhere, no venv conflicts:
```bash
pipx install sugarai
```

**Upgrade / Uninstall:**
```bash
pipx upgrade sugarai
pipx uninstall sugarai
```

<details>
<summary>Other installation methods</summary>

**pip** (requires venv activation each session)
```bash
pip install sugarai
```

**uv**
```bash
uv pip install sugarai
```

**With semantic search (recommended for memory):**
```bash
pipx install 'sugarai[memory]'
```

**With GitHub integration:**
```bash
pipx install 'sugarai[github]'
```

**All features:**
```bash
pipx install 'sugarai[all]'
```

</details>

Sugar is **project-local** by default. Each project gets its own `.sugar/` folder with its own database and config. Global memory lives at `~/.sugar/`. Like `git` - one installation, per-project state.

## Project Structure

```
~/.sugar/
└── memory.db          # Global memory (guidelines, cross-project knowledge)

~/dev/my-app/
├── .sugar/
│   ├── sugar.db       # Project memory + task queue
│   ├── config.yaml    # Project settings
│   └── prompts/       # Custom agent prompts
└── src/
```

**Recommended .gitignore:**
```gitignore
.sugar/sugar.db
.sugar/sugar.log
.sugar/*.db-*
```

Commit `.sugar/config.yaml` and `.sugar/prompts/` to share settings with your team.

## Configuration

`.sugar/config.yaml` is created on `sugar init`:

```yaml
sugar:
  dry_run: false
  loop_interval: 300
  max_concurrent_work: 3

claude:
  enable_agents: true

discovery:
  github:
    enabled: true
    repo: "user/repository"
```

## Documentation

- [Quick Start](docs/user/quick-start.md)
- [Memory System](docs/user/memory.md)
- [CLI Reference](docs/user/cli-reference.md)
- [Task Orchestration](docs/task_orchestration.md)
- [Goose Integration](docs/user/goose.md)
- [OpenCode Integration](docs/user/opencode.md)
- [GitHub Integration](docs/user/github-integration.md)
- [Configuration Guide](docs/user/configuration-best-practices.md)
- [Troubleshooting](docs/user/troubleshooting.md)

## Requirements

- Python 3.11+
- A CLI-based AI agent: [Claude Code](https://docs.anthropic.com/en/docs/claude-code), [OpenCode](https://github.com/opencode-ai/opencode), [Aider](https://aider.chat), or similar

## Contributing

Contributions welcome. See [CONTRIBUTING.md](docs/dev/contributing.md).

```bash
git clone https://github.com/roboticforce/sugar.git
cd sugar
uv pip install -e ".[dev,test,github]"
pytest tests/ -v
```

## License

**Dual License: AGPL-3.0 + Commercial**

- **Open Source (AGPL-3.0)**: Free for open source and personal use
- **Commercial License**: For proprietary use - [sugar.roboticforce.io/licensing](https://sugar.roboticforce.io/licensing)

---

> Sugar is provided "AS IS" without warranty. Review all AI-generated code before use.
