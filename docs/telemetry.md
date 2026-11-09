# Live agent telemetry (`sugar emit`, MCP tool)

Status: shipped. Related: [`local-dashboard.md`](./local-dashboard.md) §11
(event spine), [`board-server.md`](./board-server.md) (remote board + tokens),
[`audit.md`](./audit.md) (exports include ingested telemetry).

The dashboard and the macOS app can show *task state* from the queue, but "what
is the agent doing right now?" lives in the **event spine**
(`~/.sugar/events.db`) and streams to clients over SSE (`event: activity`).
`sugar emit` and the `sugar_emit_event` MCP tool are the producer side: one
command (or one tool call) per agent action, no Sugar project or database
access required.

They post to a running board's standard ingest endpoint:

```
POST <board>/api/events
Content-Type: application/json
Authorization: Bearer <token>      # only when the board has tokens
```

The server fills `id`, `ts`, `machine`, `source="ingest"` and `ingested_at`;
the event is appended to the spine and fanned out live to every `/api/events`
SSE subscriber. It shows up in the browser Activity view / live console, the
macOS Live floor, and `GET /api/activity` — and in **audit exports** of the
spine (telemetry is part of the record).

## 1. Event conventions

| kind | when | recommended payload |
|---|---|---|
| `tool.invoked` | a tool/command was invoked | `message`, `tool`, `command`, `file` |
| `run.started` | a harness/agent run begins | `message`, `model`, `harness` (top-level) |
| `run.ended` | a harness/agent run ends | `message`, `status`, `duration_s`, `tokens_in`, `tokens_out`, `cost_usd` |
| `agent.activity` | free-form progress / reasoning note | `message`, `step`, `file` |
| `usage.reported` | token/cost usage report | `tokens_in`, `tokens_out`, `cost_usd`, `model` |
| `memory.recalled` | memory was fetched | `message`, `count` |
| `signal.*` | external signal / alert | `message`, `severity` |

Only `kind` is required; these are conventions, not a schema. Unknown kinds are
accepted and stored (they just may not get a specific icon in the UI).

Recommended payload fields (all optional):

- `message` — one-line, human-readable description; this is what the Live floor
  and console show first.
- `tool`, `command`, `file` — for `tool.invoked`.
- `tokens_in`, `tokens_out`, `cost_usd`, `model` — for `usage.reported` /
  `run.ended`; the `/api/usage` aggregation (`docs/usage.md`) reads them.
- Keep payloads small (server limit: 32 KiB JSON); prefer one event per action
  over dumping large logs.

Top-level fields can be set with CLI flags / tool arguments: `actor`,
`actor_kind`, `harness`, `session_id`, `task_id`, `project`.

## 2. `sugar emit`

```
sugar emit [OPTIONS]
```

| option | default | meaning |
|---|---|---|
| `--kind` | `tool.invoked` | event kind |
| `--actor` | `$SUGAR_ACTOR` / `$SUGAR_AGENT_ID` / `$USER@host` | actor id |
| `--actor-kind` | inferred (`human`, `agent`, `system`) | actor kind |
| `--task-id` | – | related task id |
| `--harness` | `sugar` | harness/agent name |
| `--session-id` | `$SUGAR_SESSION` | session id |
| `--project` | – | project id |
| `--message` | – | shorthand for `payload.message` |
| `--payload KEY=VALUE` | – | extra payload field, repeatable; VALUE is JSON-decoded when possible (`n=3`, `ok=true`, `meta={"a":1}`) |
| `--server` | `$SUGAR_BOARD_URL` or `http://127.0.0.1:7331` | board base URL |
| `--token` | `$SUGAR_BOARD_TOKEN` | bearer token (never printed) |
| `--json` | off | print `{id, kind, task_id, actor}` |

Examples:

```bash
# One line per tool call from a harness hook
sugar emit --kind tool.invoked --task-id 9a88e031 \
  --message "running pytest tests/test_api.py" \
  --payload tool=bash --payload file=tests/test_api.py

# Run boundaries
sugar emit --kind run.started --harness claude-code --session-id abc123 \
  --message "starting run"
sugar emit --kind run.ended --harness claude-code --session-id abc123 \
  --message "run finished" --payload duration_s=412 --payload ok=true

# Usage/cost (also feeds `sugar usage` and /api/usage)
sugar emit --kind usage.reported --harness claude-code \
  --payload tokens_in=1200 --payload tokens_out=340 --payload cost_usd=0.03

# Free-form progress note tied to the task the app is showing
sugar emit --kind agent.activity --task-id 9a88e031 \
  --message "reading storage/work_queue.py"

# Remote board with tokens
SUGAR_BOARD_URL=http://board.lan:7331 SUGAR_BOARD_TOKEN=... \
  sugar emit --kind tool.invoked --message "running tests"
```

Behaviour:

- Success prints the stored event id (or JSON with `--json`), exit `0`.
- Bad CLI input (malformed `--payload`, empty `--kind`, invalid `--server`, bad
  `--actor-kind`) exits `2` without making a request.
- Network/HTTP failures (connection refused, timeout, 401/4xx/5xx) print one
  clear line and exit `1`. The HTTP timeout is 5 s so hooks never hang.
