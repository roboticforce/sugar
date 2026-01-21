---
name: sugar-recall
description: Search Sugar's memory for relevant context
---

# Recall Memory

You are helping the user retrieve relevant information from Sugar's persistent memory system.

## Process

1. **Understand the Query**: What information does the user need?
2. **Search Memory**: Use the appropriate MCP tool
3. **Present Results**: Format findings clearly
4. **Suggest Actions**: Offer to store new learnings if relevant

## Available Tools

| Tool | Use When |
| ---- | -------- |
| `recall` | Get formatted context about a topic (best for general queries) |
| `search_memory` | Search with scoring, get structured results |
| `get_project_context` | Get overall project context (preferences, decisions, patterns) |
| `list_recent_memories` | Browse recent entries, optionally by type |

## Example Interactions

**General recall:**

> User: "what do we know about authentication?"
> Use: recall with topic="authentication"

**Specific search:**

> User: "find all our database decisions"
> Use: search_memory with query="database decisions architecture"

**Project context:**

> User: "what are our coding preferences?"
> Use: get_project_context (returns all preferences, decisions, patterns)

**Recent memories:**

> User: "what did we learn recently?"
> Use: list_recent_memories with limit=10

## Presentation

Format results clearly:

```
Found 3 relevant memories:

1. [decision] Using PostgreSQL for ACID compliance
   Stored: 2 days ago

2. [preference] Always use prepared statements for queries
   Stored: 1 week ago

3. [error_pattern] Connection pool exhaustion: increase max_connections
   Stored: 3 days ago
```

## When No Results

If no memories found:

1. Confirm what was searched
2. Suggest alternative search terms
3. Offer to store new information with `/sugar-remember`

## Proactive Use

Consider using memory tools proactively:

- At the start of a task: `get_project_context`
- When encountering errors: `search_memory` for similar patterns
- Before making decisions: `recall` related past decisions
