# Sugar Telemetry — Claude Code plugin

Sends Claude Code hook payloads (session start, tool calls, stop, subagent stop)
to your **local** Sugar board at `http://127.0.0.1:7331/api/hooks/claude-code`.

- No subprocess per hook: Claude Code's native `http` hook transport posts the
  payload directly.
- Local-only by default: nothing leaves your machine unless you run the board
  behind a remote bind (see `docs/board-server.md`).
- Fail-open: if the board is down the hook times out (3s) and Claude Code
  continues; Sugar never blocks a tool call.

## Prerequisites

1. Sugar installed: `pip install 'sugarai[dashboard]'`
2. Board running: `sugar dashboard` (defaults to `127.0.0.1:7331`)
3. Recommended: `sugar connect --check` to confirm Claude Code is detected.

## Install

Once this plugin is published to a marketplace:

```text
/plugin install sugar-telemetry@roboticforce-sugar
```

Local development / manual use (no marketplace needed):

```bash
claude --plugin-dir /path/to/sugar/integrations/claude-plugin
```

Or, equivalently, let the Sugar CLI merge the same HTTP hooks into
`~/.claude/settings.json` (this is what `sugar connect` does):

```bash
sugar connect --harness claude-code --yes
```

## Verify

```bash
sugar doctor
```

Doctor posts a unique synthetic hook and checks that it shows up in
`/api/activity`, then prints `/api/coverage`. On a board without those Phase-0
endpoints yet, hooks still post and the board logs them when available.

## What is captured

The raw Claude Code hook payload for:

| Event | Why |
|---|---|
| `SessionStart` | run/session start |
| `PreToolUse` | tool chosen (name, input summary) |
| `PostToolUse` | tool result/outcome |
| `Stop` | run end |
| `SubagentStop` | subagent completion |

The board converts these into activity events (`run.started`, `tool.invoked`,
`run.ended`, …). Payloads stay on the event spine (`~/.sugar/events.db`) and are
subject to its retention policy.

## Configuration

The URL is fixed at `http://127.0.0.1:7331/api/hooks/claude-code`. If you run
the board on another port, either start it on 7331 or use `sugar connect`
with `$SUGAR_BOARD_URL` set (the CLI installer reads it).

## Uninstall

Remove the plugin through `/plugin`, or remove Sugar's hook entries from
`~/.claude/settings.json` if you installed through `sugar connect`. The
`settings.json.bak` written by the installer is the previous file.
