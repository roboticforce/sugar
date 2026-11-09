# Sugar Local Dashboard — Build Spec (frozen contract)

Status: **contract — do not change without telling the Lead.**
Feature overview (for readers, not implementers): [mission-control.md](mission-control.md).
Scope: a local, real-time, read/write view of the Sugar work queue, plus a native
macOS app that talks to it.

Two independent consumers must agree with this document:

- the Python backend (`sugar/dashboard/`, `sugar dashboard` CLI)
- the SwiftUI macOS app (`apps/macos/`)

If the code and this document disagree, the document wins and the code is a bug.

---

## 0. Why

Agents already coordinate through `.sugar/sugar.db` (SQLite `work_items`), via the
CLI and the MCP task server. The missing layer is *observability*: nobody can see
what the queue is doing, and humans cannot edit it without shell commands.

The dashboard is therefore **a client of the same source of truth**, not a second
storage layer:

```
            ┌──────────────┐        ┌──────────────────────┐
 agents ───▶│  sugar.db    │◀──────▶│ sugar dashboard (Py) │◀── REST + SSE ──▶ macOS app
 CLI   ───▶│  work_items  │  poll  │ 127.0.0.1:7331       │◀── REST + SSE ──▶ browser (/)
            └──────────────┘  400ms └──────────────────────┘
```

Rules:

- The database stays the single source of truth. No shadow state, no migrations.
- The server only uses existing `WorkQueue` methods for **writes**, so status
  transitions keep their existing semantics (hold/release context, timing, etc.).
- Reads use plain SQL against `work_items` / `task_types`.
- Everything binds to loopback only. No auth in v1.

---

## 1. CLI

```
sugar dashboard [--host 127.0.0.1] [--port 7331] [--db PATH] [--open] [--read-only]
```

- `--config` is the existing global option (`.sugar/config.yaml`).
- DB path resolution: `--db` wins, else `config["sugar"]["storage"]["database"]`
  (relative paths resolve against **cwd**, as the rest of Sugar does).
- `--open`: open the browser at `/` once the server is listening.
- `--host` must resolve to loopback; anything else exits with a clear error
  ("dashboard is local-only: bind to 127.0.0.1 or ::1"). v1 has no auth.
- `--read-only`: reject all writes with 403 (useful when someone else owns the DB).
- Missing `starlette`/`uvicorn` → exit non-zero with:
  `pip install 'sugarai[dashboard]'`.
- On start, print the URL and the DB path. Ctrl-C → clean shutdown.

New optional extra in `pyproject.toml`:

```toml
dashboard = ["starlette==0.52.1", "uvicorn==0.42.0"]
```

---

## 2. HTTP API

Base URL `http://127.0.0.1:7331`. All JSON is UTF-8, snake_case keys.

### 2.1 Task object

Every endpoint that returns a task returns this shape. `null` when a field is unset.

```json
{
  "id": "9a88e031-a2ae-4db1-b329-72f7b18ad4f7",
  "type": "feature",
  "title": "Add retry to the GitHub client",
  "description": "Long form text, may be empty.",
  "priority": 3,
  "status": "pending",
  "source": "mcp",
  "source_file": null,
  "assigned_agent": null,
  "parent_task_id": null,
  "stage": null,
  "blocked_by": [],
  "context": {},
  "acceptance_criteria": [{"description": "tests pass"}],
  "attempts": 0,
  "created_at": "2026-11-05T16:12:44Z",
  "updated_at": "2026-11-05T16:12:44Z",
  "started_at": null,
  "completed_at": null,
  "last_attempt_at": null,
  "error_message": null,
  "result": {},
  "commit_sha": null,
  "total_execution_time": 0.0,
  "verification_status": null,
  "thinking_summary": null
}
```

Rules:

- `status` ∈ `pending | active | completed | failed | hold`. Unknown values are
  passed through as-is (do not drop rows).
- `priority` is 1 (urgent) … 5 (minimal).
- `context`, `result`, `acceptance_criteria`, `blocked_by` are parsed JSON
  (`blocked_by` defaults `[]`, the others default `{}` / `null`).
- All timestamps are ISO-8601 UTC with `Z`. SQLite stores
  `YYYY-MM-DD HH:MM:SS` (UTC) — convert; never return the raw string.
- `total_execution_time` is seconds (float).

