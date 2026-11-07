# Sugar as a team bus: can it coordinate agents doing work together?
> Market context, positioning and demand-validation experiments live in
> [agent-teams-market.md](agent-teams-market.md); the architecture, Wardley map and domain
> boundaries live in [agent-teams-architecture.md](agent-teams-architecture.md).

Status: analysis, 2026-11-05, against `3.10.6.dev0` on `feature/local-dashboard`.
Companions: [mission-control.md](mission-control.md) is the shipped feature overview;
[local-dashboard.md](local-dashboard.md) is a frozen *contract*; this document is an *assessment*. It cites the code as it exists today and the `.sugar/` research notes as prior art.

**Short answer.** For durable work coordination on one machine: yes, and most of the hard part is
already built. For "another agent framework": no, and it shouldn't try. Sugar already has a durable
SQLite queue that any process can operate through the CLI and MCP, and its claim operation is
correct under concurrency. What it lacks is what makes concurrency *safe over time*: leases,
conflict-free connections, dependency-aware claims, and an HTTP surface. Those are small, incremental
changes. The differentiation is staying the substrate under heterogeneous agents, not becoming one.

---

## 0. Adopted split (2026-11-05)

The analysis below predates a concrete decision, so record the decision first:

| Lane | Owner | Rule |
|---|---|---|
| **Task state** (who is doing what) | **Hermes Kanban** (`~/.hermes/kanban.db`) | The board is the only shared task store. Cross-harness work that is not on the board does not exist. |
| **Context** (why it means the same thing) | **Sugar memory** (`.sugar/memory.db`) | Recall, not scheduling. |
| **Human mirror** | Apple Reminders | Read-only mirror for the phone; never a source of truth. |

Consequences for this repo:

- Sugar's `work_items` queue is **Sugar's own worker queue** (`sugar run`, orchestration), not a
  competing team board. The provenance/lease columns added on 2026-11-05 apply to that queue and map
  one-to-one onto the board's `created_by` / `claim_lock`, which is what the dashboard renders.
- The dashboard has two sources. For the *team* view point it at the board:
  `sugar dashboard --board kanban` (read-only SQLite reads, writes through `hermes kanban`).
  The default (`--board sugar`) is a Sugar-local view, not the board.
- Nothing in this document should be read as "make Sugar the task store" once the board exists.

---

## 1. What Sugar already is

A local-first, durable work queue — not an agent runtime. `work_items` is a 33-column SQLite table
(`sugar/storage/work_queue.py:38-59`, migrations at `:232-369`); this repo's `.sugar/sugar.db` has the
same 33-column `work_items` table, plus `task_types` and `issue_responses`. Agents operate it through:

- **CLI** — `sugar add/list/view/update/remove/hold/release/priority/status/orchestrate/context`
  (`sugar/main.py:347,683,808,994,902,930,963,1082,1842,1192,1345`).
- **MCP** — six tools and two resources over stdio (`sugar/mcp/task_server.py:82,187,248,341,444,475,535,570`;
  stdio-only runner `:600-612`), marketed as agent-agnostic: Claude Code, OpenCode, Goose, Aider
  (`README.md:229-234`).
- **MCP memory** — cross-session memory at `.sugar/memory.db` (`sugar/main.py:3924,4676`,
  `sugar/memory/store.py:1-7`).

The one hard concurrency problem — two workers must never claim the same task — is **already solved**.
`get_next_work` opens with `isolation_level=None`, runs `BEGIN IMMEDIATE`, selects the highest-priority
pending row, marks it `active`, and commits in one write transaction (`work_queue.py:458-525`;
`BEGIN IMMEDIATE` at `:469`, guarded SELECT at `:473-480`, status UPDATE at `:500-511`).
`tests/test_concurrency.py:61-151` asserts N simultaneous callers get distinct tasks or `None`; I ran
that class on 2026-11-05 — **4 passed**. This is the part most people get wrong first, and it is done
correctly.

Two honest limits (updated 2026-11-05: the claim now also records `claimed_by` and
`lease_expires_at` in the same transaction, so it is worker-aware; there is still no heartbeat or
reaper, so an expired lease is visible but not reclaimed). The loop is still a single sequential
worker:
`max_concurrent_work` (`core/loop.py:320`) loops, but each `execute_work` is awaited before the next
claim (`:322-331,430`) — no `asyncio.gather` in `loop.py`. Teams are what will exercise that claim.

---

## 2. Primitives that work today

