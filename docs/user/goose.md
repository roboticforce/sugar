# Sugar Goose Integration

Sugar integrates with [Goose](https://block.github.io/goose) (Block's open-source AI agent) via MCP (Model Context Protocol), providing task management and memory features directly in your Goose sessions.

## Overview

The Goose integration enables:
- **Task management** - Create, list, and manage Sugar tasks from Goose
- **Memory access** - Store and recall decisions, preferences, and patterns
- **Project context** - Get organized summaries of your project knowledge

## Quick Start

### Option 1: Goose CLI (Recommended)

```bash
goose configure
# Select "Add Extension" → "Command-line Extension"
# Name: sugar
# Command: npx -y sugarai-mcp
```

### Option 2: Direct Config Edit

Add to `~/.config/goose/config.yaml`:

```yaml
extensions:
  sugar:
    command: npx -y sugarai-mcp
```

After setup, restart Goose to load the Sugar extension.

## Prerequisites

- [Goose](https://block.github.io/goose) installed
- [Node.js](https://nodejs.org/) (for npx)
- Sugar initialized in your project (`sugar init`)

## Available MCP Tools

Once configured, Goose has access to these Sugar tools:

### Task Management

| Tool | Description |
|------|-------------|
| `createTask` | Add a new task to Sugar's queue |
| `listTasks` | List tasks with optional filtering |
| `viewTask` | Get detailed information about a task |
| `updateTask` | Modify task priority, status, or metadata |
| `removeTask` | Delete a task from the queue |
| `getStatus` | Get Sugar system status and queue stats |

### Memory System

| Tool | Description |
|------|-------------|
| `search_memory` | Semantic search over stored memories |
| `store_learning` | Store new decisions, preferences, or patterns |
| `get_project_context` | Get organized project summary |
| `recall` | Get formatted markdown context on a topic |
| `list_recent_memories` | List memories with optional type filter |

## Usage Examples

Once Sugar is configured in Goose, you can interact naturally:

**Task Management:**
```
"Add a task to fix the login timeout bug with high priority"
"Show me all pending bug fixes"
"What's the status of the Sugar queue?"
```

**Memory:**
```
"Remember that we use JWT with RS256 for authentication"
"What decisions have we made about the database?"
"Store this as an error pattern: connection timeouts need retry logic"
```

## Alternative: Python MCP Server

If you prefer not to use npx, Sugar also provides a built-in Python MCP server:

```bash
# Start the HTTP-based MCP server
sugar mcp serve --port 8080
```

Then configure Goose to connect to `http://localhost:8080`.

## Troubleshooting

### "Extension not found" or tools not available

1. Verify Node.js is installed: `node --version`
2. Test the MCP server manually: `npx -y sugarai-mcp`
3. Restart Goose after configuration changes

### "Not a Sugar project"

Sugar requires initialization in your project directory:

```bash
cd /path/to/your/project
sugar init
```

### Tools work but no memories/tasks

Ensure you're running Goose from a directory with a `.sugar/` folder, or that `SUGAR_PROJECT_ROOT` is set.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SUGAR_PROJECT_ROOT` | Override project directory | Current directory |
| `SUGAR_LOG_LEVEL` | Logging verbosity | `INFO` |

## Learn More

- [Memory System Guide](memory.md) - Full memory system documentation
- [CLI Reference](cli-reference.md) - All Sugar commands
- [Goose Documentation](https://block.github.io/goose) - Official Goose docs
- [MCP Server Package](https://www.npmjs.com/package/sugarai-mcp) - npm package details