### 2.2 Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/health` | – | `{"ok":true,"version":"3.10.6.dev0","db":"/abs/path/sugar.db","read_only":false,"uptime_s":12.4}` |
| GET | `/api/tasks` | query: `status`,`type`,`priority`,`q`,`limit`(200),`offset`(0),`order`(`created_at_desc`\|`priority`) | `{"tasks":[Task],"count":N}` |
| GET | `/api/tasks/{id}` | – | `{"task":Task}` / 404 `{"error":"not_found"}` |
| POST | `/api/tasks` | `{title*, type?, description?, priority?, status?, assigned_agent?, acceptance_criteria?, blocked_by?}` | 201 `{"task":Task}` |
| PATCH | `/api/tasks/{id}` | any of `title, description, type, priority, assigned_agent, acceptance_criteria, context, status` | `{"task":Task}` |
| DELETE | `/api/tasks/{id}` | – | `{"deleted":true,"id":"…"}` |
| POST | `/api/tasks/{id}/hold` | `{reason?}` | `{"task":Task}` |
| POST | `/api/tasks/{id}/release` | – | `{"task":Task}` |
| GET | `/api/stats` | – | `{"total":N,"by_status":{"pending":N,…},"by_type":{"feature":N,…},"recent_24h":N}` |
| GET | `/api/task-types` | – | `{"types":[{"id":"feature","name":"Feature","description":"…"}]}` |
| GET | `/api/events` | `Accept: text/event-stream` | SSE stream |
| GET | `/` | – | embedded HTML dashboard |

Validation / errors:

- 400 `{"error":"validation_error","detail":"…"}` for bad priority, empty title,
  unknown status transition, bad type (must be an id from `/api/task-types`).
- 404 `{"error":"not_found"}`.
- 403 `{"error":"read_only"}` when `--read-only` and the request is a write.
- 500 `{"error":"internal","detail":"…"}` (never leak a traceback to the client).

### 2.3 Write semantics (must match existing CLI behaviour)

Writes go through `WorkQueue`, not raw SQL:

- `POST /api/tasks` → `queue.add_work({...})`; `source="dashboard"`.
  `created_at`/`updated_at` come from the DB defaults.
- `PATCH`:
  - `status: "completed"` → `queue.complete_work(id, result or {})`.
  - `status: "failed"` → `queue.fail_work(id, error_message or "failed via dashboard")`.
  - `status: "hold"` → `queue.hold_work(id, reason)`.
  - `status: "pending"` on a held task → `queue.release_work(id)`.
  - `status: "active"` → `queue.update_work(id, {"status": "active", "updated_at": …})`
    (manual takeover; no lease in v1).
  - everything else → `queue.update_work(id, updates)` **with** an explicit
    `updated_at` bump (SQLite `CURRENT_TIMESTAMP` format, UTC), because
    `update_work` does not bump it.
- `DELETE` → `queue.remove_work(id)`.
- `POST …/hold` → `queue.hold_work(id, reason)`; `…/release` → `queue.release_work(id)`
  (which only works from `hold`; return 400 otherwise).
- After every write the server force-refreshes the snapshot so events go out
  immediately instead of waiting for the next poll.

---

## 3. SSE protocol

`GET /api/events`, `Content-Type: text/event-stream`.

On connect:

```
event: hello
data: {"version":"3.10.6.dev0","server":"sugar-dashboard"}

event: snapshot
data: {"tasks":[…],"stats":{…}}

```

Then deltas, one event per change:

```
event: upsert
data: {"task":{…}}

event: delete
data: {"id":"…"}

event: stats
data: {…}            // same shape as GET /api/stats
```

Then an ephemeral liveness heartbeat (not a state delta):

```
id: 1
event: presence
data: {"ts":"2026-11-09T20:00:00Z","agents":[…],"counts":{…},"uptime_s":12.3}
```

Rules:

- Emit `stats` after any batch that changes a task's status (or any change at all
  is acceptable — clients are idempotent).
- A `: keepalive\n\n` comment every 15 s.
- Multiple browsers/apps may subscribe; slow clients must not block the server
  (per-client queue, drop oldest).
- Client reconnect policy (both clients): reconnect with backoff 1 s → 10 s, and
  treat the new `snapshot` as authoritative (replace local state).
