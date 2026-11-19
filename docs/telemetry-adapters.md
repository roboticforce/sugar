# Telemetry adapters: how Sugar watches your agents

Sugar does not wrap, intercept or replace your coding agents. It gives each
harness a small, standard way to report what it is doing, normalizes those
reports into one canonical event, and stores them in the append-only event
spine next to task state. That is what makes Mission Control show live tool
calls, the Activity feed queryable, and usage/audit/trends possible across
different harnesses.

This document covers the **Phase 0** adapter surface: the canonical envelope,
fidelity levels, hook/evidence/socket ingestion, `sugar connect`, `sugar hook`,
`sugar doctor`, the coverage API/UI, redaction and current limitations.

> **Scope.** Phase 0 ships adapters for **Claude Code**, **Cursor** and
> **Codex**. Goose and DeepSeek Harness are detected but install-only in
> Phase 1 (Goose OTel/hooks, DSH session-tail/ACP). Until then they fall back to
> task/lease presence (L0/L1).

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

### 3.3 Unix ingest socket — `sugar hook` fast path

The dashboard listens on a Unix socket (default `~/.sugar/ingest.sock`,
`$SUGAR_INGEST_SOCKET` to override; `''` disables). It accepts newline-delimited
JSON:

```json
{"harness": "cursor", "body": {"hook_event_name": "postToolUse", "tool_name": "Shell", "tool_input": {"command": "npm test"}, "tool_use_id": "call_1"}}
```

Socket file mode is `0600`. It is the preferred transport for command hooks
(Cursor/Codex) because it avoids HTTP overhead. Events go through the same
normalization/redaction/dedupe pipeline as HTTP hooks.

### 3.4 Existing generic endpoints

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
| Goose | detected only (Phase 1) | — | L0 |
| DeepSeek Harness | detected only (Phase 1) | — | L0 |

- Existing config is preserved; a `.bak` backup is written next to the file.
- Installs are idempotent: re-running updates Sugar's entries and keeps
  unrelated hooks.
- The default board URL is `http://127.0.0.1:7331`; run the dashboard there.
- **Codex trust gate:** Codex hash-pins hooks. After install, open `codex`, run
  `/hooks`, and approve them. Sugar never bypasses this. For unattended
  automation only, Codex offers `--dangerously-bypass-hook-trust` — an explicit
  operator choice, not something Sugar enables.
- The registry (`~/.sugar/adapters.json`) is what the Coverage API reads.

A Claude Code plugin is also included at `integrations/claude-plugin/` for teams
that distribute hooks via the plugin marketplace instead of `sugar connect`.
Its `hooks/hooks.json` uses the same HTTP endpoint.

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

## 7. What Phase 0 does not cover yet

- **Goose** — OTel spans and hook plugins exist; adapter lands in Phase 1
  (OTLP receiver + hook plugin).
- **DeepSeek Harness** — session-log tail/ACP adapter in Phase 1. Desktop
  telemetry export is feedback-gated; Sugar will not depend on it.
- **ADK / OpenAI Agents SDK / Claude Agent SDK** — SDK/OTel adapters in
  Phase 1.
- **MCP proxy** — Phase 2 (captures MCP tool calls where harness hooks don't).
- **OTLP receiver** — Phase 1 (`/v1/traces`, `/v1/logs`).
- **Remote/team** — the existing board token mode; hook endpoints inherit it.
- **Windows** — HTTP hooks are portable; Cursor/Codex command hooks go through
  their own shell handling. `sugar hook` is intentionally small and silent.

Until an adapter lands, Sugar still shows the harness at L0/L1 through the task
board and leases — the same fallback that makes Mission Control useful even
with no hooks installed.

---

## 8. Quick verification

```bash
# Terminal 1
sugar dashboard --port 7331

# Terminal 2
sugar connect --check
sugar connect --yes
sugar doctor

# Real Claude Code session in any repo (after connect): the Live floor and
# Activity feed should show tool calls; Coverage shows Claude Code at L3.
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
