# Spend attribution

*Status: implemented on the event spine (`docs/local-dashboard.md` §11) and the
dashboard. This page covers **where agent spend comes from**: task
project/cwd provenance, harness-reported usage, grouping by model/project/task,
and optional cost estimates from an operator-supplied price table.*

Related: [`usage.md`](usage.md) for the raw reported-usage contract,
[`telemetry-adapters.md`](telemetry-adapters.md) for how harnesses report usage,
[`traces.md`](traces.md) for per-session token and cost context, and
[`audit-package.md`](audit-package.md) for exporting usage events as evidence.

Sugar does not bill, invoice or reconcile provider charges. It records what a
harness reports and, only if the operator supplies prices, adds a clearly
labelled **estimate** from token counts. Reported and estimated costs are always
separate fields.

---

## 1. Task project/cwd provenance

Tasks carry the workspace context they were created in. The CLI and the browser
dashboard write it into the task context at creation time:

```json
{
  "context": {
    "added_via": "sugar_cli",
    "cwd": "/Users/me/Dev/my-app",
    "project": "my-app"
  }
}
```

- `context.cwd` is the absolute working directory at creation;
  `context.project` is its basename.
- The fields are ordinary task context (JSON) and are returned by
  `GET /api/tasks` / `GET /api/tasks/{id}`; the dashboard shows them in task
  detail and uses `context.project` for its project filter.
- **They are recorded provenance, not derived state.** Tasks created by other
  clients (or by older Sugar versions) may omit them; an imported/edited task can
  contain any values. Sugar does not resolve the path, verify it, or backfill
  existing tasks.
- The task `context.project` and a usage event's top-level `project` are
  **different fields**. Usage grouping by `project` uses the event's `project`
  (normally set by the harness/adapter from its working directory); task views
  use `context.project`. They usually agree when both are created in the same
  workspace, but Sugar does not join them implicitly. Use the task id
  (`events.task_id` → `group_by=task`) for exact per-task attribution.

---

## 2. Reported usage

Usage is an ordinary event. A harness posts `usage.reported` (or a `run.ended`
carrying the same payload fields) to `POST /api/events`:

```bash
curl -X POST http://127.0.0.1:7331/api/events \
  -H 'content-type: application/json' \
  -d '{
    "kind": "usage.reported",
    "ts": "2026-11-09T17:30:00Z",
    "actor": "dsh",
    "actor_kind": "agent",
    "harness": "dsh",
    "task_id": "8f1c…",
    "project": "sugar",
    "payload": {
      "tokens_in": 12400,
      "tokens_out": 3200,
      "cost_usd": 0.0612,
      "model": "deepseek-flash"
    }
  }'
```

| Payload field | Meaning |
|---|---|
| `tokens_in` / `tokens_out` | Prompt/completion tokens the harness counted |
| `cost_usd` | Cost **as reported by the harness** (optional) |
| `model` | Model identifier used for grouping and optional estimation |

Attribution dimensions come from the event envelope: `actor`, `actor_kind`,
`harness`, `task_id`, `project`, `run_id`, `session_id`, `ts`. A payload with no
usable number (`tokens_in`, `tokens_out`, `cost_usd` all missing/invalid) is not
a usage event and is skipped, not counted as zero. Int-strings (`"1200"`),
numeric cost strings (`"$0.42"`), and canonical nested payloads are tolerated;
negatives/NaN/booleans are ignored field-by-field. See
[`usage.md`](usage.md) §1 for the full tolerance rules.

Harness notes:

- **DSH** emits one `usage.reported` per assistant response from
  `assistant/message.usage`. Its `tokens_in` intentionally includes cached prompt
  tokens (cache read/write) so `tokens_in + tokens_out` equals the provider's
  `totalTokens`; the fresh-input slice is preserved as `input_tokens` in the
  event summary. See [`telemetry-adapters.md`](telemetry-adapters.md).
- Other adapters report whatever their hooks/OTLP spans carry. Missing usage is
  missing, not zero.

---

## 3. Aggregation dimensions

`GET /api/usage` and `sugar usage` share one aggregation (no second logic path).
Both totals and every group carry:

| Metric | Meaning |
|---|---|
| `events` | Usage-bearing events that contributed |
| `tokens_in` / `tokens_out` / `tokens_total` | Summed tokens (`total = in + out`) |
| `cost_usd` | Summed harness-**reported** cost |
| `estimated_cost_usd` | Summed estimate from the operator price table (0 when none) |
| `cost_basis` | `reported`, `estimated`, `mixed` or `unavailable` |

