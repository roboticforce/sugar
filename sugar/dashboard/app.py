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
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from starlette.routing import Route

from ..__version__ import __version__ as SUGAR_VERSION
from .events import KEEPALIVE_INTERVAL, format_sse
from .service import (
    DashboardError,
    DashboardService,
    resolve_request_actor,
)
from .security import (
    MAX_BODY_BYTES,
    BoardAuthMiddleware,
    DashboardGuardMiddleware,
    TokenRegistry,
    set_board_cookie,
    unauthorized_response,
    forbidden_scope_response,
)
from ..evidence.query import EvidenceQueryError, query_evidence
from .traces import TraceQueryError, trace_detail_report, trace_list_report
from ..telemetry.coverage import coverage_report
from ..telemetry.normalize import normalize_harness_name
from ..telemetry import otlp as otlp_mod
from ..telemetry.pipeline import TelemetryPipeline
from ..telemetry.schema import TelemetryValidationError
from ..telemetry.socket import IngestSocketServer
from .spine import EventSpine, SpineError
from .trends import TrendsQueryError, trends_report
from .usage import UsageQueryError, usage_report
from .aibom import AibomQueryError, aibom_report

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
    tokens = getattr(request.app.state, "tokens", None)
    if isinstance(tokens, TokenRegistry) and not tokens.is_empty:
        query_token = request.query_params.get("token")
        if query_token is not None:
            record = tokens.find(query_token)
            if record is None:
                return unauthorized_response()
            # Exchange the URL token for an HttpOnly cookie, then drop it from
            # the address bar so it never lands in history or a Referer header.
            response = RedirectResponse("/", status_code=303)
            set_board_cookie(
                response,
                record.secret,
                secure=request.url.scheme == "https",
            )
            response.headers["Cache-Control"] = "no-store"
            return response
    try:
        html = INDEX_HTML.read_text(encoding="utf-8")
    except OSError:
        raise DashboardError(500, "internal", "dashboard UI asset is missing")
    return HTMLResponse(html)


async def health(request: Request) -> JSONResponse:
    """Liveness probe; intentionally unauthenticated in token mode.

    In token mode this is reachable without a credential, so it exposes only
    ``{ok, version}`` — never the absolute database path, read-only flag or
    uptime, which are useful to an attacker and useless to a liveness probe.
    """
    service = _service(request)
    tokens = getattr(request.app.state, "tokens", None)
    if isinstance(tokens, TokenRegistry) and not tokens.is_empty:
        return JSONResponse({"ok": True, "version": service.health()["version"]})
    return JSONResponse(service.health())


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


async def traces(request: Request) -> JSONResponse:
    """List session traces (``GET /api/traces``)."""
    spine = getattr(request.app.state, "spine", None)
    if spine is None:
        raise DashboardError(
            503, "spine_unavailable", "the event spine is disabled on this server"
        )
    params = request.query_params
    try:
        report = await trace_list_report(
            spine.db_path,
            limit=params.get("limit"),
            project=params.get("project"),
            harness=params.get("harness"),
            since=params.get("since"),
        )
    except TraceQueryError as exc:
        raise DashboardError(400, "validation_error", str(exc)) from exc
    return JSONResponse(report)


async def trace_detail(request: Request) -> JSONResponse:
    """Session trace + flat spans (``GET /api/traces/{trace_id}``)."""
    spine = getattr(request.app.state, "spine", None)
    if spine is None:
        raise DashboardError(
            503, "spine_unavailable", "the event spine is disabled on this server"
        )
    trace_id = request.path_params["trace_id"]
    try:
        report = await trace_detail_report(spine.db_path, trace_id)
    except TraceQueryError as exc:
        raise DashboardError(400, "validation_error", str(exc)) from exc
    if report is None:
        raise DashboardError(404, "not_found")
    return JSONResponse(report)


async def usage(request: Request) -> JSONResponse:
    """Aggregate usage/cost telemetry from the event spine (``docs/usage.md``)."""
    spine = getattr(request.app.state, "spine", None)
    if spine is None:
        raise DashboardError(
            503, "spine_unavailable", "the event spine is disabled on this server"
        )
    params = request.query_params
    try:
        report = await usage_report(
            spine.db_path,
            since=params.get("since"),
            until=params.get("until"),
            group_by=params.get("group_by"),
            kinds=(params.get("kinds") or params.get("kind") or "").strip() or None,
            actor=(params.get("actor") or "").strip() or None,
            harness=(params.get("harness") or "").strip() or None,
            limit=params.get("limit"),
        )
    except UsageQueryError as exc:
        raise DashboardError(400, "validation_error", str(exc)) from exc
    return JSONResponse(report)


