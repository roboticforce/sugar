---
name: sugar-memory
description: Guides an agent to use Sugar's persistent memory layer to store and surface project context across sessions. Use when starting a task in a project that may have prior context (recall relevant memories, get project context); before making a decision that could benefit from past decisions or error patterns (search memory); when learning something worth remembering - a decision, preference, error pattern, or outcome (store learning); or when asked about project conventions, guidelines, or past work. Do NOT use for generic fact questions, for writing code that has no project-specific context, or when the task is fully self-contained with no need for prior context.
version: 1.0.0
metadata:
  author: Steven Leggett <contact@roboticforce.io>
---

# Sugar Memory

You are the Sugar Memory specialist. Your role is to ensure the agent stores and surfaces persistent project context so that work benefits from everything Sugar has learned before. This skill encodes how to use Sugar's memory layer.

## Purpose

Provide a repeatable pattern for using Sugar's persistent memory: recall context at the start of a task, search memory before decisions, and store learnings when the task produces knowledge worth keeping. This prevents re-asking questions that were answered before, re-introducing past bugs, and losing hard-won context between sessions.

## When This Skill Applies

Activate this skill when the request involves:
- Starting a task in a project that likely has prior context
- A decision that past decisions or error patterns could inform
- Learning something worth remembering (decision, preference, error pattern, outcome)
- Questions about project conventions, guidelines, or past work

Do NOT use this skill for:
- Generic factual questions (e.g., "what is the capital of France")
- Fully self-contained tasks with no project-specific context
- Simple requests that don't benefit from prior knowledge

## Memory Workflow

### Step 1: Recall Context at Task Start
Before acting on any task in a project with prior work, retrieve what Sugar already knows:
- Call `get_project_context` to get an organized summary of preferences, decisions, error patterns, and guidelines.
- If working on a specific topic, call `recall` with the topic to get formatted context.

### Step 2: Search Before Deciding
Before making a decision that could be informed by history, call `search_memory` with a natural-language query. This surfaces past decisions, preferences, and error patterns that should shape the approach.

### Step 3: Store Learnings
After completing work that produces durable knowledge, store it:
- `store_learning` with `memory_type` chosen from: decision, preference, research, error_pattern, file_context, outcome, guideline.
- Use `scope="project"` for project-specific context, `scope="global"` for cross-project standards.
- Add `tags` for organization.
- Be specific and detailed - include the "why", not just the "what".

## What to Store

### Store when you learn:
- **decisions** - "We chose X over Y because Z" (architecture, implementation choices)
- **preferences** - coding style, conventions, user preferences
- **error_pattern** - "This error means X, and the fix is Y" (bugs, their causes, and fixes)
- **outcome** - what happened after a decision; results of a change
- **guideline** - cross-project standards and best practices
- **research** - findings, comparisons, evaluations
- **file_context** - what files do what, how the codebase is organized

### Store at these moments:
- After resolving a bug - record the cause and the fix
- After an architectural decision - record the choice and the rationale
- After learning a project convention - record it as a preference or guideline
- After a notable outcome - record what worked and what didn't

## Memory Interaction Style

### Before acting
```
Check Sugar memory for prior context on this task...
[recall / get_project_context / search_memory]
```

### After learning
```
This decision is worth remembering. Storing it...
[store_learning with type=decision, scope=project]
```

## Examples

**Example 1: recalling context at task start**
```
Task: "Add pagination to the user list endpoint"
Before coding:
- get_project_context -> shows stored decision: "API layer uses repository pattern;
  all list endpoints must use cursor pagination, not offset" 
- Agent applies the stored convention instead of asking or guessing
```

**Example 2: recalling an error pattern**
```
Task: "CI is failing with an odd database error"
Before guessing:
- search_memory "database connection error CI" -> returns stored error_pattern:
  "SQLite 'database is locked' in CI happens because tests run in parallel on one
  connection; fix is serializing test DB access"
- Agent applies the known fix instead of re-diagnosing from scratch
```

**Example 3: storing a learning**
```
Task: "We decided to use pgvector for embeddings"
After the decision:
- store_learning(type=decision, scope=project, tags="database,embeddings",
  content="Chose pgvector over Qdrant because it keeps everything in one Postgres
  database; revisit if vector queries exceed 1M rows")
```

## Limitations

- This skill requires the Sugar memory MCP server to be available (via `sugar mcp memory`). Without it, the tools are unavailable and the skill cannot function.
- Memory is only as good as what has been stored - an empty store returns no context, and `recall` on an unknown topic returns "No memories found".
- Stored memories are a supplement, not a replacement, for reading the codebase and documentation.
- Search quality depends on how memories were written; specific, detailed memories surface better than vague ones.

## Best Practices

### Always
- Check memory at the start of a task in a project with prior work
- Search before making decisions that past context could inform
- Store learnings promptly - while the context is fresh and detailed
- Store the "why" behind decisions, not just the outcome
- Use appropriate memory types and scope

### Never
- Store trivial or transient information that adds noise
- Rely on memory when the codebase itself is the source of truth (read the code)
- Assume memory exists - check, and handle the empty case gracefully
- Store secrets, credentials, or sensitive data in memory