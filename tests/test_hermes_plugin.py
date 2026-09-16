"""Tests for the Hermes memory-provider plugin (hermes-plugin/).

Hermes is not a Sugar dependency, so the plugin's Hermes imports
(``agent.memory_provider`` and ``hermes_constants``) are stubbed here with the
small surface the plugin actually uses. The MemoryProvider stub mirrors the
real ABC's required methods (name, is_available, initialize, get_tool_schemas)
and the helpers the plugin imports (RecallStatus, is_trivial_prompt,
spawn_context_thread).
"""

import json
import re
import sys
import threading
import types
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import uuid4

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "hermes-plugin"


def _is_trivial_prompt(text):
    stripped = (text or "").strip()
    if not stripped or stripped.startswith("/"):
        return True
    return bool(
        re.fullmatch(
            r"(?:yes|no|ok|okay|sure|thanks|thank you|y|n|yep|nope|hi|hey|hello|"
            r"continue|go ahead|do it|proceed|got it|cool|nice|great|done|next|lgtm|k)"
            r"[\s!?.:;,]*",
            stripped,
            re.IGNORECASE,
        )
    )


@pytest.fixture()
def plugin_module(tmp_path, monkeypatch):
    """Import hermes-plugin/__init__.py with stubbed Hermes modules and Sugar's
    global DB redirected to a temp path."""
    agent_stub = types.ModuleType("agent")
    provider_stub = types.ModuleType("agent.memory_provider")

    class MemoryProvider(ABC):  # minimal mirror of the real contract
        @property
        @abstractmethod
        def name(self): ...

        @abstractmethod
        def is_available(self): ...

        @abstractmethod
        def initialize(self, session_id: str, **kwargs): ...

        @abstractmethod
        def get_tool_schemas(self): ...

        def system_prompt_block(self):
            return ""

        def prefetch(self, query, *, session_id=""):
            return ""

        def queue_prefetch(self, query, *, session_id=""):
            return

        def recall_status(self):
            return None

        def sync_turn(self, *args, **kwargs):
            return

        def handle_tool_call(self, tool_name, args, **kwargs):
            raise NotImplementedError

        def shutdown(self):
            return

    class RecallStatus:
        def __init__(self, provider_label, count, glyph="\U0001f9e0"):
            self.provider_label = provider_label
            self.count = count
            self.glyph = glyph

    def spawn_context_thread(target, *, name, daemon=True, args=(), kwargs=None):
        t = threading.Thread(
            target=target, args=args, kwargs=kwargs or {}, name=name, daemon=daemon
        )
        t.start()
        return t

    provider_stub.MemoryProvider = MemoryProvider
    provider_stub.RecallStatus = RecallStatus
    provider_stub.is_trivial_prompt = _is_trivial_prompt
    provider_stub.spawn_context_thread = spawn_context_thread
    agent_stub.memory_provider = provider_stub
    monkeypatch.setitem(sys.modules, "agent", agent_stub)
    monkeypatch.setitem(sys.modules, "agent.memory_provider", provider_stub)

    # Stub hermes_constants so $HERMES_HOME/sugar.json resolves to tmp_path.
    hermes_constants_stub = types.ModuleType("hermes_constants")
    hermes_constants_stub.get_hermes_home = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "hermes_constants", hermes_constants_stub)

    # Redirect Sugar's global store (Path.home()/".sugar") to a temp path.
    monkeypatch.setattr(
        "sugar.memory.global_store.GLOBAL_DB_PATH",
        tmp_path / "global" / ".sugar" / "memory.db",
    )

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sugar_hermes_plugin_under_test",
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


