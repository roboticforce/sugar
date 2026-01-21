# Sugar OpenCode Integration

Sugar integrates with [OpenCode](https://github.com/opencode-ai/opencode) to provide bidirectional communication between Sugar's autonomous task queue and OpenCode sessions.

## Overview

The OpenCode integration enables:
- **Task notifications** - Get notified in OpenCode when Sugar tasks complete or fail
- **Memory injection** - Automatically inject relevant context into OpenCode sessions
- **Learning capture** - Capture learnings from OpenCode sessions back to Sugar memory
- **Event subscription** - React to OpenCode events in Sugar

## Quick Start

```bash
# Install with OpenCode support
pipx install 'sugarai[opencode]'
# Or add to existing installation
pipx inject sugarai aiohttp

# Check OpenCode connection
sugar opencode status

# Test notification
sugar opencode test

# Send manual notification
sugar opencode notify "Build completed" --level success
```

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

### `sugar opencode status`

Check OpenCode server connectivity and configuration.

```bash
sugar opencode status
```

Output:
```
OpenCode Integration Status
  Server URL: http://localhost:4096
  Status: Connected
  Active Sessions: 2
```

### `sugar opencode test`

Send a test notification to verify the integration works.

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
