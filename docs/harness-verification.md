# Verifying real harness integrations

Sugar ships adapters for several harnesses, but adapters are only useful if the
real CLI still reads the config, invokes the hook and delivers an event. Unit
tests use fixtures; this page describes the **real integration check** that runs
against the harnesses installed on a machine.

```bash
python scripts/verify_harnesses.py          # config + ingest checks
python scripts/verify_harnesses.py --real   # also attempt small live model calls
python scripts/verify_harnesses.py --json
```

The script is read-only with respect to your real configuration: it creates a
throwaway `HOME`, a throwaway board, and a throwaway project, then stops
everything. It never edits your actual Claude/Codex/Goose/DSH config, and Hermes
Kanban is served read-only.

## What it checks

| Harness | Config check | Ingest check | Live check (`--real`) |
|---|---|---|---|
| **Claude Code** | HTTP hooks in `~/.claude/settings.json` | `POST /api/hooks/claude-code` → L3 `tool.completed` | `claude -p` (SKIP if auth expires) |
| **Codex** | command hooks in `~/.codex/hooks.json` | `POST /api/hooks/codex` → L3 event | `codex exec` (SKIP if auth expires) |
| **Goose** | plugin + `hooks/hooks.json` array shape | `POST /api/hooks/goose` → L3 event | `goose run` (SKIP if provider fails) |
| **Cursor** | `~/.cursor/hooks.json` if CLI installed | `POST /api/hooks/cursor` → L2/L3 event | — |
| **OpenCode** | `sugar-tasks`/`sugar-memory` MCP config | MCP servers load in a real project | live `opencode run` calls `sugar_list_tasks` |
| **Hermes Kanban** | existing `~/.hermes/kanban.db` | board serves `/api/meta` + `/api/tasks` read-only | — |
| **DSH** | session logs under `~/.dsh/sessions` | `sugar tail dsh` replays real logs → canonical events | — |

## Status meanings

- **PASS** — the check ran and produced the expected result. For config checks
  this means the harness accepted the file **and** Sugar normalized a
  representative payload into the spine; for OpenCode `--real` it means a real
  model turn called the Sugar MCP tool.
- **SKIP** — the harness is not installed, or the live run needs model auth
  that is unavailable. A SKIP is **not** a failure and is reported with the
  reason (e.g. `OAuth session expired`).
- **FAIL** — config was not written, the hook shape was wrong, the endpoint
  rejected the payload, or a live capability that should work did not.

The script exits non-zero only on FAIL, so it can be used as a release gate on a
machine that has the harnesses/credentials configured.

## Current result (2026-11-21, macOS)

```text
HARNESS        STATUS DETAIL
claude-code    PASS   HTTP hooks installed + L3 ingest verified
codex          PASS   command hooks installed + L3 ingest verified (Codex still requires `/hooks` approval)
goose          PASS   hook plugin installed (array shape) + L3 ingest verified
cursor         SKIP   cursor CLI not installed (config path untested)
dsh            PASS   real session log tailed into canonical events
hermes         PASS   served real Hermes Kanban read-only (3 tasks)
opencode       PASS   live OpenCode called sugar_list_tasks via MCP
claude-code    SKIP   live run unavailable (auth)
codex          SKIP   live run unavailable (auth)
goose          SKIP   live run unavailable (provider/auth)
6 passed · 4 skipped · 0 failed
```

This is the honest split:

- **Proven end to end**: OpenCode (real MCP tool call), Hermes Kanban (real
  board), DSH (real session log), and the hook/ingest path for
  Claude/Codex/Goose.
- **Not proven live here**: Claude Code and Codex model runs (both OAuth
  sessions expired on this machine), Goose model runs (provider routing error).
  Their adapters installed and their payloads normalized correctly; a logged-in
  machine should report PASS for those live rows.
- **Not installed**: Cursor CLI.

## Why this is not a CI job

Real harnesses require binaries, user config and model credentials. CI runs the
deterministic adapter tests (`tests/test_connect.py`, `test_goose_adapter.py`,
`test_telemetry_ingest.py`, `test_otlp_ingest.py`, `test_dsh_tail.py`), while
this script is the manual/real-machine layer. It is also useful for support:
when a harness changes its config format, this is the first thing to run.

## Adding a harness

1. Add a `check_<harness>` function to `scripts/verify_harnesses.py`.
2. Install its adapter into the throwaway HOME and assert the expected config
   file exists and contains Sugar's hook/MCP entry.
3. Post a representative payload through the real ingestion path and assert a
   canonical event appears in `/api/activity`.
4. Add a live probe under `--real` if credentials can be detected; report SKIP
   rather than FAIL when auth is missing.
5. Update the table above with what PASS actually proves.

## Related

- [Telemetry adapters](telemetry-adapters.md) — schemas, fidelity, adapters,
  `sugar connect` / `sugar hook` / `sugar doctor`.
- [Adapters playbook](adapters.md) — adapter taxonomy and conformance rules.
- [Board server](board-server.md) — remote/token mode used by MCP and hooks.