| Primitive | Evidence | Honest status |
|---|---|---|
| **Atomic claim** | `BEGIN IMMEDIATE` at `work_queue.py:469`; tested `tests/test_concurrency.py:61-151` | Works; verified by running tests |
| **Priority ordering** | `ORDER BY priority ASC, created_at ASC` (`work_queue.py:477`); index `:63-68`; 1=urgent…5=minimal (`local-dashboard.md:110`) | Works; `sugar priority` re-orders (`main.py:1082`) |
| **Lifecycle + retries** | `pending/active/completed/failed/hold`; `fail_work` retries until `max_retries` (`work_queue.py:585-651`) | Works; a crash is *not* a retry |
| **Parent/subtask model** | `parent_task_id`, `stage`, `blocked_by`, `context_path`, `assigned_agent` (`work_queue.py:232-285`); four stages (`task_orchestrator.py:24-32`); subtasks built `:754-766`, persisted `:839-856` | Works inside orchestration only (G3) |
| **Dependency waves** | `get_ready_subtasks` requires each blocker `completed` (`work_queue.py:1098-1125`); wave loop `task_orchestrator.py:881-954`; orphan cascade `:956-966` | Correct, but single-level and orchestrator-private |
| **Per-task context dirs** | `.sugar/orchestration/<id>/context.md` (`task_orchestrator.py:1247-1260`), `research.md`/`plan.md` (`:155,164`); `sugar context` (`main.py:1345`) | Works; files, not records |
| **Specialist routing** | `AgentRouter` regexes (`agent_router.py:40-57`), explicit `assigned_agent` (`:131-136`), type fallback (`:157-177`) | Advisory only — see G3 |
| **Hold/release** | `hold_work`/`release_work` with context bookkeeping (`work_queue.py:920-992`); CLI `main.py:930,963` | Works; the only human intervention lever |

Two caveats hide in that table. **(a)** `task_types.agent` is dead data: the column exists and is
seeded (`work_queue.py:136-203`), and `sugar task-type add --agent` writes it (`main.py:3313-3319`),
but runtime routing never reads it — `_get_agent_for_type` uses a hardcoded dict
(`agent_router.py:157-177`), so editing a task type's agent changes nothing. **(b)** MCP cannot
express the team primitives: `sugar_add_task` (`task_server.py:82-91`) and `sugar_update_task`
(`:341-348`) accept no `blocked_by`, `assigned_agent`, or `parent_task_id`, so an external agent can
add and move tasks but cannot declare dependencies or take ownership.

---

## 3. The real gaps for concurrent teams

### G1 — No WAL, no busy_timeout, no retry at any connection site

**Evidence.** 39 `aiosqlite.connect` sites in `sugar/` (22 in `work_queue.py`, 10 in
`task_type_manager.py`, 5 in `issue_response_manager.py`, 2 in `main.py`). None passes `timeout=`, and
the package contains zero `journal_mode`, `busy_timeout`, or `synchronous` pragmas — the only `PRAGMA`
statements anywhere are 10 schema-introspection `PRAGMA table_info` calls. A DB created by
`WorkQueue.initialize()` reports `journal_mode=delete`; so does `.sugar/sugar.db`. The only backpressure
is Python's implicit 5-second `sqlite3` timeout.

I measured the failure mode rather than assuming it: with one connection holding `BEGIN IMMEDIATE` for
7 s, a second writer failed after **5.2 s** with `sqlite3.OperationalError: database is locked`. A light
burst (4 processes × 120 mixed add/update ops) produced zero errors. So the claim is not "concurrency
fails immediately"; it is "no headroom, no WAL, no retry, and the lock error appears as soon as
anything holds a write lock past 5 s" — which three real agents will eventually do. Every MCP client
also spawns its own `sugar mcp tasks` process (`main.py:4724-4763`), so each brings its own
connections; in rollback-journal mode readers and writers block each other, and the dashboard's
long-lived reader polling `PRAGMA data_version` every 400 ms (`local-dashboard.md:204-208`) is exactly
the pattern WAL exists for.

**Minimal fix.** One connection helper used at all 39 sites: `journal_mode=WAL` (persists on the file),
`synchronous=NORMAL`, `busy_timeout=30000`, plus bounded retry in the write methods. No schema change;
~1 mechanical PR.

### G2 — No leases, heartbeats, or worker identity; a dead agent strands a task forever

**Evidence.** No `claimed_by`, `lease_expires_at`, `heartbeat_at`, or `worker_id` in the 33-column
schema (`work_queue.py:38-59,232-369`); grep finds no lease/heartbeat code. `get_next_work` sets
`status='active'` and `started_at` and stops (`:500-511`). The only exits from `active` are
`complete_work` (`:527-583`) and `fail_work` (`:585-651`); no reaper exists, and `get_next_work` only
selects `status='pending'` (`:473-480`), so an orphaned row is invisible to every future claim.

