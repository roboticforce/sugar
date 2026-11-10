# Usage & cost attribution

*Status: implemented on the event spine (`docs/local-dashboard.md` §11). This is
"where is the money going?" for agent work: totals and per-actor/per-harness
breakdowns of **harness-reported** token and cost usage.*

Sugar does not bill or reconcile spend. The harness (Claude Code, DSH, Hermes,
OpenCode, a custom runner, …) knows what a run cost; Sugar records what it
reports and makes it queryable. One event log, one aggregation — the dashboard,
the API and the CLI cannot disagree.

---

## 1. Reporting usage

Usage travels as a normal event on the spine. Post a `usage.reported` event to the
dashboard's event API:

```bash
curl -X POST http://127.0.0.1:7331/api/events \
  -H 'content-type: application/json' \
  -d '{
    "kind": "usage.reported",
    "ts": "2026-11-09T17:30:00Z",
    "actor": "dsh",
    "actor_kind": "agent",
    "harness": "dsh",
    "machine": "steve-mac",
    "session_id": "run-42",
    "task_id": "8f1c…",
    "project": "sugar",
    "payload": {
      "tokens_in": 12400,
      "tokens_out": 3200,
      "cost_usd": 0.0612,
      "model": "claude-sonnet"
    }
  }'
```

A `run.ended` event whose payload carries the same fields is accepted as well, so
runners that already emit a terminal event do not need a second one:

```json
{"kind": "run.ended", "harness": "dsh", "task_id": "8f1c…",
 "payload": {"tokens_in": 12400, "tokens_out": 3200, "cost_usd": 0.0612}}
```

### Payload fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `tokens_in` | int (or int-string) | one of the three numbers | input/prompt tokens |
| `tokens_out` | int (or int-string) | one of the three numbers | output/completion tokens |
| `cost_usd` | number (or numeric string) | one of the three numbers | what the harness says it cost |
| `model` | string | no | recorded in the event payload, not an aggregation dimension yet |

Top-level event fields used for attribution: `actor`, `actor_kind`, `harness`,
`task_id`, `project`, `ts`. The server fills `machine`, `source="ingest"`, `id`
and `ingested_at`.

### Tolerance rules

Telemetry is untrusted input and must never break a surface:

- missing fields are `0` **when at least one** of `tokens_in`/`tokens_out`/
  `cost_usd` is usable — the event then counts;
- an event whose usage fields are all missing, null, non-numeric, negative or
  NaN is **skipped** (it is not counted as a zero-cost event);
- booleans are not numbers (`true` is ignored);
- negative values and NaN/Inf are ignored individually, not fatal (`tokens_in:
  -5` contributes 0 while `tokens_out: 300` still counts);
- int-strings (`"1200"`) and numeric cost strings (`"0.42"`, `"$0.42"`) are
  accepted;
- a payload with none of those fields, or a non-object payload, is skipped.

Cost totals are rounded to 6 decimal places in output; token totals are ints.

---

## 2. Aggregation

`aggregate_usage(events, group_by=…)` is a pure function in
`sugar/dashboard/usage.py` (no DB access). Totals and every group carry:

| Metric | Meaning |
|---|---|
| `events` | usage-bearing events that contributed (skipped garbage is not counted) |
| `tokens_in` / `tokens_out` / `tokens_total` | summed tokens (`total = in + out`) |
| `cost_usd` | summed harness-reported cost |

`group_by` dimensions:

| Dimension | Key | Unknown bucket |
|---|---|---|
| `actor` | event `actor` | `"unknown"` |
| `harness` | event `harness` | `"unknown"` |
| `task` | event `task_id` | `"unknown"` |
| `project` | event `project` | `"unknown"` |
| `day` | first 10 chars of `ts` (`YYYY-MM-DD`, UTC) | `"unknown"` |
| `kind` | event `kind` (`usage.reported` or `run.ended`) | `"unknown"` |

Groups are sorted by `cost_usd` descending, then key ascending — the most
expensive actor/harness is first. Totals-only reports have `groups: []`.

---

## 3. HTTP API

```
GET /api/usage?since=2026-11-01T00:00:00Z&until=2026-11-10T00:00:00Z
              &group_by=harness&limit=5000
```

| Query | Default | Notes |
|---|---|---|
| `since` | none | ISO-8601; inclusive; normalized to UTC `Z` |
| `until` | none | ISO-8601; inclusive |
| `group_by` | none (totals only) | `actor`, `harness`, `task`, `project`, `day`, `kind` |
| `limit` | 5000 | max events scanned, `1..50000` (bounded read) |
| `kinds` | all usage kinds | comma-separated kind filter, e.g. `usage.reported` |
| `actor` / `harness` | none | exact-match filters |

