"""
Sugar Integrations

External service integrations:
- GitHub: Issue and PR management
- OpenCode: AI coding agent integration
- SDK telemetry: Google ADK / OpenAI Agents SDK / Claude Agent SDK adapters
  that post canonical ``sugar.telemetry.v1`` events to the local event spine.

The SDK helpers are exported lazily: importing this package never requires
``google-adk`` or ``openai-agents``, and a missing optional package only raises
a clear ImportError when the corresponding plugin is instantiated.
"""

import importlib
from typing import Any, Dict, Tuple

from .github import GitHubClient, GitHubComment, GitHubIssue

# Lazy import for OpenCode to avoid aiohttp dependency
__all__ = [
    "GitHubClient",
    "GitHubIssue",
    "GitHubComment",
    "OpenCodeClient",
    "OpenCodeConfig",
    "SugarTelemetryClient",
    "EmitResult",
    "SugarADKPlugin",
    "SugarADKCallbacks",
    "ADK_AVAILABLE",
    "SugarTraceProcessor",
    "make_claude_hooks",
    "make_claude_callbacks",
]

# Lazy import cache for opencode
_opencode_imports = {}

# SDK telemetry exports: name -> (relative module, attribute)
_SDK_EXPORTS: Dict[str, Tuple[str, str]] = {
    "SugarTelemetryClient": (".client", "SugarTelemetryClient"),
    "EmitResult": (".client", "EmitResult"),
    "SugarADKPlugin": (".adk", "SugarADKPlugin"),
    "SugarADKCallbacks": (".adk", "SugarADKCallbacks"),
    "ADK_AVAILABLE": (".adk", "ADK_AVAILABLE"),
    "SugarTraceProcessor": (".openai_agents", "SugarTraceProcessor"),
    "make_claude_hooks": (".claude_sdk", "make_hooks"),
    "make_claude_callbacks": (".claude_sdk", "make_callbacks"),
}
_sdk_imports: Dict[str, Any] = {}


def __getattr__(name: str):
    """Lazy import optional integrations (OpenCode, SDK telemetry)."""
    if name in _SDK_EXPORTS:
        if name not in _sdk_imports:
            module_name, attribute = _SDK_EXPORTS[name]
            module = importlib.import_module(module_name, __name__)
            _sdk_imports[name] = getattr(module, attribute)
        return _sdk_imports[name]
    if name in ("OpenCodeClient", "OpenCodeConfig"):
        if name not in _opencode_imports:
            try:
                from .opencode import OpenCodeClient, OpenCodeConfig

                _opencode_imports["OpenCodeClient"] = OpenCodeClient
                _opencode_imports["OpenCodeConfig"] = OpenCodeConfig
            except ImportError as e:
                raise ImportError(
                    "OpenCode integration requires aiohttp. Install with: "
                    f"pip install aiohttp\nOriginal error: {e}"
                ) from e
        return _opencode_imports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