Not hypothetical here: `.sugar/sugar.db` holds one `active` row,
`9a88e031-a2ae-4db1-b329-72f7b18ad4f7` ("Test OpenCode notification integration"), `attempts=1`,
`started_at=2026-01-21 03:40:22` — still active on 2026-11-05. No automated path will ever move it
(only a human `sugar update <id> --status pending`, `main.py:1005-1010`); `get_stats` counts it
(`:741-742`), so every dashboard and `sugar status` reports phantom work. There is also no
single-instance guard: `run_continuous` writes `.sugar/sugar.pid` unconditionally
(`main.py:2143-2150`) and unlinks it unconditionally on exit (`:2165-2166`), so two `sugar run`
processes both run and whichever exits first deletes the other's pidfile.

**Minimal fix, preserving claim atomicity.** Add `claimed_by`, `lease_expires_at`, `heartbeat_at` via
the existing `ALTER TABLE` migration style, set in the *same* UPDATE inside `get_next_work`'s
transaction (`:500-511`) so the claim stays one atomic step. Add `heartbeat_work(id, worker)` and
`reap_expired(now)` (expired `active` → `pending`, attempts kept), called on loop start and
periodically; surface lease age in the dashboard. ~1 day with tests.

*Status 2026-11-05:* `created_by`/`created_by_kind`/`created_by_session`/`claimed_by` and
`lease_expires_at` now exist and the atomic claim records them (`sugar/identity.py`); the
heartbeat and the expiry reaper are still outstanding, so a dead worker still strands its task
until a human releases it.

### G3 — `get_next_work` ignores `blocked_by` (and `assigned_agent`)

**Evidence.** The claim query is `SELECT * FROM work_items WHERE status = 'pending' ORDER BY priority
ASC, created_at ASC LIMIT 1` (`work_queue.py:473-480`). No `blocked_by` predicate, no `assigned_agent`
predicate. Dependency gating exists only in `get_ready_subtasks`, called from orchestration
(`task_orchestrator.py:882`) and `sugar orchestrate` display (`main.py:1262`) — never from the claim
path. So any task persisted `pending` with a non-empty `blocked_by` is claimable before its blockers
finish, and a task assigned to `qa-engineer` can be claimed by any loop.

The orchestrator sidesteps rather than fixes this: subtasks are persisted as `status='hold'`
specifically so `get_next_work` never sees them (`task_orchestrator.py:795-801,844`), and the
orchestrator executes them itself. That workaround holds only while all dependency-graph work
originates inside one orchestration; teams author dependencies from outside.

**Minimal fix.** Filter in the claim query, inside the same `BEGIN IMMEDIATE` transaction:

```sql
... WHERE status = 'pending'
  AND (blocked_by IS NULL OR blocked_by = '[]'
       OR NOT EXISTS (SELECT 1 FROM json_each(work_items.blocked_by) b
                      JOIN work_items bw ON bw.id = b.value
                      WHERE bw.status != 'completed'))
```

plus `AND (assigned_agent IS NULL OR assigned_agent = :worker)` for scoped claims. Also show *why* a
task is unrunnable in the dashboard, and have the reaper handle dangling blocker IDs — otherwise a
typo'd dependency is a task that silently never runs. Half a day plus tests; JSON1 ships in CPython
3.11's bundled SQLite and can be asserted at startup.

### G4 — `update_work` does not bump `updated_at`

**Evidence.** `update_work` builds `UPDATE work_items SET <cols> WHERE id = ?` from the allowlist
(`work_queue.py:844-904`). `updated_at` is *allowed* (`:863`) but never added automatically. The
dashboard contract already works around it by bumping explicitly (`local-dashboard.md:155-157`); CLI
and MCP updates do not. A stale timestamp breaks "sort by recently touched" and undercuts optimistic
concurrency ("has this row changed since I read it?").

**Minimal fix.** If the caller did not supply `updated_at`, append `updated_at = CURRENT_TIMESTAMP`.
One line.

### G5 — No event or audit stream

**Evidence.** The database has three tables (`work_items`, `task_types`, `issue_responses`;
`work_queue.py:38,136`, `issue_response_manager.py:37`). No events/audit table, no append-only log, no
record of *who* changed a row. The dashboard substitutes by polling `PRAGMA data_version` every 400 ms
and diffing rows (`local-dashboard.md:204-208`) — correct for one local human, but O(n) rescans, no
history, and no way for an agent to react to a completion except polling.