class TestSugarMemoryProvider:
    def _provider(self, plugin_module, monkeypatch, cwd, agent_context="primary"):
        monkeypatch.chdir(cwd)
        p = plugin_module.SugarMemoryProvider()
        p.initialize(
            f"sess-{uuid4().hex[:8]}",
            hermes_home="/tmp",
            platform="test",
            agent_context=agent_context,
        )
        return p

    def test_register_calls_register_memory_provider(self, plugin_module):
        class Ctx:
            provider = None

            def register_memory_provider(self, provider):
                self.provider = provider

        ctx = Ctx()
        plugin_module.register(ctx)
        assert ctx.provider is not None
        assert ctx.provider.name == "sugar"

    def test_is_available_true_when_sugar_importable(self, plugin_module):
        assert plugin_module.SugarMemoryProvider().is_available() is True

    def test_tool_schemas(self, plugin_module):
        schemas = plugin_module.SugarMemoryProvider().get_tool_schemas()
        assert [s["name"] for s in schemas] == [
            "sugar_store",
            "sugar_search",
            "sugar_list_recent",
        ]
        for schema in schemas:
            assert schema["parameters"]["type"] == "object"

    def test_system_prompt_block(self, plugin_module):
        block = plugin_module.SugarMemoryProvider().system_prompt_block()
        assert "sugar_store" in block and "sugar_search" in block

    def test_store_and_search_roundtrip(self, plugin_module, monkeypatch, tmp_path):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        r = json.loads(
            p.handle_tool_call(
                "sugar_store",
                {
                    "content": "Use Kamal for deploys",
                    "memory_type": "decision",
                    "tags": "deploy",
                },
            )
        )
        assert r["status"] == "stored"

        r = json.loads(p.handle_tool_call("sugar_search", {"query": "Kamal deploy"}))
        assert any("Kamal" in item["content"] for item in r)
        p.shutdown()

    def test_store_global_scope(self, plugin_module, monkeypatch, tmp_path):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        r = json.loads(
            p.handle_tool_call(
                "sugar_store",
                {
                    "content": "Pin dependencies exactly",
                    "memory_type": "guideline",
                    "scope": "global",
                },
            )
        )
        assert r["status"] == "stored" and r["scope"] == "global"
        p.shutdown()

    def test_store_project_scope_promotes_without_project(
        self, plugin_module, monkeypatch, tmp_path
    ):
        # cwd has no .sugar -> project scope is promoted to global, not an error
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        r = json.loads(
            p.handle_tool_call("sugar_store", {"content": "note", "scope": "project"})
        )
        assert r["status"] == "stored" and r["scope"] == "global"
        assert "note" in r
        p.shutdown()

    def test_list_recent(self, plugin_module, monkeypatch, tmp_path):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        p.handle_tool_call("sugar_store", {"content": "entry one", "scope": "global"})
        r = json.loads(p.handle_tool_call("sugar_list_recent", {"limit": 5}))
        assert len(r) >= 1
        assert {"id", "type", "content"} <= set(r[0].keys())
        p.shutdown()

    def test_unknown_tool_returns_error(self, plugin_module, monkeypatch, tmp_path):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        r = json.loads(p.handle_tool_call("sugar_nope", {}))
        assert "error" in r
        p.shutdown()

    def test_empty_store_rejected(self, plugin_module, monkeypatch, tmp_path):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        r = json.loads(p.handle_tool_call("sugar_store", {"content": "  "}))
        assert "error" in r
        p.shutdown()

    def test_invalid_type_falls_back_to_decision(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        r = json.loads(
            p.handle_tool_call("sugar_store", {"content": "x", "memory_type": "bogus"})
        )
        assert r["status"] == "stored" and r["type"] == "decision"
        p.shutdown()

    def test_prefetch_returns_cached_recall(self, plugin_module, monkeypatch, tmp_path):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        p.handle_tool_call(
            "sugar_store",
            {
                "content": "Deploy rule: use Kamal",
                "memory_type": "guideline",
                "scope": "global",
            },
        )
        block = p.prefetch("Kamal deploy rules")
        assert "Kamal" in block
        st = p.recall_status()
        assert st is not None and st.count >= 1
        # Cached: second call returns the same block without re-searching
        assert p.prefetch("anything else") == block
        p.shutdown()

    def test_prefetch_trivial_prompt_skips_search(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        assert p.prefetch("thanks!") == ""
        assert p.recall_status() is None
        p.shutdown()

    def test_non_primary_context_never_writes(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(
            plugin_module, monkeypatch, tmp_path, agent_context="subagent"
        )
        r = json.loads(
            p.handle_tool_call("sugar_store", {"content": "should not persist"})
        )
        assert r["status"] == "skipped"
        p.shutdown()

    def test_sync_turn_off_by_default(self, plugin_module, monkeypatch, tmp_path):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        p.sync_turn("make a note of this", "ok")
        r = json.loads(p.handle_tool_call("sugar_list_recent", {"limit": 20}))
        assert not any("Conversation turn" in e["content"] for e in r)
        p.shutdown()

    def test_config_env_and_json_layering(self, plugin_module, monkeypatch, tmp_path):
        monkeypatch.setenv("SUGAR_SEARCH_LIMIT", "3")
        assert plugin_module._load_provider_config()["search_limit"] == 3
        # $HERMES_HOME/sugar.json (stubbed to tmp_path) overrides env
        (tmp_path / "sugar.json").write_text(json.dumps({"search_limit": 7}))
        assert plugin_module._load_provider_config()["search_limit"] == 7
