# 🍰 Sugar

The autonomous layer for AI coding agents.

<!-- mcp-name: io.github.cdnsteve/sugar -->

The autonomous layer for AI coding agents. Sugar manages your task queue, runs 24/7, and ships working code while you focus on what matters.

## What It Does

Sugar adds **autonomy and persistence** to your AI coding workflow. Instead of one-off interactions:

- **Continuous execution** - Runs 24/7, working through your task queue
- **Agent-agnostic** - Works with Claude Code, OpenCode, Aider, or any AI CLI
- **Delegate and forget** - Hand off tasks from any session
- **Builds features** - Takes specs, implements, tests, commits working code
- **Fixes bugs** - Reads error logs, investigates, implements fixes
- **GitHub integration** - Creates PRs, updates issues, tracks progress

You plan the work. Sugar executes it.

**Works with:** [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | [OpenCode](https://github.com/opencode-ai/opencode) | [Aider](https://aider.chat) | [Goose](https://block.github.io/goose) | Any CLI-based AI agent

## Install

**Recommended: pipx** (install once, use everywhere)
```bash
pipx install sugarai
```

This gives you a global `sugar` command that works in any project. Each project gets its own isolated config and database in a `.sugar/` folder.

**Upgrade / Uninstall:**
```bash
pipx upgrade sugarai    # Upgrade to latest version
pipx uninstall sugarai  # Remove completely
```

<details>
<summary>Other installation methods</summary>

**pip** (requires venv activation each session)
```bash
pip install sugarai
```

**uv** (fast alternative to pip)
```bash
uv pip install sugarai
```

**With GitHub integration:**
```bash
pipx install 'sugarai[github]'
```

</details>

## Quick Start

Sugar is **project-local** - each project has its own isolated task queue and config.

```bash
# Navigate to your project
cd ~/dev/my-app

# Initialize Sugar (creates .sugar/ folder)
sugar init

# This creates:
# - .sugar/sugar.db     (task queue database)
# - .sugar/config.yaml  (project settings)
# - .sugar/prompts/     (custom prompts)

# Add tasks to the queue
sugar add "Fix authentication timeout" --type bug_fix --urgent
sugar add "Add user profile settings" --type feature

# Start the autonomous loop
sugar run
```

Sugar will:
1. Pick up tasks from the queue
2. Execute them using your configured AI agent
3. Run tests and verify changes
4. Commit working code
5. Move to the next task

It keeps going until the queue is empty (or you stop it).

**Delegate from Claude Code:**
```
/sugar-task "Fix login timeout" --type bug_fix --urgent
```
Sugar picks it up and works on it while you keep coding.

## How It Works: Project-Local Architecture

```
Global Installation (pipx)
└── sugar CLI (~/.local/bin/sugar)

Project A                          Project B
~/dev/frontend-app/                ~/dev/backend-api/
├── .sugar/                        ├── .sugar/
│   ├── sugar.db                   │   ├── sugar.db
│   ├── config.yaml                │   ├── config.yaml
│   └── prompts/                   │   └── prompts/
├── src/                           ├── main.py
└── tests/                         └── requirements.txt

Running `sugar` uses the .sugar/ folder in your current directory
```

**One global CLI, many isolated projects.** Like `git` - one installation, per-project repositories.

## FAQ

### Do I need to install Sugar in every project?

**No!** Install Sugar once with `pipx install sugarai` and use it everywhere.

The `sugar` command is globally available, but it reads configuration from the `.sugar/` folder in your **current directory**:

- **Global CLI access**: Run `sugar` from anywhere without venv activation
- **Project-local state**: Each project's tasks and config stay isolated
- **No conflicts**: Work on multiple projects simultaneously

### Can I run Sugar on multiple projects at the same time?

Yes! Each project has its own isolated database.

```bash
# Terminal 1
cd ~/dev/frontend-app
sugar run

# Terminal 2 (simultaneously)
cd ~/dev/backend-api
sugar run
```

The two Sugar instances won't interfere with each other.

### What happens if I run `sugar` outside a project folder?

Sugar will show a friendly error:

```
❌ Not a Sugar project

   Could not find: .sugar/config.yaml

   Run 'sugar init' to initialize Sugar in this directory.
```

### Why pipx over pip?

| Installation | Global access? | Requires venv? |
|--------------|----------------|----------------|
| `pip install sugarai` | Only in active venv | Yes |
| `pipx install sugarai` | Yes, always | No |

With pipx, Sugar's dependencies don't conflict with your project's dependencies.

### Should I commit .sugar/ to git?

**Recommended .gitignore:**
```gitignore
.sugar/sugar.db       # Task queue is personal
.sugar/sugar.log      # Logs contain local paths
.sugar/*.db-*         # SQLite temp files
```

**DO commit:** `.sugar/config.yaml` and `.sugar/prompts/` to share settings with your team.

## Features

**Task Management**
- Rich task context with priorities and metadata
- Custom task types for your workflow
- Queue management and filtering

**Task Orchestration**
- Auto-decomposes complex features into subtasks
- 4-stage workflow: Research → Planning → Implementation → Review
- Specialist agent routing (frontend, backend, QA, security, DevOps)
- Parallel execution with dependency management

**Autonomous Execution**
- Specialized task agents (UX, backend, QA, security, DevOps)
- Automatic retries on failures
- Quality checks and testing

**GitHub Integration**
- Reads issues, creates PRs
- Updates issue status automatically
- Commits with proper messages

**Ralph Wiggum Integration**
- Iterative execution for complex tasks
- Self-correcting loops until tests pass
- Prevents single-shot failures

**Full docs:** [docs/ralph-wiggum.md](docs/ralph-wiggum.md)

## Configuration

`.sugar/config.yaml` is auto-generated on `sugar init`. Key settings:

```yaml
sugar:
  dry_run: false              # Set to true for testing
  loop_interval: 300          # 5 minutes between cycles
  max_concurrent_work: 3      # Parallel task execution

claude:
  enable_agents: true         # Use specialized Claude agents

discovery:
  github:
    enabled: true
    repo: "user/repository"
  error_logs:
    enabled: true
    paths: ["logs/errors/"]
```

## Integrations

### Claude Code Plugin

Sugar has native Claude Code integration. Delegate work directly from your Claude sessions.

```
/plugin install roboticforce/sugar
```

**Inside a Claude Code session:**
```
You: "I'm working on auth but need to fix these test failures.
     Can you handle the tests while I finish?"

Claude: "I'll create a Sugar task for the test fixes."

/sugar-task "Fix authentication test failures" --type test --urgent
```

**Available Slash Commands:**
- `/sugar-task` - Create tasks with rich context
- `/sugar-status` - Check queue and progress
- `/sugar-run` - Start autonomous mode

### MCP Server Integration

Sugar provides an MCP server for Goose, Claude Desktop, and other MCP clients.

**Using with Goose:**
```bash
goose configure
# Select "Add Extension" → "Command-line Extension"
# Name: sugar
# Command: npx -y sugarai-mcp
```

**Using with Claude Desktop:**
```json
{
  "mcpServers": {
    "sugar": {
      "command": "npx",
      "args": ["-y", "sugarai-mcp"],
      "env": {
        "SUGAR_PROJECT_ROOT": "/path/to/your/project"
      }
    }
  }
}
```

## Advanced Usage

**Task Orchestration**
```bash
sugar add "Add OAuth authentication" --type feature --orchestrate

# Sugar will:
# 1. RESEARCH - Search best practices, analyze codebase
# 2. PLAN - Create implementation plan with subtasks
# 3. IMPLEMENT - Route subtasks to specialists in parallel
# 4. REVIEW - Code review and test verification

sugar orchestrate <task_id> --stages
```

**Ralph Wiggum (iterative execution)**
```bash
sugar add "Implement rate limiting" --ralph --max-iterations 10
# Iterates until tests pass, not just until code is written
```

**Custom Task Types**
```bash
sugar task-type add deployment --name "Deployment" --emoji "🚀"
sugar add "Deploy to staging" --type deployment
```

**Complex Tasks with Context**
```bash
sugar add "User Dashboard" --json --description '{
  "priority": 5,
  "context": "Complete dashboard redesign",
  "agent_assignments": {
    "frontend_developer": "Implementation",
    "qa_test_engineer": "Testing"
  }
}'
```

## Troubleshooting

**Sugar not finding Claude CLI?**
```yaml
# .sugar/config.yaml
claude:
  command: "/full/path/to/claude"
```

**Tasks not executing?**
```bash
cat .sugar/config.yaml | grep dry_run  # Check dry_run is false
tail -f .sugar/sugar.log                # Monitor logs
sugar run --once                        # Test single cycle
```

**More help:**
- [Troubleshooting Guide](docs/user/troubleshooting.md)
- [GitHub Issues](https://github.com/roboticforce/sugar/issues)

## Documentation

- [Quick Start](docs/user/quick-start.md)
- [CLI Reference](docs/user/cli-reference.md)
- [Task Orchestration](docs/task_orchestration.md)
- [Ralph Wiggum](docs/ralph-wiggum.md)
- [GitHub Integration](docs/user/github-integration.md)
- [Configuration Guide](docs/user/configuration-best-practices.md)

## Requirements

- Python 3.11+
- An AI coding agent CLI:
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (default)
  - [OpenCode](https://github.com/opencode-ai/opencode)
  - [Aider](https://aider.chat)
  - Or any CLI-based AI coding tool

## Contributing

Contributions welcome! See [CONTRIBUTING.md](docs/dev/contributing.md).

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

**Sugar v3.4** - The autonomous layer for AI coding agents

> ⚠️ Sugar is provided "AS IS" without warranty. Review all AI-generated code before use.
