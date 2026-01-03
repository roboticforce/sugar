"""
Sugar MCP Server

Model Context Protocol server for Sugar, enabling integration with:
- GitHub Copilot Custom Agents
- Other MCP-compatible clients

Note: This module uses lazy imports to avoid requiring MCP dependencies
unless the MCP server is actually used. This allows the base Sugar CLI
to work without installing the optional [mcp] extras.
"""

__all__ = ["SugarMCPServer", "create_server"]

# Lazy import cache
_lazy_imports = {}


def __getattr__(name: str):
    """Lazy import to avoid requiring MCP dependencies unless actually used."""
    if name in __all__:
        if name not in _lazy_imports:
            try:
                from .server import SugarMCPServer, create_server

                _lazy_imports["SugarMCPServer"] = SugarMCPServer
                _lazy_imports["create_server"] = create_server
            except ImportError as e:
                raise ImportError(
                    f"MCP dependencies not installed. Install with: pip install sugarai[mcp]\n"
                    f"Original error: {e}"
                ) from e
        return _lazy_imports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
