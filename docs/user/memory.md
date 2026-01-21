# Sugar Memory System

Sugar's memory system provides persistent semantic memory across coding sessions. Store decisions, preferences, error patterns, and research findings - then recall them when relevant.

## Overview

The memory system solves a key problem with AI coding assistants: **context loss between sessions**. Every time you start a new session, you lose:
- Decisions you've made about architecture
- Your coding preferences and style
- Error patterns you've encountered and fixed
- Research you've done on APIs and libraries

Sugar Memory persists this knowledge and makes it searchable.

## Quick Start

```bash
# Install with memory support (enables semantic search)
pipx install 'sugarai[memory]'

# Store a preference
sugar remember "Always use async/await, never callbacks" --type preference

# Store a decision
sugar remember "Chose JWT with RS256 for auth tokens" --type decision

# Search memories
sugar recall "authentication"

# View all memories
sugar memories
```

## Memory Types

Sugar organizes memories into six categories:

| Type | Description | TTL Default | Example |
|------|-------------|-------------|---------|
| `decision` | Architecture/implementation choices | Never | "Using PostgreSQL for main DB" |
| `preference` | Coding style and conventions | Never | "Prefer early returns over nested if" |
| `file_context` | What files/modules do | Never | "payment_processor.py handles Stripe" |
| `error_pattern` | Bugs and their fixes | 90 days | "Login loop caused by missing return" |
| `research` | API docs, library findings | 60 days | "Stripe idempotency keys required" |
| `outcome` | Task results and learnings | 30 days | "Refactor improved load time 40%" |

## CLI Commands

### `sugar remember`

Store a new memory.

```bash
sugar remember "content" [options]

Options:
  --type TYPE        Memory type (decision, preference, research, etc.)
  --tags TAGS        Comma-separated tags for organization
  --file PATH        Associate with a specific file
  --ttl TTL          Time to live: 30d, 90d, 1y, never (default: never)
  --importance NUM   Importance score 0.0-2.0 (default: 1.0)
```

**Examples:**

```bash
# Basic preference
sugar remember "Use 4-space indentation for Python"

# Decision with tags
sugar remember "Chose Redis for session storage" --type decision --tags "architecture,redis"

# Research with expiration
sugar remember "Stripe API rate limit is 100/sec" --type research --ttl 90d

# File context
sugar remember "handles OAuth callback flow" --type file_context --file src/auth/callback.py

# High importance
sugar remember "NEVER use eval() for security reasons" --type preference --importance 2.0
```

### `sugar recall`

Search memories for relevant context.

```bash
sugar recall "query" [options]

Options:
  --type TYPE       Filter by memory type (or "all")
  --limit NUM       Maximum results (default: 10)
  --format FORMAT   Output: table, json, full (default: table)
```

**Examples:**

```bash
# Basic search
sugar recall "authentication"

# Filter by type
sugar recall "database errors" --type error_pattern

# JSON output for scripting
sugar recall "stripe" --format json

# Full details
sugar recall "architecture decisions" --format full --limit 5
```

### `sugar memories`

List all stored memories.

```bash
sugar memories [options]

Options:
  --type TYPE       Filter by memory type (or "all")
  --since DURATION  Filter by age (e.g., 7d, 30d, 2w)
  --limit NUM       Maximum results (default: 50)
  --format FORMAT   Output: table, json
```

**Examples:**

```bash
# List all
sugar memories

# Recent decisions
sugar memories --type decision --since 7d

# JSON export
sugar memories --format json > memories-backup.json
```

### `sugar forget`

Delete a memory by ID.

```bash
sugar forget <memory_id> [options]

Options:
  --force    Skip confirmation prompt
```

**Examples:**

```bash
# Interactive (shows confirmation)
sugar forget abc123

# Force delete
sugar forget abc123 --force
```

### `sugar export-context`

Export memories for Claude Code integration.

```bash
sugar export-context [options]

Options:
  --format FORMAT   Output: markdown, json, claude (default: markdown)
  --limit NUM       Max memories per type (default: 10)
  --types TYPES     Comma-separated types to include
```

**Examples:**

```bash
# Default markdown
sugar export-context

# JSON for programmatic use
sugar export-context --format json

# Specific types only
sugar export-context --types preference,decision
```

### `sugar memory-stats`

Show memory system statistics.

```bash
sugar memory-stats
```

Output:
```
📊 Sugar Memory Statistics

Semantic search: ✅ Available
Database: /Users/steve/project/.sugar/memory.db

Total memories: 47

By type:
  preference        12
  decision           8
  error_pattern      6
  research          15
  file_context       6

Database size: 156.2 KB
```