**Minimal fix.** Append-only `events` table (`id INTEGER PK AUTOINCREMENT, ts, task_id, type, actor,
payload JSON`) written in the *same transaction* as each transition in
`add/update/complete/fail/hold/release_work`. That ordering is the point: the log stays durable and
ordered with the state it describes. Expose `GET /api/events?since=<id>` and reuse it for SSE, so the
dashboard protocol (`local-dashboard.md:166-208`) becomes incremental. Agent-side triggers ("when X
completes, claim Y") become a cursor read — the prerequisite for real team choreography.

### G6 — The MCP HTTP server binds `0.0.0.0` with no auth

**Evidence.** `ServerConfig.host` defaults to `"0.0.0.0"`, port 8080 (`mcp/server.py:30-31`);
`create_server` repeats it (`:464`); uvicorn binds it (`:456-458`) with an SSE mount at `/mcp`
(`:437,450`). No auth middleware, token, or allowlist — the only "token" in the file is the GitHub
integration's (`:32,474`). The CLI exposes it: `sugar mcp serve --host` defaults to `0.0.0.0`
(`main.py:4631-4636`). The tools behind it are issue-responder actions, including `generate_response`
and `search_codebase` (`server.py:58,76,98,116,139,157`).

The inversion matters: the queue MCP server — the one agents actually need — is **stdio-only**
(`task_server.py:600-612`), so a teammate in another process or machine cannot reach the queue over the
network at all. The only network surface Sugar has is the unscoped one.

**Minimal fix now.** Mirror the dashboard rule: default to `127.0.0.1`, reject non-loopback hosts
(`local-dashboard.md:52-53`). Before team exposure, add a bearer token via `--token`/env. Do not ship a
team-facing HTTP server before that token exists. *(Minor G7: the MCP tools cannot express dependencies
or ownership — see §2 — so any Phase-2 API must add `blocked_by`/`assigned_agent`/`parent_task_id` as
first-class inputs.)*

---

## 4. Recommended architecture: Sugar as the team bus

```
Claude Code ──┐
OpenCode ─────┤  MCP (stdio shim)           ┌──────────────────────────┐
Copilot/Goose ┤───────────────────────────▶ │  sugar bus (one process) │
Aider / DSH ──┘  HTTP claim/heartbeat/SSE   │  · lease-aware claim     │──▶ sugar.db (WAL)
human/dashboard ─── REST + SSE ───────────▶ │  · event log             │
                                            │  · 127.0.0.1 only        │
                                            └──────────────────────────┘
```

- **One process owns the database.** The Phase-0 dashboard server becomes the bus. Agents stop opening
  SQLite themselves and call `claim` / `heartbeat` / `complete|fail`, subscribing to `events`. The stdio
  MCP server becomes a thin forwarding shim, so existing Claude Code / OpenCode / Goose / DSH setups
  keep working unchanged.
- **Agent contract stays minuscule:** three writes (`POST /api/claim?agent=`, `POST
  /api/tasks/{id}/heartbeat`, `POST /api/tasks/{id}/complete|fail`) and one stream (`GET
  /api/events?since=`). Everything else is already designed in `local-dashboard.md:67-208`.
- **Heterogeneity is the point.** The bus never runs the agent; it hands out work and records state, so
  anything that speaks MCP or HTTP participates. That is also the honest position: multi-agent tooling
  is crowded (`sugar-teams-market-research.md:3-7`), "assign work to AI" is table stakes (`:233`), and
  Sugar's defensible assets are local-first execution plus persistent memory (`:188-206`). A bus
  leverages both; another agent framework competes with Cursor, Copilot, and Devin on their turf.
- **Where the line is.** Sugar schedules work and exposes state; it must not become a prompt framework,
  merge authority, or workflow engine. Today's prompt construction (`task_orchestrator.py:1101,1216`)
  and commit/PR automation belong to *Sugar's own loop* — keep them optional and out of the bus
  contract. An agent's prompt and tools are its own business.
- **Why not "just run several stdio MCP servers"?** Each client gets its own `WorkQueue`, its own
  connections, and its own view of a file with no WAL and no leases (G1, G2). Fine for a demo; it fails
  the first time a worker dies.

---

## 5. Phased roadmap

**Phase 0 — visibility (shipping now).** Local dashboard: REST + SSE on `127.0.0.1:7331`,
loopback-only, writes via existing `WorkQueue` methods, plus the macOS client (`local-dashboard.md`,
whole doc). It can also serve a shared Hermes Kanban board (`--board kanban`, see §9), which makes it
the human view over a board two harnesses already share — without Sugar becoming the board. *Effort:* in flight. *Unlocks:* humans see and edit the queue; proves the HTTP API shape and
serializer. *Without it:* the queue is unobservable and untrustworthy.

**Phase 1 — safe concurrency on one machine.** G1 pragmas; G2 leases + heartbeat + reaper; G3 blocking
and agent filters in the claim query; G4 `updated_at` bump. All additive `ALTER TABLE` migrations in the
existing style (`work_queue.py:232-369`), each with a concurrency test. *Effort:* 2–4 days, one or two
PRs. *Unlocks:* multiple workers on one machine that neither duplicate work nor strand it on a crash.
*Without it:* the live stuck-`active` row is the default outcome of every failure.

**Phase 2 — team API.** G5 event log + `since` cursor; G6 loopback default and token; claim/heartbeat as
HTTP verbs; MCP task server forwards and gains `blocked_by`/`assigned_agent`/`parent_task_id`. *Effort:*
~1 week on top of the Phase-0 server. *Unlocks:* heterogeneous agents in separate processes (and
trusted-network machines) sharing one queue without sharing a file; event-driven triggers; the dashboard
becomes a control plane. *Without it:* coordination is limited to one process or to everyone opening
the same file.

**Phase 3 — remote / multi-machine.** Postgres, auth (tokens → SSO), TLS, per-agent permissions; shape
sketched in `sugar-teams-spec-future.md:5-7,20-25`. *Effort:* weeks. *Unlocks:* teams not on one machine;
audit-grade identity. *Without it:* single-machine only. Trigger on demand, not ambition — the prior art
is explicit that the full commercial product is premature (`sugar-teams-spec-future.md:30-31`,
`sugar-teams-market-research.md:225-237`). Migration cost is real: `WorkQueue` is raw `aiosqlite` SQL
despite `sqlalchemy` being a declared dependency (`pyproject.toml:33`), so a Postgres port rewrites the
queue rather than swapping a URL.

---

## 6. Risks and non-goals

- **SQLite write ceiling.** One writer at a time, WAL or not. Fine for a handful of agents on local disk
  doing small writes (observed: 4 concurrent writers, no errors); not fine for tens of agents, network
  filesystems, or long write transactions (observed: `database is locked` after 5.2 s against a 7 s
  hold). Phase 1 buys headroom, not unlimited scale.
- **No workflow engine.** `blocked_by` is a single-level gate with no retry policy, backoff, DAG
  versioning, or team priorities. Teams that need orchestration will bring their own — Sugar's job is
  the durable queue they run on.
- **Human approval is missing, and concurrency raises the stakes.** Sugar's loop already commits and
  opens PRs; quality gates exist but are not wired into orchestration's review stage
  (`docs/task_orchestration.md:801-803`). An approval queue should precede any unsupervised multi-agent
  merge path.
- **No identity without leases.** Until `claimed_by` exists, work cannot be attributed and per-agent
  permissions have no basis — exactly what `local-dashboard.md:298-299` flags as the deferred dependency
  on this document.
- **Market/positioning.** Multi-agent dev tooling is crowded and well-capitalized
  (`sugar-teams-market-research.md:3-7`). The queue is table stakes; that research's own conclusion is
  that memory and local-first execution are the differentiators (`:188-206,233`). The bus framing fits;
  "Sugar as an agent framework" does not.
- **Non-goals.** Not a prompt framework, workflow engine, merge authority, cloud service, or multi-tenant
  product. Not a replacement for an agent runtime — the agents stay Claude Code / OpenCode / Copilot /
  Goose / Aider / DSH.

---

## 7. How this was verified

Read `work_queue.py`, `core/loop.py`, `mcp/task_server.py`, `mcp/server.py`,
`orchestration/task_orchestrator.py`, `orchestration/agent_router.py`, `main.py` (coordination CLI + MCP
groups), `memory/store.py`, `local-dashboard.md`, `task_orchestration.md`, and the two
`.sugar/sugar-teams-*.md` notes. Counted 39 `aiosqlite.connect` sites and zero
`journal_mode`/`busy_timeout`/`synchronous` pragmas. Ran `pytest tests/test_concurrency.py -k
ConcurrentTaskPickup` → 4 passed (2026-11-05). Measured the lock failure on a throwaway DB (second
writer dies after 5.2 s against a 7 s `BEGIN IMMEDIATE` hold; fresh DB reports `journal_mode=delete`).
Inspected `.sugar/sugar.db` read-only: 33 columns, one `active` row with
`started_at=2026-01-21 03:40:22`. Could not verify whether that row was ever genuinely running (no
process introspection available here); the absence of reaper/recovery code is verified by grep.
