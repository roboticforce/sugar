"""Business logic for the Sugar local dashboard API.

All writes go through :class:`sugar.storage.work_queue.WorkQueue` so hold /
release / complete / fail semantics stay identical to the CLI and MCP server.
Reads go through :class:`~sugar.dashboard.store.TaskStore` (plain SQL).
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from ..__version__ import __version__ as SUGAR_VERSION
from ..identity import (
    ACTOR_KINDS,
    Actor,
    normalize_actor_id,
    normalize_kind,
    resolve_actor,
)
from ..storage.work_queue import WorkQueue
from .events import (
    DashboardState,
    EventBroadcaster,
    presence_interval_from_env,
)
from .serializer import utc_now_sqlite
from .store import STATUS_DEFAULTS, TaskStore

logger = logging.getLogger(__name__)

VALID_STATUSES = ("pending", "active", "completed", "failed", "hold")
CREATE_STATUSES = ("pending", "hold")
SORT_ORDERS = ("created_at_desc", "priority")

CREATE_FIELDS = frozenset(
    {
        "title",
        "type",
        "description",
        "priority",
        "status",
        "assigned_agent",
        "acceptance_criteria",
        "blocked_by",
        # Provenance: accepted but resolved through resolve_request_actor()
        # because the X-Sugar-Actor headers win over them.
        "created_by",
        "created_by_kind",
    }
)
PATCH_FIELDS = frozenset(
    {
        "title",
        "description",
        "type",
        "priority",
        "assigned_agent",
        "acceptance_criteria",
        "context",
        "status",
        # Transition-only payload keys (meaningful only with a status change).
        "result",
        "error_message",
        "reason",
    }
)


class DashboardError(Exception):
    """An HTTP-visible dashboard error (never leaks a traceback)."""

    def __init__(
        self, status_code: int, error: str, detail: Optional[str] = None
    ) -> None:
        super().__init__(detail or error)
        self.status_code = status_code
        self.error = error
        self.detail = detail


def _validation(detail: str) -> DashboardError:
    return DashboardError(400, "validation_error", detail)


def _not_found() -> DashboardError:
    return DashboardError(404, "not_found")


def _unknown_fields(body: Dict[str, Any], allowed: frozenset) -> None:
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise _validation(f"unknown field(s): {', '.join(unknown)}")


def _require_nonempty_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _validation(f"{field} is required and must be a non-empty string")
    return value.strip()


def _validate_priority(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _validation("priority must be an integer from 1 (urgent) to 5 (minimal)")
    if value < 1 or value > 5:
        raise _validation("priority must be between 1 (urgent) and 5 (minimal)")
    return value


def _validate_list(value: Any, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _validation(f"{field} must be a JSON array")
    return value


def _validate_optional_str(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _validation(f"{field} must be a string")
    return value.strip() or None


def _actor_id(value: Any, field: str) -> Optional[str]:
    """Trim/limit an actor id; blank values mean "not supplied"."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _validation(f"{field} must be a string")
    return normalize_actor_id(value)


