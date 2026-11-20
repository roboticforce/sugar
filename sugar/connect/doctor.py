"""``sugar doctor``: prove that installed adapters can actually deliver.

Doctor is end-to-end-ish: it asks the running board for health/coverage, pushes
a uniquely-tagged synthetic hook through every installed adapter path (HTTP hook
endpoint and, when present, the Unix ingest socket), then looks for that tag in
``GET /api/activity``. It is read-only against local config and never writes to
the event spine directly.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..activity import ActivityConfigError, resolve_board_url
from . import detect, registry
from .hook import (
    _forward_socket,
    build_socket_message,
    default_socket_path,
)

DEFAULT_TIMEOUT = 3.0
ACTIVITY_PROBE_LIMIT = 200
MAX_RESPONSE_BYTES = 1_000_000


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class AdapterProbe:
    harness: str
    display_name: str = ""
    transport: str = ""
    http_status: Optional[int] = None
    socket_ok: bool = False
    delivered: bool = False
    verified: Optional[bool] = None
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "harness": self.harness,
            "display_name": self.display_name,
            "transport": self.transport,
            "http_status": self.http_status,
            "socket_ok": self.socket_ok,
            "delivered": self.delivered,
            "verified": self.verified,
            "detail": self.detail,
        }


@dataclass
class DoctorResult:
    ok: bool
    server: str
    checks: List[DoctorCheck] = field(default_factory=list)
    adapters: List[AdapterProbe] = field(default_factory=list)
    coverage: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "server": self.server,
            "checks": [
                {"name": check.name, "ok": check.ok, "detail": check.detail}
                for check in self.checks
            ],
            "adapters": [probe.as_dict() for probe in self.adapters],
            "coverage": self.coverage,
        }


def _request_json(
    method: str,
    url: str,
    *,
    body: Optional[bytes] = None,
    token: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Return ``{"status": int, "data": ...}`` or raise a short human error."""
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            raw = response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read(4096).decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            payload = None
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or payload.get("error") or "")
        return {"status": int(exc.code), "data": None, "error": detail[:200]}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"status": 0, "data": None, "error": str(reason)[:200]}
    data: Any = None
    if raw:
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            data = None
    return {"status": status, "data": data}


