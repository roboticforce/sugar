# @anthropic/sugar-opencode

OpenCode plugin for Sugar autonomous task queue integration.

## Features

- **Task Management Tools**: Add, list, view, update, and remove tasks
- **Memory Integration**: Search and store learnings in Sugar's memory system
- **Session Hooks**: Automatic memory injection and learning capture
- **Slash Commands**: User-friendly command templates

## Installation

### Via npm (in opencode.json)

```json
{
  "plugin": ["@anthropic/sugar-opencode"]
}
```

### Local Installation

```bash
cp -r packages/opencode-plugin ~/.config/opencode/plugins/sugar
```

## Configuration

Add to your `opencode.json`:

```json
{
  "plugin": ["@anthropic/sugar-opencode"],
  "sugar": {
    "autoInjectMemories": true,
    "memoryLimit": 5,
    "storeLearnings": true,
    "memoryTypes": ["decision", "preference", "error_pattern"],
    "debug": false
  }
}
```

## Available Tools

| Tool             | Description                          |
| ---------------- | ------------------------------------ |
| `sugar_add`      | Add a new task to the queue          |
| `sugar_list`     | List tasks with filtering            |
| `sugar_view`     | View detailed task information       |
| `sugar_remove`   | Remove a task from the queue         |
| `sugar_priority` | Change task priority                 |
| `sugar_status`   | Get queue statistics and health      |
| `sugar_recall`   | Search Sugar's memory system         |
| `sugar_run`      | Execute one autonomous cycle         |

## Slash Commands

| Command           | Description                    |
| ----------------- | ------------------------------ |
| `/sugar-add`      | Add a task interactively       |
| `/sugar-list`     | View task queue                |
| `/sugar-status`   | System health dashboard        |
| `/sugar-run`      | Execute one task               |
| `/sugar-remove`   | Remove a task                  |
| `/sugar-priority` | Change task priority           |

## Requirements

- OpenCode >= 0.1.0
- Sugar CLI >= 3.0.0 (`pip install sugarai`)
- Sugar initialized in project (`sugar init`)

## Session Hooks

The plugin automatically:

1. **On Session Start**: Injects relevant memories from Sugar
2. **On Tool Error**: Stores error patterns for future reference
3. **On Session End**: Captures learnings from the session

## Example Usage

```
# Add a task
/sugar-add "Fix the authentication timeout bug"

# View queue
/sugar-list

# Check status
/sugar-status

# Run Sugar
/sugar-run
```

## License

MIT
