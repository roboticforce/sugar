# Mission Control: the dashboard, the app, and the event spine

Status: shipped on `feature/local-dashboard` (2026-11-05), contract in
[local-dashboard.md](local-dashboard.md), multi-agent analysis in
[agent-teams.md](agent-teams.md).

Sugar normally runs headless: a queue, a worker loop, and a memory store. Mission
Control adds the missing layer — a **local-first control plane** where a human can
see what every agent is doing, steer it, and trust the record afterwards.

Two things matter to understand it:

1. **One server, two clients.** `sugar dashboard` serves a REST + SSE API and a
   dependency-free web UI. The native macOS app (`SugarDash`) is a second client
   of the exact same API. Neither client owns state.
2. **The board stays the source of truth.** The dashboard is a *view* and a
   *steering surface*; writes go through the canonical owner (`WorkQueue` for
   Sugar's queue, `hermes kanban` for a Hermes board). It is never a second task
   store.

---

## 1. Why it exists

A fleet of agents fails in three ways that a task queue cannot see:

| Failure | What it looks like | What Mission Control gives you |
|---|---|---|
| Invisibility | "What is it doing right now?" → tail logs, ask the agent | Live task state and activity, sub-second, one screen |
| Double work | Two harnesses pick up the same task | Atomic claims with leases; claim owner visible |
| No provenance | "Who asked for this, and who is doing it?" | `created_by`, `claimed_by`, actor/harness/machine on every event |
| No audit | "What happened yesterday?" | Append-only event spine, queryable by actor/task/project |
| Human bottleneck | Only the agent can move work | The same verbs as the UI: claim, complete, block, edit, reassign |

---

## 2. The three planes

| Plane | Owner | Guarantee | Question it answers |
|---|---|---|---|
| **Coordination** | Hermes Kanban (or Sugar's queue) | atomic claim, dependencies, run history | *Who is doing what?* |
| **Context** | Sugar memory | per-project + global recall | *Why does this task mean the same thing to both agents?* |
| **Observability** | Event spine + dashboard + app | every transition recorded and streamed | *What happened, from where, for what?* |

Sugar's dashboard can serve either board source, so the observability plane works
whether the tasks live in Sugar's own worker queue or in a shared Hermes board.

---

## 3. Architecture

```
                     ┌──────────────────────────────┐
  browser  ◀── HTTP ─┤  sugar dashboard (Starlette)  │─ HTTP/SSE ─▶ SugarDash.app
                     │  127.0.0.1:7331 (loopback)    │
                     └──────┬───────────────┬───────┘
                            │ reads         │ writes
                    ┌───────▼──────┐  ┌─────▼────────────────┐
                    │  SQLite read │  │ canonical write path │
                    │  (query_only)│  │ WorkQueue / hermes   │
                    └───────┬──────┘  └─────┬────────────────┘
                            │               │
              ┌─────────────▼───┐   ┌───────▼──────────────┐
              │ Sugar queue DB  │   │ Hermes Kanban DB     │
              │ .sugar/sugar.db │   │ ~/.hermes/kanban.db  │
              └─────────────┬───┘   └───────┬──────────────┘
                            │ observers     │ observers
                     ┌──────▼───────────────▼──────┐
                     │  event spine (events.db)    │
                     └──────────────┬──────────────┘
                                    │ SSE `activity`, GET /api/activity
                                    ▼
                              dashboard + app
```

### 3.1 Board sources

```bash
sugar dashboard                      # Sugar's work queue (default)
sugar dashboard --board kanban       # a Hermes Kanban board
sugar dashboard --board kanban --kanban-board ops --hermes-bin /path/to/hermes
```

- **Reads** are plain SQL on a `PRAGMA query_only = 1` connection.
- **Writes** never touch the DB directly: Sugar writes go through `WorkQueue`
  methods (so hold/release/complete/fail semantics are identical to the CLI);
  Kanban writes shell out to `hermes kanban` (single write path, no drift).
- **Board location** is resolved exactly like Hermes: `$HERMES_KANBAN_DB`, else the
  current board (`$HERMES_KANBAN_BOARD` → `<root>/kanban/current` → `default`),
  else `<root>/kanban/boards/<slug>/kanban.db`, with `<root>` = `$HERMES_KANBAN_HOME`
  or `~/.hermes`.

### 3.2 Real-time

One long-lived connection polls `PRAGMA data_version` every 400 ms. When any
process commits (CLI, MCP server, `hermes kanban`, another tab), the dashboard
re-reads and diffs rows, then publishes per-id deltas. Clients get:

- `hello` and an authoritative `snapshot` on connect;
- `upsert` / `delete` / `stats` deltas per change;
- `activity` events from the spine;
- a keepalive comment every 15 s.

Writes made through the dashboard force an immediate refresh instead of waiting
for the next poll. Reconnect uses 1 s → 10 s backoff and treats the snapshot as
authoritative, so a dropped connection never leaves stale state.

### 3.3 One UI, many state machines

`GET /api/meta` describes the board: statuses/columns, the facet label, and which
verbs are available.

```json
{
  "board": "kanban",
  "statuses": ["triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done"],
  "facet": { "label": "Project" },
  "capabilities": {
    "create": true, "edit": true, "edit_status": false, "delete": true,
    "claim": true, "complete": true, "block": true, "unblock": true,
    "assign": true, "comment": true
  }
}
```

The page renders counts, chips, badge colours and drawer actions from that
payload, which is why the same HTML serves Sugar's five statuses and Kanban's
eight columns without forking the UI.

---

## 4. Provenance: who, and on whose behalf

Three identities are kept separate:

| Field | Question | Set by |
|---|---|---|
| `created_by` + `created_by_kind` (`human`/`agent`/`system`) + `created_by_session` | who asked for this? | CLI `--actor`, MCP initialize handshake (`clientInfo.name`), dashboard `X-Sugar-Actor` header, Kanban `created_by` |
| `assigned_agent` | who should do it? | task creation/edit |
| `claimed_by` + `lease_expires_at` | who is doing it now? | the atomic claim itself (same transaction) |

Identity resolution (`sugar/identity.py`): explicit argument → `$SUGAR_ACTOR` →
`$SUGAR_AGENT_ID` → `$USER@$host`; `sugar run` claims as `sugar-worker@host`
(`$SUGAR_WORKER_ID` overrides). Legacy rows keep `NULL` — unknown is never guessed.

Claim lifecycle: `get_next_work()` writes `claimed_by` + a 900 s lease inside the
existing `BEGIN IMMEDIATE` transaction; `complete_work`, `fail_work`,
`hold_work` and `release_work` clear both.

> **Provenance is not authorization.** On a single local machine any process can
> claim any label. The moment the queue is reachable remotely, identity must be
> bound to a credential (token → actor), not to a self-declared header.

---

## 5. The event spine

Tasks answer *who is doing what*. The spine answers *what happened, from where,
for what* — and later, with hooks, *why*.

- **Storage:** `~/.sugar/events.db` (override `--events-db` / `$SUGAR_EVENTS_DB`;
  pass `''` to disable), WAL, append-only, 30-day retention
  (`$SUGAR_EVENTS_RETENTION_DAYS`, `0` disables).
- **Observers:** the Kanban source tails the board's own `task_events` by id
  watermark; the Sugar source records snapshot diffs. Both are best-effort — a
  spine failure never breaks the dashboard or a task.
- **Ingest:** `POST /api/events` for harness telemetry (`run.started`,
  `tool.invoked`, `memory.recalled`, `signal.*`) with actor, harness, machine,
  session and `trace_id`.
- **Query:** `GET /api/activity?since_id=&limit=&kind=&actor=&task_id=` pages
  forward; the SSE stream emits `activity` for every append.

Event shape:

```json
{
  "id": 42,
  "ts": "2026-11-05T18:40:12Z",
  "kind": "task.claimed",
  "actor": "claude-code",
  "actor_kind": "agent",
  "harness": "dsh",
  "machine": "steves-macbook-pro-1",
  "session_id": "0.4.2",
  "task_id": "t_aaaa0005",
  "run_id": "12",
  "project": "sugar",
  "trace_id": null,
  "source": "observer:kanban",
  "payload": { "title": "Implement TokenBucket limiter", "status": "running" },
  "ingested_at": "2026-11-05T18:40:12Z"
}
```

What the three questions map to today: **where** = `actor` + `actor_kind` +
`harness` + `machine`; **for what** = `task_id` + `project` + payload title;
**why** = the task's captured intent plus provenance — and once harness hooks land
(P2), the memory recalls and run context that led to it.

---

## 6. Surfaces

### 6.1 Web UI

`http://127.0.0.1:7331` — queue counts, status chips, search, task editor,
hold/release/block, and a **Tasks | Activity** switch with a live activity feed
(kind glyph, actor kind icon, task link, harness/project chips, relative time).
Self-contained, no CDN, no build step; works in any browser.

### 6.2 Native macOS app

`apps/macos/` — SwiftUI `SugarDash`, SwiftPM executable, zero third-party
dependencies.

```bash
cd apps/macos && ./build-app.sh && open dist/SugarDash.app

# distributable disk image (universal, drag to Applications)
./build-dmg.sh --universal      # dist/SugarDash-<version>.dmg
```

The DMG is ad-hoc signed by default; see `apps/macos/README.md` for Developer ID
signing and notarization so recipients do not see a Gatekeeper warning.

Help → Send Feedback opens an explicit feedback form (GitHub issue or private email,
optional diagnostics, nothing sent automatically), recorded locally as a
`feedback.submitted` activity event.

**Live** view: a floor of "agents at work" cards fed by an ephemeral SSE `presence`
heartbeat — actor, task, ticking elapsed/lease countdowns, lease-health dots and a live
activity ticker (no DB writes, no audit noise), falling back to task leases on older servers.

About window with version/build, project links, a no-telemetry privacy statement
and "Copy diagnostics"; Help menu links to docs, issue tracker, privacy, terms and
support.

Window with sidebar filters, task list, detail inspector, add/edit/delete,
hold/release, menu-bar pending count, live SSE updates with reconnect, and an
Activity feed. It talks to the same REST/SSE contract as the browser, so the app
works against either board source.

### 6.3 API (the contract)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | version, db, read-only, uptime, board, write backend |
| GET | `/api/meta` | statuses/columns, facet label, capabilities |
| GET | `/api/tasks` | filters: `status`, `type`, `priority`, `q`, `created_by`, `limit`, `offset`, `order` |
| POST | `/api/tasks` | create (honours `X-Sugar-Actor` / `X-Sugar-Actor-Kind`) |
| GET/PATCH/DELETE | `/api/tasks/{id}` | detail / edit / delete |
| POST | `/api/tasks/{id}/hold`, `/release` | pause / resume (Kanban: block/unblock) |
| POST | `/api/tasks/{id}/claim`, `/complete`, `/comment` | Kanban verbs (Sugar: 400 `unsupported`) |
| GET | `/api/stats` | counts by status/type, 24 h activity |
| GET | `/api/activity` | event spine page (cursor `since_id`) |
| POST | `/api/events` | harness telemetry ingest |
| GET | `/api/events` | SSE: `hello`, `snapshot`, `upsert`, `delete`, `stats`, `activity` |

See [local-dashboard.md](local-dashboard.md) for the frozen wire shapes.

---

## 7. Security and operations

- Binds **loopback only**; non-loopback hosts are refused. No auth in v1 because
  there is no remote surface; bind to the tailnet deliberately and add a token
  before exposing it.
- `--read-only` serves the view and rejects every write with 403.
- The dashboard holds a read connection; it never mutates a board file directly.
- Cross-origin writes are refused (a random web page cannot POST to the loopback
  API), request bodies are capped at 1 MB, and the spine file is `0600`.
- SQLite is WAL + `busy_timeout`; the read connection is `query_only`, so a
  dashboard cannot corrupt or lock out the writer.
- The event spine is on its own database; losing it costs history, not work.

---

## 8. Limitations (honest)

- Single machine: one writer owns each SQLite file; remote agents need the HTTP
  path (Hermes' MCP HTTP transport is the existing option).
- Leases are recorded but not enforced: there is no heartbeat or reaper yet, so an
  expired lease is visible (claimed-by + lease age) but the task is not
  auto-reclaimed.
- "Why" is only as good as captured intent: the payload carries the task title
  and status; memory-recall provenance needs the harness hooks.
- The native app is macOS 14+; the web UI runs anywhere.
- Analytics are not built yet; the spine is the substrate for them.

---

## 9. Getting started

```bash
pip install 'sugarai[dashboard]'        # starlette + uvicorn
sugar dashboard --open                  # Sugar's queue

# or a shared Hermes board (writes go through `hermes kanban`)
sugar dashboard --board kanban

# disable the activity spine, or point it elsewhere
sugar dashboard --board kanban --events-db ''            # no activity feed
sugar dashboard --events-db /tmp/events.db               # custom spine
```

Harness telemetry:

```bash
curl -X POST localhost:7331/api/events -H 'Content-Type: application/json' \
  -d '{"kind":"run.started","actor":"dsh","actor_kind":"agent","harness":"dsh",
       "task_id":"t_123","payload":{"goal":"add rate limiting"}}'
```

Demo dataset (never touches your real `.sugar/`):

```bash
python scripts/demo_dashboard.py --serve --open
```

---

## 10. Roadmap

1. **Lease heartbeat + reaper** — reclaim a task from a dead worker automatically.
2. **Harness hooks** (DSH/Claude/Hermes plugins) — `run.started`,
   `tool.invoked`, `memory.recalled` with `trace_id`, making "why" first-class.
3. **Analytics** — flow graph (harness → project → status), per-actor
   throughput/latency, dependency DAG, expired-lease and double-claim anomalies,
   with rollups over the raw spine.
4. **Gateway/policy** — route task mutations through Sugar to enforce approvals,
   rate limits and spend caps.
5. **Remote teams** — token-bound actors over a tailnet, multi-board.

---

## Related

- [local-dashboard.md](local-dashboard.md) — frozen REST/SSE contract, board
  sources, provenance fields, event-spine schema.
- [agent-teams.md](agent-teams.md) — why Sugar is the substrate, not a framework.
- [task_orchestration.md](task_orchestration.md) — how work is decomposed.
