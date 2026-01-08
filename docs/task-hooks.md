# Task Execution Hooks

Task hooks allow you to run custom shell commands before and after task execution. This enables automated testing, linting, validation, and other workflow automation.

## Overview

Sugar supports two types of hooks per task type:

- **Pre-hooks**: Execute before the AI agent starts working on the task
- **Post-hooks**: Execute after the task is completed

## Default Hooks

Sugar comes with sensible default hooks for common task types:

| Task Type | Pre-hooks | Post-hooks |
|-----------|-----------|------------|
| `bug_fix` | - | `pytest tests/ -x --tb=short` |
| `feature` | - | `pytest tests/ -x --tb=short`, `black --check .` |
| `test` | - | `pytest tests/ -v` |
| `refactor` | - | `pytest tests/ -x --tb=short` |
| `style` | - | `black --check .` |

## Behavior

### Pre-hooks

- Execute in order before task execution begins
- If any pre-hook fails (non-zero exit code), task execution is cancelled
- Task is marked as failed with hook error details

### Post-hooks

- Execute in order after task execution completes
- If any post-hook fails, the task is marked as "needs review"
- Task is not automatically failed - allows manual review
- Hook outputs are included in task results

## Variable Substitution

Hooks support variable substitution from task data:

- `{task_id}` - Task ID
- `{task_type}` - Task type (bug_fix, feature, etc.)
- `{task_title}` - Task title
- `{task_priority}` - Task priority (1-5)

Example:
```yaml
pre_hooks:
  - "echo 'Starting {task_type}: {task_title}'"
```

## Configuration

### Set Hooks Programmatically

```python
from sugar.storage.task_type_manager import TaskTypeManager

manager = TaskTypeManager(".sugar/sugar.db")
await manager.initialize()

# Set both pre and post hooks
await manager.set_hooks_for_type(
    "bug_fix",
    pre_hooks=["echo 'Running pre-checks'"],
    post_hooks=[
        "pytest tests/ -x",
        "black --check .",
        "mypy ."
    ]
)

# Set only post hooks
await manager.set_hooks_for_type(
    "feature",
    post_hooks=["npm test", "npm run lint"]
)
```

### Get Current Hooks

```python
# Get hooks for a task type
pre_hooks = await manager.get_pre_hooks_for_type("bug_fix")
post_hooks = await manager.get_post_hooks_for_type("bug_fix")

print(f"Pre-hooks: {pre_hooks}")
print(f"Post-hooks: {post_hooks}")
```

## Hook Execution Details

### Working Directory

Hooks execute in the project root directory (where Sugar is initialized).

### Timeout

Each hook has a default timeout of 300 seconds (5 minutes). Hooks that exceed this timeout are terminated and the task fails.

### Output Capture

Both stdout and stderr are captured from hook execution and included in task results for debugging.

### Environment

Hooks run in a shell environment with:
- Current working directory set to project root
- Same environment variables as the Sugar process
- Shell features available (pipes, redirects, etc.)

## Example Workflows

### Python Project

```python
# Bug fixes must pass existing tests
await manager.set_hooks_for_type(
    "bug_fix",
    post_hooks=[
        "pytest tests/ -x",
        "black --check .",
        "flake8 ."
    ]
)

# Features require full test coverage
await manager.set_hooks_for_type(
    "feature",
    post_hooks=[
        "pytest tests/ --cov=sugar --cov-fail-under=80",
        "black --check .",
        "mypy sugar/"
    ]
)
```

### Node.js Project

```python
await manager.set_hooks_for_type(
    "feature",
    pre_hooks=["npm install"],  # Ensure dependencies
    post_hooks=[
        "npm test",
        "npm run lint",
        "npm run type-check"
    ]
)
```

### Multi-language Project

```python
await manager.set_hooks_for_type(
    "bug_fix",
    post_hooks=[
        # Backend tests
        "pytest backend/tests/",
        # Frontend tests
        "(cd frontend && npm test)",
        # Integration tests
        "./scripts/integration-tests.sh"
    ]
)
```

## Disabling Hooks

Hooks can be disabled globally in the executor configuration:

```python
config = {
    "hooks_enabled": False,  # Disable all hooks
    # ... other config
}

executor = AgentSDKExecutor(config)
```

Or set empty hook arrays:

```python
await manager.set_hooks_for_type(
    "bug_fix",
    pre_hooks=[],
    post_hooks=[]
)
```

## Best Practices

1. **Keep hooks fast**: Hooks run on every task execution. Keep them under 1-2 minutes.

2. **Use selective tests**: Instead of running all tests, run only relevant ones:
   ```bash
   pytest tests/test_auth.py  # Just auth tests
   pytest tests/ -k "user"    # Tests matching "user"
   ```

3. **Fail fast**: Use `-x` flag with pytest to stop on first failure

4. **Combine related checks**: Group related validation in a single script:
   ```bash
   ./scripts/quality-checks.sh  # Runs multiple checks
   ```

5. **Handle errors gracefully**: Pre-hooks should only fail for critical issues. Post-hooks are better for quality checks.

6. **Use post-hooks for validation**: Post-hooks allow task completion even if validation fails, enabling human review.

## Troubleshooting

### Hook Timeout

If hooks timeout frequently, increase the timeout in the executor or optimize the hook command:

```python
# In HookExecutor.execute_hooks()
result = await hook_executor.execute_hooks(
    hooks,
    "post_hooks",
    task,
    timeout=600  # 10 minutes
)
```

### Hook Failures

Check the task result for hook execution details:

```python
if result.get("post_hook_failed"):
    print(f"Failed hook: {result['post_hook_error']}")
    print(f"Hook output: {result['post_hook_result']}")
```

### Missing Commands

Ensure commands are available in the shell environment:

```bash
# Pre-hook to verify environment
pre_hooks=["which pytest || pip install pytest"]
```