Supported `group_by` dimensions: `actor`, `harness`, `task`, `project`,
**`model`**, `day` (UTC `YYYY-MM-DD`), `kind`. Missing values land in an
`unknown` bucket. Groups sort by reported cost descending, then estimated cost,
then key, so the most expensive actor/model is first.

`model` is read from `payload.model` (flat hook payload or canonical nested
payload), falling back to an `event.model` field if a producer sets one.

---

## 4. Optional price estimates

Sugar ships **no vendor prices** and never guesses. If — and only if — the
operator supplies a price table, the aggregator derives an estimate from token
counts. The table is JSON, USD **per million tokens**:

```json
{
  "deepseek-flash": {"input_per_mtok": 0.14, "output_per_mtok": 0.28},
  "claude-sonnet": {"input_per_mtok": 3.0, "output_per_mtok": 15.0}
}
```

Resolution order:

1. `$SUGAR_MODEL_PRICES` — explicit path to a JSON file;
2. otherwise `~/.sugar/model-prices.json`.

Example:

```bash
cat > ~/.sugar/model-prices.json <<'JSON'
{
  "deepseek-flash": {"input_per_mtok": 0.14, "output_per_mtok": 0.28}
}
JSON
sugar usage --group-by model
```

Semantics:

- **Reported cost is never overwritten.** `cost_usd` always contains only
  harness-reported values; `estimated_cost_usd` is additive.
- The estimate for one event is
  `(tokens_in / 1_000_000) * input_per_mtok + (tokens_out / 1_000_000) * output_per_mtok`,
  rounded to 6 decimal places at output.
- Model keys must match the reported model string exactly (trimmed). A missing
  model or missing table entry contributes **no estimate** rather than zero.
- Malformed JSON, non-object entries, negative/NaN rates and non-numeric rates
  are ignored defensively; the dashboard/API/CLI never fail because of a bad
  price table.
- `cost_basis` tells a client what it is looking at per total/group:
  `reported` (all contributing events had `cost_usd`), `estimated` (estimates
  only), `mixed` (both), `unavailable` (neither). Estimates are **not** invoices
  or provider reconciliation.
- Prices are per million tokens and in USD; Sugar does not convert currencies or
  account for cache-discount pricing separately. For DSH, `tokens_in` includes
  cache tokens (see above), so an estimate with a fresh-input rate is
  conservative and may differ from the provider's bill.

---

## 5. CLI

```bash
# Totals for the default event spine (~/.sugar/events.db or $SUGAR_EVENTS_DB)
sugar usage

# Breakdown by model (with estimates, if a price table is configured)
sugar usage --group-by model

# Project-level spend over a range
sugar usage --group-by project --since 2026-11-01 --until 2026-11-30

# One task's reported/estimated spend (filter events separately in the API/UI;
# the CLI groups by task id)
sugar usage --group-by task --json
```

Flags: `--db PATH`, `--since ISO`, `--until ISO`,
`--group-by actor|harness|task|project|model|day|kind`, `--limit N`
(bounded scan, default 5000, max 50000), `--json`. The table includes reported
cost, estimated cost, and `cost_basis`:

```
           events  tokens_in  tokens_out  tokens  cost_usd  estimated_cost_usd  cost_basis
TOTAL           2       1240         840    2080    0.4521              0.0012  mixed
deepseek-flash  1       1000         800    1800    0.4000              0.0012  reported
hermes          1        240          40     280    0.0521              0.0000  reported
```

A missing DB is an empty report (zeros, exit 0); invalid timestamps, group-by or
limit exit non-zero. The CLI requires the dashboard extra
(`pip install 'sugarai[dashboard]'`) and reads the same events DB as the
dashboard.

---

## 6. API

```
GET /api/usage?since=2026-11-01T00:00:00Z&until=2026-11-10T00:00:00Z
              &group_by=model&limit=5000
```

| Query | Default | Notes |
|---|---|---|
| `since` / `until` | none | ISO-8601, inclusive, normalized to UTC `Z` |
| `group_by` | none (totals only) | `actor`, `harness`, `task`, `project`, `model`, `day`, `kind` |
| `kinds` | all usage kinds | comma-separated, e.g. `usage.reported` |
| `actor` / `harness` | none | exact-match filters |
| `limit` | 5000 | max events scanned, `1..50000` (bounded read) |

