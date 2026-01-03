# Ralph Wiggum Integration

Sugar integrates the Ralph Wiggum iterative development technique for complex tasks that benefit from self-correction and refinement.

## Quick Start

The simplest way to use Ralph:

```bash
# 1. Add a task with --ralph flag
sugar add "Fix the auth bug" --type bug_fix --ralph \
  --completion-promise "BUG FIXED"

# 2. Run Sugar (Ralph iterates automatically in the background)
sugar run
```

**Key Points:**
- `--ralph` enables iterative execution for the task
- `--completion-promise` sets the signal that marks completion (optional, defaults to "DONE")
- `--max-iterations` limits attempts (optional, defaults to 10)
- Each iteration, Claude sees previous work and continues improving
- Task completes when the promise signal is output or max iterations reached

## What is Ralph Wiggum?

The Ralph Wiggum technique, pioneered by [Geoffrey Huntley](https://ghuntley.com/ralph/), is an iterative AI loop methodology:

1. The same prompt is fed to Claude repeatedly
2. Each iteration, Claude sees its previous work in files/git history
3. Claude iteratively improves until completion criteria are met
4. A `<promise>` tag signals successful completion

This approach is "deterministically bad in an undeterministic world" - failures are predictable, enabling systematic improvement through prompt tuning.

## When to Use Ralph

**Good for:**
- Complex debugging with unknown root causes
- Test-driven development (write tests, implement, refine)
- Exploratory refactoring
- Multi-step features requiring iteration
- Tasks where single-pass attempts often fail

**Not good for:**
- Simple, single-step tasks
- Tasks with unclear success criteria
- Production incident response (use targeted debugging)
- Tasks requiring human judgment

## Configuration

Add Ralph settings to your `.sugar/config.yaml`:

```yaml
sugar:
  # ... other settings ...

  # Ralph Wiggum Integration
  ralph:
    enabled: true

    # Safety limits
    max_iterations: 10          # Default iteration limit
    iteration_timeout: 300      # Timeout per iteration (seconds)

    # Completion detection
    completion_promise: "DONE"  # Default completion signal
    require_completion_criteria: true  # Enforce explicit exit conditions

    # Quality gates between iterations
    quality_gates_enabled: true
    stop_on_gate_failure: false  # Continue iterating on failure

    # Confidence threshold for completion
    min_confidence: 0.8
```

## Usage

### Via Sugar CLI

```bash
# Add a task that will use Ralph iterations
sugar add "Fix flaky auth tests" --type bug_fix --ralph

# Add with custom completion signal
sugar add "Implement caching" --ralph --completion-promise "CACHING COMPLETE"

# Add with custom iteration limit
sugar add "Refactor cache module" --ralph --max-iterations 15

# Full example with all options
sugar add "Refactor auth to JWT" --type refactor --ralph \
  --completion-promise "JWT AUTH COMPLETE" \
  --max-iterations 20 \
  --description "Migrate from session-based to JWT. All auth tests must pass."
```

### Via Task Queue

When adding tasks programmatically:

```python
from sugar.storage import WorkQueue

queue = WorkQueue(".sugar/sugar.db")
await queue.initialize()

await queue.add_work({
    "type": "complex_bug",
    "title": "Fix race condition in worker pool",
    "description": """
        Debug and fix the intermittent race condition.

        When complete:
        - All worker pool tests pass consistently (10 runs)
        - No thread safety warnings
        - Output: <promise>RACE CONDITION FIXED</promise>
    """,
    "context": {
        "ralph_enabled": True,
        "max_iterations": 20,
    },
})
```

### Direct Profile Usage

```python
from sugar.ralph import RalphWiggumProfile, RalphConfig, CompletionCriteriaValidator

# Create profile with custom config
config = RalphConfig(
    max_iterations=15,
    completion_promise="TASK COMPLETE",
    require_completion_criteria=True,
)
profile = RalphWiggumProfile(ralph_config=config)

# Validate prompt before execution
validator = CompletionCriteriaValidator(strict=True)
result = validator.validate(prompt)

if not result.is_valid:
    print("Missing completion criteria:")
    for suggestion in result.suggestions:
        print(f"  - {suggestion}")
else:
    # Safe to proceed with Ralph loop
    processed = await profile.process_input({"prompt": prompt})
```

## Prompt Format

### Required: Completion Signal

Every Ralph prompt MUST include a completion signal:

```
Output: <promise>YOUR_SIGNAL</promise>
```

Without this, Ralph cannot detect when the task is complete.

### Recommended: Success Criteria

Include explicit success criteria:

```markdown
Fix the authentication timeout bug.

When complete:
- All auth tests pass
- No timeout errors in 10 consecutive runs
- Error handling covers edge cases
- Output: <promise>AUTH FIXED</promise>
```

### Example Prompts

**Bug Fix:**
```markdown
Debug and fix the memory leak in the WebSocket handler.

Context:
- Memory grows unbounded after ~1000 connections
- Suspected cause: event listeners not being cleaned up

When complete:
- Memory usage stable under load test (1 hour)
- All WebSocket tests pass
- No memory warnings in logs
- Output: <promise>MEMORY LEAK FIXED</promise>
```

**TDD Feature:**
```markdown
Implement rate limiting for the API using TDD.

Requirements:
- 100 requests per minute per IP
- 429 response when exceeded
- Redis-backed for distributed support

When complete:
- All rate limiting tests pass (write tests first!)
- Integration tests verify Redis persistence
- Documentation updated
- Output: <promise>RATE LIMITING COMPLETE</promise>
```

**Refactoring:**
```markdown
Refactor the UserService to use the repository pattern.

Goals:
- Extract data access to UserRepository
- UserService depends on interface, not implementation
- Maintain 100% test coverage

When complete:
- All existing tests pass
- New repository tests added
- No direct database calls in UserService
- Output: <promise>REFACTOR COMPLETE</promise>
```

## Safety Features

### Completion Criteria Validation

Sugar validates prompts BEFORE starting Ralph loops:

```python
from sugar.ralph import CompletionCriteriaValidator

validator = CompletionCriteriaValidator(strict=True)
result = validator.validate(prompt)

if not result.is_valid:
    # Loop rejected - would run forever
    print(result.errors)
    # ['No clear completion criteria found...']

    print(result.suggestions)
    # ['Add a completion signal: Output: <promise>DONE</promise>...']
```

**Validation checks for:**
1. `<promise>` tags in the prompt
2. `--max-iterations` or `max_iterations` settings
3. "When complete:" sections with criteria

### Iteration Limits

Default safety limit: 10 iterations. Configure per-task or globally:

```yaml
# Global default
sugar:
  ralph:
    max_iterations: 10

# Per-task override
sugar add "Complex task" --ralph --max-iterations 25
```

### Stuck Detection

Ralph detects when tasks are stuck and stops automatically:

```python
# These patterns trigger early termination:
stuck_patterns = [
    "cannot proceed",
    "blocked by",
    "need human intervention",
    "unable to resolve",
]
```

### Quality Gates

Quality gates run between iterations to catch regressions:

```yaml
sugar:
  ralph:
    quality_gates_enabled: true
    stop_on_gate_failure: false  # true = stop on first failure
```

## Monitoring

### Iteration Statistics

```python
profile = RalphWiggumProfile()

# After execution
stats = profile.get_iteration_stats()
# {
#     "current_iteration": 5,
#     "total_iterations": 5,
#     "max_iterations": 10,
#     "is_complete": True,
#     "completion_reason": "Promise signal received: DONE",
#     "successful_iterations": 1,
# }
```

### Iteration History

```python
# Review what happened each iteration
for record in profile._iteration_history:
    print(f"Iteration {record['iteration']}: {'success' if record['success'] else 'continuing'}")
    print(f"  Summary: {record['summary']}")
    print(f"  Files changed: {record['files_changed']}")
```

## Integration with Sugar Loop

When Ralph is enabled for a task, Sugar's main loop:

1. **Validates** completion criteria before starting
2. **Executes** iterations using RalphWiggumProfile
3. **Checks** for completion after each iteration
4. **Runs** quality gates between iterations
5. **Stops** when promise detected or max iterations reached
6. **Reports** iteration statistics in task result

```python
# In Sugar's executor
if work_item.get("context", {}).get("ralph_enabled"):
    profile = RalphWiggumProfile(ralph_config)

    while profile.should_continue():
        result = await agent.execute(prompt)
        output = await profile.process_output(result)

        if output["complete"]:
            break
```

## Troubleshooting

### "No clear completion criteria found"

Add explicit completion criteria:
```
When complete:
- [Your criteria]
- Output: <promise>DONE</promise>
```

### Ralph loops indefinitely

1. Check that your prompt includes `<promise>` tags
2. Verify max_iterations is set
3. Check that completion criteria are achievable

### Ralph stops too early

1. Check stuck_patterns aren't matching prematurely
2. Increase max_iterations if needed
3. Review iteration history to understand why it stopped

### Quality gates failing between iterations

Set `stop_on_gate_failure: false` to continue iterating despite failures, or fix the underlying quality issues.

## API Reference

### RalphConfig

```python
@dataclass
class RalphConfig:
    max_iterations: int = 10
    completion_promise: str = "DONE"
    require_completion_criteria: bool = True
    min_confidence: float = 0.8
    iteration_timeout: int = 300
    iteration_delay: float = 1.0
    quality_gates_enabled: bool = True
    stop_on_gate_failure: bool = False
    completion_patterns: List[str] = [...]
    stuck_patterns: List[str] = [...]
```

### CompletionCriteriaValidator

```python
class CompletionCriteriaValidator:
    def __init__(self, strict: bool = True)
    def validate(self, prompt: str, config: Optional[Dict] = None) -> ValidationResult
    def extract_completion_signal(self, output: str) -> Tuple[bool, Optional[str]]
    def format_validation_error(self, result: ValidationResult) -> str
```

### RalphWiggumProfile

```python
class RalphWiggumProfile(BaseProfile):
    def __init__(self, config: Optional[ProfileConfig] = None, ralph_config: Optional[RalphConfig] = None)
    @property
    def current_iteration(self) -> int
    async def process_input(self, input_data: dict) -> dict
    async def process_output(self, output_data: dict) -> dict
    def should_continue(self) -> bool
    def reset(self) -> None
    def get_iteration_stats(self) -> dict
```

## Learn More

- [Ralph Wiggum Technique](https://ghuntley.com/ralph/) - Original methodology
- [Ralph Orchestrator](https://github.com/mikeyobrien/ralph-orchestrator) - Standalone implementation
- [Sugar Profiles](./task_orchestration.md) - Sugar's profile system
