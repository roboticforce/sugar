# Thinking Capture - Real-Time Visibility into Claude's Reasoning

Thinking Capture is a critical feature in Sugar that provides real-time visibility into Claude's reasoning process during autonomous task execution. By capturing and displaying thinking blocks from the Claude Agent SDK, you can understand exactly how Claude approaches problems and makes decisions.

## Overview

When Claude executes tasks with extended thinking enabled (like `claude-opus-4-5`), it generates thinking blocks that contain its internal reasoning. Sugar captures these thinking blocks in real-time, logs them to markdown files, and provides statistics for analysis.

## Key Features

- **Real-time capture**: Thinking blocks are captured as they stream from the Claude Agent SDK
- **Task-specific logs**: Each task gets its own markdown file in `.sugar/thinking/`
- **Statistics tracking**: Count, character count, tool usage, and timing metadata
- **CLI commands**: Easy viewing and analysis of thinking logs
- **Database integration**: Thinking metadata stored with task results

## Architecture

### Components

1. **ThinkingCapture** (`sugar/executor/thinking_display.py`)
   - Core class that captures and processes thinking blocks
   - Handles real-time logging and file writing
   - Generates statistics and summaries

2. **Agent Integration** (`sugar/agent/base.py`)
   - Modified `_execute_with_streaming()` to detect ThinkingBlock messages
   - Passes thinking to ThinkingCapture in real-time
   - Tracks both typed SDK responses and dict-based responses

3. **Executor Integration** (`sugar/executor/agent_sdk_executor.py`)
   - Creates ThinkingCapture instance for each task
   - Attaches to agent before execution
   - Stores thinking metadata in task results

4. **Database Schema** (`sugar/storage/work_queue.py`)
   - `thinking_log_path`: Path to the markdown log file
   - `thinking_summary`: Human-readable summary
   - `thinking_stats`: JSON statistics object

5. **CLI Commands** (`sugar/main.py`)
   - `sugar thinking TASK_ID`: View full thinking log
   - `sugar thinking TASK_ID --stats`: View statistics
   - `sugar thinking --list`: List all thinking logs

## Usage

### Enabling Thinking Capture

Thinking capture is **enabled by default** for all task executions. To disable it, set in your config:

```yaml
# .sugar/config.yaml
sugar:
  executor:
    thinking_capture: false
```

### Viewing Thinking Logs

#### View Full Thinking Log

```bash
sugar thinking <task-id>
```

Example output:
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

I should read the existing auth module to see what's already implemented...

---

## 10:30:25

*Considering tool: `Write`*

Based on my analysis, I'll implement JWT-based authentication with...

---

## Summary

- **Total thinking blocks:** 15
- **Total characters:** 3,247
- **Average length:** 216 chars
- **Tools considered:** Read, Write, Bash

**Completed:** 2026-01-07T10:35:42
```

#### View Statistics Only

```bash
sugar thinking <task-id> --stats
```

Example output:
```
Thinking Statistics for: Implement user authentication
Task ID: abc123
============================================================

  Thinking Blocks: 15
  Total Characters: 3,247
  Average Length: 216 chars
  Tools Considered: Read, Write, Bash
  First Thinking: 2026-01-07T10:30:05
  Last Thinking: 2026-01-07T10:35:40

  Full log: .sugar/thinking/abc123.md
```

#### List All Thinking Logs

```bash
sugar thinking --list
```

Example output:
```
Found 12 thinking log(s):

  abc123
    Path: .sugar/thinking/abc123.md
    Modified: 2026-01-07 10:35:42

  def456
    Path: .sugar/thinking/def456.md
    Modified: 2026-01-07 09:15:20

  ...
```

## Programmatic Access

### Reading Thinking Logs

```python
from sugar.executor.thinking_display import read_thinking_log

# Read a specific task's thinking log
content = read_thinking_log("task-id")
if content:
    print(content)
```

### Listing All Logs

```python
from sugar.executor.thinking_display import list_thinking_logs

# Get all thinking logs (sorted by modification time, newest first)
logs = list_thinking_logs()

for task_id, log_path, modified_time in logs:
    print(f"{task_id}: {log_path} (modified {modified_time})")
```

### Accessing from Task Results

```python
from sugar.storage.work_queue import WorkQueue

queue = WorkQueue(".sugar/work_queue.db")
await queue.initialize()

task = await queue.get_work_by_id("task-id")

if task:
    print(f"Thinking Summary: {task['thinking_summary']}")
    print(f"Thinking Log: {task['thinking_log_path']}")

    import json
    stats = json.loads(task['thinking_stats'])
    print(f"Thinking Blocks: {stats['count']}")
```

## File Structure

Thinking logs are stored in markdown format:

```
.sugar/
  thinking/
    abc123.md          # Task abc123's thinking log
    def456.md          # Task def456's thinking log
    ...