## Claude Code Integration

Sugar Memory integrates with Claude Code in two ways:

### 1. MCP Server (Recommended)

Add Sugar as an MCP server to give Claude Code full access to your memory:

```bash
claude mcp add sugar -- sugar mcp memory
```

Or add manually to `~/.claude.json`:
```json
{
  "mcpServers": {
    "sugar": {
      "type": "stdio",
      "command": "sugar",
      "args": ["mcp", "memory"]
    }
  }
}
```

**MCP Tools Available:**

| Tool | Description |
|------|-------------|
| `search_memory` | Semantic search over memories |
| `store_learning` | Store new observations/decisions |
| `get_project_context` | Get organized project summary |
| `recall` | Get formatted markdown context |
| `list_recent_memories` | List with optional type filter |

**MCP Resources:**

| Resource | Description |
|----------|-------------|
| `sugar://project/context` | Full project context |
| `sugar://preferences` | User coding preferences |

### 2. SessionStart Hook

Automatically inject context at the start of every Claude Code session:

Add to `~/.claude/settings.json`:
```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "sugar export-context"
      }]
    }]
  }
}
```

This runs `sugar export-context` at the start of each session, providing Claude with your stored preferences and recent decisions.

## Search: Semantic vs Keyword

Sugar supports two search modes:

### Semantic Search (Recommended)

Uses AI embeddings to find conceptually similar memories:
- "auth issues" finds memories about "authentication", "login", "JWT"
- Understands synonyms and related concepts
- Requires `sentence-transformers` package

**Enable:**
```bash
pipx install 'sugarai[memory]'
```

### Keyword Search (Fallback)

Uses SQLite FTS5 for text matching:
- Fast and lightweight
- No additional dependencies
- Matches exact words/phrases

Sugar automatically uses semantic search when available, falling back to keyword search otherwise.

## Storage & Data

### Location

Memories are stored per-project:
```
.sugar/
├── config.yaml
├── sugar.db      # Task queue
└── memory.db     # Memory database
```

### Schema

Each memory stores:
- `id` - Unique identifier
- `memory_type` - Category (decision, preference, etc.)
- `content` - The actual memory text
- `summary` - Optional short summary
- `metadata` - Tags, file paths, custom data
- `importance` - 0.0-2.0 score for ranking
- `created_at` - When stored
- `last_accessed_at` - Last search hit
- `access_count` - Number of times recalled
- `expires_at` - Optional expiration date

### Backup

```bash
# Export all memories to JSON
sugar memories --format json > memories-backup.json

# Copy database directly
cp .sugar/memory.db memory-backup.db
```

## Best Practices

### What to Remember

**Good memories:**
- Architecture decisions and their rationale
- Coding conventions specific to the project
- Error patterns you've debugged
- API quirks and workarounds
- File/module responsibilities

**Less useful:**
- Generic programming knowledge (Claude already knows)
- Highly volatile information
- Large code blocks (use file context instead)

### Memory Hygiene

```bash
# Review old memories periodically
sugar memories --since 90d

# Clean up irrelevant entries
sugar forget <id>

# Check stats
sugar memory-stats
```

### Organizing with Tags

```bash
# Group related memories
sugar remember "Use Redis cluster for sessions" --type decision --tags "architecture,redis,sessions"
sugar remember "Redis connection pool size: 20" --type decision --tags "architecture,redis,performance"

# Search by implied topics
sugar recall "redis configuration"
```

## Troubleshooting

### "Memory dependencies not installed"

```bash
pip install 'sugarai[memory]'
```

### "Semantic search not available"

Sentence-transformers failed to load. Check:
```bash
python -c "from sentence_transformers import SentenceTransformer; print('OK')"
```

Memory still works with keyword search.

### "Not a Sugar project"

Run from a directory with `.sugar/` folder:
```bash
sugar init  # If not initialized
```

### Slow first search

The embedding model loads on first use (~2-3 seconds). Subsequent searches are fast.

## API Reference

For programmatic access, import from `sugar.memory`:

```python
from sugar.memory import (
    MemoryStore,
    MemoryEntry,
    MemoryType,
    MemoryQuery,
    MemoryRetriever,
)

# Create store
store = MemoryStore("/path/to/memory.db")

# Store a memory
entry = MemoryEntry(
    id="my-id",
    memory_type=MemoryType.DECISION,
    content="Use PostgreSQL for main database",
)
store.store(entry)

# Search
query = MemoryQuery(query="database", limit=5)
results = store.search(query)

# Get context
retriever = MemoryRetriever(store)
context = retriever.get_project_context()
```
