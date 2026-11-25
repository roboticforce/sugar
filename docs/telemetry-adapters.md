# Telemetry adapters: how Sugar watches your agents

Sugar does not wrap, intercept or replace your coding agents. It gives each
harness a small, standard way to report what it is doing, normalizes those
reports into one canonical event, and stores them in the append-only event
spine next to task state. That is what makes Mission Control show live tool
calls, the Activity feed queryable, and usage/audit/trends possible across
different harnesses.

This document covers the **Phase 1b** adapter surface: the canonical envelope,
fidelity levels, hook/evidence/OTLP/socket ingestion, `sugar connect`,
`sugar hook`, `sugar doctor`, the coverage API/UI, SDK helpers, redaction and
current limitations.

> **Scope.** Phase 1b ships adapters for **Claude Code**, **Cursor**, **Codex**,
> **Goose** and **DeepSeek Harness** at L3, an **OTLP/HTTP receiver** that
> accepts protobuf or JSON (plus gzip) on `/v1/traces` and `/v1/logs`, and
> helpers for **Google ADK**, **OpenAI Agents SDK** and the **Claude Agent SDK**.
> DSH is tailed from its compressed session logs; hooks/ACP live events and the
> MCP proxy remain future work. Until an adapter lands, Sugar still shows the
> harness at L0/L1 through the task board and leases.

---

## 1. Fidelity levels

Coverage is explicit about how much Sugar can see. Every canonical event
carries a `fidelity` field, and the Coverage panel shows the level per harness.

| Level | Meaning | What it enables |
|---|---|---|
| **L3** | Tool calls with arguments/results (redacted summary) | Live current-action, streaming console, tool-level audit, rework analysis |
| **L2** | Tool names and status only | Live activity, tool mix, failure rates |
| **L1** | Session/run/turn lifecycle (started/ended, usage) | Who ran what and for how long; cost attribution |
| **L0** | Board/leases only (task claims, completions, retries) | Presence and queue health, no per-turn detail |

A harness can report different levels over time (e.g. a new adapter version),
and `GET /api/coverage` reports the current effective level per harness.

---

## 2. Canonical event: `sugar.telemetry.v1`

Canonical fields reuse the spine columns; extended fields live under `payload`
so existing readers (`/api/activity`, audit exports, trends) keep working.

```json
{
  "kind": "tool.completed",
  "actor": "claude-code",
  "actor_kind": "agent",
  "harness": "claude-code",
  "session_id": "3f0c…",
  "task_id": "t_abc123",
  "run_id": null,
  "project": "my-app",
  "trace_id": null,
  "ts": "2026-11-19T16:00:28Z",
  "source": "telemetry",
  "payload": {
    "schema": "sugar.telemetry.v1",
    "turn_id": "turn-1",
    "span_id": null,
    "parent_span_id": null,
    "tool_use_id": "toolu_01ABC…",
    "tool_name": "Bash",
    "status": "completed",
    "duration_ms": 842,
    "cwd": "/Users/me/my-app",
    "model": "claude-sonnet-4-5",
    "fidelity": "L3",
    "ts": "2026-11-19T16:00:28Z",
    "payload": {
      "command": "pytest tests/test_api.py",
      "exit_code": 0
    }
  }
}
```

### Event kinds

| Group | Kinds |
|---|---|
| Run | `run.started`, `run.ended` |
| Turn | `turn.started`, `turn.ended` |
| Tool | `tool.started`, `tool.completed`, `tool.failed` |
| Session | `session.started`, `session.ended` |
| Usage | `usage.reported` |
| Evidence | `verification.test_run`, `verification.check_result`, `verification.review`, `policy.decision`, `approval.granted`, `approval.denied`, `artifact.created`, `commit.linked` |

Evidence kinds are accepted through `POST /api/evidence` (and later via
verification adapters). They are ordinary spine events with the same envelope;
the prefix is what makes them queryable as the verification ledger.

---

## 3. Ingestion surfaces

### 3.1 `POST /api/hooks/{harness}` — raw harness hooks

Accepts a raw hook payload from a supported harness and normalizes it:

- `claude-code` — `SessionStart`, `SessionEnd`, `PreToolUse`, `PostToolUse`,
  `PostToolUseFailure`, `Stop`, `SubagentStart`, `SubagentStop`,
  `UserPromptSubmit` (Claude Code HTTP hooks).
