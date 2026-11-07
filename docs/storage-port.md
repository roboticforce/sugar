# Storage port: `TaskStore`, SQLite today, Postgres when it pays

*Status: implemented for SQLite (this change). Postgres is intentionally not implemented yet — the contract and
conformance suite are in place so it can be added without rewriting the core. Companion docs:
[agent-teams-architecture.md](agent-teams-architecture.md) (Wardley map, boundaries), [adapters.md](adapters.md)
(edge adapters), [local-dashboard.md](local-dashboard.md) (the HTTP/SSE contract).*

---

## 1. Why a port

The coordination core must not depend on SQLite specifics. The valuable invariants — exactly one claimant per
task, leases that expire and requeue, dependency gating, provenance, attempt history — belong to the product, not
to a database driver. The port makes three things true:

- **The invariants are testable against any backend** (a shared conformance suite, not backend-specific tests).
- **A backend swap is mechanical** (Postgres for self-hosted teams, without touching the core or adapters).
- **Backend concerns stay behind the boundary**: transactions, change detection, migrations, timestamp formats.

```
core / CLI / MCP / dashboard / adapters
                │
                ▼
        sugar.storage.ports.TaskStore        ← the contract
                │
        ┌───────┴────────┐
   WorkQueue (SQLite)   PostgresTaskStore (future)
```

## 2. The contract

`sugar/storage/ports.py` defines a `TaskStore` Protocol. Method signatures mirror `WorkQueue` today (an
incremental extraction — no caller has to change), with the invariants documented on the methods:

| Method | Invariant |
|---|---|
| `initialize()` / `close()` | idempotent setup / resource release |
| `add_work(work_item) -> id` | creates a `pending` task; persists provenance (`created_by*`) |
| `get_next_work(worker_id, lease_seconds)` | **atomic claim** of the highest-priority claimable task; sets `claimed_by` + `lease_expires_at`; increments `attempts`; skips tasks that are `hold` or blocked |
| `heartbeat(task_id, worker_id, lease_seconds)` | extends a lease **only** for the current owner; `False` if ownership was lost |
| `requeue_expired_leases(now=None)` | returns expired `active` tasks to `pending` (the reaper); attempts preserved |
| `complete_work` / `fail_work` | state transitions; `fail_work` retries while under `max_retries` |
| `hold_work` / `release_work` | park (`hold`) / return to `pending` |
| `remove_work` / `update_work` | delete / allow-listed field updates |
| `get_work_item` / `get_recent_work` / `get_stats` | reads; `blocked_by` normalized to a list, `context`/`result` to dicts |

Status tokens are Sugar's: `pending`, `active`, `completed`, `failed`, `hold` (not "held").

## 3. Conformance: the only definition of "a correct backend"

`tests/test_task_store_conformance.py` runs the same suite against every backend in `BACKENDS`. Today:
`{"sqlite": WorkQueue}`. To add Postgres: implement the protocol, then append a factory — the suite runs
automatically (CI can start a Postgres service and point the factory at it).

What it proves:

1. protocol conformance (runtime-checkable `isinstance`);
2. create/read round-trip incl. provenance and normalized `blocked_by`;
3. claim records owner + lease and increments attempts;
4. **five concurrent claimers → exactly one winner** (the core safety property);
5. heartbeat extends only the owner's lease, rejects non-owners, keeps a live task out of the reaper;
6. expired leases are requeued exactly once, are claimable again, and attempts accumulate;
7. dependencies gate claims (blocker must exist and be `completed`), blocked tasks do not starve claimable
   work behind them, missing/unreadable blockers fail closed;
8. hold/release round-trip; retries return to `pending`, then permanently `failed` at the limit;
9. stats reflect the state machine.

## 4. SQLite implementation notes (`WorkQueue`)

- **Atomic claim**: `BEGIN IMMEDIATE` serializes writers, then the scan-then-`UPDATE` is one transaction. No
  other writer can claim the row or change its blockers in between.