def _probe_body(probe_id: str, harness: str) -> bytes:
    body = {
        "session_id": f"sugar-doctor-{probe_id}",
        "hook_event_name": "SessionStart",
        "sugar_probe": probe_id,
        "tool_use_id": probe_id,
        "harness": harness,
        "source": "sugar-doctor",
        "cwd": str(Path.cwd()),
    }
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def _installed_from_coverage(
    coverage: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    if not isinstance(coverage, dict):
        return {}
    harnesses = coverage.get("harnesses")
    if not isinstance(harnesses, list):
        return {}
    installed: Dict[str, Dict[str, Any]] = {}
    for row in harnesses:
        if not isinstance(row, dict) or row.get("installed") is not True:
            continue
        name = str(row.get("harness") or "").strip()
        if name:
            installed[name] = row
    return installed


def run_doctor(
    *,
    server: Optional[str] = None,
    socket_path: Optional[str] = None,
    registry_path: Optional[Path] = None,
    home: Optional[Path] = None,
    timeout: float = DEFAULT_TIMEOUT,
    token: Optional[str] = None,
) -> DoctorResult:
    """Run the diagnostics and return a structured result (no printing)."""
    try:
        base = resolve_board_url(server)
    except ActivityConfigError as exc:
        return DoctorResult(
            ok=False,
            server=str(server or ""),
            checks=[DoctorCheck("board URL", False, str(exc))],
        )
    checks: List[DoctorCheck] = []
    probes: List[AdapterProbe] = []

    health = _request_json("GET", f"{base}/api/health", token=token, timeout=timeout)
    health_ok = health.get("status") == 200 and isinstance(health.get("data"), dict)
    checks.append(
        DoctorCheck(
            "dashboard health",
            health_ok,
            (
                f"HTTP {health.get('status')}"
                if health_ok
                else (
                    f"unreachable at {base}"
                    + (f" ({health.get('error')})" if health.get("error") else "")
                )
            ),
        )
    )

    coverage_resp = _request_json(
        "GET", f"{base}/api/coverage", token=token, timeout=timeout
    )
    coverage: Optional[Dict[str, Any]] = None
    if coverage_resp.get("status") == 200 and isinstance(
        coverage_resp.get("data"), dict
    ):
        coverage = coverage_resp["data"]
        checks.append(DoctorCheck("coverage endpoint", True, "GET /api/coverage"))
    else:
        checks.append(
            DoctorCheck(
                "coverage endpoint",
                True,
                "unavailable on this board (older build) — continuing",
            )
        )

    reg_path = registry_path
    loaded = registry.load_registry(reg_path)
    installed = registry.installed_adapters(loaded)
    source = "local registry"
    if not installed:
        installed = _installed_from_coverage(coverage)
        source = "board coverage"
    if not installed:
        checks.append(
            DoctorCheck(
                "installed adapters",
                False,
                "none installed — run `sugar connect` first",
            )
        )
        return DoctorResult(
            ok=False, server=base, checks=checks, adapters=probes, coverage=coverage
        )
    checks.append(
        DoctorCheck(
            "installed adapters",
            True,
            f"{len(installed)} from {source}: " + ", ".join(sorted(installed)),
        )
    )

    socket_file = (
        Path(socket_path).expanduser() if socket_path else default_socket_path()
    )
    socket_available = False
    try:
        socket_available = socket_file.exists()
    except OSError:  # pragma: no cover - defensive
        socket_available = False

    probe_ids: Dict[str, str] = {}
    for harness in sorted(installed):
        entry = installed[harness] if isinstance(installed[harness], dict) else {}
        spec = detect.HARNESS_SPECS.get(harness)
        probe_id = uuid.uuid4().hex
        probe_ids[harness] = probe_id
        raw = _probe_body(probe_id, harness)
        transport = str(
            entry.get("transport") or (spec.transport if spec else "") or ""
        )
        probe = AdapterProbe(
            harness=harness,
            display_name=str(
                entry.get("display_name") or (spec.display_name if spec else harness)
            ),
            transport=transport,
        )

        if transport == "session-tail":
            # DSH has no hook endpoint: verify the canonical ingest path the
            # session tail uses (POST /api/events), and report tail readiness.
            from ..dsh.discover import discover_sessions
            from ..dsh.parse import zstd_available
            from ..telemetry.schema import build_envelope

            envelope = build_envelope(
                kind="session.started",
                harness="dsh",
                summary={"probe": probe_id, "source": "sugar-doctor"},
                actor="dsh",
                actor_kind="agent",
                session_id=f"sugar-doctor-{probe_id}",
                fidelity="L3",
                source="sugar-doctor",
            )
            tail_resp = _request_json(
                "POST",
                f"{base}/api/events",
                body=json.dumps(envelope).encode("utf-8"),
                token=token,
                timeout=timeout,
            )
            probe.http_status = tail_resp.get("status")
            if probe.http_status is not None and 200 <= int(probe.http_status) < 300:
                probe.delivered = True
            else:
                probe.detail = (
                    f"ingest endpoint HTTP {probe.http_status}"
                    if probe.http_status
                    else "ingest endpoint unreachable"
                )
            sessions = discover_sessions()
            checks.append(
                DoctorCheck(
                    "dsh session tail",
                    zstd_available(),
                    f"zstd={'yes' if zstd_available() else 'NO'}; "
                    f"{len(sessions)} session log(s) found",
                )
            )
            probes.append(probe)
            continue

        hook_resp = _request_json(
            "POST",
            f"{base}/api/hooks/{harness}",
            body=raw,
            token=token,
            timeout=timeout,
        )
        probe.http_status = hook_resp.get("status")
        if probe.http_status is not None and 200 <= int(probe.http_status) < 300:
            probe.delivered = True

        if socket_available and hasattr(socket, "AF_UNIX"):
            message = build_socket_message(raw, harness)
            if _forward_socket(message, socket_file, min(timeout, 1.0)):
                probe.socket_ok = True
                probe.delivered = True

        if not probe.delivered:
            probe.detail = (
                f"hook endpoint HTTP {probe.http_status}"
                if probe.http_status
                else "hook endpoint unreachable"
            )
        probes.append(probe)

    activity = _request_json(
        "GET",
        f"{base}/api/activity?limit={ACTIVITY_PROBE_LIMIT}",
        token=token,
        timeout=timeout,
    )
    activity_available = activity.get("status") == 200
    activity_text = ""
    if activity_available:
        try:
            activity_text = json.dumps(activity.get("data"), sort_keys=True)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            activity_text = ""
    for probe in probes:
        if not probe.delivered:
            probe.verified = False
            continue
        if not activity_available:
            probe.verified = None
            continue
        marker = probe_ids.get(probe.harness, "")
        probe.verified = bool(marker) and (marker in activity_text)
        if not probe.verified:
            probe.detail = "delivered but not visible in /api/activity"
        else:
            probe.detail = "delivered and visible in activity"

    if activity_available:
        checks.append(DoctorCheck("activity verification", True, "GET /api/activity"))
    else:
        checks.append(
            DoctorCheck(
                "activity verification",
                True,
                "unavailable on this board (older build) — delivery unverified",
            )
        )

    delivered = [probe for probe in probes if probe.delivered]
    verified = [probe for probe in delivered if probe.verified]
    can_deliver = bool(delivered)
    if activity_available:
        can_deliver = bool(verified)
    checks.append(
        DoctorCheck(
            "adapter delivery",
            can_deliver,
            f"{len(delivered)}/{len(probes)} delivered"
            + (f", {len(verified)} verified" if activity_available else ""),
        )
    )

    ok = health_ok and can_deliver
    return DoctorResult(
        ok=ok, server=base, checks=checks, adapters=probes, coverage=coverage
    )