- `cursor` — `sessionStart`, `sessionEnd`, `preToolUse`, `postToolUse`,
  `postToolUseFailure`, `stop` (command hooks; see §5).
- `codex` — the Claude-compatible event names in `~/.codex/hooks.json`
  (command hooks; see §5).
- `goose` — Goose hook-plugin events (`PreToolUse`, `PostToolUse`,
  `PostToolUseFailure`, `SessionStart/End`, `Stop`, `UserPromptSubmit`,
  `BeforeReadFile`, `AfterFileEdit`, shell events; see §5).

Behavior:

- **Known harness → always `200 {"ok": true}`**, even if the event was
  deduplicated or dropped. Hooks are on the agent loop; telemetry must never
  break a tool call.
- Unknown harness → `404`; invalid JSON/body → `400`.
- The payload is normalized (tool name aliases, input/response mapping,
  timestamps, session/turn/tool ids), redacted, deduplicated, and appended to
  the spine with `source="telemetry"`.

Normalization is tolerant: missing fields become `null`, unknown tool names
are passed through, and a bad payload never raises into the caller.

### 3.2 `POST /api/evidence` — verification/evidence events

Accepts a canonical evidence body:

```json
{
  "kind": "verification.check_result",
  "harness": "claude-code",
  "session_id": "3f0c…",
  "task_id": "t_abc123",
  "tool_name": "ci",
  "status": "ok",
  "payload": {"check": "pytest", "commit": "abc123", "duration_ms": 4210}
}
```

Returns `201 {"event": <spine event>}`; invalid kinds/fields → `400`; disabled
spine → `503`. Evidence defaults to `fidelity: L3` and `source:
"telemetry:evidence"`. Use it from CI, verifiers or custom agents to put
verification results next to the work they verify.

### 3.3 `POST /v1/traces` and `POST /v1/logs` — OTLP/HTTP

OTel-native harnesses can export directly to the same spine. Both endpoints
accept standard OTLP/HTTP **protobuf** (the OTel default) and **JSON**
(`ExportTraceServiceRequest` / `ExportLogsServiceRequest`), optionally gzipped:

```bash
# OTel exporters append /v1/traces or /v1/logs to the base endpoint
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:7331
# protobuf is the default; JSON is also supported:
# export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
```

- Content types: `application/x-protobuf`, `application/protobuf`,
  `application/octet-stream`, `application/json`; encodings `identity` or
  `gzip`. Anything else → `415`.
- Protobuf requires the optional extra: `pip install 'sugarai[telemetry]'`.
  Without it, JSON still works and protobuf returns `415` with that hint.
- Success `200 {}`; malformed body → `400`; raw or decompressed body > 1 MB →
  `413`; disabled spine → `503`.
- In token mode `/v1/*` requires a write-scoped bearer token.
- Mapping: tool spans (`execute_tool*`, `function_span*`, `gen_ai.tool.name` /
  `tool.name` attrs) → `tool.completed`/`tool.failed` at **L3** with
  trace/span/parent ids; agent/workflow spans (`invoke_agent*`,
  `invoke_workflow*`) → `run.ended` at L1; model spans (`chat`,
  `generate_content*`, `stream_response_from_provider`) with token attrs →
  `usage.reported`; DSH `session-log` records and `tool/call`/`tool/result` logs
  map to the corresponding tool/turn/session kinds.
- Harness comes from `?harness=goose|adk|codex|claude-code|dsh` or the
  resource `service.name` (e.g. `goose`, `adk*`, `codex`); otherwise `unknown`.
- Events are redacted/deduped by the same pipeline and stored with
  `source="otlp:traces"` / `"otlp:logs"`.

### 3.4 Unix ingest socket — `sugar hook` fast path

The dashboard listens on a Unix socket (default `~/.sugar/ingest.sock`,
`$SUGAR_INGEST_SOCKET` to override; `''` disables). It accepts newline-delimited
JSON:

```json
{"harness": "cursor", "body": {"hook_event_name": "postToolUse", "tool_name": "Shell", "tool_input": {"command": "npm test"}, "tool_use_id": "call_1"}}
```

