"""Idempotent per-harness installers for Phase-0 telemetry adapters.

Each installer merges *only* Sugar's hook entries into the harness's existing
config:

- preserves every other key/hook the user already has;
- backs the previous file up to ``<file>.bak`` before rewriting;
- writes atomically (temp file + ``os.replace``);
- is idempotent (running twice is a no-op the second time);
- never writes during ``dry_run`` (no files, no directories, no backups).

Install nothing when the config is unreadable/invalid JSON: raising
:class:`InstallError` is safer than clobbering a user's editor settings.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import detect
from .registry import RegistryError, atomic_write_json

#: Default board ingest base (override with ``$SUGAR_BOARD_URL``).
DEFAULT_BOARD_URL = "http://127.0.0.1:7331"

CLAUDE_HOOK_TIMEOUT = 3
COMMAND_HOOK_TIMEOUT = 5

#: Claude Code hook events Sugar subscribes to (HTTP transport, no subprocess).
CLAUDE_EVENTS: Tuple[str, ...] = (
    "SessionStart",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "SubagentStop",
)

#: Cursor/Codex hook events Sugar subscribes to (command transport via shim).
COMMAND_EVENTS: Tuple[str, ...] = (
    "sessionStart",
    "sessionEnd",
    "preToolUse",
    "postToolUse",
    "postToolUseFailure",
    "stop",
)

CLAUDE_SETTINGS_RELATIVE = (".claude", "settings.json")
CURSOR_HOOKS_RELATIVE = (".cursor", "hooks.json")
CODEX_HOOKS_RELATIVE = (".codex", "hooks.json")

#: Goose 1.46 discovers hook plugins at ``~/.agents/plugins/<name>/``.
GOOSE_PLUGIN_NAME = "sugar-telemetry"
GOOSE_PLUGIN_VERSION = "0.1.0"
GOOSE_PLUGIN_DESCRIPTION = (
    "Stream Goose hook telemetry (tool calls, sessions, turns) to the local "
    "Sugar board"
)
GOOSE_PLUGIN_RELATIVE = (".agents", "plugins", GOOSE_PLUGIN_NAME)
GOOSE_HOOK_TIMEOUT = 5

#: Goose hook events Sugar subscribes to (matcher is a regex; ``.*`` = all).
GOOSE_EVENTS: Tuple[str, ...] = (
    "SessionStart",
    "SessionEnd",
    "Stop",
    "UserPromptSubmit",
    "PreToolUse",
    "PreToolUseResult",
    "PostToolUse",
    "PostToolUseFailure",
    "BeforeReadFile",
    "AfterFileEdit",
    "BeforeShellExecution",
    "AfterShellExecution",
)


class InstallError(RuntimeError):
    """An adapter config could not be read/merged/written (never clobber)."""


@dataclass
class InstallResult:
    harness: str
    status: (
        str  # installed | updated | unchanged | would-install | would-update | skipped
    )
    config_path: Optional[Path] = None
    backup_path: Optional[Path] = None
    notes: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.status in ("installed", "updated", "would-install", "would-update")


def _home(home: Optional[Path]) -> Path:
    return Path(home).expanduser() if home is not None else Path.home()


def resolve_hook_base_url(explicit: Optional[str] = None) -> str:
    """Board base URL for the Claude HTTP hook (explicit → env → default)."""
    raw = explicit
    if raw is None or not str(raw).strip():
        raw = os.environ.get("SUGAR_BOARD_URL") or DEFAULT_BOARD_URL
    return str(raw).strip().rstrip("/")


def claude_hook_url(explicit_base: Optional[str] = None) -> str:
    return resolve_hook_base_url(explicit_base) + "/api/hooks/claude-code"


def hook_command(harness: str) -> str:
    """The command Cursor/Codex run for every hook payload."""
    return f"sugar hook --harness {harness}"


def claude_settings_path(home: Optional[Path] = None) -> Path:
    return _home(home).joinpath(*CLAUDE_SETTINGS_RELATIVE)


def cursor_hooks_path(home: Optional[Path] = None) -> Path:
    return _home(home).joinpath(*CURSOR_HOOKS_RELATIVE)


def codex_hooks_path(home: Optional[Path] = None) -> Path:
    return _home(home).joinpath(*CODEX_HOOKS_RELATIVE)


def goose_plugin_dir(home: Optional[Path] = None) -> Path:
    return _home(home).joinpath(*GOOSE_PLUGIN_RELATIVE)


def goose_plugin_json_path(home: Optional[Path] = None) -> Path:
    return goose_plugin_dir(home) / "plugin.json"


def goose_hooks_path(home: Optional[Path] = None) -> Path:
    return goose_plugin_dir(home) / "hooks" / "hooks.json"


def sugar_launcher() -> str:
    """Shell-quoted launcher for hook commands (installed CLI or ``-m`` fallback)."""
    binary = shutil.which("sugar")
    if binary:
        return shlex.quote(binary)
    return f"{shlex.quote(sys.executable)} -m sugar.main"


def goose_hook_command() -> str:
    """Absolute command Goose runs for every hook payload."""
    return f"{sugar_launcher()} hook --harness goose"


def config_path_for(harness: str, home: Optional[Path] = None) -> Optional[Path]:
    if harness == detect.CLAUDE_CODE:
        return claude_settings_path(home)
    if harness == detect.CURSOR:
        return cursor_hooks_path(home)
    if harness == detect.CODEX:
        return codex_hooks_path(home)
    if harness == detect.GOOSE:
        return goose_hooks_path(home)
    return None


# ---------------------------------------------------------------------------
# Merges (pure: no I/O, safe to unit-test)
# ---------------------------------------------------------------------------


def _has_claude_hook(entries: Any, hook_url: str) -> bool:
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if (
                isinstance(hook, dict)
                and str(hook.get("type", "")).lower() == "http"
                and str(hook.get("url", "")).rstrip("/") == hook_url.rstrip("/")
            ):
                return True
    return False


def merge_claude_settings(
    existing: Dict[str, Any],
    *,
    hook_url: Optional[str] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Merge Sugar's HTTP hooks into a ``~/.claude/settings.json`` object."""
    if not isinstance(existing, dict):
        raise InstallError("Claude settings must be a JSON object")
    url = hook_url or claude_hook_url()
    data = json.loads(json.dumps(existing))  # deep copy, JSON-safe
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
        data["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise InstallError("Claude settings 'hooks' must be a JSON object")

    changed = False
    for event in CLAUDE_EVENTS:
        entries = hooks.get(event)
        if entries is None:
            entries = []
            hooks[event] = entries
        if not isinstance(entries, list):
            raise InstallError(f"Claude settings 'hooks.{event}' must be an array")
        if _has_claude_hook(entries, url):
            continue
        entries.append(
            {
                "matcher": ".*",
                "hooks": [{"type": "http", "url": url, "timeout": CLAUDE_HOOK_TIMEOUT}],
            }
        )
        changed = True
    return data, changed


def _entry_has_command(entry: Any, command: str) -> bool:
    if isinstance(entry, dict):
        value = entry.get("command")
        return isinstance(value, str) and command in value
    if isinstance(entry, str):
        return command in entry
    return False


def merge_command_hooks(
    existing: Dict[str, Any],
    command: str,
    *,
    events: Tuple[str, ...] = COMMAND_EVENTS,
    timeout: int = COMMAND_HOOK_TIMEOUT,
) -> Tuple[Dict[str, Any], bool]:
    """Merge Sugar's command hooks into a Cursor/Codex ``hooks.json`` object."""
    if not isinstance(existing, dict):
        raise InstallError("hooks.json must be a JSON object")
    data = json.loads(json.dumps(existing))  # deep copy, JSON-safe
    data.setdefault("version", 1)
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
        data["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise InstallError("hooks.json 'hooks' must be a JSON object")

    changed = False
    for event in events:
        entries = hooks.get(event)
        if entries is None:
            entries = []
            hooks[event] = entries
        if not isinstance(entries, list):
            raise InstallError(f"hooks.json 'hooks.{event}' must be an array")
        if any(_entry_has_command(entry, command) for entry in entries):
            continue
        entries.append({"command": command, "timeout": timeout})
        changed = True
    return data, changed


def _is_sugar_goose_hook(hook: Any) -> bool:
    if not isinstance(hook, dict):
        return False
    return "hook --harness goose" in str(hook.get("command") or "")


def merge_goose_plugin(existing: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Ensure the minimal Goose plugin manifest, preserving other keys."""
    if not isinstance(existing, dict):
        raise InstallError("Goose plugin.json must be a JSON object")
    data = json.loads(json.dumps(existing))  # deep copy, JSON-safe
    wanted = (
        ("name", GOOSE_PLUGIN_NAME),
        ("version", GOOSE_PLUGIN_VERSION),
        ("description", GOOSE_PLUGIN_DESCRIPTION),
    )
    changed = False
    for key, value in wanted:
        # name/version identify Sugar's own plugin (upgrade them); description is
        # only filled when the user has not customised it.
        if data.get(key) != value and (key != "description" or not data.get(key)):
            data[key] = value
            changed = True
    return data, changed


def merge_goose_hooks(
    existing: Dict[str, Any],
    command: str,
    *,
    events: Tuple[str, ...] = GOOSE_EVENTS,
    timeout: int = GOOSE_HOOK_TIMEOUT,
) -> Tuple[Dict[str, Any], bool]:
    """Merge Sugar's plugin hooks into a Goose ``hooks.json`` object.

    Goose maps event names to an object with a regex ``matcher`` and a ``hooks``
    list. Existing users' hooks/keys are preserved; our command is added once
    and refreshed if the absolute ``sugar`` path changed (idempotent).
    """
    if not isinstance(existing, dict):
        raise InstallError("Goose hooks.json must be a JSON object")
    data = json.loads(json.dumps(existing))  # deep copy, JSON-safe
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
        data["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise InstallError("Goose hooks.json 'hooks' must be a JSON object")

    changed = False
    for event in events:
        entry = hooks.get(event)
        if entry is None:
            entry = {"matcher": ".*"}
            changed = True
        if not isinstance(entry, dict):
            raise InstallError(f"Goose hooks.json 'hooks.{event}' must be an object")
        if "matcher" not in entry:
            entry["matcher"] = ".*"
            changed = True
        hook_list = entry.get("hooks")
        if hook_list is None:
            hook_list = []
            entry["hooks"] = hook_list
            changed = True
        if not isinstance(hook_list, list):
            raise InstallError(
                f"Goose hooks.json 'hooks.{event}.hooks' must be an array"
            )
        matches = [hook for hook in hook_list if _is_sugar_goose_hook(hook)]
        if not matches:
            hook_list.append(
                {"type": "command", "command": command, "timeout": timeout}
            )
            changed = True
        else:
            for hook in matches:
                if hook.get("command") != command or hook.get("timeout") != timeout:
                    hook["command"] = command
                    hook["timeout"] = timeout
                    changed = True
        hooks[event] = entry
    return data, changed


def _install_goose(*, home: Optional[Path], dry_run: bool) -> InstallResult:
    plugin_path = goose_plugin_json_path(home)
    hooks_path = goose_hooks_path(home)
    existed_before = plugin_path.exists() or hooks_path.exists()

    plugin_existing, _ = _load_json_object(plugin_path)
    plugin_data, _ = merge_goose_plugin(plugin_existing)
    hooks_existing, _ = _load_json_object(hooks_path)
    command = goose_hook_command()
    hooks_data, _ = merge_goose_hooks(hooks_existing, command)

    plugin_status, plugin_backup = _write_merge(
        plugin_path, plugin_data, dry_run=dry_run
    )
    hooks_status, hooks_backup = _write_merge(hooks_path, hooks_data, dry_run=dry_run)

    statuses = {plugin_status, hooks_status}
    if dry_run:
        if statuses == {"unchanged"}:
            status = "unchanged"
        else:
            status = "would-update" if existed_before else "would-install"
    elif statuses == {"unchanged"}:
        status = "unchanged"
    else:
        status = "updated" if existed_before else "installed"

    return InstallResult(
        harness=detect.GOOSE,
        status=status,
        config_path=hooks_path,
        backup_path=hooks_backup or plugin_backup,
        notes=[
            f"Goose plugin: {goose_plugin_dir(home)}",
            f"command hook: {command}",
            "Goose does not await blocking work: `sugar hook` is bounded and "
            "fail-open, so a stopped board never stalls the agent",
        ],
    )


# ---------------------------------------------------------------------------
# File handling
# ---------------------------------------------------------------------------


def _load_json_object(path: Path) -> Tuple[Dict[str, Any], bool]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, False
    except OSError as exc:
        raise InstallError(f"cannot read {path}: {exc}") from exc
    if not raw.strip():
        return {}, True
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InstallError(
            f"refusing to modify {path}: it is not valid JSON ({exc})"
        ) from exc
    if not isinstance(data, dict):
        raise InstallError(f"refusing to modify {path}: expected a JSON object")
    return data, True


def _encode(data: Dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _write_merge(
    path: Path,
    data: Dict[str, Any],
    *,
    dry_run: bool,
) -> Tuple[str, Optional[Path]]:
    """Write *data* to *path* with a ``.bak`` backup; returns (status, backup)."""
    encoded = _encode(data)
    existed = path.exists()
    if existed:
        try:
            previous = path.read_text(encoding="utf-8")
            previous_mode: Optional[int] = path.stat().st_mode & 0o777
        except OSError as exc:
            raise InstallError(f"cannot read {path}: {exc}") from exc
        if previous == encoded:
            return "unchanged", None
    else:
        previous = None
        previous_mode = None

    status = (
        ("would-update" if existed else "would-install")
        if dry_run
        else ("updated" if existed else "installed")
    )
    if dry_run:
        return status, (path.with_name(path.name + ".bak") if existed else None)

    backup_path = path.with_name(path.name + ".bak")
    if existed and previous is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text(previous, encoding="utf-8")
            if previous_mode is not None:
                os.chmod(backup_path, previous_mode)
        except OSError as exc:
            raise InstallError(
                f"cannot back up {path} to {backup_path}: {exc}"
            ) from exc

    try:
        atomic_write_json(path, data)
        if previous_mode is not None:
            os.chmod(path, previous_mode)
    except RegistryError as exc:
        raise InstallError(str(exc)) from exc
    return status, (backup_path if existed else None)


# ---------------------------------------------------------------------------
# Public installers
# ---------------------------------------------------------------------------


def install(
    harness: str,
    *,
    home: Optional[Path] = None,
    dry_run: bool = False,
    hook_base_url: Optional[str] = None,
) -> InstallResult:
    """Install/merge the Sugar adapter for *harness* (idempotent)."""
    spec = detect.HARNESS_SPECS.get(harness)
    if spec is None:
        raise InstallError(f"unknown harness: {harness!r}")
    if not spec.installable:
        return InstallResult(
            harness=harness,
            status="skipped",
            config_path=None,
            notes=[spec.note or "detect-only in Phase 0"],
        )

    if harness == detect.GOOSE:
        return _install_goose(home=home, dry_run=dry_run)

    path = config_path_for(harness, home)
    assert path is not None  # installable harnesses always have a config path
    notes: List[str] = []
    if harness == detect.CODEX:
        notes.append(
            "Codex hooks are trust-gated: run `codex`, then `/hooks`, and approve "
            "Sugar's hooks. Sugar never bypasses that approval."
        )

    existing, _existed = _load_json_object(path)
    if harness == detect.CLAUDE_CODE:
        url = claude_hook_url(hook_base_url)
        merged, _changed = merge_claude_settings(existing, hook_url=url)
        notes.append(f"posts HTTP hooks to {url}")
    else:
        command = hook_command(harness)
        merged, _changed = merge_command_hooks(existing, command)
        notes.append(f"command hooks invoke: {command}")

    status, backup_path = _write_merge(path, merged, dry_run=dry_run)
    return InstallResult(
        harness=harness,
        status=status,
        config_path=path,
        backup_path=backup_path,
        notes=notes,
    )