- The token comes only from `--token` or `$SUGAR_BOARD_TOKEN`; it is sent as an
  `Authorization: Bearer` header and never appears in output, errors or logs.
  Request bodies are not logged by Sugar.
- `--message` wins over a `payload.message` supplied via `--payload`.

Environment defaults:

| env | meaning |
|---|---|
| `SUGAR_BOARD_URL` | board base URL (`http://127.0.0.1:7331`) |
| `SUGAR_BOARD_TOKEN` | bearer token for a token-protected board |
| `SUGAR_ACTOR` | actor id override |
| `SUGAR_AGENT_ID` | agent id (implies `actor_kind=agent` when no explicit actor) |
| `SUGAR_ACTOR_KIND` | `human` / `agent` / `system` |
| `SUGAR_SESSION` | session id |
| `SUGAR_HARNESS` | default harness for the MCP tool (`mcp` if unset) |

Exit codes: `0` emitted, `1` board unreachable/rejected, `2` bad CLI input.

## 3. MCP tool

The Sugar task MCP server (`sugar mcp tasks`) also exposes:

```python
sugar_emit_event(
    kind: str,
    message: str | None = None,
    task_id: str | None = None,
    payload: dict | None = None,
    actor: str | None = None,
) -> dict
```

- Posts the same ingest body to `$SUGAR_BOARD_URL` (default
  `http://127.0.0.1:7331`) with `$SUGAR_BOARD_TOKEN` as a bearer token when set.
- `payload` is merged with `message` (`message` wins); `actor` defaults to the
  MCP agent identity (`$SUGAR_ACTOR` / `$SUGAR_AGENT_ID` / `$USER@host`,
  kind `agent`), harness defaults to `$SUGAR_HARNESS` or `mcp`.
- Never raises: returns `{"ok": true, "id": <event id>}` or
  `{"ok": false, "error": "<safe message>"}`. The token is never returned.

Example (from an MCP client / agent):

```json
{
  "tool": "sugar_emit_event",
  "arguments": {
    "kind": "tool.invoked",
    "task_id": "9a88e031-a2ae-4db1-b329-72f7b18ad4f7",
    "message": "running pytest tests/test_api.py",
    "payload": {"tool": "bash", "file": "tests/test_api.py"}
  }
}
```

## 4. Wiring a harness

The producer is intentionally a stateless one-shot command, so any harness that
can run a shell command can stream activity:

**One command per tool call** (Claude Code hooks, OpenCode hooks, DSH hooks):

```bash
# e.g. a PostToolUse hook with $TOOL_NAME / $COMMAND / $TASK_ID available
sugar emit --kind tool.invoked --task-id "$SUGAR_TASK_ID" \
  --message "$TOOL_NAME: $COMMAND" \
  --payload tool="$TOOL_NAME" --payload command="$COMMAND"
```

**A small wrapper loop** for a long-running agent process:

```bash
run_step() {  # run_step <message> <command...>
  sugar emit --kind tool.invoked --message "$1" --payload command="$2" || true
  shift
  "$@"
}
run_step "running tests" pytest -q
run_step "formatting" black sugar/
sugar emit --kind run.ended --message "run finished" --payload duration_s="$SECONDS"
```

**Usage reporting** for clients that know their token/cost numbers: emit
`usage.reported` (or include `tokens_in`/`tokens_out`/`cost_usd` in
`run.ended`); `sugar usage` and `GET /api/usage` aggregate them.

Emission is best-effort: a failed `sugar emit` should never fail the agent's
actual work. Use `|| true` in hooks (the command already exits non-zero on a
down board, with a one-line reason).

## 5. Where the events land, and what they are not

- **Spine**: appended to `~/.sugar/events.db` (default). Retention applies:
  raw events are pruned after `$SUGAR_EVENTS_RETENTION_DAYS` (default 30; `0`
  disables). If you need durable evidence, run `sugar audit export` on a
  schedule (see `docs/audit.md`); telemetry events are included in exports.
- **Live SSE**: every appended event is fanned out as `event: activity,
  data: <Event>` on `/api/events`; the dashboard Activity view/live console and
  the macOS Live floor consume it. Presence (`docs/local-dashboard.md` §11.5)
  is a separate ephemeral heartbeat and is never stored.
- **Not** a metrics backend: no sampling, rollups or provider billing
  reconciliation; cost numbers are whatever the harness reports. See
  `docs/usage.md` for the aggregation rules and their limits.

## 6. Security and privacy

- Events contain whatever you put in `message`/`payload`: **do not emit
  secrets**, tokens, credentials, full file contents or customer data. Treat
  the spine and audit exports as sensitive logs.
- On a token-protected board (`docs/board-server.md`), emit needs a `write`
  token (`$SUGAR_BOARD_TOKEN` / `--token`); the token is never printed or
  logged by the CLI or MCP tool.
- A remote plain-HTTP board sends token + payload in cleartext on the network;
  terminate TLS in a reverse proxy for untrusted networks (see
  `docs/board-server.md`).
- The ingest endpoint rejects when the spine is disabled on the server
  (`503 spine_unavailable`); `sugar emit` reports that as a clear error (exit 1).
