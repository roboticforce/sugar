# Evidence ledger: proving what actually happened

A task board can tell you an agent said it was done. It cannot tell you whether
the tests passed, what command ran, who approved it, or which commit it
verified. Evidence is how Sugar closes that gap.

Every verification result, policy decision, approval, artifact and commit link
is an ordinary event in the append-only spine — with a task, actor, harness,
timestamp and structured payload — so it can be queried live, shown in Mission
Control, and exported with the audit tools.

---

## 1. Evidence kinds

| Kind | Use it for |
|---|---|
| `verification.test_run` | A test suite run (pytest, npm test, integration suite) |
| `verification.check_result` | Any other check (lint, typecheck, build, security scan) |
| `verification.review` | A human or agent review outcome |
| `policy.decision` | A policy check (allowed/denied, sandbox policy, spend rule) |
| `approval.granted` / `approval.denied` | Approval workflow outcomes |
| `artifact.created` | A build, report, screenshot, bundle or other artifact |
| `commit.linked` | A commit associated with the work |

These are spine kinds, not a separate store. They appear in Activity, exports,
trends and retention exactly like task and telemetry events.

## 2. Evidence event

```json
{
  "kind": "verification.check_result",
  "actor": "claude-code",
  "actor_kind": "agent",
  "harness": "claude-code",
  "task_id": "t_abc123",
  "session_id": "3f0c…",
  "ts": "2026-11-20T22:14:45Z",
  "source": "telemetry:evidence",
  "payload": {
    "schema": "sugar.telemetry.v1",
    "tool_name": "ci",
    "status": "passed",
    "fidelity": "L3",
    "payload": {
      "check": "pytest",
      "command": "pytest -q",
      "exit_code": 0,
      "commit": "abc123"
    }
  }
}
```

`status` is accepted on the envelope (`payload.status`) and in the summary so
filters and badges are consistent. Use free-form summary keys for check name,
command, exit code, commit, duration, URL or a short report.

## 3. HTTP API

### `POST /api/evidence`

```json
{
  "kind": "verification.check_result",
  "harness": "claude-code",
  "task_id": "t_abc123",
  "status": "passed",
  "payload": {"check": "pytest", "command": "pytest -q", "exit_code": 0}
}
```

→ `201 {"event": Event}`; invalid kind/payload → `400`; spine disabled → `503`.
Token mode requires a write-scoped bearer token (same as other write routes).

### `GET /api/evidence`

```bash
curl 'localhost:7331/api/evidence?task_id=t_abc123&status=failed&limit=20'
```

Query parameters:

| Param | Meaning |
|---|---|
| `since`, `until` | Inclusive ISO-8601 timestamps |
| `task_id` | Exact task id |
| `kind` | One of the evidence kinds above |
| `status` | Case-insensitive status filter (`passed`, `failed`, `error`, `blocked`, `ok`, …) |
| `limit` | 1–500, default 100 |
| `offset` | ≥ 0, default 0 |

Response:

```json
{
  "events": [Event, ...],
  "last_id": 42,
  "summary": {
    "total": 7,
    "by_status": {"passed": 5, "failed": 2},
    "by_kind": {"verification.check_result": 6, "approval.granted": 1}
  }
}
```

Events are oldest→newest (like `/api/activity`); `summary` covers the whole
filtered set, independent of pagination. Invalid filters → `400`; no spine →
`503`; token mode requires a read-scoped token.

## 4. CLI

Record evidence as part of a local workflow or CI step:

```bash
sugar evidence record \
  --kind verification.check_result \
  --status passed \
  --task-id t_abc123 \
  --harness claude-code \
  --check pytest \
  --command "pytest -q" \
  --payload exit_code=0 \
  --commit abc123
```

- `--kind` is required (one of the evidence kinds).
- `--status` is optional but recommended.
- `--check`, `--command`, `--commit`, `--title`, `--summary` map to summary
  fields; `--payload key=value` adds arbitrary fields (JSON-decoded when
  possible), e.g. `--payload exit_code=0`.
- `--json` prints a compact `{id,kind,status}`.
- Board failures exit 1; bad arguments/config exit 2.
- `$SUGAR_BOARD_URL` (default `http://127.0.0.1:7331`) and
  `$SUGAR_BOARD_TOKEN` are honored; `--url` overrides.

Inspect:

```bash
sugar evidence list --task-id t_abc123
sugar evidence list --kind verification.check_result --status failed --json
sugar evidence summary --since 2026-11-20
```

`list` renders `TIME KIND STATUS HARNESS TASK SUMMARY`; `--json` prints the raw
API response. `summary` prints totals plus by-status/by-kind counts.

### CI example

```yaml
- run: pytest -q
- if: always()
  run: |
    sugar evidence record --kind verification.test_run \
      --status ${{ job.status }} --task-id "$TASK_ID" \
      --check pytest --command "pytest -q" \
      --payload exit_code="${{ steps.test.outputs.exit }}"
```

## 5. Mission Control

The browser dashboard has an **Evidence** control next to Coverage:

- count badge (red when anything is failed),
- total/passed/failed/blocked summary chips,
- filters by task, kind and status,
- rows with relative time, kind/status badges, harness, task and summary,
- refresh every 15 s while open; Escape/backdrop close; mobile-safe,
- a clear “Evidence unavailable on this server” fallback for older servers.

Use it to answer “what verified this task, and did it pass?” without opening a
terminal. Agents can record evidence themselves over the API.

## 6. Export and retention

Evidence uses the same append-only spine and retention as all other events
(`~/.sugar/events.db`, default 30 days, `$SUGAR_EVENTS_RETENTION_DAYS`). Export
with:

```bash
sugar audit export --output evidence-export.jsonl
sugar audit verify evidence-export.jsonl
```

The audit manifest gives you count, first/last id, SHA-256 and optional HMAC.
Per-event signatures are not part of the evidence record itself; the export
manifest is the tamper-evidence boundary. If you need long-term retention,
export before the retention window prunes the spine.

## 7. Design notes and limits

- **Status vocabulary is intentionally broad**, not a rigid enum, so a check can
  report `passed`, `failed`, `blocked`, `error`, `ok`, `skipped` or a custom
  value. Filters use a known set; unknown statuses still count in summaries.
- **Summary scans are bounded** (10k rows) for the panel; very large evidence
  sets should use `since`/`until`/`task_id` filters or export instead.
- **Evidence is not a substitute for a signature.** It records what a harness or
  operator reported. Pair it with audit exports, approvals and policy decisions
  for a stronger chain.
- **No separate migrations**: evidence is spine-native, so it works with every
  existing store/read path (Activity, audit, SSE, coverage/fidelity).

## Related

- [Telemetry adapters](telemetry-adapters.md) — how harness events reach the
  spine, fidelity levels and adapters.
- [Audit exports](audit.md) — canonical JSONL, manifest, HMAC, verify.
- [Mission Control](mission-control.md) — Activity, Live floor and coverage.
- [Local Dashboard API](local-dashboard.md) — full REST/SSE contract.
