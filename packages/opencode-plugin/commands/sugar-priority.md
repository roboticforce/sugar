---
name: sugar-priority
description: Change task priority
---

# Change Task Priority

Reprioritize a task in the Sugar queue.

## Priority Levels

| Input          | Level   | Description      |
| -------------- | ------- | ---------------- |
| 5 or "urgent"  | Highest | Do immediately   |
| 4 or "high"    | High    | Do soon          |
| 3 or "normal"  | Normal  | Standard priority|
| 2 or "low"     | Low     | When time permits|
| 1 or "minimal" | Lowest  | Eventually       |

## Usage

> User: "make task abc123 urgent"
> Use: sugar_priority with taskId="abc123", level="urgent"

> User: "set task xyz789 to priority 2"
> Use: sugar_priority with taskId="xyz789", level=2