Socket file mode is `0600`. It is the preferred transport for command hooks
(Cursor/Codex) because it avoids HTTP overhead. Events go through the same
normalization/redaction/dedupe pipeline as HTTP hooks.

### 3.5 Existing generic endpoints

- `POST /api/events` — raw spine event (`sugar emit`/custom scripts); unchanged.
- `GET /api/activity` — history.
- `GET /api/coverage` — adapter coverage (§6).
- SSE `/api/events` — `activity` events fan out to Mission Control.

---

## 4. Canonical coverage API

`GET /api/coverage`:

```json
{
  "generated_at": "2026-11-19T16:00:28Z",
  "summary": {"L3": ["claude-code"], "L2": ["cursor"], "L1": [], "L0": ["goose", "dsh"]},
  "harnesses": [
    {
      "harness": "claude-code",
      "display_name": "Claude Code",
      "detected": true,
      "installed": true,
      "fidelity": "L3",
      "transport": "http-hook",
      "last_event_at": "2026-11-19T16:00:28Z",
      "events_24h": 42,
      "adapter_version": "phase0",
      "notes": "posts HTTP hooks to http://127.0.0.1:7331/api/hooks/claude-code"
    }
  ]
}
```

- `detected`/`installed` come from the adapter registry at
  `~/.sugar/adapters.json` (override with `$SUGAR_ADAPTERS_FILE`) plus live
  detection.
- Events are counted from the spine over the last 24 hours; harnesses seen only
  in events appear as `detected` with `installed: false`.
- The browser dashboard shows this as the **Coverage** control (L0–L3 badges,
  events/24h, last seen, notes). Older servers without the route show
  "Coverage unavailable on this server".

---

## 5. `sugar connect`, `sugar hook`, `sugar doctor`

### `sugar connect`

```bash
sugar connect --check                 # detect + matrix only (no writes)
sugar connect --dry-run               # show planned config changes
sugar connect --yes                   # install for every detected harness
sugar connect --harness cursor --yes  # one harness
```

Installs:

| Harness | What is written | Transport | Fidelity |
|---|---|---|---|
| Claude Code | `~/.claude/settings.json` hooks (`SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop`) as `type: "http"` to `/api/hooks/claude-code` | HTTP hook (no process) | L3 |
| Cursor | `~/.cursor/hooks.json` command hooks calling `sugar hook --harness cursor` | command hook | L3 |
| Codex | `~/.codex/hooks.json` command hooks calling `sugar hook --harness codex` | command hook | L3 |
| Goose | `~/.agents/plugins/sugar-telemetry/{plugin.json,hooks/hooks.json}` calling `sugar hook --harness goose` | hook plugin | L3 |
| DeepSeek Harness | macOS LaunchAgent running `sugar tail dsh --watch` (non-macOS: prints the manual command) | session tail | L3 |

- Existing config is preserved; a `.bak` backup is written next to the file.
- Installs are idempotent: re-running updates Sugar's entries and keeps
  unrelated hooks.
- The default board URL is `http://127.0.0.1:7331`; run the dashboard there.
- **Codex trust gate:** Codex hash-pins hooks. After install, open `codex`, run
  `/hooks`, and approve them. Sugar never bypasses this. For unattended
  automation only, Codex offers `--dangerously-bypass-hook-trust` — an explicit
  operator choice, not something Sugar enables.
- **Goose:** installs a user plugin under `~/.agents/plugins/sugar-telemetry/`
  (plugin.json + hooks/hooks.json). Hooks are awaited by Goose, so the command
  uses the short-timeout, fail-open `sugar hook` shim; matchers are regex.
- **DeepSeek Harness:** DSH has no hook API, so Sugar tails its compressed
  session logs (`~/.dsh/sessions/…/session.v4.jsonl.zstd`). On macOS `connect`
  writes a LaunchAgent; on other platforms it prints the manual
  `sugar tail dsh --watch` command. A decompressor (`zstd` on PATH, or the
  optional `zstandard` package) is required. The LaunchAgent is inert until you
  run the printed `launchctl load -w …` command. The tail also maps assistant
  usage and subagent/team/artifact events (see `sugar tail dsh` below) so Usage,
  Trace Explorer and the observed AIBOM show real DSH work.
