# Trends: time-bucketed work analytics

Status: shipped. Related: [`local-dashboard.md`](./local-dashboard.md) §11.3
(event-spine HTTP API), [`usage.md`](./usage.md) (raw usage aggregation),
[`audit.md`](./audit.md) (exports).

`GET /api/trends` answers "how is the work going over time?" from the event
spine: task counts, event volume, harness-reported tokens/cost, and cycle time,
grouped by actor / harness / project and bucketed by UTC hour, day or week. The
buckets are **zero-filled and continuous**, so charts never have holes. There is
no new datastore and no billing: task counts come from the `task.*` events,
usage numbers are whatever harnesses reported, and cycle time is paired within
the scanned window.

```
GET /api/trends?since=ISO&until=ISO&bucket=hour|day|week&group_by=actor|harness|project&limit=
```

Token auth is inherited from `/api/*` (`docs/board-server.md`); the endpoint is
read-only. A disabled event spine returns `503 spine_unavailable` (same as
`/api/usage`). Invalid parameters return `400 {"error":"validation_error",...}`.

## 1. Parameters and defaults

| param | values | default |
|---|---|---|
| `since` | ISO-8601 timestamp (inclusive) | `until - 7 days` |
| `until` | ISO-8601 timestamp (inclusive) | now (UTC) |
| `bucket` | `hour`, `day`, `week` | `day` |
| `group_by` | `actor`, `harness`, `project` | `actor` |
| `limit` | max events to scan | `50000` (max `200000`) |

- Both bounds are inclusive: an event at exactly `until` is included.
- `since` must be `<= until`.
- Timestamps are normalized to UTC; date-only inputs mean midnight UTC.
- The bucket list starts at the UTC-aligned bucket containing `since` and ends at
  the aligned bucket containing `until`. For example, `bucket=day` with a range
  covering Nov 2 00:00 → Nov 9 00:00 yields buckets Nov 2 … Nov 9 (inclusive
  endpoints; the last bucket may be partially covered).
- Weeks start on **Monday 00:00 UTC** (ISO weeks).
- At most `MAX_BUCKETS` (10,000) bucket starts are allowed; a finer bucket over a
  huge range returns a validation error (`use a coarser bucket or narrower range`).
- `limit` bounds the number of spine rows read (`ORDER BY id ASC LIMIT limit+1`
  → `truncated`). Larger values clamp to 200,000. Narrow the range if you need a
  complete picture beyond the bound.

## 2. Response

```json
{
  "since": "2026-11-02T00:00:00Z",
  "until": "2026-11-09T00:00:00Z",
  "bucket": "day",
  "group_by": "actor",
  "buckets": ["2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z", "…"],
  "series": [
    {
      "key": "claude-code",
      "points": [
        {
          "bucket": "2026-11-02T00:00:00Z",
          "tasks_created": 2, "tasks_claimed": 3,
          "tasks_completed": 2, "tasks_failed": 1,
          "events": 18,
          "tokens_in": 1200, "tokens_out": 300, "cost_usd": 0.0123
        }
      ],
      "totals": {
        "tasks_created": 5, "tasks_claimed": 6, "tasks_completed": 4,
        "tasks_failed": 1, "events": 40,
        "tokens_in": 3000, "tokens_out": 800, "cost_usd": 0.031
      },
      "avg_cycle_time_s": 142.5
    }
  ],
  "totals": {
    "tasks_created": 5, "tasks_claimed": 6, "tasks_completed": 4,
    "tasks_failed": 1, "events": 40,
    "tokens_in": 3000, "tokens_out": 800, "cost_usd": 0.031
  },
  "event_count": 123,
  "truncated": false
}
```

- `buckets` is the shared x-axis (ascending, zero-filled). Every series has one
  `points` entry per bucket, in the same order.
- `series` is sorted by `key` ascending (stable for legends/charts).
- `points`/`totals` always contain all eight metrics; absent values are `0` (or
  `0.0` for cost).
- `event_count` is the number of spine rows scanned for this query (after the
  `since`/`until` SQL filter and the `limit` bound).
- `truncated: true` means the scan hit `limit`; the response is a prefix of the
  range (ordered by event id), not the complete range.

## 3. Metric definitions

Per event, the group key comes from the event field for the chosen dimension
(`actor`, `harness`, `project`); missing/blank values become `"unknown"`. The
event's bucket is derived from its `ts`. An event contributes to exactly one
series point.

