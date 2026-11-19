"""Implementations behind the ``sugar connect`` and ``sugar doctor`` commands.

Kept out of ``sugar/main.py`` so the Click entrypoint stays declarative and the
matrix/diagnostic rendering is unit-testable without a runner.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import click

from . import detect, registry
from .doctor import DoctorResult, run_doctor
from .installers import InstallError, install

MATRIX_HEADERS = ("HARNESS", "DETECTED", "FIDELITY", "TRANSPORT", "ACTION")


@dataclass
class ConnectRow:
    harness: str
    display_name: str
    detected: bool
    fidelity: str
    transport: str
    action: str
    note: str = ""


@dataclass
class ConnectOutcome:
    exit_code: int
    rows: List[ConnectRow] = field(default_factory=list)
    installed: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)


def build_rows(
    detections: Dict[str, detect.Detection],
    reg: Dict[str, Any],
) -> List[ConnectRow]:
    """Build the harness/detected/fidelity/action matrix (no side effects)."""
    adapters = reg.get("adapters") if isinstance(reg.get("adapters"), dict) else {}
    rows: List[ConnectRow] = []
    for harness in detect.HARNESS_NAMES:
        detection = detections[harness]
        entry = adapters.get(harness) if isinstance(adapters, dict) else None
        installed = isinstance(entry, dict) and entry.get("installed") is True
        fidelity = (
            str(entry.get("fidelity"))
            if installed and entry.get("fidelity")
            else detection.fidelity
        )
        transport = (
            str(entry.get("transport"))
            if installed and entry.get("transport")
            else (detection.transport or "")
        )
        if not detection.detected:
            action = "skip (not detected)"
            note = ""
        elif not detection.installable:
            action = "detect-only (phase 1)"
            note = detection.note
        elif installed:
            action = "keep (installed)"
            note = ""
        else:
            action = "install"
            note = ""
        rows.append(
            ConnectRow(
                harness=harness,
                display_name=detection.display_name,
                detected=detection.detected,
                fidelity=fidelity or "-",
                transport=transport or "-",
                action=action,
                note=note,
            )
        )
    return rows


def format_matrix(rows: Sequence[ConnectRow]) -> str:
    """Render the connect matrix with plain-text alignment (no dependencies)."""
    cells: List[List[str]] = []
    for row in rows:
        cells.append(
            [
                row.harness,
                "yes" if row.detected else "no",
                row.fidelity,
                row.transport,
                row.action,
            ]
        )
    widths = [
        max([len(MATRIX_HEADERS[i])] + [len(row[i]) for row in cells])
        for i in range(len(MATRIX_HEADERS))
    ]
    lines = ["  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(MATRIX_HEADERS))]
    lines.append("  " + "  ".join("-" * width for width in widths))
    for row in cells:
        lines.append(
            "  "
            + "  ".join(row[i].ljust(widths[i]) for i in range(len(MATRIX_HEADERS)))
        )
    return "\n".join(lines)


def _codex_trust_lines() -> List[str]:
    return [
        "",
        "⚠️  Codex hooks are trust-gated.",
        "    Open `codex`, run `/hooks`, and approve the Sugar hooks. Sugar never",
        "    bypasses this approval.",
        "    For unattended automation only, "
        "`codex --dangerously-bypass-hook-trust`",
        "    exists — that is your explicit choice, not something Sugar enables.",
    ]


def _windows_lines(harnesses: Iterable[str]) -> List[str]:
    command_harnesses = [
        harness for harness in harnesses if harness in (detect.CURSOR, detect.CODEX)
    ]
    if os.name != "nt" or not command_harnesses:
        return []
    return [
        "",
        "ℹ️  Windows: command hooks invoke `sugar` from PATH. If Sugar was installed",
        "    with pip, run `sugar --version` once in the same shell (or use the",
        "    full path in the harness config) before relying on the hooks.",
    ]


def _save_registry_with_backup(reg: Dict[str, Any], registry_path: Path) -> None:
    if registry_path.exists():
        try:
            shutil.copy2(
                registry_path, registry_path.with_name(registry_path.name + ".bak")
            )
        except OSError:  # pragma: no cover - best effort backup
            pass
    registry.save_registry(reg, registry_path)


def run_connect(
    *,
    check: bool = False,
    dry_run: bool = False,
    harnesses: Sequence[str] = (),
    yes: bool = False,
    home: Optional[Path] = None,
    registry_path: Optional[Path] = None,
    path: Optional[str] = None,
) -> ConnectOutcome:
    """Detect, plan, optionally install adapters and record the registry."""
    detections = detect.detect_all(home=home, path=path)
    reg_path = (
        Path(registry_path).expanduser()
        if registry_path is not None
        else registry.default_registry_path(home=home)
    )
    reg = registry.load_registry(reg_path)
    rows = build_rows(detections, reg)
    requested = list(dict.fromkeys(harnesses))

    targets: List[str] = []
    if requested:
        for harness in requested:
            detection = detections[harness]
            if not detection.detected:
                continue
            if not detection.installable:
                continue
            targets.append(harness)
    else:
        targets = [
            harness
            for harness in detect.INSTALLABLE_HARNESSES
            if detections[harness].detected
        ]

    if requested:
        for row in rows:
            if row.harness in requested and row.harness not in targets:
                if not detections[row.harness].detected:
                    row.action = "skip (not detected)"
                elif not detections[row.harness].installable:
                    row.action = "detect-only (phase 1)"

    echo = click.echo
    echo("Sugar connect — harness telemetry adapters")
    echo()
    echo(format_matrix(rows))
    echo()
    for row in rows:
        if row.note:
            echo(f"  {row.harness}: {row.note}")

    detected_installable = [
        harness
        for harness in detect.INSTALLABLE_HARNESSES
        if detections[harness].detected
    ]
    if check:
        echo()
        echo("(--check: detection only; no files were written)")
        _print_trust_and_windows(rows, detected_installable)
        return ConnectOutcome(
            exit_code=0,
            rows=rows,
            skipped=[row.harness for row in rows if not row.detected],
        )

    if not targets:
        echo()
        echo("No supported harness detected (or selected). Nothing to install.")
        echo("Install a supported agent, or pass --harness to target one explicitly.")
        _print_trust_and_windows(rows, detected_installable)
        return ConnectOutcome(exit_code=0, rows=rows)

    plan = [row for row in rows if row.harness in targets]
    echo()
    echo("Plan:")
    for row in plan:
        verb = "would install" if dry_run else row.action
        echo(f"  • {row.harness:<12} {verb}")
    if dry_run:
        echo()
        echo("(--dry-run: no files were written)")
        _print_trust_and_windows(rows, targets)
        return ConnectOutcome(exit_code=0, rows=rows, skipped=[])

    if not yes:
        if not click.confirm("\nApply these changes?", default=True):
            echo("Nothing changed.")
            _print_trust_and_windows(rows, targets)
            return ConnectOutcome(exit_code=0, rows=rows)

    outcome = ConnectOutcome(exit_code=0, rows=rows)
    for harness in targets:
        try:
            result = install(harness, home=home, dry_run=False)
        except InstallError as exc:
            outcome.failed.append(harness)
            echo(f"  ❌ {harness}: {exc}")
            continue
        if result.status == "skipped":
            outcome.skipped.append(harness)
            echo(f"  • {harness}: skipped ({'; '.join(result.notes)})")
            continue
        spec = detect.HARNESS_SPECS[harness]
        entry = registry.adapter_entry(
            spec.display_name,
            installed=True,
            fidelity=spec.fidelity,
            transport=spec.transport,
            config_path=str(result.config_path) if result.config_path else "",
            notes=" ".join(result.notes),
        )
        registry.record_adapter(reg, harness, entry)
        outcome.installed.append(harness)
        detail = f" → {result.config_path}" if result.config_path else ""
        backup = f" (backup: {result.backup_path})" if result.backup_path else ""
        icon = "✅" if result.status in ("installed", "updated") else "•"
        echo(f"  {icon} {harness}: {result.status}{detail}{backup}")

    if outcome.installed:
        try:
            _save_registry_with_backup(reg, reg_path)
            echo(f"  📝 registry: {reg_path}")
        except registry.RegistryError as exc:
            outcome.failed.append("registry")
            outcome.exit_code = 1
            echo(f"  ❌ registry: {exc}")
    if outcome.failed:
        outcome.exit_code = 1
        echo(f"\n❌ {len(outcome.failed)} failure(s): {', '.join(outcome.failed)}")
    else:
        echo("\n✅ Connect complete.")
    _print_trust_and_windows(rows, targets)
    return outcome


def _print_trust_and_windows(
    rows: Sequence[ConnectRow], considered: Iterable[str]
) -> None:
    considered = list(considered)
    if detect.CODEX in considered:
        for line in _codex_trust_lines():
            click.echo(line)
    for line in _windows_lines(considered):
        click.echo(line)


# ---------------------------------------------------------------------------
# Doctor rendering
# ---------------------------------------------------------------------------


def run_doctor_command(
    *,
    server: Optional[str] = None,
    socket_path: Optional[str] = None,
    registry_path: Optional[Path] = None,
    home: Optional[Path] = None,
    json_output: bool = False,
) -> int:
    result: DoctorResult = run_doctor(
        server=server,
        socket_path=socket_path,
        registry_path=registry_path,
        home=home,
    )
    if json_output:
        click.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        return 0 if result.ok else 1

    click.echo("Sugar doctor — telemetry path check")
    click.echo(f"  Board: {result.server}")
    click.echo()
    for check in result.checks:
        icon = "✅" if check.ok else "❌"
        line = f"  {icon} {check.name}"
        if check.detail:
            line += f": {check.detail}"
        click.echo(line)
    if result.adapters:
        click.echo()
        click.echo("  Adapters:")
        for probe in result.adapters:
            verification = {
                True: "verified in activity",
                False: "NOT verified",
                None: "unverified (no activity endpoint)",
            }[probe.verified]
            delivered = "delivered" if probe.delivered else "FAILED"
            click.echo(
                f"    • {probe.harness:<12} {delivered} via "
                f"{probe.transport or 'unknown'} — {verification}"
            )
    coverage = result.coverage
    if isinstance(coverage, dict):
        click.echo()
        lines = format_coverage(coverage)
        for line in lines:
            click.echo(line)
    else:
        click.echo()
        click.echo("  Coverage: /api/coverage unavailable on this board.")
    click.echo()
    click.echo("✅ doctor passed" if result.ok else "❌ doctor failed")
    return 0 if result.ok else 1


def format_coverage(coverage: Dict[str, Any]) -> List[str]:
    """Render the frozen ``GET /api/coverage`` shape as short lines."""
    lines = ["  Coverage:"]
    summary = coverage.get("summary")
    if isinstance(summary, dict):
        for level in ("L3", "L2", "L1", "L0"):
            harnesses = summary.get(level)
            if isinstance(harnesses, list) and harnesses:
                lines.append(
                    f"    {level}: {', '.join(str(item) for item in harnesses)}"
                )
    rows = coverage.get("harnesses")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("harness") or "?"
            installed = "installed" if row.get("installed") else "not installed"
            events = row.get("events_24h")
            suffix = f", {events} events/24h" if events is not None else ""
            lines.append(
                f"    • {name:<12} {installed}, fidelity "
                f"{row.get('fidelity') or '-'}{suffix}"
            )
    return lines