```

Each log file contains:
- Header with task title and ID
- Timestamp for when thinking capture started
- Timestamped thinking blocks
- Tool use context (when applicable)
- Summary section with statistics

## Database Schema

The `work_items` table includes these thinking-related columns:

```sql
thinking_log_path TEXT         -- Path to .md file
thinking_summary TEXT          -- Human-readable summary
thinking_stats TEXT            -- JSON stats object
```

Example thinking_stats JSON:
```json
{
  "count": 15,
  "total_characters": 3247,
  "average_length": 216,
  "tool_uses_considered": ["Read", "Write", "Bash"],
  "first_thinking": "2026-01-07T10:30:05",
  "last_thinking": "2026-01-07T10:35:40"
}
```

## Implementation Details

### SDK Integration

The thinking capture integrates with Claude Agent SDK's streaming responses:

```python
# In sugar/agent/base.py
async for message in query(prompt=prompt, options=options):
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, ThinkingBlock):
                thinking_content = block.thinking
                signature = block.signature

                # Pass to capture
                if self._thinking_capture:
                    self._thinking_capture.capture(
                        thinking_content=thinking_content,
                        signature=signature
                    )
```

### Non-Blocking Capture

Thinking capture is designed to be non-blocking:

- Captures happen in the same event loop but don't block execution
- File writes are performed synchronously but are fast (small amounts of data)
- Errors in thinking capture don't fail the task execution

### Performance

- Minimal overhead: < 1% execution time impact
- Small file sizes: Typical thinking logs are 2-10 KB
- Efficient storage: Only text content, no binary data
- Automatic cleanup: Can be managed with `.sugar/cleanup` commands

## Best Practices

### 1. Review Thinking for Complex Tasks

For complex or failed tasks, always review the thinking log to understand Claude's reasoning:

```bash
sugar thinking <task-id>
```

### 2. Disable for High-Volume Tasks

If running thousands of simple tasks, consider disabling thinking capture to save disk space:

```yaml
sugar:
  executor:
    thinking_capture: false
```

### 3. Archive Old Thinking Logs

Periodically archive or clean up old thinking logs:

```bash
# Archive logs older than 30 days
find .sugar/thinking -name "*.md" -mtime +30 -exec gzip {} \;

# Or delete them
find .sugar/thinking -name "*.md" -mtime +30 -delete
```

### 4. Use Statistics for Analysis

Analyze thinking patterns across tasks:

```python
from sugar.storage.work_queue import WorkQueue
import json

queue = WorkQueue(".sugar/work_queue.db")
await queue.initialize()

tasks = await queue.get_recent_work(limit=100, status="completed")

total_thinking_blocks = 0
for task in tasks:
    if task.get('thinking_stats'):
        stats = json.loads(task['thinking_stats'])
        total_thinking_blocks += stats['count']

print(f"Average thinking blocks per task: {total_thinking_blocks / len(tasks)}")
```

## Troubleshooting

### No Thinking Captured

**Problem**: Task completed but no thinking log was generated.

**Possible causes**:
1. Thinking capture disabled in config
2. Model doesn't support extended thinking (e.g., claude-sonnet-4 vs claude-opus-4-5)
3. Task failed before any thinking occurred

**Solution**:
```bash
# Check config
cat .sugar/config.yaml | grep thinking_capture

# Check task details
sugar show <task-id>

# Ensure using a model with extended thinking
# claude-opus-4-5 and newer models support extended thinking
```

### Thinking Log File Not Found

**Problem**: Database says thinking exists but file is missing.

**Solution**:
```bash
# Check if .sugar/thinking directory exists
ls -la .sugar/thinking/

# Recreate directory if missing
mkdir -p .sugar/thinking

# Check database for path
sugar show <task-id> --format compact | grep thinking
```

### Large Thinking Logs

**Problem**: Some thinking logs are very large (> 100 KB).

**Explanation**: This is normal for complex tasks where Claude does extensive reasoning.

**Management**:
```bash
# Find large thinking logs
find .sugar/thinking -name "*.md" -size +100k -ls

# Compress large logs
find .sugar/thinking -name "*.md" -size +100k -exec gzip {} \;
```

## Future Enhancements

Potential improvements to thinking capture:

1. **Streaming display**: Show thinking in real-time during `sugar run`
2. **Thinking search**: Search across all thinking logs for patterns
3. **Thinking analysis**: AI-powered analysis of thinking patterns
4. **Thinking replay**: Visualize thinking timeline with tool uses
5. **Thinking comparison**: Compare thinking across similar tasks
6. **Export formats**: Export thinking to PDF, HTML, or JSON

## Related Documentation

- [Agent SDK Integration](./agent-sdk.md)
- [Quality Gates](./quality-gates.md)
- [Task Execution](./task-execution.md)
- [Database Schema](./database-schema.md)

## API Reference

See the full API documentation in the source files:

- `sugar/executor/thinking_display.py` - Core thinking capture classes
- `sugar/agent/base.py` - Agent SDK integration
- `sugar/executor/agent_sdk_executor.py` - Executor integration
- `sugar/storage/work_queue.py` - Database integration