| metric | source |
|---|---|
| `tasks_created` | count of `task.created` events |
| `tasks_claimed` | count of `task.claimed` events (retries can claim the same task more than once) |
| `tasks_completed` | count of `task.completed` events |
| `tasks_failed` | count of `task.failed` events |
| `events` | count of **all** scanned events (any kind, including task/usage/telemetry) in the bucket |
| `tokens_in` / `tokens_out` | sum over `usage.reported` and `run.ended` payloads, normalized tolerantly like `/api/usage` |
| `cost_usd` | sum of `cost_usd` from those payloads; harness-reported, rounded to 6 dp |
| `avg_cycle_time_s` | per-series mean over completed tasks of `completed_at - last_claim_at` (see below) |

Usage payloads are parsed with the same tolerant normalizer as `/api/usage`
(`sugar/dashboard/usage.py`): missing/null/invalid/negative/NaN fields and
non-numeric garbage are ignored; a payload with no usable numeric field does not
contribute. `events` still counts the event itself.

`totals` sums every in-range scanned event (including groups with only unknown
keys). Series totals sum that series' points, so the series totals add up to the
global totals.

### Cycle time (`avg_cycle_time_s`)

For each `task.completed` event with a `task_id`, find that task's
`task.claimed` events in the same scanned window and take the **latest claim at
or before** the completion timestamp; the cycle time is the difference in
seconds. The pair contributes to the completed event's series/bucket (i.e. the
group of the completing event), and the series value is the mean
(`sum / count`, rounded to 1 decimal). It is `null` when a series has no
matchable pairs.

Assumptions and limits:

- A completed task with no prior claim in the window contributes nothing.
- Claims after the completion (out-of-order timestamps) are ignored.
- Multiple completions for the same `task_id` each pair independently.
- Cycle time is **time between claim and completion**, not queue wait, review,
  or wall-clock duration of the agent process. Use `run.started`/`run.ended` for
  process durations.
- Because the scan is bounded, very large ranges can miss the claim (or make a
  later claim the "latest prior") — the number is exact within the scanned
  window, not over all history.

## 4. Examples

```bash
# Last 7 days, per actor, daily (defaults)
curl -s 'http://127.0.0.1:7331/api/trends' | jq '.totals, (.series | length)'

# Hourly throughput for the last 24h, per harness
curl -s 'http://127.0.0.1:7331/api/trends?since=2026-11-08T00:00:00Z&until=2026-11-09T00:00:00Z&bucket=hour&group_by=harness'

# Weekly for a month, per project, remote board with a read token
curl -s -H "Authorization: Bearer $TOKEN" \
  'http://board.lan:7331/api/trends?since=2026-10-01T00:00:00Z&until=2026-11-01T00:00:00Z&bucket=week&group_by=project'

# CLI (same computation over the local spine)
sugar trends
sugar trends --since 2026-11-01 --bucket hour --group-by harness
sugar trends --since 2026-10-01 --until 2026-11-01 --bucket week --json
```

CLI output is a compact table (`group, completed, failed, avg_cycle, events,
cost_usd`) preceded by the range/bucket/event count. `--json` prints the exact
API response. The CLI reads the events DB read-only (`--db`, default
`$SUGAR_EVENTS_DB` or `~/.sugar/events.db`) and requires the dashboard extra
(`pip install 'sugarai[dashboard]'`) like the dashboard itself.

## 5. Non-goals / caveats

- No billing reconciliation or pricing table: `cost_usd` is harness-reported.
- No per-task ACLs (a read token sees all series); token auth only gates access
  to the board (`docs/board-server.md`).
- No rollups: every request scans up to `limit` spine rows in memory. For very
  large histories, narrow the range/bucket or add a future rollup table; the
  `truncated` flag tells you when the bound was hit.
- Retention pruning (`$SUGAR_EVENTS_RETENTION_DAYS`, default 30) removes old raw
  events; export before pruning (`docs/audit.md`) if you need long-term trends.
- Events with an unusable `ts` cannot be bucketed and are ignored for metrics
  (the spine itself only writes normalized timestamps).
- Zero-filled buckets are derived from the requested range, not from the data;
  a series that only has activity in one bucket still returns all buckets.

## Seeding demo data

The Trends view needs history. To fill a **throwaway** event-spine database with
two weeks of synthetic work across projects and agents (never your real
`~/.sugar/events.db`):

```bash
python scripts/seed_demo_trends.py \
  --db /tmp/sugar-dashboard-demo/.sugar/events.db --days 14 --reset
```

Seeded rows use `source="seed-trends"` and `machine="demo"`; `--reset` deletes
only those rows. Combine with `scripts/simulate_agent_activity.py` for live
activity on top of the seeded history, and point SugarDash at the demo board to
see the charts populated.
