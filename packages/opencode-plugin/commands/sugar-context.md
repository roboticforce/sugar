---
name: sugar-context
description: Load project context from Sugar's memory at session start
---

# Project Context

Load and display the full project context from Sugar's memory system.

## When to Use

- At the start of a new session
- Before working on an unfamiliar part of the codebase
- When you need a refresher on project conventions

## Process

1. Call `get_project_context` MCP tool
2. Present the context in a clear, organized format
3. Highlight any critical preferences or patterns

## Presentation

Format the context as:

```
Project Context from Sugar Memory
=================================

Coding Preferences:
- Always use async/await, never callbacks
- Prefer early returns over nested conditionals
- Use TypeScript strict mode

Recent Decisions:
- Using PostgreSQL for the database (ACID, JSON support)
- JWT with RS256 for authentication tokens
- Monorepo structure with pnpm workspaces

Known Error Patterns:
- Login loop: missing return after redirect
- Connection timeout: increase pool size to 20

File Context:
- src/auth/ handles all authentication logic
- src/api/routes/ contains REST endpoints
```

## After Loading

1. Summarize key points relevant to the current task
2. Mention `/sugar-remember` to add new context
3. Suggest `/sugar-recall [topic]` for deeper dives

## If No Context

If memory is empty:

1. Explain this is a new project or memory hasn't been populated
2. Suggest storing key decisions with `/sugar-remember`
3. Offer to help identify important context to store
