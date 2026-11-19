"""Read/write the local adapter registry (``~/.sugar/adapters.json``).

The registry is how ``sugar connect``, ``sugar doctor`` and (later) the board's
``GET /api/coverage`` know which adapters this machine installed and at what
fidelity. Writes are atomic and the format is versioned::

    {"version": 1,
     "adapters": {
       "claude-code": {"display_name": "Claude Code", "installed": true,
                       "fidelity": "L3", "transport": "http-hook",
                       "installed_at": "2026-11-19T14:00:00Z",
                       "config_path": "/home/me/.claude/settings.json",
                       "adapter_version": "phase0", "notes": ""}
     }}

A missing or corrupt registry degrades to "no adapters installed" — it must
never crash ``sugar connect``/``sugar doctor``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

REGISTRY_VERSION = 1
ADAPTER_VERSION = "phase0"
DEFAULT_REGISTRY_PATH = ("~", ".sugar", "adapters.json")


class RegistryError(ValueError):
    """The registry could not be written (permissions, bad directory, ...)."""


def default_registry_path(home: Optional[Path] = None) -> Path:
    """Resolve the registry path (env overrides win over home).

    ``$SUGAR_ADAPTERS_PATH`` is Sugar's name; ``$SUGAR_ADAPTERS_FILE`` is
    honoured too because the Phase-0 coverage reader in ``sugar.telemetry``
    uses it. Both point at the same JSON file.
    """
    for variable in ("SUGAR_ADAPTERS_PATH", "SUGAR_ADAPTERS_FILE"):
        override = os.environ.get(variable, "").strip()
        if override:
            return Path(override).expanduser()
    base = Path(home).expanduser() if home is not None else Path.home()
    return base.joinpath(*DEFAULT_REGISTRY_PATH[1:])


def empty_registry() -> Dict[str, Any]:
    return {"version": REGISTRY_VERSION, "adapters": {}}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_registry(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the registry, returning an empty one when absent/corrupt."""
    registry_path = Path(path) if path is not None else default_registry_path()
    try:
        raw = registry_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_registry()
    except OSError:
        return empty_registry()
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return empty_registry()
    if not isinstance(data, dict):
        return empty_registry()
    adapters = data.get("adapters")
    if not isinstance(adapters, dict):
        adapters = {}
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        version = REGISTRY_VERSION
    # Keep unknown top-level keys so future versions can round-trip.
    result = dict(data)
    result["version"] = version
    result["adapters"] = adapters
    return result


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON atomically (temp file in the same dir + ``os.replace``)."""
    target = Path(path).expanduser()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    except OSError as exc:
        raise RegistryError(f"cannot prepare registry at {target}: {exc}") from exc
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(encoded)
        os.replace(temp, target)
    except OSError as exc:
        raise RegistryError(f"cannot write registry at {target}: {exc}") from exc
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:  # pragma: no cover - cleanup best effort
            pass


def save_registry(registry: Dict[str, Any], path: Optional[Path] = None) -> Path:
    target = Path(path) if path is not None else default_registry_path()
    atomic_write_json(target, registry)
    return target


def adapter_entry(
    display_name: str,
    *,
    installed: bool,
    fidelity: str,
    transport: Optional[str],
    config_path: Optional[str] = None,
    notes: str = "",
    installed_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build one registry adapter entry (the on-disk contract shape)."""
    return {
        "display_name": display_name,
        "installed": bool(installed),
        "fidelity": fidelity,
        "transport": transport,
        "installed_at": installed_at or utc_now_iso(),
        "config_path": config_path or "",
        "adapter_version": ADAPTER_VERSION,
        "notes": notes or "",
    }


def record_adapter(
    registry: Dict[str, Any],
    harness: str,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    """Upsert *entry* for *harness* in *registry* (in memory, returns registry)."""
    adapters = registry.setdefault("adapters", {})
    if not isinstance(adapters, dict):  # pragma: no cover - load normalizes
        adapters = {}
        registry["adapters"] = adapters
    adapters[harness] = entry
    registry["version"] = REGISTRY_VERSION
    return registry


def installed_adapters(registry: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Entries with ``installed: true`` (tolerant of malformed rows)."""
    adapters = registry.get("adapters") if isinstance(registry, dict) else None
    if not isinstance(adapters, dict):
        return {}
    return {
        harness: entry
        for harness, entry in adapters.items()
        if isinstance(entry, dict) and entry.get("installed") is True
    }
