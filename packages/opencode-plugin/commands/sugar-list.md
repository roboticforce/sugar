---
name: sugar-list
description: View Sugar task queue with filtering options
---

# List Sugar Tasks

Display the current Sugar task queue with optional filtering.

## Common Queries

- "show all pending tasks" -> sugar_list with status="pending"
- "what's urgent?" -> sugar_list with priority=5
- "show bug fixes" -> sugar_list with type="bug_fix"
- "show my task queue" -> sugar_list with no filters

## Output Interpretation

Present tasks in a scannable format:

```
[STATUS] Title (ID: xxx) - Priority N
```

Status indicators:

- **Pending**: Ready for execution
- **Hold**: Paused, won't be picked up
- **Active**: Currently being worked on
- **Completed**: Successfully finished
- **Failed**: Execution failed

## Follow-up Suggestions

Based on the results, suggest:

- `/sugar-view <id>` for task details
- `/sugar-priority <id>` to reprioritize
- `/sugar-run` to start execution