- `presence` is emitted every `presence_interval` seconds (default 2.0 via
  `SUGAR_PRESENCE_INTERVAL`, injectable with `create_app(...,
  presence_interval=...)`) **only while at least one SSE client is connected**.
  It is derived from the in-memory snapshot (no DB query per tick), is **never
  written to the event spine** and never appears in `/api/activity`/exports. It
  is a liveness hint for ticking elapsed/lease timers, not a state delta: the
  `snapshot`/`upsert`/`delete` messages remain the source of truth.
- Each presence frame carries an SSE `id:` sequence number (1, 2, 3, … while the
  server runs). It is not a task id and has no replay semantics; a reconnect
  always starts with `hello`/`snapshot`.
- Presence payload: `ts` (ISO-8601 UTC `Z`), `agents` (tasks whose status is
  `active`, sorted by `lease_expires_at` ascending with null leases last, fields
  `actor` = `claimed_by` else `created_by`, `task_id`, `title`, `status`,
  `lease_expires_at`, `started_at`, `attempts`), `counts` (the last stats
  `by_status` map plus `total`), and `uptime_s` (seconds since the state
  started, `0.0` before start).

Change detection: one long-lived `aiosqlite` connection polls
`PRAGMA data_version` every 400 ms. When it changes (another process committed)
or after a local write, re-read all rows, diff against the in-memory snapshot,
and emit per-id `upsert`/`delete`. This catches writes from `sugar add`, the MCP
server, `sugar run`, and the CLI — not just dashboard edits.

---

## 4. Browser fallback (`/`)

One self-contained HTML page, **no CDN, no build step, no external fonts**:

- Header: queue counts (pending / active / hold / completed / failed), connection
  dot (live / reconnecting), DB path.
- Filter chips by status + type filter + text search (client-side is fine).
- Table: priority, status badge, type, title, attempts, age/updated. Click row →
  detail drawer with description, context, acceptance criteria, result/error.
- Buttons: **New task**, **Edit**, **Hold**, **Release**, **Delete** (confirm).
- New/Edit form: title, description, type (select from `/api/task-types`),
  priority (1–5), status (edit only!), assigned agent.
- Live via `EventSource('/api/events')`; re-render on snapshot/deltas without
  losing scroll or the open drawer.
- Dark theme, system font stack. Works when the macOS app is not installed.

---

## 5. Native macOS app (`apps/macos/`)

SwiftPM executable, no Xcode project required.

```
apps/macos/
├── Package.swift                  # swift-tools-version 6.0, platforms [.macOS(.v14)]
├── Sources/SugarDash/
│   ├── SugarDashApp.swift          # @main, WindowGroup (+ MenuBarExtra w/ pending count)
│   ├── Models.swift                # Task, Stats, TaskType, Health (Codable)
│   ├── APIClient.swift             # REST + SSE via URLSession (async/await)
│   ├── AppModel.swift              # @MainActor @Observable store: tasks, stats, connection
│   ├── ContentView.swift           # NavigationSplitView: sidebar filters, list, detail
│   ├── TaskDetailView.swift        # read-only detail + actions
│   └── TaskEditorView.swift        # add/edit sheet
├── build-app.sh                    # swift build -c release + assemble SugarDash.app
└── README.md
```

Requirements:

- `swift build -c release` passes on Swift 6.4 / macOS 26 (use
  `swiftLanguageMode(.v5)` to avoid strict-concurrency churn).
- `./build-app.sh` produces `apps/macos/dist/SugarDash.app` (Info.plist with
  `CFBundleIdentifier=io.roboticforce.sugardash`, `LSMinimumSystemVersion=14.0`,
  `NSHighResolutionCapable`), ad-hoc signed (`codesign -s -`) and launchable with
  `open dist/SugarDash.app`.
- Server URL configurable in the app (default `http://127.0.0.1:7331`,
  persisted in `UserDefaults`); a "Reconnect" action.
- Live updates over SSE; a connection state indicator; no polling loop.
- Full CRUD: create, edit, hold/release, delete with confirmation.
- Menu bar extra: pending count + "Open Dashboard"; clicking a task focuses the window.
- No third-party dependencies.

---

## 6. Verification (acceptance)

Backend:

