# CLI Reference

Complete reference for all Sugar command-line interface commands.

## Global Options

```bash
sugar [OPTIONS] COMMAND [ARGS]...
```

### Global Options

- `--config TEXT` - Configuration file path (default: `.sugar/config.yaml`)
- `--help` - Show help message and exit

## Commands

### `sugar help`

Show comprehensive Sugar help and getting started guide.

```bash
sugar help
```

**What it shows:**
- Quick start steps
- Overview of Sugar's dual operation modes (autonomous + manual)
- Core commands summary
- Configuration basics
- Project structure
- Safety features
- Documentation links
- Tips and troubleshooting

**Example:**
```bash
sugar help
```

This command provides a complete overview of Sugar in your terminal, perfect for quick reference without needing to open documentation files.

---

### `sugar init`

Initialize Sugar in a project directory.

```bash
sugar init [OPTIONS]
```

**Options:**
- `--project-dir PATH` - Project directory to initialize (default: current directory)

**Examples:**
```bash
# Initialize in current directory
sugar init

# Initialize in specific directory
sugar init --project-dir /path/to/my/project
```

**What it creates:**
- `.sugar/` directory with configuration and database
- `.sugar/config.yaml` - project configuration
- `.sugar/sugar.db` - SQLite task database
- `.sugar/logs/` - Sugar-specific logs
- `logs/errors/` - Error log monitoring directory

---

### `sugar add`

Add a new task to the Sugar work queue.

```bash
sugar add TITLE [OPTIONS]
```

**Arguments:**
- `TITLE` - Task title (required)

**Standard Options:**
- `--type TYPE` - Task type: `bug_fix`, `feature`, `test`, `refactor`, `documentation` (default: `feature`)
- `--priority INTEGER` - Priority level 1-5 (1=low, 5=urgent, default: 3)
- `--description TEXT` - Detailed task description
- `--urgent` - Mark as urgent (sets priority to 5)
- `--orchestrate` - Enable task orchestration for complex features
- `--skip-stages TEXT` - Skip specific orchestration stages (comma-separated)
- `--acceptance-criteria TEXT` - JSON array of acceptance criteria for task verification

**Complex Data Input Options:**
- `--input-file PATH` - JSON file containing task data
- `--stdin` - Read task data from stdin (JSON format)
- `--json` - Parse description as JSON and store in context

**Ralph Wiggum Options (Iterative Execution):**
- `--ralph` - Enable iterative execution for complex tasks that benefit from self-correction
- `--completion-promise TEXT` - Custom completion signal (default: DONE). Requires `--ralph`
- `--max-iterations INTEGER` - Maximum iterations before auto-stopping (default: 10)

Ralph mode runs tasks iteratively until completion criteria are met. Each iteration, Claude sees previous work and continues improving. Ideal for:
- Complex debugging requiring multiple attempts
- TDD workflows (write tests, implement, refine)
- Exploratory refactoring
- Tasks with clear success criteria (tests pass, linting clean)

**Intelligent Triage Options:**
- `--triage` - Enable intelligent triage to auto-detect execution mode and completion criteria

Triage mode analyzes task complexity and codebase capabilities to:
- Automatically recommend Ralph mode for complex tasks
- Generate appropriate completion criteria based on task type
- Detect available test frameworks, linters, and quality gates
- Enrich task context with triage analysis

When `--triage` is enabled and Ralph mode is recommended with high confidence (60%+), it will be automatically enabled.

**Triage Examples:**
```bash
# Let Sugar decide if Ralph mode is needed
sugar add "Fix authentication bug" --type bug_fix --triage

# Triage for a complex refactor (likely to recommend Ralph)
sugar add "Refactor to use repository pattern" --type refactor --triage \
  --description "Update data access layer. All tests must pass."
```