async def aibom(request: Request) -> JSONResponse:
    """Observed AIBOM inventory v0 from the event spine (``GET /api/aibom``)."""
    spine = getattr(request.app.state, "spine", None)
    if spine is None:
        raise DashboardError(
            503, "spine_unavailable", "the event spine is disabled on this server"
        )
    params = request.query_params
    try:
        report = await aibom_report(
            spine.db_path,
            project=params.get("project"),
            since=params.get("since"),
            limit=params.get("limit"),
        )
    except AibomQueryError as exc:
        raise DashboardError(400, "validation_error", str(exc)) from exc
    return JSONResponse(report)


async def trends(request: Request) -> JSONResponse:
    """Time-bucketed work analytics from the event spine (``docs/trends.md``)."""
    spine = getattr(request.app.state, "spine", None)
    if spine is None:
        raise DashboardError(
            503, "spine_unavailable", "the event spine is disabled on this server"
        )
    params = request.query_params
    try:
        report = await trends_report(
            spine.db_path,
            since=params.get("since"),
            until=params.get("until"),
            bucket=params.get("bucket"),
            group_by=params.get("group_by"),
            limit=params.get("limit"),
        )
    except TrendsQueryError as exc:
        raise DashboardError(400, "validation_error", str(exc)) from exc
    return JSONResponse(report)


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


async def hooks_ingest(request: Request) -> JSONResponse:
    """Ingest a raw harness hook payload (``POST /api/hooks/{harness}``).

    Always returns ``200 {"ok": true}`` for a known harness, even if the event is
    ignored/deduped/storage is unavailable (fail-open for hook callers). Unknown
    harnesses are ``404``; malformed JSON is ``400``.
    """
    harness = request.path_params["harness"]
    if normalize_harness_name(harness) is None:
        raise DashboardError(404, "not_found")
    body = await _read_json_object(request)
    pipeline = getattr(request.app.state, "telemetry_pipeline", None)
    if pipeline is not None:
        try:
            await pipeline.ingest_hook(harness, body)
        except TelemetryValidationError as exc:
            raise DashboardError(400, "validation_error", str(exc)) from exc
        except Exception:  # hooks must never fail their caller
            logger.warning("telemetry: hook ingest failed", exc_info=True)
    return JSONResponse({"ok": True})


async def evidence_list(request: Request) -> JSONResponse:
    """List evidence events (``GET /api/evidence``), oldest -> newest."""
    spine = getattr(request.app.state, "spine", None)
    if spine is None:
        raise DashboardError(
            503, "spine_unavailable", "the event spine is disabled on this server"
        )
    params = request.query_params
    try:
        report = await query_evidence(
            spine.db_path,
            since=params.get("since"),
            until=params.get("until"),
            task_id=params.get("task_id"),
            kind=params.get("kind"),
            status=params.get("status"),
            limit=params.get("limit"),
            offset=params.get("offset"),
        )
    except EvidenceQueryError as exc:
        raise DashboardError(400, "validation_error", str(exc)) from exc
    return JSONResponse(report)


async def evidence_ingest(request: Request) -> JSONResponse:
    """Ingest a canonical evidence event (``POST /api/evidence``) -> 201."""
    spine = getattr(request.app.state, "spine", None)
    pipeline = getattr(request.app.state, "telemetry_pipeline", None)
    if spine is None or pipeline is None:
        raise DashboardError(
            503, "spine_unavailable", "the event spine is disabled on this server"
        )
    body = await _read_json_object(request)
    try:
        result = await pipeline.ingest_evidence(body)
    except TelemetryValidationError as exc:
        raise DashboardError(400, "validation_error", str(exc)) from exc
    if result.stored and result.event is not None:
        return JSONResponse({"event": result.event}, status_code=201)
    return JSONResponse({"ok": False, "dropped": True}, status_code=202)


def _otlp_auth_error(request: Request):
    """Return a 401/403 response for token-protected ``/v1/*``, else None."""
    tokens = getattr(request.app.state, "tokens", None)
    if not isinstance(tokens, TokenRegistry) or tokens.is_empty:
        return None
    authorization = request.headers.get("authorization", "")
    parts = authorization.strip().split(None, 1)
    candidate = (
        parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else None
    )
    record = tokens.find(candidate)
    if record is None:
        return unauthorized_response()
    if not record.allows("POST"):
        return forbidden_scope_response(record.scope, "POST")
    return None


