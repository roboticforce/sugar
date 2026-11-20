"""DeepSeek Harness (DSH) session-log telemetry adapter.

DSH appends compressed JSONL session logs under ``~/.dsh/sessions/``. This
package tail-decodes them and replays the interesting events (sessions, turns,
tool calls/results, approvals) as canonical ``sugar.telemetry.v1`` events to a
running Sugar board. There is no DSH plugin API to install, so the transport is a
session tail (``session-tail``) and the installer ships a macOS LaunchAgent (or a
manual command elsewhere). See ``docs/telemetry-adapters.md``.
"""

from __future__ import annotations

__all__: list[str] = []