```bash
# targeted (pytest.ini has --cov-fail-under=80; disable cov for focused runs)
pytest tests/test_dashboard.py -p no:cacheprovider --no-cov -q

# smoke against a throwaway project, real HTTP
python scripts/demo_dashboard.py --serve          # seeds /tmp DB + serves on :7331
curl -s localhost:7331/api/health | jq
curl -s localhost:7331/api/tasks | jq '.count'
curl -sN localhost:7331/api/events | head -20     # see hello + snapshot
curl -s -X POST localhost:7331/api/tasks -H 'Content-Type: application/json' \
     -d '{"title":"dashboard smoke test","type":"chore"}'
```

macOS app:

```bash
cd apps/macos && swift build -c release && ./build-app.sh
open dist/SugarDash.app   # window shows seeded tasks; edits appear in /api/tasks
```

Regression: `pytest tests/test_dashboard.py --no-cov -q` plus
`python -c "import sugar.main"` and `sugar --help | grep dashboard`.

---

## 7. Out of scope for v1 (write it down, don't build it)

> **Remote/team mode (v5, opt-in).** The default remains loopback-only and
> unauthenticated. `sugar dashboard --bind ADDR --allow-remote --token-file PATH`
> exposes the same board to a team with scoped bearer tokens and a browser
> session cookie; TLS is expected from a reverse proxy. See
> [`board-server.md`](./board-server.md). The Origin guard, 1 MB body cap and
> response headers apply in both modes.

- Auth, remote access, TLS, multi-user permissions (for the default server;
  remote mode is documented separately in `board-server.md`).
- Leases/heartbeats, agent identity, task claiming UI (the queue has no lease
  columns yet — see `docs/agent-teams.md`).
- Event persistence / audit log (SSE is in-process; a reconnect gets a fresh
  snapshot, which is enough).
- Charts, cost tracking, memory browsing, log tailing.

---

## 8. Implementation notes (v1, shipped)

Small deviations from the contract above, all deliberate:

- **`/api/task-types` is a union**: distinct `task_types` rows plus every `type`
  observed in `work_items`. `WorkQueue`'s legacy bootstrap seeds only five types
  while `TaskTypeManager` seeds ten, and older/demo databases contain others
  (`chore`, `style`, `perf`, …). Validating against the union keeps existing rows
  editable; the contract invariant ("type must be an id from /api/task-types")
  still holds.
- **`acceptance_criteria` is `null` when unset** (not `[]`). `blocked_by` is `[]`.
- **`PATCH status:"failed"` keeps Sugar's retry semantics.** It calls
  `WorkQueue.fail_work`, so a task under `max_retries` goes back to `pending` with
  `error_message` recorded and only becomes terminal `failed` after retries are
  exhausted. A human "fail permanently" action would need `max_retries=0`; it is
  not part of v1.
- **`count`** in `GET /api/tasks` is the total matching rows before
  `limit`/`offset`; `limit` is capped at 1000.
- **`--bind`** defaults to `127.0.0.1`; non-loopback binds require
  `--allow-remote` plus a token source (see `board-server.md`). `--host` is kept
  as a deprecated alias. With no tokens configured the server is still
  loopback-only and unauthenticated.
- **Version** in `/api/health` and `hello` comes from `sugar.__version__`, so it
  follows the installed package metadata (`sugar --version` shows the same value).
- **macOS app**: hold sends the fixed reason `"Held from the SugarDash app"`;
  clearing the assigned agent sends `""`, which the server maps to `NULL`
  (equivalent to "unassigned").
- Shutdown is graceful: SIGINT stops the watcher, closes the SSE streams and the
  database connection, and exits 0.

Verified end-to-end on 2026-11-05: 33 backend tests, a real uvicorn smoke run
(`hello`/`snapshot`/`upsert`/`delete`, cross-process writes visible over SSE in
under a second), a headless-browser screenshot of `/`, and the signed
`SugarDash.app` holding an established SSE connection to the server
(`netstat`/`lsof`), with live data from the seeded demo queue.

---

## 9. Board source v2: `--board kanban` (Hermes Kanban)

Purpose: point the same dashboard at a *shared* board instead of Sugar's queue,
so Hermes and DeepSeek Harness (and any other MCP client) have one visible truth.

```
sugar dashboard --board kanban [--db ~/.hermes/kanban.db] [--kanban-board NAME] [--hermes-bin PATH]
```

- Default DB for `--board kanban`: `$HERMES_KANBAN_DB`, else `~/.hermes/kanban.db`.
  No `.sugar/config.yaml` is required in this mode — it runs from any directory.
