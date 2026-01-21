---
name: sugar-add
description: Add a new task to Sugar's autonomous work queue
---

# Add Sugar Task

You are helping the user add a task to Sugar's autonomous development queue.

## Process

1. **Understand the Request**: Parse what the user wants to accomplish
2. **Classify the Task**: Determine type (bug_fix, feature, test, refactor, documentation)
3. **Assess Priority**: Based on urgency and impact (1-5 scale)
4. **Create the Task**: Use the sugar_add tool

## Priority Guide

| Priority | Label   | Use When                                    |
| -------- | ------- | ------------------------------------------- |
| 5        | Urgent  | Production is broken, security issue        |
| 4        | High    | Blocking other work, deadline approaching   |
| 3        | Normal  | Standard development work                   |
| 2        | Low     | Nice to have, backlog items                 |
| 1        | Minimal | Future consideration, ideas                 |

## Task Types

- **bug_fix**: Something is broken or not working as expected
- **feature**: New functionality or capability
- **test**: Adding or improving test coverage
- **refactor**: Code improvement without changing behavior
- **documentation**: README, comments, API docs

## Example Interactions

**Simple task:**

> User: "add task to fix the login timeout bug"
> Use: sugar_add with title="Fix login timeout bug", type="bug_fix", priority=4

**Urgent task:**

> User: "urgent: API is returning 500 errors"
> Use: sugar_add with title="Fix API 500 errors", type="bug_fix", urgent=true

**Complex task:**

> User: "add task to implement user dashboard with charts"
> Use: sugar_add with title="Implement user dashboard with charts", type="feature", priority=3, triage=true

## After Creating

1. Confirm the task was created with its ID
2. Mention they can view the queue with `/sugar-list`
3. Suggest `/sugar-run` if they want to execute it now