- The registry (`~/.sugar/adapters.json`) is what the Coverage API reads.

A Claude Code plugin is also included at `integrations/claude-plugin/` for teams
that distribute hooks via the plugin marketplace instead of `sugar connect`.
Its `hooks/hooks.json` uses the same HTTP endpoint.

### `sugar tail dsh`

```bash
sugar tail dsh                  # scan once (default)
sugar tail dsh --watch          # poll every 2s (launchd/systemd/manual)
sugar tail dsh --since 2026-11-01 --quiet
```

DSH writes compressed JSONL session logs. The tailer maps:

- `session`, `turn/*`, `tool/call`, `tool/result` (call/result pairing, error
  detection) and `approval/policy` to canonical events at L3;
- `assistant/message` usage to `usage.reported` per model response, with the
  model and turn context. `tokens_in` includes cache read/write so
  `tokens_in + tokens_out` equals DSH's own `totalTokens`; the fresh-input slice
  remains `input_tokens` (details in
  [`spend-attribution.md`](spend-attribution.md));
- parent-side `subagent/catalog` to `subagent.started`; `team/task` to
  `team.task`; `deliverables/presented` files and successful `write` tool
  results to `artifact.created` (paths only); successful `edit` results to an
  optional `workspace.changed` (paths and diff count, never contents).

These enrichment events feed Trace Explorer and the observed AIBOM. Messages,
todos and job bookkeeping are otherwise ignored. A per-session checkpoint
(`$SUGAR_DSH_STATE` or `~/.sugar/dsh-tail-state.json`) makes restarts resume
where they stopped; delete it to replay a session. Delivery is fail-open and
at-most-once per `(DSH sequence, canonical event)` — one raw log event can yield
several canonical events (e.g. a tool result plus an artifact), and a board
outage loses that cycle rather than retrying forever.

### `sugar hook`

The command Cursor and Codex hooks invoke:

```bash
sugar hook --harness cursor
```

Reads one hook JSON object from stdin, forwards it to the Unix socket (preferred)
or `POST /api/hooks/<harness>`, and **always exits 0** with no output. A stopped
board must never block a tool call. Set `$SUGAR_HOOK_DEBUG=1` to print delivery
details to stderr. `--socket`, `--url` and `$SUGAR_BOARD_URL` override the
defaults.

### `sugar doctor`

```bash
sugar doctor
sugar doctor --json
```

Checks `/api/health`, sends a uniquely tagged synthetic hook through each
installed adapter path (HTTP endpoint and/or Unix socket), verifies it appears
in `/api/activity`, and prints the coverage matrix. Exits 1 when the board is
unreachable or no installed adapter can deliver. This is the end-to-end check
after `sugar connect`.

---

## 6. Redaction and privacy

Hook payloads can contain commands with tokens, file contents and tool output.
Sugar is local-first, but that does not make unredacted capture safe.

- `$SUGAR_TELEMETRY_REDACTION` = `metadata` | `summary` | `full`
  (default `summary`).
  - `metadata`: tool name/status/timing only; arguments and results dropped.
  - `summary`: command names, file paths, short output/exit codes; secret-like
    values redacted.
  - `full`: payloads up to the size cap (use deliberately).
- `$SUGAR_TELEMETRY_MAX_BYTES` caps the redacted summary (default 8 KB).
- Secret-like patterns (API keys, bearer tokens, long hex/base64, private keys)
  are replaced with `[REDACTED]` before the event is written.
- The spine is local SQLite; hook/evidence endpoints are loopback-only unless
  the board is explicitly started in remote/token mode (see
  [board-server.md](board-server.md)).
- The redaction level applies at ingest; it is not retroactive. For regulated
  environments, use `metadata` for routine capture and `full` only for a
  bounded investigation.

Hooks themselves run with the user's privileges and receive raw payloads
before Sugar sees them. Treat the hook config as code, review it, and back it
up (Sugar writes `.bak` files).

---

## 7. SDK helpers (no code changes in the agent)

SDK-based agents can report through a small, dependency-free helper that posts
canonical events to `POST /api/events`. The helper builds the
`sugar.telemetry.v1` envelope and applies redaction client-side, so the
`/api/events` path does not depend on hook-format normalization. Delivery is
fail-open and bounded (1.5 s default, clamped 0.2–5 s).

