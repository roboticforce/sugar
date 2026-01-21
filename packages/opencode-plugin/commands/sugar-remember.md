---
name: sugar-remember
description: Store a learning, decision, or preference in Sugar's memory
---

# Store Memory

You are helping the user store important information in Sugar's persistent memory system.

## Process

1. **Understand What to Remember**: Parse what the user wants to store
2. **Classify the Memory**: Determine the appropriate type
3. **Store It**: Use the `store_learning` MCP tool
4. **Confirm**: Tell the user what was stored

## Memory Types

| Type | Use When |
| ---- | -------- |
| `decision` | Architecture choices, implementation decisions, "we decided to..." |
| `preference` | Coding style, conventions, "always use...", "never do..." |
| `error_pattern` | Bug patterns and their fixes, "this error means..." |
| `research` | Findings, discoveries, documentation notes |
| `file_context` | What files do, where things are located |
| `outcome` | Results of previous attempts, what worked/didn't |

## Example Interactions

**Storing a preference:**

> User: "remember that we always use async/await in this project"
> Use: store_learning with content="Always use async/await, never callbacks", type="preference"

**Storing a decision:**

> User: "remember we chose PostgreSQL for the database"
> Use: store_learning with content="Using PostgreSQL as the main database for ACID compliance and JSON support", type="decision"

**Storing an error pattern:**

> User: "remember that the login loop bug was caused by missing return statement"
> Use: store_learning with content="Login loop bug: caused by missing return statement in auth handler after redirect", type="error_pattern"

## Best Practices

When storing memories:

1. **Be specific**: Include context and reasoning, not just facts
2. **Add tags**: Use relevant tags for easier recall (e.g., "auth,security")
3. **Include the "why"**: Future sessions benefit from understanding reasoning

## After Storing

1. Confirm the memory was stored with its ID
2. Mention they can search memories with `/sugar-recall`
3. Suggest related memories if relevant
