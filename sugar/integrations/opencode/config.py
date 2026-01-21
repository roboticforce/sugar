"""
OpenCode Integration Configuration
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class OpenCodeConfig:
    """Configuration for OpenCode integration."""

    # Server connection
    server_url: str = "http://localhost:4096"
    api_key: Optional[str] = None
    timeout: float = 30.0

    # Auto-injection settings
    auto_inject: bool = True
    inject_memory_types: List[str] = field(
        default_factory=lambda: ["decision", "preference", "error_pattern"]
    )
    memory_limit: int = 5

    # Notification settings
    notify_on_completion: bool = True
    notify_on_failure: bool = True

    # Sync settings
    sync_interval: float = 5.0  # seconds

    # Feature flags
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "OpenCodeConfig":
        """Create config from environment variables."""
        return cls(
            server_url=os.environ.get("OPENCODE_SERVER_URL", "http://localhost:4096"),
            api_key=os.environ.get("OPENCODE_API_KEY"),
            timeout=float(os.environ.get("OPENCODE_TIMEOUT", "30.0")),
            enabled=os.environ.get("SUGAR_OPENCODE_ENABLED", "true").lower() == "true",
        )

    @classmethod
    def from_sugar_config(cls, sugar_config: dict) -> "OpenCodeConfig":
        """Create config from Sugar configuration dict."""
        opencode_config = sugar_config.get("integrations", {}).get("opencode", {})

        return cls(
            server_url=opencode_config.get("server_url", "http://localhost:4096"),
            api_key=opencode_config.get("api_key"),
            timeout=opencode_config.get("timeout", 30.0),
            auto_inject=opencode_config.get("auto_inject", True),
            inject_memory_types=opencode_config.get(
                "inject_memory_types", ["decision", "preference", "error_pattern"]
            ),
            memory_limit=opencode_config.get("memory_limit", 5),
            notify_on_completion=opencode_config.get("notify_on_completion", True),
            notify_on_failure=opencode_config.get("notify_on_failure", True),
            sync_interval=opencode_config.get("sync_interval", 5.0),
            enabled=opencode_config.get("enabled", True),
        )
