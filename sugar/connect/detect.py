"""Detect installed coding-agent harnesses on this machine.

Detection is deliberately read-only and *fast*: existence checks plus at most a
2-second ``<binary> --version`` call. Nothing here installs anything, touches
harness config, or raises on a missing/broken harness — a failed probe is just
"not detected".
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

DETECT_TIMEOUT_SECONDS = 2.0

#: Harness ids used everywhere (registry, hook endpoint, CLI).
CLAUDE_CODE = "claude-code"
CURSOR = "cursor"
CODEX = "codex"
GOOSE = "goose"
DSH = "dsh"


@dataclass(frozen=True)
class HarnessSpec:
    """Static Phase-0 capability description for one harness."""

    name: str
    display_name: str
    #: L3 = hook with tool args/results (HTTP hook or harness plugin hook),
    #: L2 = command hook via ``sugar hook`` (tool names/status),
    #: L1 = session-level only, L0 = detect-only (no telemetry path yet).
    fidelity: str
    transport: Optional[str]
    installable: bool
    note: str = ""


#: Ordered registry of known harnesses (display/capability source of truth).
HARNESS_SPECS: Dict[str, HarnessSpec] = {
    CLAUDE_CODE: HarnessSpec(
        name=CLAUDE_CODE,
        display_name="Claude Code",
        fidelity="L3",
        transport="http-hook",
        installable=True,
    ),
    CURSOR: HarnessSpec(
        name=CURSOR,
        display_name="Cursor",
        fidelity="L2",
        transport="command-hook",
        installable=True,
    ),
    CODEX: HarnessSpec(
        name=CODEX,
        display_name="Codex",
        fidelity="L2",
        transport="command-hook",
        installable=True,
        note="hooks are trust-gated: approve them in `codex` via /hooks",
    ),
    GOOSE: HarnessSpec(
        name=GOOSE,
        display_name="Goose",
        fidelity="L3",
        transport="hook-plugin",
        installable=True,
        note="plugin hooks at ~/.agents/plugins/sugar-telemetry",
    ),
    DSH: HarnessSpec(
        name=DSH,
        display_name="DeepSeek Harness",
        fidelity="L0",
        transport=None,
        installable=False,
        note="detected only; DSH adapter lands in Phase 1b",
    ),
}

HARNESS_NAMES = tuple(HARNESS_SPECS)
INSTALLABLE_HARNESSES = tuple(
    name for name, spec in HARNESS_SPECS.items() if spec.installable
)


@dataclass
class Detection:
    """Result of probing one harness (never raises)."""

    harness: str
    display_name: str
    detected: bool
    fidelity: str
    transport: Optional[str]
    installable: bool
    note: str = ""
    binary: Optional[str] = None
    version: Optional[str] = None
    evidence: List[str] = field(default_factory=list)


def _home(home: Optional[Path]) -> Path:
    return Path(home).expanduser() if home is not None else Path.home()


def _which(name: str, path: Optional[str]) -> Optional[str]:
    try:
        return shutil.which(name, path=path)
    except (OSError, ValueError):  # pragma: no cover - exotic PATH failures
        return None


def _binary_version(
    binary: str, *, timeout: float = DETECT_TIMEOUT_SECONDS
) -> Optional[str]:
    """Best-effort ``<binary> --version`` (2s cap); None on any failure."""
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=max(0.1, timeout),
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    output = (completed.stdout or completed.stderr or "").strip()
    if not output:
        return None
    first_line = output.splitlines()[0].strip()
    return first_line[:120] or None


def _candidate_paths(harness: str, home: Path) -> List[Path]:
    if harness == CLAUDE_CODE:
        return [home / ".claude"]
    if harness == CURSOR:
        return [home / ".cursor"]
    if harness == CODEX:
        return [home / ".codex"]
    if harness == GOOSE:
        return [home / ".config" / "goose"]
    if harness == DSH:
        return [
            home / "Applications" / "DeepSeek Harness.app",
            Path("/Applications/DeepSeek Harness.app"),
        ]
    return []


def _binary_name(harness: str) -> Optional[str]:
    return {
        CLAUDE_CODE: "claude",
        CURSOR: "cursor",
        CODEX: "codex",
        GOOSE: "goose",
        DSH: "dsh",
    }.get(harness)


def detect_harness(
    harness: str,
    *,
    home: Optional[Path] = None,
    path: Optional[str] = None,
    which: Optional[Callable[[str, Optional[str]], Optional[str]]] = None,
    get_version: Optional[Callable[[str], Optional[str]]] = None,
) -> Detection:
    """Probe one harness. ``home``/``path``/``which``/``get_version`` are
    injectable so tests (and Windows CI) never execute a real harness."""
    spec = HARNESS_SPECS.get(harness)
    if spec is None:
        return Detection(
            harness=harness,
            display_name=harness,
            detected=False,
            fidelity="L0",
            transport=None,
            installable=False,
            note="unknown harness",
        )

    resolved_home = _home(home)
    evidence: List[str] = []
    for candidate in _candidate_paths(harness, resolved_home):
        try:
            exists = candidate.exists()
        except OSError:  # pragma: no cover - permission edge
            exists = False
        if exists:
            evidence.append(str(candidate))

    bin_name = _binary_name(harness)
    binary: Optional[str] = None
    version: Optional[str] = None
    if bin_name:
        lookup = which or _which
        try:
            binary = lookup(bin_name, path)
        except Exception:  # pragma: no cover - injected callable failure
            binary = None
        if binary:
            evidence.append(f"{bin_name} in PATH")
            probe = get_version or _binary_version
            try:
                version = probe(binary)
            except Exception:  # pragma: no cover - injected callable failure
                version = None

    return Detection(
        harness=harness,
        display_name=spec.display_name,
        detected=bool(evidence),
        fidelity=spec.fidelity,
        transport=spec.transport,
        installable=spec.installable,
        note=spec.note,
        binary=binary,
        version=version,
        evidence=evidence,
    )


def detect_all(
    *,
    home: Optional[Path] = None,
    path: Optional[str] = None,
    which: Optional[Callable[[str, Optional[str]], Optional[str]]] = None,
    get_version: Optional[Callable[[str], Optional[str]]] = None,
) -> Dict[str, Detection]:
    """Detect every known harness, keyed by harness id (insertion order)."""
    return {
        harness: detect_harness(
            harness,
            home=home,
            path=path,
            which=which,
            get_version=get_version,
        )
        for harness in HARNESS_NAMES
    }
