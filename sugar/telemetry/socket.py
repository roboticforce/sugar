"""Unix-domain-socket fast path for telemetry ingest.

Newline-delimited JSON lines ``{"harness": "...", "body": {...}}`` are fed through
the same pipeline as the HTTP hook endpoint. Default:
``$SUGAR_INGEST_SOCKET`` or ``~/.sugar/ingest.sock``; an explicitly empty
``$SUGAR_INGEST_SOCKET`` disables it. The socket file is chmod 0600.

Startup is best-effort (the dashboard must keep working if the socket path is
unavailable), and stale socket files are replaced.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENV_SOCKET = "SUGAR_INGEST_SOCKET"
DEFAULT_SOCKET_NAME = "ingest.sock"
MAX_LINE_BYTES = 1_000_000
DEFAULT_SOCKET_MODE = 0o600


def resolve_socket_path(explicit: Any = None) -> Optional[Path]:
    """Resolve the ingest socket path; ``None`` means disabled."""
    if explicit is not None:
        raw = str(explicit)
    elif ENV_SOCKET in os.environ:
        raw = os.environ.get(ENV_SOCKET) or ""
    else:
        raw = str(Path.home() / ".sugar" / DEFAULT_SOCKET_NAME)
    if not raw.strip():
        return None
    return Path(raw.strip()).expanduser()


class IngestSocketServer:
    """Owns the Unix socket listener and forwards lines to the pipeline."""

    def __init__(self, pipeline: Any, path: Any = None):
        self.pipeline = pipeline
        self.path = resolve_socket_path(path)
        self._server: Optional[asyncio.AbstractServer] = None

    @property
    def enabled(self) -> bool:
        return self.path is not None

    @property
    def listening(self) -> bool:
        return self._server is not None

    async def start(self) -> bool:
        if self.path is None or self._server is not None:
            return False
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.debug(
                "telemetry socket: cannot create directory %s: %s", path.parent, exc
            )
            return False
        if path.exists():
            if await _socket_is_live(path):
                logger.warning("telemetry socket already in use: %s", path)
                return False
            try:
                path.unlink()
            except OSError as exc:  # pragma: no cover - exotic filesystem
                logger.debug("telemetry socket: cannot remove stale socket: %s", exc)
                return False
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_client, path=str(path), limit=MAX_LINE_BYTES
            )
        except (OSError, ValueError) as exc:
            # Never break the dashboard because the socket path is unavailable.
            logger.debug("telemetry socket: cannot listen on %s: %s", path, exc)
            self._server = None
            return False
        try:
            os.chmod(path, DEFAULT_SOCKET_MODE)
        except OSError:  # pragma: no cover - Windows / exotic filesystems
            logger.debug("telemetry socket: could not chmod %s", path)
        logger.info("telemetry socket listening at %s", path)
        return True

    async def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            try:
                await server.wait_closed()
            except Exception:  # pragma: no cover - defensive
                logger.debug("telemetry socket: close failed", exc_info=True)
        if self.path is not None:
            try:
                self.path.unlink()
            except OSError:
                pass

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                try:
                    line = await reader.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    break
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    continue
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    continue
                try:
                    await self.pipeline.ingest_socket_message(message)
                except Exception:  # pipeline is fail-open; never kill the listener
                    logger.debug("telemetry socket: ingest failed", exc_info=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # pragma: no cover - client already gone
                pass


async def _socket_is_live(path: Path) -> bool:
    """Best-effort check whether another process is listening on *path*."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(path=str(path)), timeout=0.2
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # pragma: no cover
        pass
    return True
