"""Tests for the SDK telemetry integration helpers.

No optional SDK and no network are required: ADK callbacks are exercised through
:class:`SugarADKCallbacks`, OpenAI spans through duck-typed fakes, and delivery
through an injected transport (or a monkeypatched ``post_event``).
"""

import types
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sugar import activity
from sugar.integrations import client as client_module
from sugar.integrations.adk import (
    ADK_AVAILABLE,
    SugarADKCallbacks,
    SugarADKPlugin,
    install as install_adk,
)
from sugar.integrations.client import EmitResult, SugarTelemetryClient
from sugar.integrations.claude_sdk import make_callbacks as make_claude_callbacks
from sugar.integrations.openai_agents import (
    SugarTraceProcessor,
    install as install_openai,
)
from sugar.integrations import openai_agents as openai_module
from sugar.telemetry.schema import (
    SCHEMA,
    TelemetryValidationError,
    validate_envelope,
)

ENV_NAMES = (
    "SUGAR_BOARD_URL",
    "SUGAR_BOARD_TOKEN",
    "SUGAR_TELEMETRY_REDACTION",
    "SUGAR_TELEMETRY_MAX_BYTES",
    "SUGAR_TELEMETRY_DEBUG",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Never inherit the developer's telemetry configuration."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


class RecordingClient:
    """Duck-typed stand-in for SugarTelemetryClient that records emit calls."""

    def __init__(self):
        self.events = []
        self.raise_on_emit = False

    def emit(self, kind, harness=None, **fields):
        if self.raise_on_emit:
            raise RuntimeError("transport exploded")
        self.events.append({"kind": kind, "harness": harness, **fields})
        return EmitResult(ok=True, kind=kind)

    def kinds(self):
        return [event["kind"] for event in self.events]


def _payload(event):
    return event.get("summary") or {}


# ---------------------------------------------------------------------------
# client: canonical envelope + redaction
# ---------------------------------------------------------------------------


class TestClientEnvelope:
    def test_build_event_is_canonical_envelope(self):
        client = SugarTelemetryClient(
            harness="adk", actor="root_agent", session_id="sess-1"
        )
        event = client.build_event(
            "tool.completed",
            tool_name="Bash",
            tool_use_id="toolu_1",
            status="completed",
            duration_ms=842,
            fidelity="L3",
            summary={"command": "pytest -q", "exit_code": 0},
        )

        validated = validate_envelope(event)
        assert validated["kind"] == "tool.completed"
        assert validated["harness"] == "adk"
        assert validated["actor"] == "root_agent"
        assert validated["session_id"] == "sess-1"
        assert validated["source"] == "telemetry:sdk"
        assert validated["ts"].endswith("Z")
        payload = validated["payload"]
        assert payload["schema"] == SCHEMA
        assert payload["tool_name"] == "Bash"
        assert payload["tool_use_id"] == "toolu_1"
        assert payload["status"] == "completed"
        assert payload["duration_ms"] == 842
        assert payload["fidelity"] == "L3"
        assert payload["payload"]["command"] == "pytest -q"

    def test_build_event_redacts_secrets_and_sensitive_keys(self):
        client = SugarTelemetryClient(harness="adk", redaction="summary")
        event = client.build_event(
            "tool.completed",
            summary={
                "command": "curl -H 'Authorization: Bearer abcdefgh12345678'",
                "api_key": "sk-abcdefghijklmnopqrstuvwxyz",
                "nested": {"password": "hunter2"},
            },
        )
        summary = event["payload"]["payload"]
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in str(summary)
        assert summary["api_key"] == "[REDACTED]"
        assert summary["nested"]["password"] == "[REDACTED]"
        assert "Bearer abcdefgh12345678" not in str(summary)

    def test_metadata_redaction_drops_summary(self, monkeypatch):
        monkeypatch.setenv("SUGAR_TELEMETRY_REDACTION", "metadata")
        client = SugarTelemetryClient(harness="adk")
        event = client.build_event("tool.started", summary={"command": "pytest"})
        assert event["payload"]["payload"] == {}

    def test_full_redaction_keeps_content_but_still_redacts_secrets(self, monkeypatch):
        monkeypatch.setenv("SUGAR_TELEMETRY_REDACTION", "full")
        client = SugarTelemetryClient(harness="adk")
        long_output = "x" * 600
        event = client.build_event(
            "tool.completed",
            summary={"output": long_output, "token": "ghp_" + "a" * 30},
        )
        summary = event["payload"]["payload"]
        assert summary["output"] == long_output
        assert summary["token"] == "[REDACTED]"

    def test_max_bytes_truncates_summary(self):
        client = SugarTelemetryClient(harness="adk", max_bytes=120)
        event = client.build_event("tool.completed", summary={"output": "x" * 5000})
        summary = event["payload"]["payload"]
        assert summary.get("_truncated") is True
        assert len(str(summary)) < 500

    def test_empty_kind_is_rejected(self):
        client = SugarTelemetryClient(harness="adk")
        with pytest.raises(TelemetryValidationError):
            client.build_event("", summary={})

    def test_timeout_is_clamped(self):
        assert SugarTelemetryClient(timeout=0.01).timeout == 0.2
        assert SugarTelemetryClient(timeout=600).timeout == 5.0
        assert SugarTelemetryClient().timeout == 1.5


# ---------------------------------------------------------------------------
# client: delivery is fail-open and env-configurable
# ---------------------------------------------------------------------------


class TestClientDelivery:
    def test_emit_posts_canonical_body_and_returns_result(self):
        captured = {}

        def transport(body):
            captured["body"] = body
            return {"id": 42, **body}

        client = SugarTelemetryClient(harness="adk", transport=transport)
        result = client.emit(
            "tool.started",
            tool_name="Bash",
            status="started",
            fidelity="L3",
            summary={"input": {"command": "pytest"}},
        )

        assert result.ok is True
        assert result.kind == "tool.started"
        assert result.event["id"] == 42
        body = captured["body"]
        assert validate_envelope(body)["payload"]["schema"] == SCHEMA
        assert body["harness"] == "adk"
        assert body["payload"]["payload"]["input"]["command"] == "pytest"

    def test_emit_never_raises_when_board_unreachable(self, monkeypatch):
        def boom(*args, **kwargs):
            raise activity.ActivityError("cannot reach the Sugar board")

        monkeypatch.setattr(client_module.activity, "post_event", boom)
        client = SugarTelemetryClient(harness="adk")
        result = client.emit("tool.failed", summary={"error": "boom"})

        assert result.ok is False
        assert result.event is None
        assert "cannot reach" in result.error

    def test_emit_never_raises_on_invalid_board_url(self, monkeypatch):
        monkeypatch.setenv("SUGAR_BOARD_URL", "ftp://not-http")
        client = SugarTelemetryClient(harness="adk")
        result = client.emit("tool.started", summary={})
        assert result.ok is False
        assert "invalid board URL" in result.error

    def test_board_url_env_override(self, monkeypatch):
        assert SugarTelemetryClient().ingest_url() == "http://127.0.0.1:7331/api/events"
        monkeypatch.setenv("SUGAR_BOARD_URL", "http://board.example:8123/")
        client = SugarTelemetryClient()
        assert client.ingest_url() == "http://board.example:8123/api/events"

    def test_post_rejects_invalid_envelope_without_raising(self):
        client = SugarTelemetryClient(harness="adk")
        result = client.post({"kind": "tool.started", "harness": "adk"})
        assert result.ok is False
        assert result.error
        # Not a dict at all: still fail-open.
        assert client.post(None).ok is False

    def test_debug_traces_failure_without_payload(self, monkeypatch, capsys):
        monkeypatch.setenv("SUGAR_TELEMETRY_DEBUG", "1")

        def boom(body):
            raise RuntimeError("connection refused")

        client = SugarTelemetryClient(harness="adk", transport=boom)
        secret = "sk-this-should-never-be-printed"
        result = client.emit("tool.started", summary={"api_key": secret})

        assert result.ok is False
        err = capsys.readouterr().err
        assert "[sugar-telemetry]" in err
        assert secret not in err
        assert "connection refused" in err


# ---------------------------------------------------------------------------
# ADK plugin
# ---------------------------------------------------------------------------


def _adk_context(**overrides):
    values = {
        "session": SimpleNamespace(id="sess-1"),
        "invocation_id": "inv-1",
        "function_call_id": "toolu_1",
        "state": {"sugar_task_id": "task-9", "sugar_project": "sugar"},
        "cwd": "/repo",
        "agent": SimpleNamespace(name="root_agent"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestADKPlugin:
    def test_plugin_requires_google_adk(self):
        if ADK_AVAILABLE:  # pragma: no cover - google-adk is not a test dep
            pytest.skip("google-adk is installed")
        with pytest.raises(ImportError, match="google-adk"):
            SugarADKPlugin()
        with pytest.raises(ImportError, match="google-adk"):
            install_adk()

    @pytest.mark.asyncio
    async def test_tool_lifecycle_emits_started_and_completed(self):
        recorder = RecordingClient()
        callbacks = SugarADKCallbacks(recorder)
        tool = SimpleNamespace(name="Bash")
        context = _adk_context()

        assert (
            await callbacks.before_tool_callback(
                tool=tool, args={"command": "pytest -q"}, tool_context=context
            )
            is None
        )
        assert (
            await callbacks.after_tool_callback(
                tool=tool,
                args={"command": "pytest -q"},
                tool_context=context,
                tool_response={"exit_code": 0},
            )
            is None
        )

        assert recorder.kinds() == ["tool.started", "tool.completed"]
        started, completed = recorder.events
        assert started["tool_name"] == "Bash"
        assert started["tool_use_id"] == "toolu_1"
        assert started["session_id"] == "sess-1"
        assert started["run_id"] == "inv-1"
        assert started["task_id"] == "task-9"
        assert started["project"] == "sugar"
        assert started["cwd"] == "/repo"
        assert started["actor"] == "root_agent"
        assert started["fidelity"] == "L3"
        assert started["status"] == "started"
        assert _payload(started)["input"] == {"command": "pytest -q"}
        assert completed["status"] == "completed"
        assert isinstance(completed["duration_ms"], int)
        assert _payload(completed)["response"] == {"exit_code": 0}

    @pytest.mark.asyncio
    async def test_tool_error_callback_emits_failed(self):
        recorder = RecordingClient()
        callbacks = SugarADKCallbacks(recorder)
        tool = SimpleNamespace(name="Bash")
        context = _adk_context()
        await callbacks.before_tool_callback(tool=tool, args={}, tool_context=context)
        await callbacks.on_tool_error_callback(
            tool=tool,
            args={},
            tool_context=context,
            error=ValueError("command failed"),
        )

        assert recorder.kinds() == ["tool.started", "tool.failed"]
        failed = recorder.events[-1]
        assert failed["status"] == "failed"
        assert "command failed" in _payload(failed)["error"]

    @pytest.mark.asyncio
    async def test_callbacks_swallow_client_failures(self):
        recorder = RecordingClient()
        recorder.raise_on_emit = True
        callbacks = SugarADKCallbacks(recorder)
        tool = SimpleNamespace(name="Bash")
        assert (
            await callbacks.before_tool_callback(tool=tool, args={}, tool_context=None)
            is None
        )
        assert (
            await callbacks.after_tool_callback(
                tool=tool, args={}, tool_context=None, tool_response=None
            )
            is None
        )
        assert (
            await callbacks.on_tool_error_callback(
                tool=tool, args={}, tool_context=None, error="boom"
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_callbacks_tolerate_missing_context(self):
        recorder = RecordingClient()
        callbacks = SugarADKCallbacks(recorder, task_id="fallback-task")
        await callbacks.before_tool_callback(tool="shell", args=None, tool_context=None)
        event = recorder.events[0]
        assert event["tool_name"] == "shell"
        assert event["task_id"] == "fallback-task"
        assert event["session_id"] is None
        assert event["cwd"]  # falls back to os.getcwd()


# ---------------------------------------------------------------------------
# OpenAI Agents SDK trace processor
# ---------------------------------------------------------------------------


class FunctionSpanData:  # matches the SDK class name we dispatch on
    def __init__(self, name="Bash", input=None, output=None):
        self.name = name
        self.input = input
        self.output = output


class GenerationSpanData:
    def __init__(self, model="gpt-5", usage=None):
        self.model = model
        self.usage = usage


class AgentSpanData:
    def __init__(self, name="researcher"):
        self.name = name


class FakeSpan:
    def __init__(
        self,
        span_data,
        span_id="span-1",
        trace_id="trace-1",
        parent_id=None,
        error=None,
    ):
        self.span_data = span_data
        self.span_id = span_id
        self.trace_id = trace_id
        self.parent_id = parent_id
        self.error = error
        self.started_at = datetime(2026, 11, 19, 16, 0, 0, tzinfo=timezone.utc)
        self.ended_at = datetime(2026, 11, 19, 16, 0, 1, tzinfo=timezone.utc)


class FakeTrace:
    def __init__(
        self,
        trace_id="trace-1",
        group_id="conv-1",
        metadata=None,
        name="agent_run",
        error=None,
    ):
        self.trace_id = trace_id
        self.group_id = group_id
        self.metadata = metadata or {}
        self.name = name
        self.error = error
        self.started_at = datetime(2026, 11, 19, 16, 0, 0, tzinfo=timezone.utc)
        self.ended_at = datetime(2026, 11, 19, 16, 0, 5, tzinfo=timezone.utc)


class TestOpenAIProcessor:
    def test_function_span_maps_to_tool_started_and_completed(self):
        recorder = RecordingClient()
        processor = SugarTraceProcessor(recorder)
        span = FakeSpan(FunctionSpanData(input={"command": "pytest"}, output="ok"))

        processor.on_span_start(span)
        processor.on_span_end(span)

        assert recorder.kinds() == ["tool.started", "tool.completed"]
        started, completed = recorder.events
        assert started["tool_name"] == "Bash"
        assert started["tool_use_id"] == "span-1"
        assert started["trace_id"] == "trace-1"
        assert started["span_id"] == "span-1"
        assert started["fidelity"] == "L3"
        assert _payload(started)["input"] == {"command": "pytest"}
        assert completed["status"] == "completed"
        assert isinstance(completed["duration_ms"], int)
        assert _payload(completed)["output"] == "ok"

    def test_span_error_maps_to_tool_failed(self):
        recorder = RecordingClient()
        processor = SugarTraceProcessor(recorder)
        span = FakeSpan(FunctionSpanData(input={"command": "pytest"}), error="boom")

        processor.on_span_start(span)
        processor.on_span_end(span)

        assert recorder.kinds() == ["tool.started", "tool.failed"]
        failed = recorder.events[-1]
        assert failed["status"] == "failed"
        assert _payload(failed)["error"] == "boom"

    def test_generation_span_maps_to_usage_reported(self):
        recorder = RecordingClient()
        processor = SugarTraceProcessor(recorder)
        span = FakeSpan(
            GenerationSpanData(
                model="gpt-5", usage={"input_tokens": 12, "output_tokens": 4}
            ),
            span_id="span-gen",
        )

        processor.on_span_start(span)
        processor.on_span_end(span)

        assert recorder.kinds() == ["usage.reported"]
        usage = recorder.events[0]
        assert usage["model"] == "gpt-5"
        assert usage["fidelity"] == "L1"
        assert usage["status"] == "completed"
        assert usage.get("tool_name") is None
        assert _payload(usage)["usage"] == {"input_tokens": 12, "output_tokens": 4}

    def test_trace_and_agent_spans_map_to_run_and_turn(self):
        recorder = RecordingClient()
        processor = SugarTraceProcessor(recorder)
        trace = FakeTrace(metadata={"sugar_task_id": "task-7"})

        processor.on_trace_start(trace)
        span = FakeSpan(AgentSpanData(name="researcher"), span_id="span-agent")
        processor.on_span_start(span)
        processor.on_span_end(span)
        processor.on_trace_end(trace)

        assert recorder.kinds() == [
            "run.started",
            "turn.started",
            "turn.ended",
            "run.ended",
        ]
        started = recorder.events[0]
        assert started["run_id"] == "trace-1"
        assert started["trace_id"] == "trace-1"
        assert started["session_id"] == "conv-1"
        assert started["task_id"] == "task-7"
        turn = recorder.events[1]
        assert _payload(turn)["agent"] == "researcher"
        ended = recorder.events[-1]
        assert ended["status"] == "completed"
        assert isinstance(ended["duration_ms"], int)

    def test_trace_error_marks_run_failed(self):
        recorder = RecordingClient()
        processor = SugarTraceProcessor(recorder)
        trace = FakeTrace(error="agent crashed")
        processor.on_trace_start(trace)
        processor.on_trace_end(trace)
        assert recorder.kinds() == ["run.started", "run.ended"]
        assert recorder.events[-1]["status"] == "failed"

    def test_processor_tolerates_unknown_or_missing_spans(self):
        recorder = RecordingClient()
        processor = SugarTraceProcessor(recorder)
        processor.on_span_start(SimpleNamespace())
        processor.on_span_end(SimpleNamespace())
        processor.on_span_start(SimpleNamespace(span_data=object()))
        processor.on_span_end(SimpleNamespace(span_data=None))
        processor.on_trace_start(SimpleNamespace())
        processor.on_trace_end(SimpleNamespace())
        assert recorder.events == []

    def test_processor_swallows_client_failures(self):
        recorder = RecordingClient()
        recorder.raise_on_emit = True
        processor = SugarTraceProcessor(recorder)
        processor.on_span_start(FakeSpan(FunctionSpanData()))
        processor.on_span_end(FakeSpan(FunctionSpanData()))
        processor.shutdown()
        processor.force_flush()

    def test_install_requires_openai_agents(self):
        try:
            import agents.tracing  # noqa: F401
        except ImportError:
            pass
        else:  # pragma: no cover - openai-agents is not a test dep
            pytest.skip("openai-agents is installed")
        with pytest.raises(ImportError, match="openai-agents"):
            install_openai()

    def test_install_registers_processor(self, monkeypatch):
        fake_tracing = types.ModuleType("agents.tracing")
        registered = []
        fake_tracing.add_trace_processor = registered.append
        fake_tracing.set_trace_processors = lambda processors: registered.append(
            processors
        )
        monkeypatch.setattr(openai_module, "_import_tracing", lambda: fake_tracing)
        recorder = RecordingClient()

        processor = install_openai(client=recorder)
        assert isinstance(processor, SugarTraceProcessor)
        assert processor in registered

        processor = install_openai(client=recorder, replace=True)
        assert registered[-1] == [processor]


# ---------------------------------------------------------------------------
# Claude Agent SDK hooks
# ---------------------------------------------------------------------------


class TestClaudeHooks:
    @pytest.mark.asyncio
    async def test_tool_lifecycle_emits_started_and_completed(self):
        recorder = RecordingClient()
        callbacks = make_claude_callbacks(recorder)
        hook_input = {
            "hook_event_name": "PreToolUse",
            "session_id": "sess-1",
            "cwd": "/repo",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q"},
        }

        assert await callbacks["PreToolUse"](hook_input, "toolu_1", None) == {}
        assert (
            await callbacks["PostToolUse"](
                {
                    **hook_input,
                    "hook_event_name": "PostToolUse",
                    "tool_response": {"exit_code": 0},
                },
                "toolu_1",
                None,
            )
            == {}
        )

        assert recorder.kinds() == ["tool.started", "tool.completed"]
        started, completed = recorder.events
        assert started["tool_name"] == "Bash"
        assert started["tool_use_id"] == "toolu_1"
        assert started["session_id"] == "sess-1"
        assert started["cwd"] == "/repo"
        assert _payload(started)["input"] == {"command": "pytest -q"}
        assert isinstance(completed["duration_ms"], int)
        assert _payload(completed)["response"] == {"exit_code": 0}

    @pytest.mark.asyncio
    async def test_failure_hook_emits_tool_failed(self):
        recorder = RecordingClient()
        callbacks = make_claude_callbacks(recorder)
        await callbacks["PostToolUseFailure"](
            {
                "hook_event_name": "PostToolUseFailure",
                "tool_name": "Bash",
                "tool_input": {"command": "pytest"},
                "error": "exit code 1",
            },
            "toolu_2",
            None,
        )
        assert recorder.kinds() == ["tool.failed"]
        failed = recorder.events[0]
        assert failed["status"] == "failed"
        assert _payload(failed)["error"] == "exit code 1"

    def test_make_hooks_wraps_sdk_hook_matchers(self):
        pytest.importorskip("claude_agent_sdk")
        from sugar.integrations.claude_sdk import make_hooks

        recorder = RecordingClient()
        hooks = make_hooks(recorder)
        assert sorted(hooks) == ["PostToolUse", "PostToolUseFailure", "PreToolUse"]
        matchers = hooks["PreToolUse"]
        assert len(matchers) == 1
        assert callable(matchers[0].hooks[0])


# ---------------------------------------------------------------------------
# package surface
# ---------------------------------------------------------------------------


def test_lazy_package_exports_work_without_optional_sdks():
    from sugar.integrations import (
        ADK_AVAILABLE as module_adk_available,
        EmitResult as module_emit_result,
        SugarTelemetryClient as module_client,
        SugarTraceProcessor as module_processor,
    )

    assert module_client is SugarTelemetryClient
    assert module_emit_result is EmitResult
    assert module_processor is SugarTraceProcessor
    assert module_adk_available is ADK_AVAILABLE