async def _read_otlp_body(request: Request) -> bytes:
    """Read an OTLP body with the same 1 MB raw cap as the JSON endpoints."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise DashboardError(413, "payload_too_large", "request body is too large")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise DashboardError(413, "payload_too_large", "request body is too large")
    return raw


def _parse_otlp_json(body: bytes) -> dict:
    if not body.strip():
        return {}
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise DashboardError(400, "validation_error", "invalid JSON body")
    if not isinstance(data, dict):
        raise DashboardError(400, "validation_error", "JSON body must be an object")
    return data


async def _otlp_ingest(
    request: Request, mapper, protobuf_decoder, source: str
) -> JSONResponse:
    """Shared OTLP handler: auth -> wire format -> gzip -> JSON/protobuf -> mapper."""
    denied = _otlp_auth_error(request)
    if denied is not None:
        return denied
    content_type = otlp_mod.classify_content_type(request.headers.get("content-type"))
    if content_type is None:
        raise DashboardError(415, "unsupported_media_type", otlp_mod.CONTENT_TYPE_HINT)
    if content_type == "protobuf" and not otlp_mod.protobuf_available():
        raise DashboardError(
            415, "unsupported_media_type", otlp_mod.PROTOBUF_INSTALL_HINT
        )
    content_encoding = request.headers.get("content-encoding", "")
    try:
        otlp_mod.normalize_content_encoding(content_encoding)
    except otlp_mod.OTLPUnsupportedEncoding as exc:
        raise DashboardError(415, "unsupported_media_type", str(exc)) from exc
    spine = getattr(request.app.state, "spine", None)
    pipeline = getattr(request.app.state, "telemetry_pipeline", None)
    if spine is None or pipeline is None:
        raise DashboardError(
            503, "spine_unavailable", "the event spine is disabled on this server"
        )
    raw = await _read_otlp_body(request)
    try:
        body = otlp_mod.decode_content_encoding(raw, content_encoding, MAX_BODY_BYTES)
    except otlp_mod.OTLPBodyTooLarge as exc:
        raise DashboardError(413, "payload_too_large", str(exc)) from exc
    except otlp_mod.OTLPFormatError as exc:
        raise DashboardError(400, "validation_error", str(exc)) from exc
    if content_type == "json":
        payload = _parse_otlp_json(body)
    else:
        try:
            payload = protobuf_decoder(body)
        except otlp_mod.OTLPProtobufUnavailable as exc:
            raise DashboardError(
                415, "unsupported_media_type", otlp_mod.PROTOBUF_INSTALL_HINT
            ) from exc
        except otlp_mod.OTLPFormatError as exc:
            raise DashboardError(400, "validation_error", str(exc)) from exc
    harness_hint = request.query_params.get("harness")
    try:
        events = mapper(payload, harness_hint or None)
    except otlp_mod.OTLPFormatError as exc:
        raise DashboardError(400, "validation_error", str(exc)) from exc
    try:
        await otlp_mod.ingest_otlp_events(events, pipeline, source=source)
    except Exception:  # pragma: no cover - exporters get a 200; storage is best effort
        logger.exception("otlp: ingest failed")
    return JSONResponse({})


async def otlp_traces(request: Request) -> JSONResponse:
    """OTLP/HTTP trace export (JSON or protobuf, optional gzip) -> events."""
    return await _otlp_ingest(
        request, otlp_mod.spans_to_events, otlp_mod.decode_trace_protobuf, "otlp:traces"
    )


async def otlp_logs(request: Request) -> JSONResponse:
    """OTLP/HTTP log export (JSON or protobuf, optional gzip) -> events."""
    return await _otlp_ingest(
        request, otlp_mod.logs_to_events, otlp_mod.decode_log_protobuf, "otlp:logs"
    )


async def coverage(request: Request) -> JSONResponse:
    """Adapter coverage matrix from the registry + recent spine events."""
    spine = getattr(request.app.state, "spine", None)
    try:
        report = await coverage_report(spine.db_path if spine is not None else None)
    except Exception:  # pragma: no cover - defensive; coverage must not 500 often
        logger.exception("coverage report failed")
        raise DashboardError(500, "internal", "coverage report unavailable")
    return JSONResponse(report)


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
                    event, data, event_id = await asyncio.wait_for(
                        queue.get(), timeout=KEEPALIVE_INTERVAL
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield format_sse(event, data, event_id)
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
    presence_interval: float | None = None,
    service: Any = None,
    events_db: Any = None,
    tokens: TokenRegistry | None = None,
    trust_same_origin: bool | None = None,
    ingest_socket: str | None = None,
) -> Starlette:
    """Build the dashboard ASGI app for ``db_path``.

    Startup initializes the Sugar database (creating tables for a new path)
    before the first request is served. Pass ``service`` to serve a different
    board (e.g. :class:`sugar.dashboard.kanban.service.KanbanService`); the
    service must expose the same read/write surface as ``DashboardService``.

    ``tokens`` is an optional :class:`~sugar.dashboard.security.TokenRegistry`.
    When present every ``/api/*`` request except ``/api/health`` needs a scoped
    bearer token or the ``sugar_board`` cookie, and same-host origins are
    accepted for writes (remote browser UI). With no tokens (the default) the
    server keeps its loopback-only, unauthenticated behaviour.

    ``presence_interval`` controls the ephemeral SSE ``presence`` heartbeat
    cadence (default: ``$SUGAR_PRESENCE_INTERVAL`` or 2.0 seconds). It applies
    when the app builds its own :class:`DashboardService`; a custom ``service``
    owns its own state.

    ``ingest_socket`` overrides the telemetry Unix-socket path (default
    ``$SUGAR_INGEST_SOCKET`` or ``~/.sugar/ingest.sock``; an empty string
    disables it). When the event spine is enabled, hook ingestion is reachable
    at ``POST /api/hooks/{harness}`` and evidence at ``POST /api/evidence``; the
    socket serves the same pipeline for fast command hooks.
    """
    resolved_db = str(Path(db_path).expanduser().resolve())
    dashboard = service or DashboardService(
        resolved_db,
        read_only=read_only,
        poll_interval=poll_interval,
        presence_interval=presence_interval,
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
        pipeline = None
        ingest_socket_server = None
        if spine is not None:
            dashboard.spine = spine
            pipeline = TelemetryPipeline(spine, dashboard.broadcaster)
        app.state.telemetry_pipeline = pipeline
        await dashboard.start()
        if pipeline is not None:
            try:
                ingest_socket_server = IngestSocketServer(pipeline, path=ingest_socket)
                if await ingest_socket_server.start():
                    app.state.ingest_socket = str(ingest_socket_server.path)
            except Exception:  # pragma: no cover - socket is an optional fast path
                logger.exception("dashboard: telemetry socket failed to start")
                ingest_socket_server = None
        try:
            yield
        finally:
            if ingest_socket_server is not None:
                await ingest_socket_server.stop()
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
        Route("/api/traces", traces, methods=["GET"]),
        Route("/api/traces/{trace_id}", trace_detail, methods=["GET"]),
        Route("/api/usage", usage, methods=["GET"]),
        Route("/api/trends", trends, methods=["GET"]),
        Route("/api/aibom", aibom, methods=["GET"]),
        Route("/api/events", events, methods=["GET"]),
        Route("/api/events", ingest_event, methods=["POST"]),
        Route("/api/hooks/{harness}", hooks_ingest, methods=["POST"]),
        Route("/api/evidence", evidence_list, methods=["GET"]),
        Route("/api/evidence", evidence_ingest, methods=["POST"]),
        Route("/api/coverage", coverage, methods=["GET"]),
        Route("/v1/traces", otlp_traces, methods=["POST"]),
        Route("/v1/logs", otlp_logs, methods=["POST"]),
    ]

    registry = tokens if isinstance(tokens, TokenRegistry) else None
    if trust_same_origin is None:
        trust_same_origin = bool(registry and not registry.is_empty)
    middleware = [
        Middleware(DashboardGuardMiddleware, trust_same_origin=trust_same_origin)
    ]
    if registry is not None and not registry.is_empty:
        middleware.append(Middleware(BoardAuthMiddleware, tokens=registry))

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=middleware,
        exception_handlers={
            DashboardError: _dashboard_error_handler,
            StarletteHTTPException: _http_error_handler,
            Exception: _internal_error_handler,
        },
    )
    app.state.dashboard = dashboard
    app.state.spine = spine
    app.state.tokens = registry
    app.state.telemetry_pipeline = None
    app.state.ingest_socket = None
    return app
