# Sugar Telemetry — Goose plugin

Reports Goose hook activity (sessions, turns, tool calls with arguments/results)
to your **local** Sugar board via `sugar hook --harness goose`.

Goose 1.46 discovers hook plugins at `~/.agents/plugins/<name>/`; this directory
is the source copy of the `sugar-telemetry` plugin.

## Install (recommended)

```bash
sugar connect --harness goose --yes
```

The installer writes `~/.agents/plugins/sugar-telemetry/{plugin.json,hooks/hooks.json}`
with the **absolute** path to your `sugar` executable (falling back to
`python -m sugar.main`), preserves any existing files (with `.bak` backups), and
is idempotent. It then records the adapter in `~/.sugar/adapters.json` as L3 /
`hook-plugin`.

## Install (manual)

Copy this directory to `~/.agents/plugins/sugar-telemetry/`, then edit the
`command` fields to point at your Sugar executable:

```json
{"type": "command", "command": "/absolute/path/to/sugar hook --harness goose", "timeout": 5}
```

## Verify

```bash
sugar doctor
```

## Notes

- Hooks are awaited by Goose, so the command is deliberately bounded: it posts
  to the local Unix ingest socket (preferred) or the board's
  `/api/hooks/goose`, and always exits 0 (fail-open). A stopped board never
  blocks the agent.
- The `matcher` field is a regex; `.*` means every tool.
- Telemetry stays on the local event spine (`~/.sugar/events.db`) subject to its
  retention policy.
