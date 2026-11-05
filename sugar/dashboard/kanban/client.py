"""Subprocess client for the canonical ``hermes kanban`` write path.

The dashboard never writes ``kanban.db`` directly: every mutation goes through
the Hermes CLI so claims, TTLs, event rows, dependencies and review gates keep
their one true implementation. See ``docs/local-dashboard.md`` §9.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from typing import Iterable, List, Optional, Sequence

_TASK_ID_RE = re.compile(r"\bt_[0-9a-f]{6,}\b")


class BoardError(Exception):
    """A ``hermes kanban`` call failed (or could not be started)."""

    def __init__(
        self, message: str, detail: str = "", returncode: Optional[int] = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.returncode = returncode


def resolve_hermes_bin(explicit: Optional[str] = None) -> Optional[str]:
    """Find the CLI: ``--hermes-bin``, ``$HERMES_BIN``, ``PATH``, then the
    Hermes install directory."""
    candidates = (
        explicit,
        os.environ.get("HERMES_BIN"),
        shutil.which("hermes"),
        os.path.expanduser("~/.hermes/hermes-agent/.hermes/bin/hermes"),
    )
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _joined(words: Sequence[str]) -> List[str]:
    return [str(word) for word in words if word is not None]


def build_create_args(
    title: str,
    *,
    body: Optional[str] = None,
    priority: Optional[int] = None,
    assignee: Optional[str] = None,
    project: Optional[str] = None,
    parents: Iterable[str] = (),
    created_by: str = "dashboard",
) -> List[str]:
    args = ["create", title, "--json", "--created-by", created_by]
    if body:
        args += ["--body", body]
    if priority is not None:
        args += ["--priority", str(priority)]
    if assignee:
        args += ["--assignee", assignee]
    if project:
        args += ["--project", project]
    for parent in parents:
        args += ["--parent", str(parent)]
    return args


def build_edit_args(
    task_id: str,
    *,
    title: Optional[str] = None,
    body: Optional[str] = None,
    priority: Optional[int] = None,
) -> List[str]:
    args = ["edit", task_id]
    if title is not None:
        args += ["--title", title]
    if body is not None:
        args += ["--body", body]
    if priority is not None:
        args += ["--priority", str(priority)]
    return args


def build_assign_args(
    task_id: str, profile: str, *, reassign: bool = False
) -> List[str]:
    command = "reassign" if reassign else "assign"
    return [command, task_id, profile or "none"]


def build_claim_args(task_id: str, ttl: Optional[int] = None) -> List[str]:
    args = ["claim", task_id]
    if ttl is not None:
        args += ["--ttl", str(int(ttl))]
    return args


def build_complete_args(
    task_ids: Sequence[str],
    *,
    result: Optional[str] = None,
    summary: Optional[str] = None,
) -> List[str]:
    args = ["complete", *_joined(task_ids)]
    if result:
        args += ["--result", result]
    if summary:
        args += ["--summary", summary]
    return args


def build_comment_args(
    task_id: str, text: str, *, author: Optional[str] = None
) -> List[str]:
    args = ["comment", task_id, text]
    if author:
        args += ["--author", author]
    return args


def build_block_args(
    task_id: str, *, reason: Optional[str] = None, kind: Optional[str] = None
) -> List[str]:
    args = ["block", task_id]
    if reason:
        args.append(reason)
    if kind:
        args += ["--kind", kind]
    return args


def build_unblock_args(
    task_ids: Sequence[str], *, reason: Optional[str] = None
) -> List[str]:
    args = ["unblock"]
    if reason:
        args += ["--reason", reason]
    args += _joined(task_ids)
    return args


def build_archive_args(task_ids: Sequence[str]) -> List[str]:
    return ["archive", *_joined(task_ids)]


def extract_task_id(output: str) -> Optional[str]:
    """Pull a task id out of ``create --json`` (or any human-readable output)."""
    text = (output or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("id", "task_id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        task = payload.get("task")
        if isinstance(task, dict) and isinstance(task.get("id"), str):
            return task["id"]
    match = _TASK_ID_RE.search(text)
    return match.group(0) if match else None


class HermesKanbanClient:
    """Small async wrapper around ``hermes kanban …`` (one call per mutation)."""

    def __init__(
        self,
        binary: Optional[str] = None,
        board: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self.binary = resolve_hermes_bin(binary)
        self.board = board
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def describe(self) -> dict:
        return {
            "write_backend": "hermes-kanban-cli",
            "hermes_bin": self.binary,
            "board_override": self.board,
        }

    def command(self, args: Sequence[str]) -> List[str]:
        if not self.binary:
            raise BoardError(
                "hermes CLI not found (set --hermes-bin, $HERMES_BIN, or PATH)"
            )
        command = [self.binary, "kanban"]
        if self.board:
            command += ["--board", self.board]
        command += list(args)
        return command

    async def run(self, args: Sequence[str]) -> str:
        """Run one ``hermes kanban`` invocation, returning stdout."""
        command = self.command(args)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise BoardError(f"could not start hermes: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise BoardError(
                f"hermes kanban timed out after {self.timeout:.0f}s",
                detail=" ".join(command[:4]),
            )

        out = (stdout or b"").decode("utf-8", "replace").strip()
        err = (stderr or b"").decode("utf-8", "replace").strip()
        if process.returncode != 0:
            detail = (err or out or f"exit {process.returncode}").strip()
            raise BoardError(
                "hermes kanban failed",
                detail=detail[:500],
                returncode=process.returncode,
            )
        return out

    # -- verbs -------------------------------------------------------------

    async def create(
        self,
        title: str,
        *,
        body: Optional[str] = None,
        priority: Optional[int] = None,
        assignee: Optional[str] = None,
        project: Optional[str] = None,
        parents: Iterable[str] = (),
        created_by: str = "dashboard",
    ) -> Optional[str]:
        output = await self.run(
            build_create_args(
                title,
                body=body,
                priority=priority,
                assignee=assignee,
                project=project,
                parents=parents,
                created_by=created_by,
            )
        )
        return extract_task_id(output)

    async def edit(
        self,
        task_id: str,
        *,
        title: Optional[str] = None,
        body: Optional[str] = None,
        priority: Optional[int] = None,
    ) -> None:
        await self.run(
            build_edit_args(task_id, title=title, body=body, priority=priority)
        )

    async def assign(
        self, task_id: str, profile: str, *, reassign: bool = False
    ) -> None:
        await self.run(build_assign_args(task_id, profile, reassign=reassign))

    async def claim(self, task_id: str, ttl: Optional[int] = None) -> None:
        await self.run(build_claim_args(task_id, ttl))

    async def complete(
        self,
        task_ids: Sequence[str],
        *,
        result: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> None:
        await self.run(build_complete_args(task_ids, result=result, summary=summary))

    async def comment(
        self, task_id: str, text: str, *, author: Optional[str] = None
    ) -> None:
        await self.run(build_comment_args(task_id, text, author=author))

    async def block(
        self, task_id: str, *, reason: Optional[str] = None, kind: Optional[str] = None
    ) -> None:
        await self.run(build_block_args(task_id, reason=reason, kind=kind))

    async def unblock(
        self, task_ids: Sequence[str], *, reason: Optional[str] = None
    ) -> None:
        await self.run(build_unblock_args(task_ids, reason=reason))

    async def archive(self, task_ids: Sequence[str]) -> None:
        await self.run(build_archive_args(task_ids))
