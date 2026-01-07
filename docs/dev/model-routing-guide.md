# Model Routing Guide

Sugar includes an intelligent model routing system that automatically selects the optimal Claude model based on task complexity. This enables cost-effective model selection while ensuring complex tasks get the capabilities they need.

## Overview

The model routing system uses a tier-based approach:

| Tier | Complexity | Use Cases | Typical Model |
|------|------------|-----------|---------------|
| **simple** | 1-2 | Docs, style fixes, chores | Claude Haiku |
| **standard** | 3 | Bug fixes, tests, CI tasks | Claude Sonnet |
| **complex** | 4-5 | Features, refactors, security | Claude Sonnet/Opus |

## Configuration

Model routing is configured in `.sugar/config.yaml`:

```yaml
sugar:
  models:
    # Map tiers to actual Claude model names
    simple: "claude-haiku-3-5-20241022"
    standard: "claude-sonnet-4-20250514"
    complex: "claude-sonnet-4-20250514"

    # Enable/disable dynamic routing
    dynamic_routing: true
```

### Key Design Decisions

1. **Tiers, Not Model Names**: The database stores tier names (`simple`, `standard`, `complex`), not actual model identifiers. This allows you to update models without database migrations.

2. **Config-Based Resolution**: Actual model names are resolved at runtime from config, making it easy to switch to newer models as they release.

3. **Graceful Fallbacks**: Unknown tiers default to `standard`, ensuring tasks always get executed.

## How Model Selection Works

### 1. Task Type Defaults

Each task type has a default tier and complexity level:

| Task Type | Default Tier | Complexity Level |
|-----------|--------------|------------------|
| `docs` | simple | 1 |
| `style` | simple | 1 |
| `chore` | simple | 2 |
| `test` | standard | 2 |
| `bug_fix` | standard | 3 |
| `ci` | standard | 2 |
| `feature` | complex | 3 |
| `refactor` | complex | 4 |
| `perf` | complex | 4 |
| `security` | complex | 4 |

### 2. Runtime Analysis

Even after determining the initial tier, the router performs runtime analysis on the task title and description. It looks for:

**Complexity Indicators** (may upgrade tier):
- refactor, migrate, redesign, rewrite, architecture
- system-wide, multi-file, complex, comprehensive
- integrate, breaking change, major, overhaul

**Simplicity Indicators** (may downgrade tier):
- typo, comment, formatting, style, rename
- trivial, minor, quick, simple, update docs

### 3. Override Mechanisms

Tasks can override model selection in their context:

```python
# Explicit model override
task["context"]["model_override"] = "claude-opus-4-5-20251101"

# Explicit tier override
task["context"]["model_tier"] = "complex"
```

## Implementation Details

### ModelRouter Class

Location: `sugar/orchestration/model_router.py`

```python
from sugar.orchestration.model_router import ModelRouter, ModelTier, create_model_router

# Initialize with config
router = create_model_router(config)

# Route a task
selection = router.route(task, task_type_info)
print(f"Using model: {selection.model}")
print(f"Tier: {selection.tier.value}")
print(f"Reason: {selection.reason}")
```

### ModelSelection Dataclass

The `route()` method returns a `ModelSelection` with:

| Field | Type | Description |
|-------|------|-------------|
| `model` | str | Actual Claude model name |
| `tier` | ModelTier | The tier (SIMPLE/STANDARD/COMPLEX) |
| `reason` | str | Explanation for the selection |
| `task_type` | str | Task type that influenced decision |
| `complexity_level` | int | Complexity level (1-5) |
| `override_applied` | bool | Whether an override was used |

## Database Schema

Task types store their tier information:

```sql
-- In task_types table
model_tier TEXT DEFAULT 'standard'    -- simple|standard|complex
complexity_level INTEGER DEFAULT 3    -- 1-5 scale
```

This is automatically managed when creating or updating task types.

## Cost Optimization

By using model routing, you can significantly reduce API costs:

| Scenario | Without Routing | With Routing | Savings |
|----------|----------------|--------------|---------|
| 10 doc fixes | 10x Sonnet | 10x Haiku | ~85% |
| 5 bug fixes | 5x Sonnet | 5x Sonnet | 0% |
| 3 features | 3x Sonnet | 3x Complex | Varies |

The key insight is that simple tasks like documentation fixes, style changes, and chores don't need the full reasoning capabilities of larger models.

## Customizing Task Type Tiers

Use the task type CLI to customize tiers:

```bash
# List task types with their tiers
sugar task-type list

# Update a task type's tier
sugar task-type update docs --tier simple --complexity 1
sugar task-type update feature --tier complex --complexity 4
```

## Best Practices

1. **Start with Defaults**: The default tier assignments work well for most projects.

2. **Monitor and Adjust**: Review task outcomes and adjust tiers if tasks are failing or over-resourced.

3. **Use Config for Model Updates**: When Anthropic releases new models, update your config rather than database.

4. **Override Sparingly**: Use context overrides for exceptional cases, not as the norm.

5. **Consider Task Scope**: Multi-file tasks generally need higher tiers than single-file changes.

## Integration with AgentSDKExecutor

The `AgentSDKExecutor` automatically uses the model router when `dynamic_routing` is enabled:

```python
# In executor
if self.model_router and self.dynamic_routing_enabled:
    selection = self.model_router.route(task, task_type_info)
    model = selection.model
else:
    model = self.default_model
```

## Troubleshooting

### Task Using Wrong Model

1. Check the task type's tier: `sugar task-type list`
2. Verify config has correct model mappings
3. Check for context overrides in task data
4. Review runtime analysis in logs (DEBUG level)

### Model Not Found Error

Ensure the model name in config is valid:
```yaml
models:
  simple: "claude-haiku-3-5-20241022"  # Must be exact model ID
```

### Routing Not Working

Verify `dynamic_routing` is enabled:
```yaml
sugar:
  models:
    dynamic_routing: true  # Must be true
```

## See Also

- [Configuration Best Practices](../user/configuration-best-practices.md)
- [Task Orchestration](../task_orchestration.md)
- [Agent SDK Integration](../user/agent-sdk.md)
