"""Local, real-time dashboard for the Sugar work queue.

The package exposes :func:`create_app` for anyone embedding the ASGI app and
lazily exposes the uvicorn runner through :mod:`sugar.dashboard.server` (the
CLI imports it only when the optional dashboard dependencies are installed).
"""

from .app import create_app
from .service import DashboardError

__all__ = ["create_app", "DashboardError"]