### Google ADK

```python
from sugar.integrations.adk import SugarADKPlugin

app = App(name="my_app", root_agent=agent, plugins=[SugarADKPlugin()])
# or: adk web --extra_plugins=sugar.integrations.adk.SugarADKPlugin
```

Maps `before_tool_callback` / `after_tool_callback` / `on_tool_error_callback`
to `tool.started` / `tool.completed` / `tool.failed` (L3, harness `adk`).
`google-adk` is optional: importing the module works without it; instantiating
the plugin without the SDK raises a clear `ImportError`.

### OpenAI Agents SDK

```python
from sugar.integrations.openai_agents import install

install()               # add_trace_processor: keep existing processors
install(replace=True)   # set_trace_processors: no OpenAI trace egress
```

Maps `FunctionSpanData` → tool kinds, `GenerationSpanData` → `usage.reported`,
agent/turn spans → turn/run kinds. Tolerant of missing/renamed span fields.

### Claude Agent SDK

CLI users get hooks from `sugar connect` automatically. Programmatic embedders
can use:

```python
from claude_agent_sdk import ClaudeAgentOptions
from sugar.integrations.claude_sdk import make_hooks

options = ClaudeAgentOptions(hooks=make_hooks())
```

Callbacks always return `{}` and never block a tool call.

### Custom agents

```python
from sugar.integrations.client import SugarTelemetryClient

client = SugarTelemetryClient(harness="my-harness")
client.emit("tool.completed", tool_name="Bash", status="completed",
            fidelity="L3", summary={"command": "pytest -q", "exit_code": 0})
```

Set `$SUGAR_BOARD_URL` and (token mode) `$SUGAR_BOARD_TOKEN`; the client never
raises into the agent.

## 8. What Phase 1b does not cover yet

- **DSH live ACP events** — the session-tail adapter ships; ACP-managed runs
  (`dsh --profile acp`) are not consumed live yet. Desktop OTel export is
  feedback-gated; Sugar will not depend on it.
- **MCP proxy** — Phase 2 (captures MCP tool calls where harness hooks don't).
- **Remote/team** — the existing board token mode; hook and `/v1/*` endpoints
  inherit it.
- **Windows** — HTTP hooks are portable; Cursor/Codex/Goose command hooks go
  through their own shell handling. `sugar hook` is intentionally small and
  silent; the DSH LaunchAgent is macOS-only (manual tail elsewhere).

Until an adapter lands, Sugar still shows the harness at L0/L1 through the task
board and leases — the same fallback that makes Mission Control useful even
with no hooks installed.

---

## 9. Quick verification

```bash
# Terminal 1
sugar dashboard --port 7331

# Terminal 2
sugar connect --check
sugar connect --yes
sugar doctor

# Real Claude Code session in any repo (after connect): the Live floor and
# Activity feed should show tool calls; Coverage shows Claude Code at L3.

# Goose
sugar connect --harness goose --yes
# then start a goose session; Coverage shows Goose at L3.

# OTLP JSON (Goose/ADK/Codex/Claude OTel)
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:7331
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json

# SDK
python -c "from sugar.integrations.openai_agents import install; install()"

# DeepSeek Harness
sugar tail dsh                  # one scan
sugar tail dsh --watch          # or install the LaunchAgent:
sugar connect --harness dsh --yes
```

Manual test without a harness:

```bash
curl -s -X POST localhost:7331/api/hooks/claude-code \
  -H 'Content-Type: application/json' \
  -d '{"hook_event_name":"PostToolUse","session_id":"demo","tool_name":"Bash",
       "tool_input":{"command":"pytest -q"},"tool_response":{"exit_code":0},
       "tool_use_id":"toolu_demo"}'
curl -s localhost:7331/api/coverage
```

## Related

- [Telemetry](telemetry.md) — event kinds and producer conventions (`sugar emit`).
- [Mission Control](mission-control.md) — dashboard/app, Live floor, spine.
- [Local Dashboard API](local-dashboard.md) — frozen REST/SSE contract.
- [Board Server](board-server.md) — remote/token mode and TLS.
- [Audit exports](audit.md) — evidence out of the spine.
