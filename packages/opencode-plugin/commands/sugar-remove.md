---
name: sugar-remove
description: Remove a task from the work queue
---

# Remove Sugar Task

Permanently remove a task from Sugar's work queue.

## Caution

This action cannot be undone. Confirm with the user before removing.

## Usage

1. User provides task ID
2. Confirm the removal
3. Execute sugar_remove
4. Confirm success

## Example

> User: "remove task abc123"
> Confirm: "Remove task abc123? This cannot be undone."
> If confirmed: sugar_remove with taskId="abc123"
