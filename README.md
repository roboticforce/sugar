# Sugar 🍰

The dev team that never stops.

<!-- mcp-name: io.github.cdnsteve/sugar -->

Autonomous AI development for Claude Code. Sugar builds features, fixes bugs, and ships code while you focus on what matters.

## What It Does

Think of Sugar as **Claude Code with persistence**. Instead of one-off interactions:

- **Continuous execution** - Runs 24/7, working through your task queue
- **Delegate from Claude** - Hand off tasks during interactive sessions
- **Builds features** - Takes specs, implements, tests, commits working code
- **Fixes bugs** - Reads error logs, investigates, implements fixes
- **GitHub integration** - Creates PRs, updates issues, tracks progress
- **Smart discovery** - Finds work from errors, issues, and code analysis

You plan the work. Sugar executes it.

**Works with:** [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | [Goose](https://block.github.io/goose/docs/mcp/sugar-mcp) | [Claude Desktop](https://claude.ai/download)

## Install

```bash
pip install sugarai
```

Or use uv (much faster):
```bash
uv pip install sugarai
```

## Quick Start

```bash
# Initialize in your project
cd your-project
sugar init

# Add tasks to the queue
sugar add "Fix authentication timeout" --type bug_fix --urgent
sugar add "Add user profile settings" --type feature

# Start the loop
sugar run
```

Sugar will:
1. Pick up tasks from the queue
2. Execute them using Claude Code
3. Run tests and verify changes
4. Commit working code
5. Move to the next task

It keeps going until the queue is empty (or you stop it).

**Or delegate from Claude Code:**
```
/sugar-task "Fix login timeout" --type bug_fix --urgent
```
Sugar picks it up and works on it while you keep coding.

## Real Example

**Simple tasks:**
```bash
# Quick task creation
sugar add "Fix authentication timeout" --type bug_fix --urgent
sugar add "Add user profile settings" --type feature --priority 4
```

**Complex tasks with rich context** (recommended for best results):
```bash
sugar add "User Dashboard Redesign" --json --description '{
  "priority": 5,
  "type": "feature",
  "context": "Complete overhaul of user dashboard with modern UI/UX patterns",
  "business_context": "User feedback shows dashboard is confusing. Goal: reduce support tickets by 40%",
  "technical_requirements": [
    "React 18 with TypeScript",
    "Responsive design (mobile-first)",
    "Real-time data updates via WebSocket",
    "Accessibility compliance (WCAG 2.1 AA)"
  ],
  "agent_assignments": {
    "ux_design_specialist": "Design system and user flows",
    "frontend_developer": "Implementation and optimization",
    "qa_test_engineer": "Testing and validation"
  },
  "success_criteria": [
    "Dashboard loads in < 2 seconds",
    "Mobile responsive on all breakpoints",
    "Passes accessibility audit",
    "User testing shows 90%+ satisfaction"
  ],
  "requirements": [
    "Dark mode support",
    "Customizable widget layout",
    "Export dashboard data to PDF"
  ]
}'
```

**Why JSON format?** Rich context gives Claude Code everything it needs to build production-quality features autonomously. The more detail you provide, the better the results.

```bash
# Start autonomous mode
sugar run

# Check progress anytime
sugar status
sugar list --status completed

# Sugar handles:
# - Writing the code
# - Running tests
# - Making commits
# - Creating PRs (if configured)
# - Updating GitHub issues
```

## Features

**Task Management**
- Rich task context with priorities and metadata
- Custom task types for your workflow
- Queue management and filtering

**Task Orchestration** *(New in v3.0)*
- Auto-decomposes complex features into subtasks
- 4-stage workflow: Research → Planning → Implementation → Review
- Specialist agent routing (frontend, backend, QA, security, DevOps)
- Parallel execution with dependency management

**Autonomous Execution**
- Specialized Claude agents (UX, backend, QA)
- Automatic retries on failures
- Quality checks and testing

**GitHub Integration**
- Reads issues, creates PRs
- Updates issue status automatically
- Commits with proper messages

**Smart Discovery**
- Monitors error logs
- Analyzes code quality
- Identifies missing tests
- Auto-creates tasks from findings

**Issue Responder**
- AI-powered GitHub issue analysis
- Generates contextual responses
- Confidence-based auto-posting
- Searchable issue history

**Ralph Wiggum Integration**
- Iterative execution for complex tasks
- Self-correcting loops until tests pass
- Prevents single-shot failures
- Automatic completion detection

**Advanced Security & Control** *(New in v3.4)*
- Tool restrictions by task type (tier-based)
- Wildcard bash permissions with fnmatch patterns
- Pre/post execution hooks for automation
- Thinking capture for reasoning visibility

## Ralph Wiggum: Why Sugar Gets It Right

Here's the thing about AI coding: **single-shot attempts often fail on complex tasks**.

Ask Claude to implement a feature in one go, and you might get something that's 80% right. But that 20% means broken tests, edge cases missed, or subtle bugs. You end up going back and forth, manually iterating until it works.

**Ralph Wiggum fixes this by design.**

Instead of trying to complete a task perfectly the first time, Sugar can feed the same prompt repeatedly. Each iteration:

1. Claude sees its previous work in the files
2. Runs tests and sees what's failing
3. Fixes issues and improves the implementation
4. Repeats until the task is actually complete

```bash
# Without Ralph (traditional single-shot):
sugar add "Implement rate limiting"
# Claude attempts once, maybe tests fail, task marked "done" anyway

# With Ralph (iterative):
sugar add "Implement rate limiting" --ralph --max-iterations 10
# Claude iterates: implement → test → fix → test → fix → done
# Only marked complete when tests actually pass
```

**Think of it like code review cycles**, but automated. Junior dev writes code, tests fail, they fix it, tests pass, PR merged. Ralph does this loop automatically.

### When to Use Ralph

| Task Type | Without Ralph | With Ralph |
|-----------|---------------|------------|
| Simple bug fix | Works fine | Overkill |
| New feature | Hit or miss | Iterates until working |
| Complex refactor | Often breaks things | Self-corrects |
| TDD implementation | Tests often fail | Keeps going until green |
| Flaky test debugging | Might give up | Tries multiple approaches |

### How It Works

```
Iteration 1: "Implement rate limiting"
  → Creates RateLimiter class
  → Tests: 2 passing, 3 failing

Iteration 2: Same prompt, sees previous work
  → Fixes failing tests
  → Tests: 4 passing, 1 failing

Iteration 3: Same prompt, sees more progress
  → Handles edge case
  → Tests: 5 passing, 0 failing
  → Outputs: <promise>DONE</promise>
  → Task complete!
```

The `<promise>` tag is how Claude signals "I'm actually done." Without it, Ralph knows to keep iterating.

### Setup

**Ralph is built into Sugar** - no separate installation required. Just enable it in your config:

```yaml
# .sugar/config.yaml
sugar:
  ralph:
    enabled: true
    max_iterations: 10
    require_completion_criteria: true
```

Or use the `--ralph` flag when adding tasks:

```bash
sugar add "Complex feature" --ralph --max-iterations 15
```

### Safety First

Ralph won't run forever. You must include:
- A `<promise>` tag in your prompt (completion signal)
- OR `--max-iterations` flag (safety limit)

Sugar validates this BEFORE starting. No completion criteria = task rejected.

### Interactive Use (Claude Code)

For interactive Ralph loops in Claude Code sessions (outside of Sugar), install the Ralph Wiggum plugin:

```bash
# If you have the toolkit installed:
/ralph-wiggum:ralph-loop "Fix the flaky tests" --max-iterations 10
```

**Full docs:** [docs/ralph-wiggum.md](docs/ralph-wiggum.md)

## Issue Responder

Automatically analyze and respond to GitHub issues with AI-powered insights. Sugar understands issue context, codebase structure, and project patterns to generate helpful responses.

```bash
# List open issues
sugar issue list

# Analyze an issue
sugar issue analyze 42

# Generate AI response (preview)
sugar issue respond 42

# Generate and post if confident
sugar issue respond 42 --post
```

The Issue Responder evaluates confidence before posting. Use `--force-post` to override the confidence check, or adjust the threshold with `--confidence-threshold`.

**Custom Prompts:** Customize Sugar's responses per-project by creating `.sugar/prompts/issue_responder.json`:

```json
{
  "instructions": "You are a helpful assistant for MyProject. Be friendly and professional.",
  "guidelines": ["Always search the codebase first", "Include file paths"],
  "constraints": ["Never share API keys", "Don't promise release dates"]
}
```

**Full documentation:** [docs/issue-responder.md](docs/issue-responder.md)

## How It Works

```
┌─────────────────────────────────────────────────────────┐
│                    The Sugar Loop                       │
└─────────────────────────────────────────────────────────┘

  You                    Priority Queue               Sugar
   │                          │                         │
   │  sugar add "task"        │                         │
   ├─────────────────────────>│                         │
   │                          │                         │
   │                          │  Picks highest priority │
   │                          │<────────────────────────┤
   │                          │                         │
   │                          │                         │
   │                     Claude Code                    │
   │                          │                         │
   │                          │  Executes in background │
   │                          │  (uses agents, tests)   │
   │                          │                         │
   │                          ▼                         │
   │                     Completes Work                 │
   │                          │                         │
   │                          │  Commits, updates       │
   │                          │                         │
   │                          │  Back to queue ────────>│
   │                          │                         │
   └──────────────────────────┴─────────────────────────┘
                              ↻ Repeat
```

**The continuous execution loop:**

1. **You assign** - Add tasks with priorities and context
2. **Sugar picks up** - Grabs highest priority work from the queue
3. **Claude Code executes** - Runs in background, uses specialized agents as needed
4. **Completes work** - Tests, commits, moves to next task
5. **Repeat** - Continuous execution until queue is empty

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
  code_quality:
    enabled: true
```

## Use Sugar from Claude Code

**Sugar has native Claude Code integration!** Delegate work to Sugar directly from your Claude sessions.

### Install the Plugin

```
/plugin install roboticforce/sugar
```

> **Note**: If you see "Plugin not found in any marketplace", make sure you're using `roboticforce/sugar` (the GitHub repository path).

### Delegate Work from Claude

**Inside a Claude Code session:**

```
You: "I'm working on authentication but need to fix these test failures.
Can you handle the test fixes while I finish the auth flow?"

Claude: "I'll create a Sugar task for the test fixes so you can keep coding."

/sugar-task "Fix authentication test failures" --type test --urgent
```

**Why this is powerful:** Claude Code handles your interactive work while Sugar autonomously fixes the tests in the background. No context switching.

### Example Workflow

```
You: "Found a memory leak in the cache module. Add it to the queue."

Claude:
/sugar-task "Fix memory leak in cache module" --json --description '{
  "priority": 5,
  "type": "bug_fix",
  "context": "Memory usage grows unbounded in production",
  "technical_requirements": ["Profile memory usage", "Add cleanup cycle"],
  "agent_assignments": {
    "tech_lead": "Investigate root cause and fix"
  }
}'

Task created! You can check progress with /sugar-status
```

### Available Slash Commands

- `/sugar-task` - Create tasks with rich context
- `/sugar-status` - Check queue and progress
- `/sugar-run` - Start autonomous mode
- `/sugar-review` - Review pending tasks
- `/sugar-analyze` - Analyze code for potential work

### MCP Server Integration

Sugar provides an MCP server for integration with Goose, Claude Desktop, and other MCP clients.

**Using with Goose:**

Sugar is an official extension in the [Goose extensions library](https://block.github.io/goose/docs/mcp/sugar-mcp).

```bash
# Via Goose CLI
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

**Available MCP Tools:**
- `createTask` - Create development tasks
- `listTasks` - List/filter tasks
- `viewTask` / `updateTask` / `removeTask` - Manage tasks
- `getStatus` - Get system metrics
- `runOnce` - Execute one autonomous cycle

## Architecture (v3.0)

Sugar 3.0 is built natively on the **Claude Agent SDK**, providing:

**Agent Foundation**
- `SugarAgent` - Native SDK integration with streaming and tool use
- Quality gate hooks - PreToolUse/PostToolUse security checks
- Workflow profiles - Specialized behaviors for different tasks

**Distribution Options**
- **GitHub Action** - Event-driven, BYOK (Bring Your Own Key)
- **MCP Server** - Native Python or Node.js for Claude Desktop/Goose
- **Python Package** - Direct library usage
- **CLI** - Local development

**SaaS Features** (Enterprise)
- Usage tracking per customer
- API key management with rate limiting
- Tiered pricing (Free → Enterprise)

## Requirements

- Python 3.11+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (for CLI mode)

## Documentation

- **[Quick Start](docs/user/quick-start.md)** - Get running in 5 minutes
- **[CLI Reference](docs/user/cli-reference.md)** - All commands
- **[Task Orchestration](docs/task_orchestration.md)** - Complex feature decomposition
- **[GitHub Integration](docs/user/github-integration.md)** - Connect to GitHub
- **[Configuration Guide](docs/user/configuration-best-practices.md)** - Best practices
- **[Task Hooks](docs/task-hooks.md)** - Pre/post execution hooks
- **[Thinking Capture](docs/thinking-capture.md)** - View Claude's reasoning
- **[Claude Code Plugin](.claude-plugin/README.md)** - Native integration

## Advanced Usage

**Task Orchestration**

For complex features, Sugar can automatically decompose work and route to specialist agents:

```bash
# Add a feature that will be orchestrated
sugar add "Add user authentication with OAuth" --type feature --orchestrate

# Sugar will:
# 1. RESEARCH - Search best practices, analyze codebase
# 2. PLAN - Create implementation plan with subtasks
# 3. IMPLEMENT - Route subtasks to specialists in parallel
# 4. REVIEW - Code review and test verification

# Check orchestration status
sugar orchestrate <task_id> --stages

# View accumulated context
sugar context <task_id>
```

Specialist agents are automatically assigned based on task content:
- `frontend-designer` - UI, components, styling
- `backend-developer` - APIs, databases, services
- `qa-engineer` - Testing, test strategies
- `security-engineer` - Auth, vulnerabilities
- `devops-engineer` - CI/CD, infrastructure

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
    "ux_design_specialist": "UI/UX design",
    "frontend_developer": "Implementation",
    "qa_test_engineer": "Testing"
  }
}'
```

**Multiple Projects**
```bash
# Run Sugar on multiple projects simultaneously
cd /path/to/project-a && sugar run &
cd /path/to/project-b && sugar run &
cd /path/to/project-c && sugar run &
```

## Troubleshooting

**Sugar not finding Claude CLI?**
```bash
# Specify Claude path in .sugar/config.yaml
claude:
  command: "/full/path/to/claude"
```

**Tasks not executing?**
```bash
# Check dry_run is disabled
cat .sugar/config.yaml | grep dry_run

# Monitor logs
tail -f .sugar/sugar.log

# Test single cycle
sugar run --once
```

**Need help?**
- [Troubleshooting Guide](docs/user/troubleshooting.md)
- [GitHub Issues](https://github.com/roboticforce/sugar/issues)

## Contributing

Contributions welcome! See [CONTRIBUTING.md](docs/dev/contributing.md) for guidelines.

```bash
# Development setup
git clone https://github.com/roboticforce/sugar.git
cd sugar

# Install with uv (recommended)
uv pip install -e ".[dev,test,github]"

# Or with pip
pip install -e ".[dev,test,github]"

# Run tests
pytest tests/ -v

# Format code
black .
```

## License

MIT - see [LICENSE](LICENSE) and [TERMS.md](TERMS.md)

---

**Sugar v3.4** - Autonomous development powered by Claude Agent SDK

> ⚠️ Sugar is provided "AS IS" without warranty. Review all AI-generated code before use. See [TERMS.md](TERMS.md) for details.