- **Reads go straight to SQLite** (read-only connection, `PRAGMA query_only=1`),
  with the same `PRAGMA data_version` polling → SSE pipeline as the Sugar source.
- **Writes never touch the DB.** They shell out to `hermes kanban …`, the same
  canonical write path Hermes and the MCP shim use. Binary resolution:
  `--hermes-bin`, `$HERMES_BIN`, `PATH`, `~/.hermes/hermes-agent/.hermes/bin/hermes`.
- Loopback-only, read-only mode, SSE protocol, and the task JSON shape are
  unchanged.

### 9.1 `GET /api/meta`

```json
{
  "board": "kanban",
  "title": "Hermes Kanban",
  "db": "/Users/steve/.hermes/kanban.db",
  "statuses": ["todo", "triage", "ready", "running", "review", "blocked", "done", "scheduled"],
  "facet": {"label": "Project"},
  "capabilities": {
    "create": true, "edit": true, "edit_status": false, "delete": true,
    "claim": true, "complete": true, "block": true, "unblock": true,
    "assign": true, "comment": true
  }
}
```

Sugar source returns `board:"sugar"`, its five statuses, `facet.label:"Type"`,
and `capabilities` `create/edit/edit_status/delete/hold/release: true` with
`claim/complete/block/unblock/assign/comment: false`.

The browser UI renders counts, chips, badge colours and drawer actions **from
`/api/meta`**, so one page serves both boards.

### 9.2 Mapping (kanban row → task dict)

| Task field | Kanban source |
|---|---|
| `id`, `title`, `description` | `id`, `title`, `body` |
| `status` | true kanban status (`todo/triage/ready/running/review/blocked/done/scheduled`); `archived` is excluded from listings |
| `priority` | `priority` (1–5) |
| `type` | `project_id`, or `"task"` when unset |
| `assigned_agent` | `assignee` |
| `source` | `created_by` |
| `blocked_by` | parent ids from `task_links` |
| `created_at`/`started_at`/`completed_at` | same columns, unix → ISO-8601 `Z` |
| `updated_at` | max(`started_at`, `completed_at`, `last_heartbeat_at`, `created_at`) |
| `attempts` | number of `task_runs` rows |
| `error_message` | `last_failure_error` |
| `result` | `{"text": <result>}` when set, else `{}` |
| `context` | `claim_lock`, `claim_expires`, `block_kind`, `block_recurrences`, `workspace_kind`, `workspace_path`, `branch_name`, `project_id`, `tenant`, `current_run_id`, `consecutive_failures` |
| `context.activity` | detail only: `comments[]`, `runs[]`, `events[]` (most recent first, capped) |

### 9.3 Extra endpoints (registered for both sources)

| Method | Path | Body | Kanban command |
|---|---|---|---|
| POST | `/api/tasks/{id}/claim` | `{ttl?}` | `kanban claim <id> [--ttl N]` |
| POST | `/api/tasks/{id}/complete` | `{result?, summary?}` | `kanban complete <id> …` |
| POST | `/api/tasks/{id}/comment` | `{text, author?}` | `kanban comment <id> <text> --author …` |

Sugar answers these with 400 `{"error":"unsupported"}`; the UI hides them via
capabilities.

Mapping of the existing verbs for kanban:

- `POST …/hold` → `kanban block <id> <reason> [--kind K]`
- `POST …/release` → `kanban unblock <id>`
- `PATCH` `title`/`description`/`priority` → `kanban edit <id> …`
- `PATCH` `assigned_agent` → `kanban assign <id> <profile>` (empty string → `none`)
- `PATCH` `type`/`status` → 400 (transitions are commands, not field edits)
- `DELETE` → `kanban delete <id>`

A failing `hermes` call returns 502 `{"error":"board_error","detail":"<stderr>"}`
so the UI can show the actual CLI complaint.

### 9.4 Implementation notes

- The five legacy verbs keep working on the kanban board: `hold` → `block`,
  `release` → `unblock`, `delete` → `archive` (reversible; there is no purge in
  v1), `PATCH` → `edit` + `assign`. `edit_status` is false because Kanban
  transitions are commands (`claim`, `complete`, `block`, `promote`,
  `request-review`, …), not field writes.
- `archived` is never a column: listings exclude it, but `?status=archived`
  still returns archived rows so nothing is lost.
