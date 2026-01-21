---
name: sugar-run
description: Execute one autonomous development cycle
---

# Run Sugar Cycle

Execute one autonomous development cycle. Sugar will:

1. Pick the highest priority pending task
2. Execute it using the configured AI agent
3. Mark as completed or failed based on outcome

## Options

- **Dry run**: Simulate without making changes
- **Validate**: Check configuration first

## Usage Patterns

- "run sugar" -> sugar_run
- "test sugar without changes" -> sugar_run with dryRun=true
- "validate sugar config" -> sugar_run with validate=true

## Important Notes

- Long-running operation (may take several minutes)
- One task per cycle
- Check /sugar-status after for results
