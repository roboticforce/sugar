---
name: sugar-memory
description: Use Sugar's local persistent memory in Hermes. Activate at the start of non-trivial work to recall prior decisions, error patterns and guidelines, before making a decision past sessions may already have settled, and after finishing work when something durable was learned. Do NOT use for trivia, one-off questions, or work with no project context.
version: 1.0.0
metadata:
  author: Steven Leggett <contact@roboticforce.io>
---

# Sugar memory

Sugar is the agent's durable memory: SQLite on this machine, split into a
project store (`.sugar/memory.db`) and a global store (`~/.sugar/memory.db`).
Nothing leaves the machine. Recall is also injected automatically each turn;
this skill is about the deliberate reads and writes that automatic recall
cannot do.

## Recall before deciding

Call `sugar_search` at the start of non-trivial work and before any decision
that past sessions may already have answered:

- starting a task in an existing project
- choosing a library, pattern, or deployment path
- touching an area that has failed before
- answering "how do we do X here?"

Search with the task's own vocabulary, not generic words. `sugar_search` ranks
project memories first and always lets global guidelines surface.

## Store after learning

Call `sugar_store` when work produces something worth carrying forward. Store
the *conclusion and its reason*, not the transcript:

| Type | Use for |
|------|---------|
| `decision` | A choice made and why ("chose Kamal over raw Docker for deploys because …") |
| `preference` | A standing user or team preference ("pin dependencies exactly") |
| `error_pattern` | A failure and its fix, so it is not repeated |
| `guideline` | A cross-project rule that should always apply |
| `research` | A finding that took real work to establish |
| `outcome` | What a completed piece of work actually produced |
| `file_context` | Durable facts about a file or module |

Scope matters:

- `scope: "project"` (default) — only true for this repository.
- `scope: "global"` — applies everywhere: user preferences, house rules,
  cross-project conventions. Use this for anything you would want in a fresh
  repo.

Prefer a few precise entries over many vague ones. Do not store secrets, and do
not store what the code or git history already records.

## Browse

`sugar_list_recent` shows the newest entries, optionally filtered by type — use
it when the user asks what has been remembered, or when search returns nothing
and you want to see what is there.

## Writing well

- One idea per entry, stated so it makes sense months later without context.
- Include the *why*; a bare "use X" rots, "use X because Y" survives.
- Tag with `tags` for retrieval ("deploy,postgres").
- If the user corrects you, store the correction as a `preference` so it
  outlives the session.