- Clearing the description sends `--body ""` (Hermes treats a missing `--body`
  as "unchanged").
- The board's `kanban.db` is located exactly like Hermes does: `$HERMES_KANBAN_DB`,
  else the current board (`$HERMES_KANBAN_BOARD` → `<root>/kanban/current` →
  `default`), else `<root>/kanban/boards/<slug>/kanban.db`, with
  `<root> = $HERMES_KANBAN_HOME` or `~/.hermes`.
- Write failures surface the CLI's stderr as `502 board_error` — e.g. a
  sandboxed shell that cannot write `~/.hermes/installs/.../*.lock` shows
  `hermes: install state is not writable by this user`.
- Because `hermes` may not be runnable from every shell, the CLI prints the
  resolved binary at startup and `--hermes-bin` overrides it.
- Write-path tests use a stub `hermes` executable; read-path tests use a
  synthetic board schema. The real `~/.hermes/kanban.db` is only ever opened
  with `query_only=1`.

---

## 10. Actor provenance (v3)

Sugar records **provenance, not authorization**: on a local machine any
process can claim any label. Identity only becomes a boundary when the queue is
reachable remotely, and then it must be bound to a credential.

Three identities stay separate: `created_by` (who asked), `assigned_agent` (who
should do it), `claimed_by` (who is doing it now, with a lease).

### 10.1 Task JSON additions

| Field | Type |
|---|---|
| `created_by` | string or null |
| `created_by_kind` | `"human"` \| `"agent"` \| `"system"` \| null |
| `created_by_session` | string or null (client version / session for traceback) |
| `claimed_by` | string or null |
| `lease_expires_at` | ISO-8601 `Z` or null |

Legacy rows are `null` (unknown is truthful; never backfilled with a guess).

### 10.2 Sugar work queue

- Columns added by `WorkQueue._migrate_identity_columns`.
- `get_next_work(worker_id=None, lease_seconds=900)` records `claimed_by` and
  `lease_expires_at` in the same `BEGIN IMMEDIATE` transaction as the claim.
  `worker_id` defaults to `sugar.identity.resolve_actor()`.
- `complete_work` / `fail_work` / `hold_work` / `release_work` clear both.
- `sugar add` accepts `--actor` / `--actor-kind`; environment precedence is
  `$SUGAR_ACTOR` → `$SUGAR_AGENT_ID` → `$USER@$host`.
- `sugar run` claims with `$SUGAR_WORKER_ID` (default `sugar-worker@host`).

### 10.3 MCP task server

Actor comes from the initialize handshake:
`ctx.request_context.session.client_params.clientInfo.name` (kind `agent`,
session = `clientInfo.version`), falling back to `"mcp-client"`. No flag needed.

### 10.4 HTTP API

- `POST /api/tasks` accepts `created_by` / `created_by_kind`; the
  `X-Sugar-Actor` and `X-Sugar-Actor-Kind` headers win over the body, and both
  default to `resolve_actor()` (a human `${USER}@${host}`).
- `GET /api/tasks?created_by=<id>` filters by creator.
- Kanban source: `created_by` ← `tasks.created_by` (kind null),
  `claimed_by` ← `claim_lock`, `lease_expires_at` ← `claim_expires`; create sends
  `--created-by <actor>`.

### 10.5 Clients

Browser UI and `SugarDash.app` show "Created by" with a human/agent/system icon
and "Claimed by" when a lease is held. Read-only views still show them.

### 10.6 Implementation notes

- `created_by*` is immutable provenance: `PATCH` rejects it as an unknown field.
  `created_by_session` is not a POST body key — it comes from `$SUGAR_SESSION`
  (or the MCP client version).
- Header/body precedence is per field: a valid header wins, a blank one falls
  through to the body, then to the default actor; an invalid kind is a 400 only
  when it is the value actually used.
- MCP falls back to `mcp-client` (kind `agent`) when the handshake is
  unavailable, rather than to the local human identity.
- Kanban has no kind/session columns, so those stay `null` there; `claim_lock`
  and `claim_expires` are surfaced as `claimed_by` / `lease_expires_at`.
- The lease is recorded but not yet enforced: there is no heartbeat or reaper,
  so an expired lease is visible (dashboard/app) but the task is not
  automatically reclaimed.

---

