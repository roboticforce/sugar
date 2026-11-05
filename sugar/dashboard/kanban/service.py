"""Dashboard service backed by a Hermes Kanban board.

Same HTTP surface as :class:`sugar.dashboard.service.DashboardService`: reads are
served from a read-only SQLite connection, writes are delegated to the canonical
``hermes kanban`` CLI. See ``docs/local-dashboard.md`` §9.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ...__version__ import __version__ as SUGAR_VERSION
from ...identity import Actor, normalize_actor_id
from ..events import DashboardState, EventBroadcaster
from ..service import (
    DashboardError,
    _not_found,
    _parse_query_int,
    _validate_optional_str,
    _validate_priority,
    _validation,
    resolve_request_actor,
)
from . import mapping
from .client import BoardError, HermesKanbanClient
from .store import KanbanStore

logger = logging.getLogger(__name__)

CREATE_FIELDS = frozenset(
    {
        "title",
        "type",
        "description",
        "priority",
        "assigned_agent",
        "blocked_by",
        # Provenance (§10.4): resolved through resolve_request_actor(); the
        # X-Sugar-Actor headers win over the body, same as the Sugar source.
        "created_by",
        "created_by_kind",
    }
)
PATCH_FIELDS = frozenset({"title", "description", "priority", "assigned_agent"})
SORT_ORDERS = ("created_at_desc", "priority")
BLOCK_KINDS = ("capability", "dependency", "needs_input", "transient")


def _require_nonempty_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _validation(f"{field} is required and must be a non-empty string")
    return value.strip()


class KanbanService:
    """Owns the Kanban store, snapshot state and the Hermes CLI client."""

    board = "kanban"

    def __init__(
        self,
        db_path: str,
        *,
        read_only: bool = False,
        poll_interval: float = 0.4,
        hermes_bin: Optional[str] = None,
        kanban_board: Optional[str] = None,
        actor: Optional[str] = None,
    ):
        self.db_path = db_path
        self.read_only = read_only
        # Installation-level default actor; HTTP headers / body and finally
        # sugar.identity.resolve_actor() take precedence (docs §10.4).
        self.actor = actor
        self.store = KanbanStore(db_path)
        self.broadcaster = EventBroadcaster()
        self.state = DashboardState(self.store, self.broadcaster, poll_interval)
        self.client = HermesKanbanClient(hermes_bin, kanban_board)
        self.spine = None  # optional EventSpine, wired by create_app
        self._observer = None
        self.started_at: Optional[float] = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        await self.state.start()
        self.started_at = self.state.started_at
        if self.spine is not None:
            from ..observers import KanbanObserver

            self._observer = KanbanObserver(
                self.store, self.spine, broadcaster=self.broadcaster
            )
            await self._observer.start()

    async def stop(self) -> None:
        if self._observer is not None:
            observer, self._observer = self._observer, None
            await observer.stop()
        await self.state.stop()

    # -- helpers -----------------------------------------------------------

    def ensure_writable(self) -> None:
        if self.read_only:
            raise DashboardError(403, "read_only")

    def health(self) -> Dict[str, Any]:
        uptime = 0.0
        if self.started_at is not None:
            uptime = round(max(0.0, time.monotonic() - self.started_at), 2)
        payload = {
            "ok": True,
            "version": SUGAR_VERSION,
            "db": self.db_path,
            "read_only": self.read_only,
            "uptime_s": uptime,
            "board": self.board,
        }
        payload.update(self.client.describe())
        return payload

    def meta(self) -> Dict[str, Any]:
        return {
            "board": self.board,
            "title": "Hermes Kanban",
            "db": self.db_path,
            "statuses": list(mapping.KANBAN_STATUSES),
            "facet": {"label": "Project"},
            "capabilities": {
                "create": True,
                "edit": True,
                "edit_status": False,
                "delete": True,
                "claim": True,
                "complete": True,
                "block": True,
                "unblock": True,
                "assign": True,
                "comment": True,
            },
        }

    async def _refresh_and_get(self, task_id: str) -> Dict[str, Any]:
        # Always re-read the single task: the snapshot omits activity, and every
        # write response includes it (comments/runs/events) for the detail drawer.
        await self.state.refresh()
        task = await self.store.fetch_task(task_id)
        if task is None:
            raise _not_found()
        return task

    async def _write(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        """Run a board mutation, surfacing CLI failures as a 502."""

        def _detail(exc: BoardError) -> str:
            text = (exc.detail or exc.message or "").strip()
            return text[:500] or "hermes kanban failed"

        try:
            return await operation()
        except BoardError as exc:
            logger.warning("kanban board write failed: %s", _detail(exc))
            raise DashboardError(502, "board_error", _detail(exc)) from exc

    async def _require_task(self, task_id: str) -> Dict[str, Any]:
        task = await self.store.fetch_task(task_id)
        if task is None:
            raise _not_found()
        return task

    # -- reads -------------------------------------------------------------

    async def list_tasks(self, params: Any) -> Dict[str, Any]:
        status = (params.get("status") or "").strip()
        if status in ("", "all"):
            status = None
        elif status != mapping.ARCHIVED and status not in mapping.KANBAN_STATUSES:
            raise _validation(
                "status must be one of: " + ", ".join(mapping.KANBAN_STATUSES)
            )

        task_type = (params.get("type") or "").strip() or None

        priority: Optional[int] = None
        raw_priority = params.get("priority")
        if raw_priority not in (None, ""):
            try:
                priority = int(raw_priority)
            except (TypeError, ValueError):
                raise _validation("priority must be an integer from 1 to 5")
            priority = _validate_priority(priority)

        query = params.get("q")
        if query is not None:
            query = query.strip() or None

        created_by = normalize_actor_id(params.get("created_by"))

        order = params.get("order") or "created_at_desc"
        if order not in SORT_ORDERS:
            raise _validation("order must be 'created_at_desc' or 'priority'")

        limit = _parse_query_int(params, "limit", 200, 1, 1000)
        offset = _parse_query_int(params, "offset", 0, 0)

        tasks, total = await self.store.fetch_tasks(
            status=status,
            task_type=task_type,
            priority=priority,
            query=query,
            order=order,
            # Kanban has no server-side provenance filter column here; filter
            # the (small, local board) candidate set in memory to keep the
            # total/page honest.
            limit=1000 if created_by else limit,
            offset=0 if created_by else offset,
        )
        if created_by:
            matching = [task for task in tasks if task.get("created_by") == created_by]
            total = len(matching)
            tasks = matching[offset : offset + limit]  # noqa: E203 (black style)
        return {"tasks": tasks, "count": total}

    async def get_task(self, task_id: str) -> Dict[str, Any]:
        task = await self.store.fetch_task(task_id)
        if task is None:
            raise _not_found()
        return task

    async def get_stats(self) -> Dict[str, Any]:
        return await self.store.fetch_stats()

    async def get_task_types(self) -> List[Dict[str, Any]]:
        return await self.store.fetch_task_types()

    # -- writes ------------------------------------------------------------

    async def create_task(
        self, body: Dict[str, Any], *, actor: Optional[Actor] = None
    ) -> Dict[str, Any]:
        self.ensure_writable()
        unknown = sorted(set(body) - CREATE_FIELDS)
        if unknown:
            raise _validation(f"unknown field(s): {', '.join(unknown)}")

        # Same resolution as the Sugar source: headers > body > board default >
        # resolve_actor(). Board create must send that actor, never a literal.
        resolved_actor = actor or resolve_request_actor(body, default_actor=self.actor)

        title = _require_nonempty_str(body.get("title"), "title")
        description = body.get("description")
        if description is not None and not isinstance(description, str):
            raise _validation("description must be a string")
        priority = _validate_priority(body.get("priority", mapping.DEFAULT_PRIORITY))
        assigned_agent = _validate_optional_str(
            body.get("assigned_agent"), "assigned_agent"
        )
        project = _validate_optional_str(body.get("type"), "type")
        if project == mapping.DEFAULT_PROJECT:
            project = None

        blocked_by = body.get("blocked_by") or []
        if not isinstance(blocked_by, list) or not all(
            isinstance(item, str) for item in blocked_by
        ):
            raise _validation("blocked_by must be an array of task id strings")

        task_id = await self._write(
            lambda: self.client.create(
                title,
                body=description or None,
                priority=priority,
                assignee=assigned_agent,
                project=project,
                parents=blocked_by,
                created_by=resolved_actor.id,
            )
        )
        await self.state.refresh()
        if not task_id:
            created = await self._find_by_title(title)
            if created is None:
                raise DashboardError(
                    502,
                    "board_error",
                    "task was created but its id could not be determined",
                )
            return created
        return await self._refresh_and_get(task_id)

    async def _find_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        tasks, _ = await self.store.fetch_tasks(query=title, limit=20)
        for task in tasks:
            if task.get("title") == title:
                return task
        return None

    async def update_task(self, task_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_writable()
        if not body:
            raise _validation("no fields to update")
        forbidden = sorted(
            set(body)
            - PATCH_FIELDS
            - {
                "status",
                "type",
                "context",
                "acceptance_criteria",
                "result",
                "error_message",
                "reason",
            }
        )
        if forbidden:
            raise _validation(f"unknown field(s): {', '.join(forbidden)}")
        for field in ("status", "type", "context", "acceptance_criteria"):
            if field in body:
                raise _validation(
                    f"{field!r} is not editable on this board; use the board's verbs"
                    " (claim / complete / block / unblock / assign / edit)"
                )

        current = await self._require_task(task_id)

        title: Optional[str] = None
        if "title" in body:
            title = _require_nonempty_str(body["title"], "title")
        description: Optional[str] = None
        if "description" in body:
            description = body["description"]
            if description is not None and not isinstance(description, str):
                raise _validation("description must be a string")
            # hermes treats a missing --body as "unchanged"; "" clears it.
            description = description if description is not None else ""
        priority: Optional[int] = None
        if "priority" in body:
            priority = _validate_priority(body["priority"])

        if title is not None or description is not None or priority is not None:
            await self._write(
                lambda: self.client.edit(
                    task_id,
                    title=title,
                    body=description,
                    priority=priority,
                )
            )

        if "assigned_agent" in body:
            agent = _validate_optional_str(body["assigned_agent"], "assigned_agent")
            current_agent = current.get("assigned_agent")
            await self._write(
                lambda: self.client.assign(
                    task_id,
                    agent or "none",
                    reassign=bool(current_agent) and current_agent != agent,
                )
            )

        return await self._refresh_and_get(task_id)

    async def delete_task(self, task_id: str) -> Dict[str, Any]:
        self.ensure_writable()
        await self._require_task(task_id)
        await self._write(lambda: self.client.archive([task_id]))
        await self.state.refresh()
        return {"deleted": True, "id": task_id}

    async def hold_task(self, task_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_writable()
        reason = body.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise _validation("reason must be a string")
        kind = body.get("kind")
        if kind is not None and kind not in BLOCK_KINDS:
            raise _validation("kind must be one of: " + ", ".join(BLOCK_KINDS))
        await self._require_task(task_id)
        await self._write(
            lambda: self.client.block(task_id, reason=reason or None, kind=kind)
        )
        return await self._refresh_and_get(task_id)

    async def release_task(self, task_id: str) -> Dict[str, Any]:
        self.ensure_writable()
        await self._require_task(task_id)
        await self._write(lambda: self.client.unblock([task_id]))
        return await self._refresh_and_get(task_id)

    async def claim_task(self, task_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_writable()
        ttl = body.get("ttl")
        if ttl is not None:
            if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
                raise _validation("ttl must be a positive integer (seconds)")
        await self._require_task(task_id)
        await self._write(lambda: self.client.claim(task_id, ttl))
        return await self._refresh_and_get(task_id)

    async def complete_task(self, task_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_writable()
        result = body.get("result")
        summary = body.get("summary")
        for field, value in (("result", result), ("summary", summary)):
            if value is not None and not isinstance(value, str):
                raise _validation(f"{field} must be a string")
        await self._require_task(task_id)
        await self._write(
            lambda: self.client.complete(
                [task_id], result=result or None, summary=summary or None
            )
        )
        return await self._refresh_and_get(task_id)

    async def comment_task(self, task_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_writable()
        text = _require_nonempty_str(body.get("text"), "text")
        author = _validate_optional_str(body.get("author"), "author")
        await self._require_task(task_id)
        await self._write(lambda: self.client.comment(task_id, text, author=author))
        return await self._refresh_and_get(task_id)
