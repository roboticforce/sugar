"""CLI implementation for ``sugar tail dsh``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import click

from .parse import DshConfigError, normalize_since
from .tail import (
    DEFAULT_INTERVAL,
    DEFAULT_STATE,
    DshStateError,
    TailResult,
    default_state_path,
    make_emitter,
    tail_once,
    watch,
)

SUMMARY_PREFIX = "dsh tail:"


def format_summary(result: TailResult) -> str:
    """One-line cycle summary for humans."""
    parts = [
        f"{result.sessions} session{'s' if result.sessions != 1 else ''}",
        f"{result.events} event{'s' if result.events != 1 else ''} seen",
        f"{result.emitted} delivered",
    ]
    if result.skipped:
        parts.append(f"{result.skipped} skipped")
    if result.failed:
        parts.append(f"{result.failed} failed")
    return f"{SUMMARY_PREFIX} " + ", ".join(parts)


def _print_errors(result: TailResult, *, limit: int = 5) -> None:
    for message in result.errors[:limit]:
        click.echo(f"⚠️  {message}", err=True)
    if len(result.errors) > limit:
        click.echo(f"⚠️  ... {len(result.errors) - limit} more warning(s)", err=True)


def run_tail_dsh(
    *,
    watch_mode: bool = False,
    sessions_dir: Optional[str] = None,
    state_file: Optional[str] = None,
    interval: Optional[float] = None,
    url: Optional[str] = None,
    since: Optional[str] = None,
    quiet: bool = False,
) -> int:
    """Scan/tail DSH logs; returns a process exit code."""
    try:
        normalize_since(since)
    except DshConfigError as exc:
        click.echo(f"❌ {exc}", err=True)
        return 2

    directory = Path(sessions_dir).expanduser() if sessions_dir else None
    state_path = Path(state_file).expanduser() if state_file else default_state_path()
    emitter = make_emitter(board_url=url)

    if not watch_mode:
        try:
            result = tail_once(
                sessions_dir=directory,
                state_path=state_path,
                emit=emitter,
                since=since,
            )
        except DshConfigError as exc:  # no decompressor available
            click.echo(f"❌ {exc}", err=True)
            return 2
        except DshStateError as exc:
            click.echo(f"❌ {exc}", err=True)
            return 2
        if not quiet:
            click.echo(format_summary(result))
            _print_errors(result)
            if result.sessions == 0:
                click.echo(
                    f"   (no DSH sessions under {directory or '~/.dsh/sessions'})"
                )
        return 0

    delay = DEFAULT_INTERVAL if interval is None else max(0.2, float(interval))

    def on_cycle(cycle: TailResult) -> None:
        if quiet:
            return
        if cycle.events or cycle.errors:
            click.echo(format_summary(cycle))
            _print_errors(cycle)

    if not quiet:
        click.echo(
            f"{SUMMARY_PREFIX} watching {directory or '~/.dsh/sessions'} "
            f"every {delay:g}s (Ctrl-C to stop)"
        )
    try:
        totals = watch(
            sessions_dir=directory,
            state_path=state_path,
            since=since,
            interval=delay,
            emit=emitter,
            on_cycle=on_cycle,
        )
    except KeyboardInterrupt:
        if not quiet:
            click.echo(f"{SUMMARY_PREFIX} stopped")
        return 0
    except (DshConfigError, DshStateError) as exc:
        click.echo(f"❌ {exc}", err=True)
        return 2
    if not quiet:
        click.echo(format_summary(totals))
        _print_errors(totals)
    return 0


__all__ = ["DEFAULT_INTERVAL", "DEFAULT_STATE", "format_summary", "run_tail_dsh"]