## 11. Event spine (v4) — the "central nervous system" slice

The board answers *who is doing what*; the spine answers *what happened, from where,
for what and why*. It is deliberately **not** a second task store: tasks stay on the
board (or Sugar's own worker queue), the spine only records transitions and activity.

### 11.1 Storage

Default `~/.sugar/events.db` (override `--events-db PATH` / `$SUGAR_EVENTS_DB`), created
on startup. Additive only; no migration of existing data.

```sql
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,                -- ISO-8601 Z (when it happened)
  kind TEXT NOT NULL,              -- see 11.2
  actor TEXT,                      -- who caused it
  actor_kind TEXT,                 -- human|agent|system
  harness TEXT,                    -- claude-code|dsh|hermes|opencode|sugar|...
  machine TEXT,                    -- short hostname
  session_id TEXT,
  task_id TEXT,
  run_id TEXT,
  project TEXT,
  trace_id TEXT,                   -- stitches request -> task -> completion
  source TEXT NOT NULL,            -- observer:kanban|observer:sugar|ingest
  payload TEXT,                    -- JSON object, small
  ingested_at TEXT NOT NULL
);
-- indexes on ts, kind, actor, task_id
```

Retention: raw events are pruned after 30 days (`SUGAR_EVENTS_RETENTION_DAYS`,
`0` disables) at startup and hourly. Rollups come later.

### 11.2 Kinds (observed + ingested)

`task.created`, `task.updated`, `task.claimed`, `task.held`, `task.released`,
`task.blocked`, `task.unblocked`, `task.assigned`, `task.commented`,
`task.completed`, `task.failed`, `task.archived`, `task.lease_expired` (lease
reaper); plus free-form ingest kinds
(`run.started`, `run.ended`, `tool.invoked`, `memory.recalled`, `signal.*`).

### 11.3 HTTP

- `GET /api/activity?since_id=&limit=100&kind=&actor=&task_id=` →
  `{"events": [Event], "last_id": N}` (newest last, so a client can page forward).
- `GET /api/usage?since=&until=&group_by=actor|harness|task|project|day|kind&limit=` —
  aggregates harness-reported usage (`usage.reported` / `run.ended` payloads) from
  the spine: totals and grouped token/cost rows (see `docs/usage.md`).
- `POST /api/events` — harness telemetry. Body: `{kind*, actor?, actor_kind?,
  harness?, session_id?, task_id?, run_id?, project?, trace_id?, payload?}`;
  server fills `ts`, `machine`, `source="ingest"`, `id`, `ingested_at`. Unknown
  `actor_kind` → 400; unknown fields ignored.
- SSE `/api/events` gains `event: activity`, `data: <Event>` for every appended
  event (observers and ingest alike). No keepalive change.

### 11.4 Observers

- **Kanban:** tail `task_events` by ascending `id` with a watermark; map
  `created/claimed/commented/completed/blocked/unblocked/assigned/archived` to the
  kinds above; actor from the event payload (`assignee`/`lock`/`author`) or the task's
  `created_by` for `created`; `harness` from `created_by`; `project` from `project_id`.
- **Sugar queue:** snapshot diffs (the dashboard already computes them) emit
  `task.created` / `task.updated` / `task.claimed` / `task.completed` / `task.failed` /
  `task.held` / `task.released` with the queue's own `created_by`/`claimed_by`.
  The lease reaper emits an explicit `task.lease_expired` event instead of a
  generic diff (see `docs/storage-port.md`).

Observers are best-effort: a spine failure must never break the dashboard or a task.

### 11.5 Presence heartbeat (ephemeral — not an event)

`presence` is deliberately **not** an event-spine kind. It is derived from the
in-memory board snapshot and sent on `/api/events` (see §3) every
`SUGAR_PRESENCE_INTERVAL` seconds (default 2.0) while clients are connected:

- no spine append, no `/api/activity` entry, no audit-export content — presence
  must never add audit noise or grow the ledger;
- only emitted when `EventBroadcaster.client_count > 0`, so idle servers do no
  work;
- payload is the frozen presence shape from §3 (`ts`, `agents`, `counts`,
  `uptime_s`), cheap to build and safe to drop (the next tick supersedes it);
- timestamps are normalized to ISO-8601 UTC `Z` from the task snapshot
  (`lease_expires_at`/`started_at` may be `null`).