Response shape:

```json
{
  "totals": {
    "events": 12, "tokens_in": 84000, "tokens_out": 21000,
    "tokens_total": 105000, "cost_usd": 0.7341,
    "estimated_cost_usd": 0.2011, "cost_basis": "mixed"
  },
  "groups": [
    {"key": "deepseek-flash", "events": 8, "tokens_in": 61000,
     "tokens_out": 15000, "tokens_total": 76000,
     "cost_usd": 0.5121, "estimated_cost_usd": 0.18,
     "cost_basis": "mixed"}
  ],
  "since": "2026-11-01T00:00:00Z",
  "until": "2026-11-10T00:00:00Z",
  "group_by": "model",
  "events_scanned": 12,
  "truncated": false
}
```

- Unknown `group_by`, bad timestamps and invalid limits → `400
  {"error":"validation_error"}`.
- No event spine on the server → `503 {"error":"spine_unavailable"}`.
- With board tokens configured, `/api/usage` needs a `read`-scoped token like
  every other `/api/*` route; default loopback mode is unauthenticated.
- `truncated: true` means the aggregate covers the first `limit` matching events
  in id order; narrow the range or page for full history (rollups are future
  work).

---

## 7. Dashboard

- The Activity view has a **Usage** panel loaded once when the view opens (no SSE
  stream). It shows totals (events, tokens in/out/total, reported cost, estimated
  cost when available), and a group selector: `harness` (default), `actor`,
  `project`, `task`, `model`, `day`. Rows show reported vs estimated cost and
  token detail; older servers without `/api/usage` hide the panel gracefully.
- A **model summary** groups the recent activity feed by model (events, tool
  count, projects, first/last seen) and can filter the feed to one model.
- Task views show task project/cwd provenance from `context.project` /
  `context.cwd`; the project filter uses that recorded value.
- The Overview (human layer) shows a compact usage metric with the same
  reported/estimated split, plus attention items that can include spend anomalies
  (tokens with no cost attribution).
- Trends also charts harness-reported spend over time (`docs/trends.md`).

---

## 8. Limitations (read these before using numbers in a report)

- **Self-reported.** `cost_usd` is whatever the harness sent; it can be wrong,
  missing, rounded, in another currency, or absent entirely. Sugar does not call
  provider APIs or reconcile invoices.
- **Estimates are not bills.** They depend on an operator-maintained table that
  Sugar does not ship or refresh. Model names/aliases must match exactly; price
  changes, discounts, cache pricing, tiering and batch pricing are not modelled.
- **DSH cache caveat.** DSH's `tokens_in` includes cached prompt tokens so token
  totals match the provider's `totalTokens`; price estimates use the configured
  input rate for all of it, so they are approximate and usually conservative.
- **Project provenance is recorded, not inferred.** Task `context.project` is a
  basename captured at creation; events' `project` field is separate. There is no
  automatic project join or historical backfill.
- **Retention prunes the source.** The spine prunes raw events after
  `$SUGAR_EVENTS_RETENTION_DAYS` (default 30, `0` disables). Export/roll up
  before pruning if you need long histories; usage totals are only as complete as
  the retained events.
- **One machine, one spine.** `events.db` is per-machine; cross-machine totals
  require collecting exports. There are no budgets, quotas, alerts or chargeback
  workflows — this is visibility, not billing.
- **Trust boundary.** Usage events are as trustworthy as the local append-only
  log. For tamper-evident evidence, see [`audit.md`](audit.md) and
  [`audit-package.md`](audit-package.md); for task-level verified evidence see
  [`evidence.md`](evidence.md).

---

## 9. Related documentation

- [`usage.md`](usage.md) — reported usage contract, tolerance rules, API/CLI
  reference, bounded reads.
- [`telemetry-adapters.md`](telemetry-adapters.md) — how each harness reports
  usage (including DSH's per-response mapping).
- [`traces.md`](traces.md) — per-session token/cost context and trace insights.
- [`trends.md`](trends.md) — time-bucketed spend alongside throughput/failures.
- [`audit-package.md`](audit-package.md) — scoped evidence package including
  usage/event exports.
- [`local-dashboard.md`](local-dashboard.md) §11 — event-spine schema and
  retention.
