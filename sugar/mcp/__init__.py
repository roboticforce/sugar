"""
Sugar MCP Server

Model Context Protocol server for Sugar, enabling integration with:
- Claude Code (via memory server)
- GitHub Copilot Custom Agents
- Other MCP-compatible clients

Note: This module uses lazy imports to avoid requiring MCP dependencies
unless the MCP server is actually used. This allows the base Sugar CLI
to work without installing the optional [mcp] extras.
"""

__all__ = [
    "SugarMCPServer",
    "create_server",
    "create_memory_mcp_server",
    "run_memory_server",
    "create_task_mcp_server",
    "run_task_server",
]

# Lazy import cache
_lazy_imports = {}


def __getattr__(name: str):
    """Lazy import to avoid requiring MCP dependencies unless actually used."""
    if name in __all__:
        if name not in _lazy_imports:
            try:
                if name in ("SugarMCPServer", "create_server"):
                    from .server import SugarMCPServer, create_server

                    _lazy_imports["SugarMCPServer"] = SugarMCPServer
                    _lazy_imports["create_server"] = create_server
                elif name in ("create_memory_mcp_server", "run_memory_server"):
                    from .memory_server import (
                        create_memory_mcp_server,
                        run_memory_server,
                    )

                    _lazy_imports["create_memory_mcp_server"] = create_memory_mcp_server
                    _lazy_imports["run_memory_server"] = run_memory_server
                elif name in ("create_task_mcp_server", "run_task_server"):
                    from .task_server import (
                        create_task_mcp_server,
                        run_task_server,
                    )

                    _lazy_imports["create_task_mcp_server"] = create_task_mcp_server
                    _lazy_imports["run_task_server"] = run_task_server
            except ImportError as e:
                raise ImportError(
                    f"MCP dependencies not installed. Install with: pip install sugarai[mcp]\n"
                    f"Original error: {e}"
                ) from e
        return _lazy_imports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
