"""Starlette application factory for the Sugar local dashboard.

See ``docs/local-dashboard.md`` for the frozen HTTP/SSE contract.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from ..__version__ import __version__ as SUGAR_VERSION
from .events import KEEPALIVE_INTERVAL, format_sse
from .service import (
    DashboardError,
    DashboardService,
    resolve_request_actor,
)
from .security import MAX_BODY_BYTES, DashboardGuardMiddleware
from .spine import EventSpine, SpineError

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _service(request: Request) -> DashboardService:
    return request.app.state.dashboard


def _error_response(exc: DashboardError) -> JSONResponse:
    payload: dict = {"error": exc.error}
    if exc.detail:
        payload["detail"] = exc.detail
    return JSONResponse(payload, status_code=exc.status_code)


async def _dashboard_error_handler(request: Request, exc: DashboardError):
    return _error_response(exc)


async def _http_error_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return JSONResponse({"error": "not_found"}, status_code=404)
    detail = exc.detail if isinstance(exc.detail, str) else None
    payload = {"error": "http_error"}
    if detail:
        payload["detail"] = detail
    return JSONResponse(payload, status_code=exc.status_code)


async def _internal_error_handler(request: Request, exc: Exception):
    logger.error(
        "dashboard: unhandled error on %s %s",
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(
        {"error": "internal", "detail": "internal server error"},
        status_code=500,
    )


async def _read_json_object(request: Request) -> dict:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise DashboardError(413, "payload_too_large", "request body is too large")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise DashboardError(413, "payload_too_large", "request body is too large")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise DashboardError(400, "validation_error", "invalid JSON body")
    if not isinstance(data, dict):
        raise DashboardError(400, "validation_error", "JSON body must be an object")
    return data


async def homepage(request: Request) -> HTMLResponse:
    try:
        html = INDEX_HTML.read_text(encoding="utf-8")
    except OSError:
        raise DashboardError(500, "internal", "dashboard UI asset is missing")
    return HTMLResponse(html)


async def health(request: Request) -> JSONResponse:
    return JSONResponse(_service(request).health())


async def list_tasks(request: Request) -> JSONResponse:
    return JSONResponse(await _service(request).list_tasks(request.query_params))


async def create_task(request: Request) -> JSONResponse:
    body = await _read_json_object(request)
    # Provenance (§10.4): headers win over the body; both default to the
    # locally resolved actor. Resolved here so every board source shares it.
    actor = resolve_request_actor(
        body,
        header_actor=request.headers.get("X-Sugar-Actor"),
        header_kind=request.headers.get("X-Sugar-Actor-Kind"),
    )
    task = await _service(request).create_task(body, actor=actor)
    return JSONResponse({"task": task}, status_code=201)


async def get_task(request: Request) -> JSONResponse:
    task = await _service(request).get_task(request.path_params["task_id"])
    return JSONResponse({"task": task})


async def update_task(request: Request) -> JSONResponse:
    body = await _read_json_object(request)
    task = await _service(request).update_task(request.path_params["task_id"], body)
    return JSONResponse({"task": task})


async def delete_task(request: Request) -> JSONResponse:
    result = await _service(request).delete_task(request.path_params["task_id"])
    return JSONResponse(result)


async def hold_task(request: Request) -> JSONResponse:
    body = await _read_json_object(request)
    task = await _service(request).hold_task(request.path_params["task_id"], body)
    return JSONResponse({"task": task})


async def release_task(request: Request) -> JSONResponse:
    task = await _service(request).release_task(request.path_params["task_id"])
    return JSONResponse({"task": task})


async def claim_task(request: Request) -> JSONResponse:
    body = await _read_json_object(request)
    task = await _service(request).claim_task(request.path_params["task_id"], body)
    return JSONResponse({"task": task})


async def complete_task(request: Request) -> JSONResponse:
    body = await _read_json_object(request)
    task = await _service(request).complete_task(request.path_params["task_id"], body)
    return JSONResponse({"task": task})


async def comment_task(request: Request) -> JSONResponse:
    body = await _read_json_object(request)
    task = await _service(request).comment_task(request.path_params["task_id"], body)
    return JSONResponse({"task": task})


async def activity(request: Request) -> JSONResponse:
    spine = getattr(request.app.state, "spine", None)
    if spine is None:
        raise DashboardError(
            503, "spine_unavailable", "the event spine is disabled on this server"
        )
    params = request.query_params
    try:
        since_id = int(params.get("since_id") or 0)
    except (TypeError, ValueError):
        raise DashboardError(400, "validation_error", "since_id must be an integer")
    try:
        limit = int(params.get("limit") or 100)
    except (TypeError, ValueError):
        raise DashboardError(400, "validation_error", "limit must be an integer")
    events, last_id = await spine.list_events(
        since_id=max(0, since_id),
        limit=limit,
        kind=(params.get("kind") or "").strip() or None,
        actor=(params.get("actor") or "").strip() or None,
        task_id=(params.get("task_id") or "").strip() or None,
    )
    return JSONResponse({"events": events, "last_id": last_id})


async def ingest_event(request: Request) -> JSONResponse:
    spine = getattr(request.app.state, "spine", None)
    if spine is None:
        raise DashboardError(
            503, "spine_unavailable", "the event spine is disabled on this server"
        )
    body = await _read_json_object(request)
    try:
        event = await spine.append(body, source="ingest")
    except SpineError as exc:
        raise DashboardError(400, "validation_error", str(exc)) from exc
    # Fan out live to every SSE client, same channel as the observers.
    _service(request).broadcaster.publish("activity", event)
    return JSONResponse({"event": event}, status_code=201)


async def meta(request: Request) -> JSONResponse:
    return JSONResponse(_service(request).meta())


async def stats(request: Request) -> JSONResponse:
    return JSONResponse(await _service(request).get_stats())


async def task_types(request: Request) -> JSONResponse:
    return JSONResponse({"types": await _service(request).get_task_types()})


async def events(request: Request) -> StreamingResponse:
    service = _service(request)
    queue = service.broadcaster.subscribe()
    try:
        snapshot = await service.state.snapshot()
    except Exception:
        service.broadcaster.unsubscribe(queue)
        raise

    async def event_stream():
        try:
            yield format_sse(
                "hello", {"version": SUGAR_VERSION, "server": "sugar-dashboard"}
            )
            yield format_sse("snapshot", snapshot)
            while True:
                try:
                    event, data = await asyncio.wait_for(
                        queue.get(), timeout=KEEPALIVE_INTERVAL
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield format_sse(event, data)
        finally:
            service.broadcaster.unsubscribe(queue)
            logger.debug("dashboard: SSE client disconnected")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def create_app(
    db_path: str,
    *,
    read_only: bool = False,
    poll_interval: float = 0.4,
    service: Any = None,
    events_db: Any = None,
) -> Starlette:
    """Build the dashboard ASGI app for ``db_path``.

    Startup initializes the Sugar database (creating tables for a new path)
    before the first request is served. Pass ``service`` to serve a different
    board (e.g. :class:`sugar.dashboard.kanban.service.KanbanService`); the
    service must expose the same read/write surface as ``DashboardService``.
    """
    resolved_db = str(Path(db_path).expanduser().resolve())
    dashboard = service or DashboardService(
        resolved_db, read_only=read_only, poll_interval=poll_interval
    )
    # The activity spine is opt-in per server: pass a path (the CLI always does)
    # or leave it None for a pure board/queue view (tests, embedding).
    spine = EventSpine(events_db) if events_db else None

    @asynccontextmanager
    async def lifespan(app: Starlette):
        nonlocal spine
        if spine is not None:
            try:
                await spine.start()
            except Exception:  # pragma: no cover - best effort observability
                logger.exception(
                    "dashboard: event spine unavailable, continuing without it"
                )
                spine = None
                app.state.spine = None
        if spine is not None:
            dashboard.spine = spine
        await dashboard.start()
        try:
            yield
        finally:
            await dashboard.stop()
            if spine is not None:
                await spine.close()

    routes = [
        Route("/", homepage, methods=["GET"]),
        Route("/api/health", health, methods=["GET"]),
        Route("/api/meta", meta, methods=["GET"]),
        Route("/api/tasks", list_tasks, methods=["GET"]),
        Route("/api/tasks", create_task, methods=["POST"]),
        Route("/api/tasks/{task_id}", get_task, methods=["GET"]),
        Route("/api/tasks/{task_id}", update_task, methods=["PATCH"]),
        Route("/api/tasks/{task_id}", delete_task, methods=["DELETE"]),
        Route("/api/tasks/{task_id}/hold", hold_task, methods=["POST"]),
        Route("/api/tasks/{task_id}/release", release_task, methods=["POST"]),
        Route("/api/tasks/{task_id}/claim", claim_task, methods=["POST"]),
        Route("/api/tasks/{task_id}/complete", complete_task, methods=["POST"]),
        Route("/api/tasks/{task_id}/comment", comment_task, methods=["POST"]),
        Route("/api/stats", stats, methods=["GET"]),
        Route("/api/task-types", task_types, methods=["GET"]),
        Route("/api/activity", activity, methods=["GET"]),
        Route("/api/events", events, methods=["GET"]),
        Route("/api/events", ingest_event, methods=["POST"]),
    ]

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[Middleware(DashboardGuardMiddleware)],
        exception_handlers={
            DashboardError: _dashboard_error_handler,
            StarletteHTTPException: _http_error_handler,
            Exception: _internal_error_handler,
        },
    )
    app.state.dashboard = dashboard
    app.state.spine = spine
    return app