Response (200):

```json
{
  "totals": {"events": 12, "tokens_in": 84000, "tokens_out": 21000,
             "tokens_total": 105000, "cost_usd": 0.7341},
  "groups": [
    {"key": "dsh", "events": 8, "tokens_in": 61000, "tokens_out": 15000,
     "tokens_total": 76000, "cost_usd": 0.5121}
  ],
  "since": "2026-11-01T00:00:00Z",
  "until": "2026-11-10T00:00:00Z",
  "group_by": "harness",
  "events_scanned": 12,
  "truncated": false
}
```

- `since`/`until` echo the normalized bounds (`null` when not given);
  `events_scanned` is the number of events read; `truncated` is `true` when more
  matched than `limit` (the aggregate then covers the first `limit` events in id
  order).
- Unknown `group_by`, bad timestamps, and non-integer/out-of-range `limit` →
  `400 {"error": "validation_error", "detail": "…"}`.
- No event spine configured on the server → `503 {"error": "spine_unavailable"}`.
- With board tokens configured (`docs/board-server.md`), `/api/usage` requires a
  `read`-scoped token/cookie like every other `/api/*` route. The default
  loopback/no-auth mode is unchanged. Usage is read-only, so read-only boards and
  `read` tokens can use it.

---

## 4. CLI

```bash
# Totals for the default event spine (~/.sugar/events.db or $SUGAR_EVENTS_DB)
sugar usage

# A range, broken down by day
sugar usage --since 2026-11-01 --group-by day

# Machine-readable, for scripts
sugar usage --group-by harness --json
```

Flags: `--db PATH`, `--since ISO`, `--until ISO`,
`--group-by actor|harness|task|project|day|kind`, `--limit N` (bounded scan,
default 5000), `--json`.

Example table:

```
           events  tokens_in  tokens_out  tokens  cost_usd
TOTAL           2       1240         840    2080    0.4521
dsh             1       1000         800    1800    0.4000
hermes          1        240          40     280    0.0521

(no group_by; pass --group-by actor|harness|task|project|day|kind)
```

The CLI reads the same events DB as the dashboard (`--db` wins; otherwise
`$SUGAR_EVENTS_DB`, then `~/.sugar/events.db`) and applies the same aggregation.
A missing DB is an empty report (zeros, exit 0). Invalid `--since`/`--until` or
an unknown `--group-by` exits non-zero with a message. The CLI needs the
dashboard extra (`pip install 'sugarai[dashboard]'`) because it lives next to the
event spine; it does not start a server.

---

## 5. Dashboard panel

The Activity view shows a small **Usage** panel: totals (events, tokens in/out/
total, reported cost) plus the top five harnesses and actors by cost. It is
loaded once when the Activity view opens (no SSE stream) and hides itself on
older servers that do not expose `/api/usage`. Task/Activity behaviour is
otherwise untouched.

---

## 6. Bounded reads (and why there are no rollups yet)

The reader filters in SQL (`since`, `until`, `kinds`, `actor`, `harness`,
`since_id`) and reads at most `limit + 1` rows, so a large events DB is never
loaded whole — the aggregate covers the first `limit` matching events in id
order and reports `truncated: true`. For full history, page with
`read_usage_events(..., since_id=last_id, limit=…)` from
`sugar/dashboard/usage.py`; rollup tables are a later optimisation, not needed
for a local team board.

---

## 7. What this is NOT

- **No provider billing reconciliation.** `cost_usd` is self-reported by the
  harness; it can be wrong, missing, or in a different currency unit. Sugar does
  not call provider APIs or compare against invoices.
- **No pricing table.** Sugar does not derive cost from tokens × model prices.
  A harness that only knows tokens reports `tokens_*` and omits `cost_usd`.
- **No budgets, quotas, alerts or chargeback.** This is a visibility primitive,
  not a billing system.
- **No `model` breakdown yet.** `model` rides along in the event payload; adding
  it as a group dimension is a small follow-up (append a dimension in
  `usage.GROUP_BY_DIMENSIONS` + CLI choice).
- **No tamper evidence.** Usage events are as trustworthy as the local event
  log; the signed export/verify workflow belongs to `docs/audit.md`.
- **Retention still applies.** The spine prunes raw events after
  `$SUGAR_EVENTS_RETENTION_DAYS` (default 30, `0` disables) at startup and
  hourly. Export or roll up before pruning if you need longer history.
- **Not multi-tenant.** The spine is per-machine (one `events.db`); aggregating
  across machines requires collecting their exports.