def _actor_kind(value: Any, field: str) -> Optional[str]:
    """Validate a provenance kind; blank values mean "not supplied"."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _validation(f"{field} must be a string")
    if not value.strip():
        return None
    kind = normalize_kind(value)
    if kind is None:
        raise _validation(f"{field} must be one of: {', '.join(ACTOR_KINDS)}")
    return kind


def resolve_request_actor(
    body: Dict[str, Any],
    *,
    header_actor: Optional[str] = None,
    header_kind: Optional[str] = None,
    default_actor: Optional[str] = None,
) -> Actor:
    """Resolve the creator identity for ``POST /api/tasks`` (§10.4).

    Precedence: ``X-Sugar-Actor`` / ``X-Sugar-Actor-Kind`` headers, then the
    body's ``created_by`` / ``created_by_kind``, then ``default_actor`` (board
    installation default, if any), then :func:`sugar.identity.resolve_actor`
    (``${USER}@${host}``, kind ``human``). Only the value that wins is
    validated, so a header truly overrides a body field.
    """
    explicit = _actor_id(header_actor, "X-Sugar-Actor")
    if explicit is None:
        explicit = _actor_id(body.get("created_by"), "created_by")
    if explicit is None:
        explicit = _actor_id(default_actor, "actor")

    kind = _actor_kind(header_kind, "X-Sugar-Actor-Kind")
    if kind is None:
        kind = _actor_kind(body.get("created_by_kind"), "created_by_kind")

    return resolve_actor(explicit, kind=kind)


def _parse_query_int(
    params: Any,
    name: str,
    default: int,
    minimum: int,
    maximum: Optional[int] = None,
) -> int:
    raw = params.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise _validation(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise _validation(f"{name} must be >= {minimum}")
        raise _validation(f"{name} must be between {minimum} and {maximum}")
    return value


class DashboardService:
    """Owns the queue, store, snapshot state and broadcaster for one server."""

    def __init__(
        self,
        db_path: str,
        read_only: bool = False,
        poll_interval: float = 0.4,
        presence_interval: Optional[float] = None,
    ):
        self.db_path = db_path
        self.read_only = read_only
        self.queue = WorkQueue(db_path)
        self.store = TaskStore(db_path)
        self.broadcaster = EventBroadcaster()
        if presence_interval is None:
            presence_interval = presence_interval_from_env()
        self.presence_interval = presence_interval
        self.state = DashboardState(
            self.store,
            self.broadcaster,
            poll_interval,
            presence_interval=presence_interval,
        )
        self.spine = None  # optional EventSpine, wired by create_app
        self.started_at: Optional[float] = None
        # Reap expired leases (crashed workers) periodically. Reads are cheap;
        # writes happen only when a lease actually expired.
        self.reap_interval = max(
            1.0, float(os.environ.get("SUGAR_REAP_INTERVAL", "30"))
        )
        self._reaper: Optional[asyncio.Task] = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        # Ensure the task_types table exists with the standard defaults before
        # WorkQueue.initialize() creates the legacy 5-type table on a fresh DB.
        try:
            from ..storage.task_type_manager import TaskTypeManager

            await TaskTypeManager(self.db_path).initialize()
        except Exception:  # pragma: no cover - non-fatal bootstrap problem
            logger.warning("dashboard: task type initialization failed", exc_info=True)
        await self.queue.initialize()
        self.state.spine = self.spine
        await self.state.start()
        self.started_at = self.state.started_at
        if not self.read_only:
            self._reaper = asyncio.create_task(
                self._reap_loop(), name="sugar-dashboard-reaper"
            )

    async def stop(self) -> None:
        reaper = self._reaper
        self._reaper = None
        if reaper is not None:
            reaper.cancel()
            try:
                await reaper
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover - defensive
                logger.debug("dashboard reaper exited with error", exc_info=True)
        await self.state.stop()

    # -- lease reaper ------------------------------------------------------

    async def _reap_loop(self) -> None:
        """Periodically return expired leases to the queue."""
        while True:
            await asyncio.sleep(self.reap_interval)
            try:
                await self._reap_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - reaping must not kill the server
                logger.exception("dashboard: lease reaper failed")

    async def _reap_once(self) -> List[str]:
        """Reap once; emit a ``task.lease_expired`` activity event per task.

        The generic status-diff observer is suppressed for these ids so the
        activity feed shows one precise event instead of a diff duplicate.
        """
        if self.read_only:
            return []
        reaped = await self.queue.requeue_expired_leases()
        if not reaped:
            return []
        await self.state.refresh(suppress_activity_for=set(reaped))
        for task_id in reaped:
            task = self.state.get_task(task_id)
            if task is None:  # pragma: no cover - task deleted mid-reap
                continue
            await self.state.record_activity(
                "task.lease_expired",
                task,
                actor="sugar-reaper",
                actor_kind="system",
                previous_status="active",
                extra={"reason": "lease expired without heartbeat"},
            )
        logger.warning("♻️ dashboard reaped %d expired lease(s)", len(reaped))
        return reaped

    # -- helpers -----------------------------------------------------------

    def ensure_writable(self) -> None:
        if self.read_only:
            raise DashboardError(403, "read_only")

    async def _require_valid_type(self, task_type: str) -> str:
        valid = await self.store.fetch_task_type_ids()
        if task_type not in valid:
            raise _validation(
                f"unknown task type: {task_type!r} "
                "(must be an id from /api/task-types)"
            )
        return task_type

    async def _refresh_and_get(self, task_id: str) -> Dict[str, Any]:
        await self.state.refresh()
        task = self.state.get_task(task_id)
        if task is None:
            task = await self.store.fetch_task(task_id)
        if task is None:
            raise _not_found()
        return task

    # -- reads -------------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        uptime = 0.0
        if self.started_at is not None:
            uptime = round(max(0.0, time.monotonic() - self.started_at), 2)
        return {
            "ok": True,
            "version": SUGAR_VERSION,
            "db": self.db_path,
            "read_only": self.read_only,
            "uptime_s": uptime,
        }

    async def list_tasks(self, params: Any) -> Dict[str, Any]:
        status = (params.get("status") or "").strip()
        if status in ("", "all"):
            status = None

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
            created_by=created_by,
            order=order,
            limit=limit,
            offset=offset,
        )
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
        _unknown_fields(body, CREATE_FIELDS)

        # Resolve before validation so an invalid HTTP-supplied kind is a 400.
        resolved_actor = actor or resolve_request_actor(body)

        title = _require_nonempty_str(body.get("title"), "title")
        task_type = body.get("type", "feature")
        if not isinstance(task_type, str) or not task_type.strip():
            raise _validation("type must be a non-empty string")
        task_type = await self._require_valid_type(task_type.strip())

        description = body.get("description", "")
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise _validation("description must be a string")

        priority = _validate_priority(body.get("priority", 3))

        status = body.get("status", "pending")
        if status not in CREATE_STATUSES:
            raise _validation("initial status must be 'pending' or 'hold'")

        assigned_agent = _validate_optional_str(
            body.get("assigned_agent"), "assigned_agent"
        )

        acceptance_criteria = _validate_list(
            body.get("acceptance_criteria"), "acceptance_criteria"
        )

        blocked_by = _validate_list(body.get("blocked_by"), "blocked_by")
        if not all(isinstance(item, str) for item in blocked_by):
            raise _validation("blocked_by must be an array of task id strings")

        work_item: Dict[str, Any] = {
            "type": task_type,
            "title": title,
            "description": description,
            "priority": priority,
            "status": status,
            "source": "dashboard",
            "context": {"added_via": "sugar_dashboard"},
            "acceptance_criteria": acceptance_criteria,
            "blocked_by": blocked_by,
        }
        work_item.update(resolved_actor.as_task_fields())
        if assigned_agent:
            work_item["assigned_agent"] = assigned_agent

        try:
            task_id = await self.queue.add_work(work_item)
        except Exception as exc:
            logger.exception("dashboard: add_work failed")
            raise DashboardError(500, "internal", "could not create task") from exc

        return await self._refresh_and_get(task_id)

    async def update_task(self, task_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_writable()
        _unknown_fields(body, PATCH_FIELDS)
        if not body:
            raise _validation("no fields to update")

        existing = await self.queue.get_work_item(task_id)
        if existing is None:
            raise _not_found()

        updates: Dict[str, Any] = {}

        if "title" in body:
            updates["title"] = _require_nonempty_str(body["title"], "title")

        if "description" in body:
            description = body["description"]
            if description is not None and not isinstance(description, str):
                raise _validation("description must be a string or null")
            updates["description"] = description

        if "type" in body:
            task_type = body["type"]
            if not isinstance(task_type, str) or not task_type.strip():
                raise _validation("type must be a non-empty string")
            updates["type"] = await self._require_valid_type(task_type.strip())

        if "priority" in body:
            updates["priority"] = _validate_priority(body["priority"])

        if "assigned_agent" in body:
            updates["assigned_agent"] = _validate_optional_str(
                body["assigned_agent"], "assigned_agent"
            )

        if "acceptance_criteria" in body:
            updates["acceptance_criteria"] = _validate_list(
                body["acceptance_criteria"], "acceptance_criteria"
            )

        if "context" in body:
            context = body["context"]
            if context is not None and not isinstance(context, dict):
                raise _validation("context must be a JSON object or null")
            try:
                json.dumps(context)
            except (TypeError, ValueError):
                raise _validation("context must be JSON-serializable")
            updates["context"] = context

        status: Optional[str] = None
        if "status" in body:
            status = body["status"]
            if not isinstance(status, str) or status not in VALID_STATUSES:
                raise _validation("status must be one of: " + ", ".join(VALID_STATUSES))

        if "result" in body:
            if status != "completed":
                raise _validation("result is only valid with status='completed'")
            if not isinstance(body["result"], dict):
                raise _validation("result must be a JSON object")
        if "error_message" in body:
            if status != "failed":
                raise _validation("error_message is only valid with status='failed'")
            if not isinstance(body["error_message"], str):
                raise _validation("error_message must be a string")
        if "reason" in body:
            if status != "hold":
                raise _validation("reason is only valid with status='hold'")
            if body["reason"] is not None and not isinstance(body["reason"], str):
                raise _validation("reason must be a string")

        if updates:
            updates["updated_at"] = utc_now_sqlite()
            updated = await self.queue.update_work(task_id, updates)
            if not updated:
                raise _not_found()

        if status is not None:
            current = await self.queue.get_work_item(task_id)
            if current is None:
                raise _not_found()
            await self._apply_status(task_id, status, current, body)

        return await self._refresh_and_get(task_id)

    async def _apply_status(
        self,
        task_id: str,
        status: str,
        current: Dict[str, Any],
        body: Dict[str, Any],
    ) -> None:
        if status == "completed":
            await self.queue.complete_work(task_id, body.get("result") or {})
        elif status == "failed":
            # fail_work keeps Sugar's existing retry semantics: a task under
            # max_retries (3) returns to 'pending' with the error recorded.
            await self.queue.fail_work(
                task_id, body.get("error_message") or "failed via dashboard"
            )
        elif status == "hold":
            await self.queue.hold_work(task_id, body.get("reason"))
        elif status == "pending" and current.get("status") == "hold":
            released = await self.queue.release_work(task_id)
            if not released:
                raise _validation("could not release task: it is no longer on hold")
        else:
            updated = await self.queue.update_work(
                task_id,
                {"status": status, "updated_at": utc_now_sqlite()},
            )
            if not updated:
                raise _not_found()

    async def hold_task(self, task_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure_writable()
        reason = body.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise _validation("reason must be a string")

        existing = await self.queue.get_work_item(task_id)
        if existing is None:
            raise _not_found()

        await self.queue.hold_work(task_id, reason.strip() if reason else None)
        return await self._refresh_and_get(task_id)

    async def release_task(self, task_id: str) -> Dict[str, Any]:
        self.ensure_writable()
        existing = await self.queue.get_work_item(task_id)
        if existing is None:
            raise _not_found()
        if existing.get("status") != "hold":
            raise _validation("task is not on hold")
        released = await self.queue.release_work(task_id)
        if not released:
            raise _validation("could not release task: it is no longer on hold")
        return await self._refresh_and_get(task_id)

    async def delete_task(self, task_id: str) -> Dict[str, Any]:
        self.ensure_writable()
        existing = await self.queue.get_work_item(task_id)
        if existing is None:
            raise _not_found()
        removed = await self.queue.remove_work(task_id)
        if not removed:
            raise _not_found()
        await self.state.refresh()
        return {"deleted": True, "id": task_id}

    # -- board metadata + verbs this source does not implement --------------

    def meta(self) -> Dict[str, Any]:
        """Describe this board to the UI (``docs/local-dashboard.md`` §9.1)."""
        return {
            "board": "sugar",
            "title": "Sugar work queue",
            "db": self.db_path,
            "statuses": list(STATUS_DEFAULTS),
            "facet": {"label": "Type"},
            "capabilities": {
                "create": True,
                "edit": True,
                "edit_status": True,
                "delete": True,
                "hold": True,
                "release": True,
                "claim": False,
                "complete": False,
                "block": False,
                "unblock": False,
                "assign": False,
                "comment": False,
            },
        }

    def _unsupported(self, action: str) -> None:
        raise DashboardError(
            400,
            "unsupported",
            f"{action} is not supported by the Sugar work queue source",
        )

    async def claim_task(self, task_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self._unsupported("claim")

    async def complete_task(self, task_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self._unsupported("complete")

    async def comment_task(self, task_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self._unsupported("comment")
