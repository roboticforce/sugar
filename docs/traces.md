# Trace Explorer

*Status: implemented as a read-only projection over the event spine. This page
covers the trace/span model, the `/api/traces` API, the Trace Explorer UI modes
(Timeline and Graph), trace insights, DSH multi-agent enrichment, and the
limitations of what a trace can claim.*

Related: [`local-dashboard.md`](local-dashboard.md) for the frozen dashboard API
and event-spine schema, [`telemetry-adapters.md`](telemetry-adapters.md) for how
harnesses produce the underlying events, [`spend-attribution.md`](spend-attribution.md)
for token/cost context, and [`audit-package.md`](audit-package.md) for exporting
trace evidence.

Sugar does not run an external tracing backend. It **derives** traces from the
same append-only event spine used by Activity, Usage and Audit: events that share
a `session_id` are grouped into one trace per harness/session, then projected
into a small span tree. There is no new schema and no write path.

---

## 1. Trace identity and summary

A trace id is `<harness>:<session_id>`, e.g. `dsh:11111111-2222-…`. A trace
summary contains:

| Field | Meaning |
|---|---|
| `trace_id` | `<harness>:<session_id>` |
| `harness` | `dsh`, `claude-code`, `codex`, `cursor`, `goose`, … |
| `project`, `cwd` | From the session/spine event fields when present |
| `title` | Session title/workspace/project heuristic, else trace id |
| `started_at`, `last_event_at`, `ended_at` | ISO timestamps from recorded events |
| `duration_ms` | `ended_at - started_at` when both exist (can be null) |
| `events`, `turns`, `tool_calls` | Recorded counts |
| `models` | Distinct models seen in the session |
| `tokens_total` | Token totals carried by usage events |
| `status` | `ok`, `failed` or `running` |
| `subagents`, `artifacts` | Enriched counts when subagent/artifact events exist |

Status is derived conservatively: failed tool calls/approvals or a failed session
mark the trace failed; an unended session is running; otherwise it is ok.
DSH session ids may be prefixed (`session-…`) or bare UUIDs depending on the
session header; graph joins normalize the prefix, while the exposed trace id keeps
the recorded session id.

---

## 2. API

Both endpoints are read-only. They require a `read`-scoped token when the board
is running with tokens; the default loopback server has no auth. A server without
an event spine returns `503 {"error":"spine_unavailable"}`.

### `GET /api/traces`

```
GET /api/traces?limit=20&project=sugar&harness=dsh&since=2026-11-01T00:00:00Z
```

| Query | Default | Notes |
|---|---|---|
| `limit` | 20 | Traces returned, cap 100 |
| `project` | none | Exact project filter |
| `harness` | none | Exact harness filter |
| `since` | none | ISO-8601 lower bound |

Response: `{"traces": [summary, ...]}` — newest sessions first, bounded by
`limit`. Invalid parameter values → `400 {"error":"validation_error"}`.

### `GET /api/traces/{trace_id}`

Response: `{"trace": summary, "spans": [span, ...], "insights": {...}}`.
`insights` is additive: if the derived insight source is unavailable it degrades
to a `watch` / "Insights unavailable" object instead of failing the response.
Unknown ids → `404`; the id is split at the first colon
(`<harness>:<session_id>`).

The backend reads up to `50,000` events per scan and projects every span
input/output through a bounded depth/size/items projection (1,000 chars,
4 levels, 20 items) so a trace response never ships a multi-megabyte event
payload.

---

## 3. Span model

Each span is a small object:

```json
{
  "span_id": "tool:call_abc",
  "parent_span_id": "step:dsh:s1:1:2",
  "kind": "tool",
  "name": "bash",
  "actor": "dsh",
  "harness": "dsh",
  "model": null,
  "status": "completed",
  "start": "2026-11-09T17:30:01Z",
  "end": "2026-11-09T17:30:02Z",
  "duration_ms": 1000,
  "project": "sugar",
  "cwd": "/Users/me/Dev/sugar",
  "input": {"command": "pytest -q"},
  "output": {"exit_code": 0},
  "attributes": {"event_kind": "tool.completed"}
}
```

Span kinds used by the UI: `session`, `turn`, `tool`, `mcp`, `llm`, `approval`,
`artifact`, `subagent`, `task` (plus policy/verification detail in attributes
where events provide it). Common pairing rules:

- `turn.started` / `turn.ended` pair into a turn span, using recorded timestamps.
- Tool calls pair `tool.started` with `tool.completed`/`tool.failed` by
  `tool_use_id` (DSH `callId`); unmatched starts or completions still produce a
  span with the status they imply.
- MCP tools are tool spans whose name looks like `mcp__…` (classified `mcp`).
- LLM spans come from `usage.reported`/`run.ended` usage events and carry the
  model plus token attributes.
- Approvals/policy decisions and artifacts become their own spans when the
  corresponding events are present.
- Async job details (e.g. bash `job/registered`/`job/settled`) contribute
  duration/status/output attributes where the adapter records them.
- When no explicit `span_id` exists, ids are derived from event ids/call ids;
  collisions get a deterministic numeric suffix.

`parent_span_id` is derived from the structural relationships above (tool →
step/turn, approval → tool call, artifact → producing call, etc.), not from a
producer-supplied trace context.

---

## 4. Trace Explorer UI

Open the dashboard and choose **Traces**. The left list shows each session with
status, project/harness, event/turn/tool counts, models, tokens, duration and
enriched subagent/artifact badges. Selecting a trace opens the inspector.

### Timeline mode

The default mode renders a typed trace with three coordinated panes:

- **Actor swimlanes** — spans laid out on a time axis by actor lane
  (`main`, `dsh`, `subagent`, `user`, `system`, …) so parallel agent work is
  visually separable.