**Ralph Examples:**
```bash
# Simple iterative bug fix
sugar add "Fix auth timeout" --type bug_fix --ralph

# With custom completion signal
sugar add "Implement rate limiting" --type feature --ralph \
  --completion-promise "RATE LIMITING COMPLETE"

# With higher iteration limit for complex refactoring
sugar add "Refactor database layer" --type refactor --ralph \
  --max-iterations 20 \
  --description "Refactor to repository pattern. Output <promise>REFACTOR DONE</promise> when tests pass."
```

**Standard Examples:**
```bash
# Basic task
sugar add "Implement user login"

# Feature with priority
sugar add "Add search functionality" --type feature --priority 4

# Urgent bug fix
sugar add "Fix authentication crash" --type bug_fix --urgent

# With detailed description
sugar add "Refactor API endpoints" --type refactor --priority 3 --description "Clean up REST API structure and improve error handling"
```

**Acceptance Criteria Examples:**

Acceptance criteria define what must be verified before a task is marked complete:

```bash
# Bug fix with test requirements
sugar add "Fix login timeout" --type bug_fix \
  --acceptance-criteria '[{"type": "test_suite", "command": "pytest tests/auth/"}]'

# Feature requiring new test file
sugar add "Add user profile endpoint" --type feature \
  --acceptance-criteria '[
    {"type": "test_suite", "command": "pytest", "expected_failures": 0},
    {"type": "file_exists", "file_pattern": "tests/test_profile.py"}
  ]'

# Security fix with specific checks
sugar add "Fix SQL injection in search" --type bug_fix --priority 5 \
  --acceptance-criteria '[
    {"type": "test_suite", "command": "pytest tests/security/"},
    {"type": "no_regressions", "description": "No new vulnerabilities"}
  ]'
```

**Supported Criterion Types:**
- `test_suite` - Run tests and verify pass/fail counts
- `file_exists` - Verify file exists at path or pattern
- `string_in_file` - Check for string in file
- `code_change` - Verify code changes were made
- `no_regressions` - Ensure no test regressions
- `http_status` - Verify HTTP endpoint returns expected status

