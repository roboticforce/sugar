# Sugar OpenCode Integration

Sugar integrates with [OpenCode](https://github.com/opencode-ai/opencode) to provide MCP-based communication between Sugar's autonomous task queue and OpenCode sessions.

## Overview

The OpenCode integration enables:
- **MCP servers** - Task management and memory access directly in OpenCode
- **Memory injection** - Automatically inject relevant context into OpenCode sessions
- **Learning capture** - Capture learnings from OpenCode sessions back to Sugar memory

## Quick Start

```bash
# One-command setup - configures OpenCode automatically
sugar opencode setup

# Restart OpenCode to load the new MCP servers

# Verify setup
sugar opencode status
```

The setup command:
- Finds your OpenCode config file (`~/.config/opencode/opencode.json` or `.opencode/opencode.json`)
- Adds `sugar-tasks` and `sugar-memory` MCP servers
- Preserves your existing configuration

After setup, OpenCode will have access to Sugar's tools:
- `sugar_add_task` - Add tasks to the queue
- `sugar_list_tasks` - View queued tasks
- `sugar_remember` - Store memories
- `sugar_recall` - Search memories

## Configuration

### Environment Variables

```bash
export OPENCODE_SERVER_URL="http://localhost:4096"  # Default
export OPENCODE_API_KEY="your-api-key"              # Optional
export OPENCODE_TIMEOUT="30.0"                      # Seconds
export SUGAR_OPENCODE_ENABLED="true"                # Enable/disable
```

### Sugar Config File

Add to `.sugar/config.yaml`:

```yaml
integrations:
  opencode:
    enabled: true
    server_url: "http://localhost:4096"
    api_key: null  # Optional
    timeout: 30.0

    # Auto-injection settings
    auto_inject: true
    inject_memory_types:
      - decision
      - preference
      - error_pattern
    memory_limit: 5

    # Notification settings
    notify_on_completion: true
    notify_on_failure: true

    # Sync interval for event polling
    sync_interval: 5.0
```

## CLI Commands

### `sugar opencode setup`

Automatically configure OpenCode to use Sugar's MCP servers.

```bash
sugar opencode setup [options]

Options:
  --yes, -y        Skip confirmation prompts
  --dry-run        Show what would be changed without modifying files
  --config PATH    Path to OpenCode config file (auto-detected if not specified)
  --no-memory      Don't add the memory MCP server
  --no-tasks       Don't add the tasks MCP server
```

**Examples:**

```bash
# Interactive setup (recommended)
sugar opencode setup

# Non-interactive for scripts/CI
sugar opencode setup --yes

# Preview changes without applying
sugar opencode setup --dry-run

# Only add task management (no memory)
sugar opencode setup --no-memory
```

The command searches for OpenCode config in this order:
1. `OPENCODE_CONFIG` environment variable
2. `OPENCODE_CONFIG_DIR` environment variable
3. `.opencode/opencode.json` (project-local)
4. `~/.config/opencode/opencode.json` (user config)

### `sugar opencode status`

Check OpenCode integration status and configuration.

```bash
sugar opencode status
```

Output:
```
OpenCode Integration Status
  Enabled: Yes
  aiohttp: Installed
  Server URL: http://localhost:4096
  Auto-inject: Yes
```

### `sugar opencode test`

Test connection to the OpenCode HTTP server (if enabled).

```bash
sugar opencode test
```

### `sugar opencode notify`

Send a notification to the OpenCode TUI.

```bash
sugar opencode notify "message" [options]

Options:
  --level LEVEL   Notification level: info, success, warning, error (default: info)
```

**Examples:**

```bash
# Info notification
sugar opencode notify "Build started"

# Success notification
sugar opencode notify "All tests passed" --level success

# Error notification
sugar opencode notify "Deployment failed" --level error
```

## Automatic Task Notifications

When Sugar runs tasks autonomously, it automatically notifies OpenCode:

| Event | Notification |
|-------|--------------|
| Task started | Info: "Task Started: {id}" |
| Task completed | Success: "Task Completed: {id}" with execution time |
| Task failed | Error: "Task Failed: {id}" with error message |

These notifications appear in the OpenCode TUI, keeping you informed of Sugar's progress.

### Disabling Notifications

```yaml
# In .sugar/config.yaml
integrations:
  opencode:
    notify_on_completion: false
    notify_on_failure: false
```

## Memory Injection

Sugar can inject relevant memories into OpenCode sessions to provide context.

### How It Works

1. When an OpenCode session starts, Sugar searches for relevant memories
2. Memories matching configured types are formatted as markdown
3. Context is injected as a system message in the session

### Injected Memory Types

By default, Sugar injects:
- **Decisions** - Architecture and implementation choices
- **Preferences** - Coding style and conventions
- **Error patterns** - Known bugs and fixes

### Example Injected Context

```markdown
## Sugar Context (from memory)

### Previous Decisions
- Using PostgreSQL for main database
- JWT with RS256 for authentication tokens

### Coding Preferences
- Always use async/await, never callbacks
- Prefer early returns over nested if statements

### Known Error Patterns
- Login loop caused by missing return statement in auth handler
```

### Customizing Injection

```yaml
integrations:
  opencode:
    auto_inject: true
    inject_memory_types:
      - decision
      - preference
      - error_pattern
      - research  # Add research notes
    memory_limit: 10  # More memories per type
```

## Learning Capture

Sugar can capture learnings from OpenCode sessions and store them as memories.

### Pattern Detection

The `LearningCapture` class detects:
- Decisions made during coding ("I decided to...", "Let's use...")
- Errors encountered and their fixes
- Preferences expressed ("Always...", "Never...", "Prefer...")

### Automatic Capture

When enabled, Sugar monitors OpenCode sessions and extracts learnings:

```python
from sugar.integrations.opencode import LearningCapture, OpenCodeConfig

config = OpenCodeConfig.from_env()
capture = LearningCapture(config)

# Process a session message
learnings = await capture.extract_learnings(message_content)
for learning in learnings:
    print(f"Detected {learning.type}: {learning.content}")
```

## API Reference

### OpenCodeClient

The main client for communicating with OpenCode.

```python
from sugar.integrations.opencode import OpenCodeClient, OpenCodeConfig

config = OpenCodeConfig(
    server_url="http://localhost:4096",
    timeout=30.0,
)

async with OpenCodeClient(config) as client:
    # Check connectivity
    is_healthy = await client.health_check()

    # List sessions
    sessions = await client.list_sessions()

    # Inject context
    await client.inject_context(
        session_id="abc123",
        context="Remember to use async/await",
        role="system",
    )

    # Send notification
    from sugar.integrations.opencode import NotificationLevel
    await client.notify(
        title="Build Complete",
        message="All 47 tests passed",
        level=NotificationLevel.SUCCESS,
    )
```

### ContextInjector

Handles automatic memory injection into sessions.

```python
from sugar.integrations.opencode import ContextInjector, OpenCodeConfig

config = OpenCodeConfig.from_env()
injector = ContextInjector(config)

# Inject relevant memories into a session
success = await injector.inject_for_session(session_id="abc123")
```

### Convenience Functions

For simple one-off operations:

```python
from sugar.integrations.opencode import (
    notify_task_started,
    notify_task_completed,
    notify_task_failed,
)

# Fire-and-forget notifications (non-blocking)
notify_task_started("task-123", "Fix login bug")
notify_task_completed("task-123", "Fix login bug", execution_time=45.2)
notify_task_failed("task-123", "Fix login bug", "Test assertion failed")
```

## Troubleshooting

### "aiohttp not available"

Install the OpenCode dependencies:

```bash
pipx inject sugarai aiohttp
# Or
pip install 'sugarai[opencode]'
```

### "Connection refused"

OpenCode server isn't running or is on a different port:

```bash
# Check OpenCode is running
curl http://localhost:4096/health

# Check configured URL
sugar opencode status
```

### "Client not connected"

The client must be used as an async context manager:

```python
# Wrong
client = OpenCodeClient()
await client.health_check()  # RuntimeError!

# Correct
async with OpenCodeClient() as client:
    await client.health_check()
```

### Notifications not appearing

1. Check OpenCode TUI is running and visible
2. Verify connectivity: `sugar opencode status`
3. Test with: `sugar opencode test`
4. Check notification settings in config

### Memory injection not working

1. Ensure memories exist: `sugar memories`
2. Check injection is enabled in config
3. Verify memory types match configured `inject_memory_types`
4. Check OpenCode session is active

## Architecture

```
┌─────────────┐     HTTP/SSE      ┌─────────────┐
│   Sugar     │◄─────────────────►│  OpenCode   │
│             │                   │   Server    │
│ ┌─────────┐ │                   │             │
│ │ Notifier│─┼──notifications───►│  ┌───────┐  │
│ └─────────┘ │                   │  │  TUI  │  │
│             │                   │  └───────┘  │
│ ┌─────────┐ │                   │             │
│ │Injector │─┼──context─────────►│  ┌───────┐  │
│ └─────────┘ │                   │  │Session│  │
│             │                   │  └───────┘  │
│ ┌─────────┐ │                   │             │
│ │ Capture │◄┼──events───────────│             │
│ └─────────┘ │                   │             │
└─────────────┘                   └─────────────┘
```

## Comparison with Claude Code Integration

| Feature | Claude Code | OpenCode |
|---------|-------------|----------|
| Integration method | MCP Server | HTTP API |
| Memory access | Full (MCP tools) | Injection-based |
| Notifications | N/A | TUI notifications |
| Event subscription | N/A | SSE streaming |
| Session management | Automatic | Manual/API |

Both integrations can be used simultaneously - Sugar detects which clients are available.