- **Call tree** — the parent/child span tree with collapsible nodes; selecting a
  span highlights it everywhere.
- **Span inspector** — kind/status/start/end/duration/project/cwd/model plus the
  bounded input, output and attributes payloads.

### Graph mode

The **Graph** switch renders a layered architecture/artifact graph derived from
the same spans:

- layers: **Inputs**, **Models/agents**, **Outputs/Artifacts**, and
  **Verification/Approvals**;
- repeated tool/MCP calls are aggregated into one node with a `×N` badge so a
  long agent loop stays readable;
- artifact nodes are created from span `attributes.paths`/`attributes.artifacts`
  and linked to the span that produced them (dashed artifact edge);
- parent → child span edges are drawn, with subagent links using their own dashed
  style;
- a node cap (`80`) keeps every layer represented on large traces.

The graph is an **interpretation of recorded spans**, not a declared architecture
diagram. A dashed edge means "this span produced/referenced this artifact" or
"this span is a subagent relationship", not a build-system dependency.

---

## 5. Insights (additive trace analysis)

`GET /api/traces/{trace_id}` adds an `insights` object derived from the trace,
its verification evidence, adapter coverage and usage — no LLM generation and no
invented findings:

```json
{
  "risk": "unverified",
  "headline": "Completed without verification evidence",
  "bullets": ["no verification evidence", "dsh coverage is L1"],
  "flags": [
    {"kind": "unverified", "severity": "medium", "detail": "no verification evidence for this session"},
    {"kind": "coverage", "severity": "medium", "detail": "dsh coverage is L1"}
  ]
}
```

- `risk`: `failed`, `blocked`, `unverified`, `watch` or `ok`.
- `flags[].kind`: `failure`, `policy`, `unverified`, `coverage`, `spend` or
  `long_running`; severities are `high`/`medium`/`low`.
- Signals include failed spans, denied approvals, unverified completed sessions,
  L0/L1 adapter coverage, tokens with no cost attribution, and sessions running
  beyond the attention threshold (`$SUGAR_ATTENTION_LONG_RUNNING_SECONDS`,
  default 2h).
- The same evidence feeds the **Overview** attention queue
  (`GET /api/attention`), which also surfaces failed sessions, blocked tasks,
  verification gaps, coverage gaps, denied approvals, pending approvals and
  conservative spend anomalies with severity ordering. Attention items are
  signals to inspect, not verdicts.

Insight text is derived heuristically; treat it as a pointer to evidence, not
as an audit conclusion.

---

## 6. DSH multi-agent enrichment

The DSH session-tail emits extra canonical events that make a trace show the real
multi-agent shape (see [`telemetry-adapters.md`](telemetry-adapters.md)):

| Event | Trace effect |
|---|---|
| `subagent.started` | A `subagent` span with `parent_session_id`, `child_session_id`, `label`, `mode`; parent/child sessions surface as separate traces linked through attributes |
| `team.task` | A `task` span carrying team id, task id, subject, status, owner id/name and revision — the delegation work item, not a Sugar board task |
| `artifact.created` | An `artifact` span and an artifact-graph node for the recorded path (paths only, never file contents) |
| `workspace.changed` | Optional event for edit tool results with reliable paths; it is not currently projected as a span (safe to ignore) |

Evidence: sessions, turns, tool calls, usage, approvals, artifacts, subagent
relationships and team tasks all come from the same tail. Traces without
enrichment events still work; they simply have no subagent/team/artifact spans.
Tail delivery is at-most-once per `(DSH sequence, canonical event)`, so a raw log
event that produces both a tool result and an artifact event is delivered once
for each canonical event.

---

## 7. Limitations

- **Derived, not native tracing.** Traces are reconstructed from recorded events;
  they cannot see work that never reached the spine (missing adapter, tail not
  running, retention pruned the events).
- **Bounded.** Scans, returned traces (`limit`), span payload depth/items, and
  graph nodes are capped. A huge session is summarised, not fully rendered.
- **Status/insights are heuristics.** "failed"/"running"/"unverified" and insight
  flags are conservative derivations, not guarantees; a missing verification
  event is not proof that no verification happened.
- **Enrichment depends on producers.** Subagent/team/artifact spans require the
  DSH enrichment events; other harnesses may have fewer span kinds. Unknown event
  kinds are ignored rather than invented.
- **No distributed tracing.** There is no W3C trace-context propagation across
  machines or services; a trace id is local to Sugar's spine (`<harness>:<session>`),
  and cross-machine ordering requires collecting exports.
- **Sensitive metadata.** Inputs/outputs/attributes are projected from event
  payloads (already redacted by the telemetry pipeline where configured). Treat
  trace responses and audit packages as sensitive; they can contain paths,
  prompts or tool output fragments within the bounds above.
- **No sampling or query language.** The API exposes bounded list/detail views;
  there is no span query language, retention beyond the spine, or cost-based
  sampling.
- **Graph is an interpretation.** Aggregated tool nodes, artifact nodes and
  dashed subagent/artifact edges are UI constructs from span attributes, not
  declared dependencies; absence of an edge is not proof of no relationship.

---

## 8. Related documentation

- [`local-dashboard.md`](local-dashboard.md) — frozen REST/SSE contract and event
  spine schema.
- [`telemetry-adapters.md`](telemetry-adapters.md) — canonical events and the DSH
  session-tail adapter.
- [`spend-attribution.md`](spend-attribution.md) — usage/model/cost context that
  traces and insights surface.
- [`audit-package.md`](audit-package.md) — trace summaries/span details included
  in a scoped audit package.
- [`evidence.md`](evidence.md) — verification records insights use.
- [`mission-control.md`](mission-control.md) — Overview/Mission Control surfaces.
