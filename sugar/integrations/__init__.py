"""
Sugar Integrations

External service integrations:
- GitHub: Issue and PR management
- OpenCode: AI coding agent integration
"""

from .github import GitHubClient, GitHubComment, GitHubIssue

# Lazy import for OpenCode to avoid aiohttp dependency
__all__ = [
    "GitHubClient",
    "GitHubIssue",
    "GitHubComment",
    "OpenCodeClient",
    "OpenCodeConfig",
]

# Lazy import cache for opencode
_opencode_imports = {}


def __getattr__(name: str):
    """Lazy import OpenCode integration to avoid requiring aiohttp."""
    if name in ("OpenCodeClient", "OpenCodeConfig"):
        if name not in _opencode_imports:
            try:
                from .opencode import OpenCodeClient, OpenCodeConfig

                _opencode_imports["OpenCodeClient"] = OpenCodeClient
                _opencode_imports["OpenCodeConfig"] = OpenCodeConfig
            except ImportError as e:
                raise ImportError(
                    f"OpenCode integration requires aiohttp. Install with: pip install aiohttp\n"
                    f"Original error: {e}"
                ) from e
        return _opencode_imports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
