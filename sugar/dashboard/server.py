"""Async runner for the Sugar local dashboard (uvicorn)."""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def run_dashboard(
    db_path: str,
    host: str = "127.0.0.1",
    port: int = 7331,
    read_only: bool = False,
    open_browser: bool = False,
    board: str = "sugar",
    kanban_board: str = None,
    hermes_bin: str = None,
    events_db: str = None,
    tokens: object = None,
) -> None:
    """Serve the dashboard until the process is interrupted.

    ``board="kanban"`` serves a Hermes Kanban board (read-only SQLite reads,
    ``hermes kanban`` writes) instead of Sugar's work queue.

    ``tokens`` is an optional :class:`sugar.dashboard.security.TokenRegistry`.
    When supplied, the API requires scoped bearer tokens/cookies (remote/team
    mode); when omitted the server stays loopback-only and unauthenticated.
    """
    import uvicorn

    from .app import create_app

    service = None
    if board == "kanban":
        from .kanban.service import KanbanService

        service = KanbanService(
            db_path,
            read_only=read_only,
            hermes_bin=hermes_bin,
            kanban_board=kanban_board,
        )
    app = create_app(
        db_path,
        read_only=read_only,
        service=service,
        events_db=events_db,
        tokens=tokens,
    )
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
        # Only trust proxy headers from a same-host reverse proxy; this is what
        # makes an HTTPS-terminating proxy produce a Secure session cookie.
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
    server = uvicorn.Server(config)

    opener = None
    if open_browser:
        opener = asyncio.create_task(
            _open_browser_when_ready(server, host, port),
            name="sugar-dashboard-opener",
        )

    try:
        await server.serve()
    finally:
        if opener is not None:
            opener.cancel()
            try:
                await opener
            except asyncio.CancelledError:
                pass


async def _open_browser_when_ready(server, host: str, port: int) -> None:
    import webbrowser

    while not server.started and not server.should_exit:
        await asyncio.sleep(0.1)
    if not server.started:
        return
    url = f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"
    try:
        webbrowser.open(url)
    except Exception:  # pragma: no cover - desktop integration is best effort
        logger.warning("dashboard: could not open a browser at %s", url)
