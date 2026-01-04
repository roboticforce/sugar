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

## Tips

💡 Use `--dry-run --once` to safely test Sugar behavior  
💡 Check `.sugar/sugar.log` for detailed execution logs  
💡 Tasks are isolated per project - each project needs its own `sugar init`  
💡 Use `sugar status` to monitor progress and queue health