If no acceptance criteria are specified, default templates are applied based on task type. See [Acceptance Criteria Templates](#acceptance-criteria-templates) for defaults.

**Complex Data Examples:**

**1. JSON File Input** (best for Claude Code integration):
```bash
# Create a task from a JSON file
sugar add "API Task" --input-file /path/to/task.json
```

Example `task.json`:
```json
{
  "title": "Implement OAuth2",
  "type": "feature",
  "priority": 4,
  "description": "Add OAuth2 authentication system",
  "context": {
    "requirements": ["JWT tokens", "refresh logic", "user session management"],
    "complexity": "high",
    "estimated_hours": 12,
    "dependencies": ["auth library", "database migration"],
    "acceptance_criteria": [
      "Users can login with OAuth2",
      "Tokens are properly refreshed",
      "Session state is maintained"
    ]
  }
}
```

**2. Stdin Input** (perfect for programmatic integration):
```bash
# From Claude Code slash commands or scripts
echo '{
  "title": "Fix authentication bug",
  "type": "bug_fix",
  "priority": 5,
  "description": "Users cannot log in after password reset",
  "context": {
    "source": "claude_code",
    "error_logs": ["/var/log/auth.log"],
    "affected_users": 150,
    "urgency_reason": "blocking user access"
  }
}' | sugar add "Critical Auth Fix" --stdin

# Pipe complex data from scripts
task_generator.py | sugar add "Generated Task" --stdin
```

**3. JSON Description Parsing** (structured descriptions):
```bash
# Parse description as JSON for rich context
sugar add "Database Migration" --json --description '{
  "tables": ["users", "sessions", "tokens"],
  "migration_type": "schema_update",
  "rollback_strategy": "backup_first",
  "estimated_downtime": "5 minutes"
}'
```

**Use Cases for Complex Input:**

- **🤖 Claude Code Integration**: Slash commands can send rich task data without shell escaping issues
- **📊 Automated Task Creation**: Scripts can create tasks with full context and metadata
- **🔗 External Tool Integration**: CI/CD systems, monitoring tools, and issue trackers can create detailed tasks
- **📝 Rich Task Context**: Store requirements, dependencies, acceptance criteria, and other structured data

**Benefits:**
- **No Shell Escaping**: JSON data doesn't break with complex strings, quotes, or special characters
- **Full Data Preservation**: Arrays, objects, and nested data maintain their structure
- **Extensible Context**: Add any custom fields needed for your workflow
- **Tool Integration**: Perfect for external tools that need to pass complex data structures

---

### `sugar list`

List tasks in the Sugar work queue.

```bash
sugar list [OPTIONS]
```

**Options:**
- `--status TYPE` - Filter by status: `pending`, `active`, `completed`, `failed`, `all` (default: `all`)
- `--type TYPE` - Filter by type: `bug_fix`, `feature`, `test`, `refactor`, `documentation`, `all` (default: `all`)
- `--limit INTEGER` - Number of tasks to show (default: 10)

**Examples:**
```bash
# List all tasks
sugar list

# List only pending tasks
sugar list --status pending

# List only bug fixes
sugar list --type bug_fix

# List last 5 completed tasks
sugar list --status completed --limit 5

# List pending features
sugar list --status pending --type feature
```

---

### `sugar view`

View detailed information about a specific task.

```bash
sugar view TASK_ID
```

**Arguments:**
- `TASK_ID` - Task ID to view (required)

**Examples:**
```bash
sugar view task-abc123
```

**Shows:**
- Complete task details
- Execution history
- Context and metadata
- Results or error information

---

### `sugar update`

Update an existing task.

```bash
sugar update TASK_ID [OPTIONS]
```

**Arguments:**
- `TASK_ID` - Task ID to update (required)

**Options:**
- `--title TEXT` - Update task title
- `--description TEXT` - Update task description
- `--priority INTEGER` - Update priority (1-5)
- `--type TYPE` - Update task type
- `--status STATUS` - Update status: `pending`, `active`, `completed`, `failed`

**Examples:**
```bash
# Update priority
sugar update task-abc123 --priority 5

# Update title and description
sugar update task-abc123 --title "New title" --description "Updated description"

# Mark as completed
sugar update task-abc123 --status completed
```

---

### `sugar priority`

Change the priority of a task with intuitive shortcuts or numeric values.

```bash
sugar priority TASK_ID [OPTIONS]
```

**Arguments:**
- `TASK_ID` - Task ID to update priority (required)

**Options:**
- `--priority, -p INTEGER` - Set priority (1=highest, 5=lowest)
- `--urgent` - Set priority to urgent (1) 🔥
- `--high` - Set priority to high (2) ⚡
- `--normal` - Set priority to normal (3) 📋
- `--low` - Set priority to low (4) 📝
- `--minimal` - Set priority to minimal (5) 💤

**Examples:**
```bash
# Quick priority shortcuts
sugar priority task-abc123 --urgent
sugar priority task-abc123 --high
sugar priority task-abc123 --normal

# Numeric priority setting
sugar priority task-abc123 --priority 1
sugar priority task-abc123 -p 4

# Shows visual feedback
✅ Priority changed: 📋 normal → 🔥 urgent
   Task: Implement user authentication
```

**Features:**
- **Visual Indicators**: Shows before/after priority with emojis
- **Error Prevention**: Validates inputs and prevents conflicting options
- **Flexible Input**: Use memorable flags or numeric values
- **Clear Feedback**: Displays task title and priority change

---

### `sugar debug`

Generate comprehensive diagnostic information for troubleshooting Sugar issues.

```bash
sugar debug [OPTIONS]
```

**Options:**
- `--format [json|yaml|text]` - Output format (default: json)
- `-o, --output TEXT` - Write output to file instead of stdout
- `--include-sensitive` - Include sensitive information (paths, tokens) - use with caution

**Examples:**
```bash
# Basic diagnostic report (JSON format)
sugar debug

# Human-readable text format
sugar debug --format text

# Save to file for GitHub issue
sugar debug --output sugar-diagnostic.json

# Include sensitive info for internal debugging
sugar debug --include-sensitive --format yaml
```

**What it captures:**
- **System Information**: Sugar version, platform, Python version
- **Tool Status**: Claude CLI, GitHub CLI, Git availability and versions
- **Configuration**: Project settings (sanitized by default)
- **Work Queue Status**: Task counts, recent items, error messages
- **Git Repository**: Current branch, changes, remotes (sanitized)
- **Potential Issues**: Automated analysis with suggested fixes

**Use Cases:**
- **GitHub Issues**: Attach diagnostic output when reporting bugs
- **Troubleshooting**: Quickly identify configuration problems
- **Support**: Share system state with maintainers (sensitive data excluded)
- **Debugging**: Understand why Sugar isn't making commits/branches

**Privacy Note:** 
By default, sensitive information (file paths, tokens, repository URLs) is redacted. Use `--include-sensitive` only when necessary and never share sensitive diagnostics publicly.

---

### `sugar remove`

Remove a task from the work queue.

```bash
sugar remove TASK_ID
```

**Arguments:**
- `TASK_ID` - Task ID to remove (required)

**Examples:**
```bash
sugar remove task-abc123
```

---

### `sugar status`

Show Sugar system status and queue statistics.

```bash
sugar status
```

**Shows:**
- Total tasks count
- Tasks by status (pending, active, completed, failed)
- Recent activity (24 hours)
- Next few pending tasks

**Example output:**
```
🤖 Sugar System Status
========================================
📊 Total Tasks: 15
⏳ Pending: 5
⚡ Active: 1
✅ Completed: 8
❌ Failed: 1
📈 Recent (24h): 12

🔜 Next Tasks:
--------------------
🚨 [bug_fix] Fix critical auth bug
P4 [feature] Add user dashboard
P3 [test] Add integration tests
```

---

### `sugar learnings`

View Sugar's learning progress log with session summaries, success/failure patterns, and recommendations.

```bash
sugar learnings [OPTIONS]
```

**Options:**
- `--lines INTEGER` - Number of recent lines to show (default: all)
- `--sessions INTEGER` - Number of recent sessions to show (default: 5)
- `--clear` - Clear the learnings log (creates backup first)

**Examples:**
```bash
# View all learnings
sugar learnings

# View last 50 lines
sugar learnings --lines 50

# View last 3 sessions
sugar learnings --sessions 3

# Clear and start fresh (creates backup)
sugar learnings --clear
```

**What it shows:**
- Session summaries with completion stats
- Success rate and task velocity
- Successful task types and sources
- Common failure patterns and reasons
- Automated recommendations

**Example output:**
```
## Session Summary - 2024-01-15T10:30:00Z

### 📊 Performance Metrics
- **Total Tasks Processed:** 12
- **Success Rate:** 83.3%
- **Completed Tasks:** 10
- **Failed Tasks:** 2
- **Velocity:** 4.2 tasks/day
- **Average Execution Time:** 8m 23s

### ✅ Success Patterns
**Successful Task Types:**
- feature: 5 tasks
- bug_fix: 3 tasks
- test: 2 tasks

### 💡 Recommendations
- 🎯 **priority_adjustment:** Consider increasing priority for bug_fix tasks
- ⚡ **optimization:** Tests are passing consistently - good coverage
```

The learnings log is stored at `.sugar/LEARNINGS.md` and is automatically updated after each Sugar session.

---

### `sugar run`

Start the Sugar autonomous development system.

```bash
sugar run [OPTIONS]
```

**Options:**
- `--dry-run` - Run in simulation mode (override config setting)
- `--once` - Run one cycle and exit
- `--validate` - Validate configuration and exit

**Examples:**
```bash
# Test run (safe mode)
sugar run --dry-run --once

# Validate configuration
sugar run --validate

# Start continuous operation
sugar run

# Force dry run mode
sugar run --dry-run
```

**Modes:**
- **Dry Run**: Shows what would be done without making changes
- **Once**: Runs one discovery/execution cycle then exits
- **Continuous**: Runs forever until interrupted (Ctrl+C)
- **Validate**: Checks configuration and Claude CLI setup

---

### `sugar orchestrate`

View or manage task orchestration status.

```bash
sugar orchestrate [TASK_ID] [OPTIONS]
```

**Arguments:**
- `TASK_ID` - Task ID to view orchestration details (optional)

**Options:**
- `--stages` - Show detailed stage information

**Examples:**
```bash
# View all orchestrating tasks
sugar orchestrate

# View specific task's orchestration status
sugar orchestrate task-abc123

# View stage details
sugar orchestrate task-abc123 --stages
```

**Shows:**
- Current orchestration stage (research, planning, implementation, review)
- Subtasks and their status
- Agent assignments
- Stage completion status

---

### `sugar context`

View accumulated context for an orchestrated task.

```bash
sugar context TASK_ID
```

**Arguments:**
- `TASK_ID` - Task ID to view context (required)

**Examples:**
```bash
sugar context task-abc123
```

**Shows:**
- Research findings accumulated during orchestration
- Implementation plan from planning stage
- Subtask results
- Files modified across subtasks

---

### `sugar thinking`

View Claude's thinking logs for task execution, providing visibility into Claude's reasoning process.

```bash
sugar thinking [TASK_ID] [OPTIONS]
```

**Arguments:**
- `TASK_ID` - Task ID to view thinking for (required unless using `--list`)

**Options:**
- `--list, -l` - List all available thinking logs
- `--stats, -s` - Show thinking statistics only

**Examples:**
```bash
# View full thinking log for a task
sugar thinking task-abc123

# View thinking statistics
sugar thinking task-abc123 --stats

# List all thinking logs
sugar thinking --list
```

**Shows:**
- **Full Log**: Complete thinking blocks with timestamps and tool considerations
- **Statistics**: Count of thinking blocks, total characters, tools considered, timing
- **List**: All available thinking logs sorted by modification time

**Thinking Log Contents:**
```markdown
# Thinking Log: Implement user authentication

**Task ID:** abc123
**Started:** 2026-01-07T10:30:00

---

## 10:30:05

First, I need to understand the current authentication system...

---

## 10:30:12

*Considering tool: `Read`*

I should read the existing auth module...

---

## Summary

- **Total thinking blocks:** 15
- **Total characters:** 3,247
- **Average length:** 216 chars
- **Tools considered:** Read, Write, Bash

**Completed:** 2026-01-07T10:35:42
```

**Use Cases:**
- **Debugging**: Understand why a task succeeded or failed
- **Learning**: Study how Claude approaches problems
- **Verification**: Confirm Claude is following expected strategies
- **Analysis**: Track thinking patterns across tasks

**Note:** Thinking capture is enabled by default. To disable, set `thinking_capture: false` in `.sugar/config.yaml`.

See [Thinking Capture Guide](../thinking-capture.md) for full documentation.

---

## Memory Commands

Sugar's memory system provides persistent semantic memory across coding sessions.

### `sugar remember`

Store a memory for future reference.

```bash
sugar remember "CONTENT" [OPTIONS]
```

**Options:**
- `--type TYPE` - Memory type: `decision`, `preference`, `research`, `file_context`, `error_pattern`, `outcome` (default: `decision`)
- `--tags TEXT` - Comma-separated tags for organization
- `--file PATH` - Associate with a specific file
- `--ttl TEXT` - Time to live: `30d`, `90d`, `1y`, `never` (default: `never`)
- `--importance FLOAT` - Importance score 0.0-2.0 (default: 1.0)

**Examples:**
```bash
# Store a preference
sugar remember "Always use async/await, never callbacks" --type preference

# Decision with tags
sugar remember "Chose JWT with RS256 for auth" --type decision --tags "auth,security"

# Research with expiration
sugar remember "Stripe API rate limit: 100/sec" --type research --ttl 90d

# File context
sugar remember "Handles OAuth callbacks" --type file_context --file src/auth/callback.py
```

---

### `sugar recall`

Search memories for relevant context.

```bash
sugar recall "QUERY" [OPTIONS]
```

**Options:**
- `--type TYPE` - Filter by memory type (or `all`)
- `--limit INTEGER` - Maximum results (default: 10)
- `--format FORMAT` - Output format: `table`, `json`, `full` (default: `table`)

**Examples:**
```bash
# Basic search
sugar recall "authentication"

# Filter by type
sugar recall "database errors" --type error_pattern

# JSON output
sugar recall "stripe" --format json

# Full details
sugar recall "architecture" --format full --limit 5
```

---

### `sugar memories`

List all stored memories.

```bash
sugar memories [OPTIONS]
```

**Options:**
- `--type TYPE` - Filter by memory type (or `all`)
- `--since TEXT` - Filter by age (e.g., `7d`, `30d`, `2w`)
- `--limit INTEGER` - Maximum results (default: 50)
- `--format FORMAT` - Output format: `table`, `json`

**Examples:**
```bash
# List all
sugar memories

# Recent decisions
sugar memories --type decision --since 7d

# Export to JSON
sugar memories --format json > backup.json
```

---

### `sugar forget`

Delete a memory by ID.

```bash
sugar forget MEMORY_ID [OPTIONS]
```

**Options:**
- `--force` - Skip confirmation prompt

**Examples:**
```bash
# Interactive deletion
sugar forget abc123

# Force delete
sugar forget abc123 --force
```

---

### `sugar export-context`

Export memories for Claude Code integration.

```bash
sugar export-context [OPTIONS]
```

**Options:**
- `--format FORMAT` - Output format: `markdown`, `json`, `claude` (default: `markdown`)
- `--limit INTEGER` - Max memories per type (default: 10)
- `--types TEXT` - Comma-separated types to include (default: `decision,preference,error_pattern`)

**Use Cases:**
- **SessionStart Hook**: Auto-inject context into Claude Code sessions
- **Backup**: Export memories for external storage
- **Sharing**: Share context across team members

**Claude Code Hook Configuration:**
```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "sugar export-context"
      }]
    }]
  }
}
```

---

### `sugar memory-stats`

Show memory system statistics.

```bash
sugar memory-stats
```

**Output includes:**
- Semantic search availability
- Database path and size
- Total memory count
- Count by memory type

---

### `sugar mcp memory`

Start the Sugar Memory MCP server for Claude Code integration.

```bash
sugar mcp memory [OPTIONS]
```

**Options:**
- `--transport TEXT` - Transport protocol: `stdio` (default: `stdio`)

**Claude Code Integration:**
```bash
# Add Sugar memory to Claude Code
claude mcp add sugar -- sugar mcp memory
```

**MCP Tools Exposed:**
- `search_memory` - Semantic search over memories
- `store_learning` - Store new observations/decisions
- `get_project_context` - Get organized project summary
- `recall` - Get formatted markdown context
- `list_recent_memories` - List with type filtering

**MCP Resources:**
- `sugar://project/context` - Full project context
- `sugar://preferences` - User coding preferences

See [Memory System Guide](memory.md) for full documentation.

---

### `sugar task-type`

Manage custom task types for your project.

```bash
sugar task-type [COMMAND] [OPTIONS]
```

**Subcommands:**

**`sugar task-type list`** - List all task types with their configurations

```bash
sugar task-type list
```

**Shows:**
- Task type name and emoji
- Model tier (simple/standard/complex)
- Tool restrictions
- Bash permissions
- Pre/post execution hooks

**`sugar task-type update`** - Update task type configuration

```bash
sugar task-type update TYPE [OPTIONS]
```

**Options:**
- `--tier TEXT` - Model tier: simple, standard, complex
- `--complexity INTEGER` - Complexity level (1-5)
- `--allowed-tools TEXT` - Comma-separated list of allowed tools
- `--disallowed-tools TEXT` - Comma-separated list of disallowed tools
- `--bash-permissions TEXT` - Comma-separated bash permission patterns
- `--pre-hooks TEXT` - Comma-separated pre-execution hooks
- `--post-hooks TEXT` - Comma-separated post-execution hooks

**Examples:**
```bash
# Change model tier for docs tasks
sugar task-type update docs --tier simple --complexity 1

# Set tool restrictions for feature tasks
sugar task-type update feature --allowed-tools "Read,Write,Edit,Bash"

# Add bash permissions for test tasks
sugar task-type update test --bash-permissions "pytest *,python -m pytest *"

# Add pre-execution hooks for bug_fix tasks
sugar task-type update bug_fix --pre-hooks "git status,pytest tests/ --collect-only"
```

See [Task Hooks Guide](../task-hooks.md) for full documentation on hooks.

---

## Task Status Lifecycle

```
pending → active → completed
            ↓
         failed
```

- **pending** - Task added but not yet started
- **active** - Task currently being executed by Claude
- **completed** - Task finished successfully
- **failed** - Task execution failed (can be retried)

## Task Types

- **`bug_fix`** - Fixing bugs, errors, or issues
- **`feature`** - Adding new functionality
- **`test`** - Writing or updating tests
- **`refactor`** - Improving code structure without changing functionality
- **`documentation`** - Writing or updating documentation

## Priority Levels

- **1** - Low priority (nice to have)
- **2** - Below normal priority
- **3** - Normal priority (default)
- **4** - High priority (important)
- **5** - Urgent priority (critical, shown with 🚨)

## Exit Codes

- **0** - Success
- **1** - General error
- **2** - Configuration error
- **3** - Claude CLI not found

## Environment Variables

- `SUGAR_CONFIG` - Override default config file path
- `SUGAR_LOG_LEVEL` - Set logging level (DEBUG, INFO, WARNING, ERROR)

## Acceptance Criteria Templates

When no acceptance criteria are specified, Sugar applies default templates based on task type:

### Feature Tasks
- All tests must pass
- Code changes must be made
- No test regressions

### Bug Fix Tasks
- All tests must pass
- Code changes must fix the bug
- No test regressions

### Refactor Tasks
- All tests must pass after refactoring
- No test regressions
- Code must be refactored

### Test Tasks
- All tests must pass (including new ones)
- Test files must be created/modified

### Documentation Tasks
- Documentation files must be updated

### Security Tasks
- All tests must pass
- Security-related changes must be made
- No regressions

You can override these defaults using `--acceptance-criteria` or by configuring default criteria per task type in the database.

## Completion Signals

Sugar recognizes several completion signal patterns in task output:

| Pattern | Example | Description |
|---------|---------|-------------|
| `<promise>TEXT</promise>` | `<promise>DONE</promise>` | Standard Ralph completion |
| `<complete>TEXT</complete>` | `<complete>Feature implemented</complete>` | Explicit completion |
| `<done reason="..."/>` | `<done reason="All tests pass"/>` | XML-style completion |
| `TASK_COMPLETE: desc` | `TASK_COMPLETE: Bug fixed` | Plain text completion |

These signals help Sugar determine when a task has genuinely completed, especially for iterative (Ralph) tasks.

## Tips

💡 Use `--dry-run --once` to safely test Sugar behavior
💡 Check `.sugar/sugar.log` for detailed execution logs
💡 Tasks are isolated per project - each project needs its own `sugar init`
💡 Use `sugar status` to monitor progress and queue health
💡 Use `sugar learnings` to review execution patterns and recommendations
💡 Specify `--acceptance-criteria` for tasks requiring specific verification