- **Dependency scan**: because JSON1 availability varies across Python/SQLite builds, `blocked_by` is evaluated
  in Python inside the transaction. Pending rows are scanned in batches (`CLAIM_SCAN_BATCH = 100`, ordered by
  priority/created_at/id) so a long run of blocked rows cannot hide claimable work below them. Worst case is
  O(pending) per claim; a future backend can push this into SQL (`NOT EXISTS … json_each`) without changing the
  contract.
- **Blocked semantics** (matches `get_ready_subtasks`): a task is claimable only if *every* `blocked_by` id
  exists and is `completed`. Missing ids and unreadable JSON fail closed — running prerequisites we cannot
  evaluate is worse than a visibly blocked task.
- **Leases**: stored as UTC `"%Y-%m-%d %H:%M:%S"` (`sugar.identity.LEASE_TIMESTAMP_FORMAT`), so lexicographic
  ordering is chronological. `heartbeat` extends; `requeue_expired_leases(now=…)` is injectable for tests.
- **Attempts**: incremented by the claim (so a task that keeps dying is visible); the reaper does not reset or
  increment them.
- **Reads**: `get_work_item` normalizes `blocked_by` (NULL/`"[]"` → `[]`, valid JSON array → list, unreadable →
  left raw for inspection) and parses `context`/`result`.

## 5. What a Postgres backend must implement

| Concern | SQLite (today) | Postgres (must) |
|---|---|---|
| Claim | `BEGIN IMMEDIATE` + scan/`UPDATE` | `UPDATE … WHERE id = (SELECT id … ORDER BY priority, created_at, id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *` (or a CTE claim) — one statement/transaction |
| Reaper | `UPDATE … WHERE status='active' AND lease_expires_at < ?` under `BEGIN IMMEDIATE` | `UPDATE … WHERE … RETURNING id`; `now()` in UTC |
| Leases | text UTC timestamp | `timestamptz` (normalize to UTC); conformance suite only sees the port values |
| JSON fields | TEXT JSON | `jsonb` (or keep TEXT for parity; JSONB enables indexing) |
| Migrations | ad-hoc `_migrate_*` + `PRAGMA table_info` | per-dialect DDL + a `schema_version` ledger (worth adding for SQLite too) |
| Change detection | dashboard polls `PRAGMA data_version` | consume the **event spine** (or `LISTEN/NOTIFY`); don't leak backend change feeds into surfaces |
| Read-only UI connections | `PRAGMA query_only` | read-only role / `SET TRANSACTION READ ONLY` |
| Isolation | single-writer WAL | MVCC + `lock_timeout` retry policy for the claim |

The conformance suite is the gate: a Postgres implementation that does not pass it is not a valid backend, even
if it "works" for the happy path.

## 6. Non-goals of this change (explicit)

- **No Postgres implementation yet** — waiting for the team/hosted validation signal (see
  [agent-teams-market.md](agent-teams-market.md)).
- **Heartbeat/reaper are not wired into the execution loop yet.** `sugar run` still claims once with a lease;
  a follow-up should call `heartbeat` during long tasks and run `requeue_expired_leases` periodically (or on
  startup), with events emitted for reaped tasks.
- **Event emission is not in the store** — the dashboard's spine/observers capture transitions. When the store
  learns to append events, it must do so in the same transaction as the mutation.
- **Single-process concurrency only.** `BEGIN IMMEDIATE` protects within one process/host; cross-machine claims
  need a central board server (one process) or a backend with row-level locking (Postgres).
- **No schema migration ledger yet** — the existing incremental migrations remain; adding `schema_version` is the
  next storage-hygiene step.

## 7. Next steps

1. Wire the reaper + heartbeat into `sugar run` (and the dashboard's background refresh), emitting
   `task.lease_expired` events.
2. Add `schema_version` + a single migrations path for SQLite.
3. Implement `PostgresTaskStore` behind the port; run the conformance suite in CI with a service container.
4. Point `sugar board` at a store by URL/driver so self-hosted teams can choose the backend without changing
   adapters or surfaces.
