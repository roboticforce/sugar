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
        """Mirror the real contract: returns an UNSTARTED thread.

        Hermes binds profile contextvars inside this helper and leaves starting
        to the caller, so a provider that forgets ``.start()`` silently does no
        background work. Tests must model that rather than paper over it.
        """
        return threading.Thread(
            target=target, args=args, kwargs=kwargs or {}, name=name, daemon=daemon
        )

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
    def _provider(
        self, plugin_module, monkeypatch, cwd, agent_context="primary", session_cwd=None
    ):
        monkeypatch.chdir(cwd)
        p = plugin_module.SugarMemoryProvider()
        kwargs = {
            "hermes_home": str(cwd),
            "platform": "test",
            "agent_context": agent_context,
        }
        if session_cwd is not None:
            kwargs["cwd"] = str(session_cwd)
        p.initialize(f"sess-{uuid4().hex[:8]}", **kwargs)
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

    # -- Hermes contract regressions ------------------------------------------

    def test_spawn_context_thread_returns_unstarted_thread(self, plugin_module):
        """The helper Hermes provides does NOT start the thread."""
        probe = plugin_module.spawn_context_thread(lambda: None, name="probe")
        assert probe.ident is None
        probe.start()
        probe.join(5)

    def _recording_spawn(self, plugin_module, monkeypatch):
        created = []
        real = plugin_module.spawn_context_thread

        def recording(target, *, name, daemon=True, args=(), kwargs=None):
            thread = real(target, name=name, daemon=daemon, args=args, kwargs=kwargs)
            created.append((name, thread))
            return thread

        monkeypatch.setattr(plugin_module, "spawn_context_thread", recording)
        return created

    def test_queue_prefetch_starts_its_background_thread(
        self, plugin_module, monkeypatch, tmp_path
    ):
        created = self._recording_spawn(plugin_module, monkeypatch)
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        p.queue_prefetch("a non-trivial question about deploy rules")
        assert [name for name, _t in created] == ["sugar-memory-prefetch"]
        thread = created[0][1]
        thread.join(5)
        assert not thread.is_alive()
        p.shutdown()

    def test_recall_refreshes_on_the_next_turn(
        self, plugin_module, monkeypatch, tmp_path
    ):
        """Regression: recall must not freeze on the first turn's result."""
        created = self._recording_spawn(plugin_module, monkeypatch)
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        p.handle_tool_call(
            "sugar_store", {"content": "Deploys go through Kamal", "scope": "global"}
        )
        first = p.prefetch("how do we deploy")
        assert "Kamal" in first

        p.handle_tool_call(
            "sugar_store",
            {"content": "Migrations use alembic upgrade head", "scope": "global"},
        )
        p.queue_prefetch("how do migrations run")
        assert created, "queue_prefetch did not spawn a background refresh"
        created[0][1].join(5)

        second = p.prefetch("how do migrations run")
        assert "alembic" in second.lower()
        assert second != first
        p.shutdown()

    def test_recall_status_matches_only_the_last_prefetch(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        p.handle_tool_call(
            "sugar_store",
            {
                "content": "Kamal deploy rule",
                "memory_type": "guideline",
                "scope": "global",
            },
        )
        assert p.prefetch("Kamal deploy")
        assert p.recall_status() is not None
        # A trivial prompt injects nothing, so the indicator must go away.
        assert p.prefetch("thanks!") == ""
        assert p.recall_status() is None
        p.shutdown()

    def test_uses_session_cwd_for_project_store(
        self, plugin_module, monkeypatch, tmp_path
    ):
        """Regression: project memory follows the session workspace, not os.getcwd()."""
        workspace = tmp_path / "workspace"
        (workspace / ".sugar").mkdir(parents=True)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()

        p = self._provider(plugin_module, monkeypatch, elsewhere, session_cwd=workspace)
        r = json.loads(
            p.handle_tool_call(
                "sugar_store", {"content": "project-only fact", "scope": "project"}
            )
        )
        assert r["status"] == "stored"
        assert r["scope"] == "project"
        assert (workspace / ".sugar" / "memory.db").exists()
        p.shutdown()

    def test_malformed_search_limit_env_does_not_break_initialize(
        self, plugin_module, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("SUGAR_SEARCH_LIMIT", "not-a-number")
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        assert p._config["search_limit"] == 5
        p.shutdown()

    def test_search_limit_coercion(self, plugin_module):
        assert plugin_module._coerce_int("7", 5, 1, 20) == 7
        assert plugin_module._coerce_int("0", 5, 1, 20) == 5
        assert plugin_module._coerce_int("9999", 5, 1, 20) == 5
        assert plugin_module._coerce_int(None, 5, 1, 20) == 5
        assert plugin_module._coerce_int("abc", 5, 1, 20) == 5

    def test_session_switch_rebinds_and_resets_recall(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        p.handle_tool_call(
            "sugar_store", {"content": "Kamal deploy rule", "scope": "global"}
        )
        assert p.prefetch("Kamal deploy")
        p.on_session_switch("brand-new-session", reset=True)
        assert p._session_id == "brand-new-session"
        assert p._cached_block == ""
        assert p.recall_status() is None
        p.shutdown()

    def test_backup_paths_include_the_global_store(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        p.handle_tool_call("sugar_store", {"content": "backup me", "scope": "global"})
        paths = p.backup_paths()
        assert paths and all(Path(x).exists() for x in paths)
        assert any(x.endswith("memory.db") for x in paths)
        p.shutdown()

    def test_backup_paths_works_without_initialize(self, plugin_module):
        assert isinstance(plugin_module.SugarMemoryProvider().backup_paths(), list)

    def test_plugin_manifest_is_catalog_shaped(self):
        import yaml

        manifest = yaml.safe_load(
            (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
        )
        assert manifest["name"] == "sugar"
        for field in ("version", "description", "requires_hermes"):
            assert manifest.get(field), f"plugin.yaml is missing {field}"
        assert (
            "pip_dependencies" not in manifest
        ), "use the canonical python_dependencies field"
        deps = manifest["python_dependencies"]
        spec = next(d for d in deps if d.startswith("sugarai"))
        assert ">=" in spec and "<" in spec, "declare a floor and an upper bound"

    # -- v2 surfaces: checkpoint API v2, built-in memory mirroring, skill ------

    def test_checkpoint_api_v2_declared(self, plugin_module):
        # The host reads this off the class, before/without initialize().
        assert (
            plugin_module.SugarMemoryProvider.pre_compress_checkpoint_api_version == 2
        )

    def _recent(self, p, memory_type=None, limit=20):
        payload = {"limit": limit}
        if memory_type:
            payload["memory_type"] = memory_type
        return json.loads(p.handle_tool_call("sugar_list_recent", payload))

    def test_checkpoint_archives_transcript_evidence(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        messages = [
            {
                "role": "user",
                "content": "We must use Kamal for deploys, not raw docker.",
            },
            {"role": "assistant", "content": "Understood - Kamal it is."},
            {"role": "system", "content": "system prompt that must not be archived"},
            {"role": "tool", "content": "tool output that must not be archived"},
        ]
        out = p.on_pre_compress(messages, require_checkpoint=True)
        assert "Sugar archived" in out
        entries = self._recent(p, memory_type="research")
        assert any("Kamal" in e["content"] for e in entries)
        assert not any("system prompt" in e["content"] for e in entries)
        p.shutdown()

    def test_checkpoint_is_idempotent(self, plugin_module, monkeypatch, tmp_path):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        messages = [{"role": "user", "content": "same transcript"}]
        first = p.on_pre_compress(messages, require_checkpoint=True)
        second = p.on_pre_compress(messages, require_checkpoint=True)
        assert "Sugar archived" in first
        assert "already archived" in second
        assert len(self._recent(p, memory_type="research")) == 1
        p.shutdown()

    def test_checkpoint_skips_message_lists_without_evidence(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        assert p.on_pre_compress([{"role": "system", "content": "x"}]) == ""
        assert p.on_pre_compress([]) == ""
        p.shutdown()

    def test_checkpoint_truncates_oversized_transcripts(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        messages = [{"role": "user", "content": "x" * 9000}]
        assert "truncated" in p.on_pre_compress(messages, require_checkpoint=True)
        entries = self._recent(p, memory_type="research")
        assert len(entries[0]["content"]) <= 4200
        p.shutdown()

    def test_checkpoint_fails_closed_when_archive_raises(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(plugin_module, monkeypatch, tmp_path)

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(p, "_store", boom)
        with pytest.raises(RuntimeError, match="checkpoint archive failed"):
            p.on_pre_compress(
                [{"role": "user", "content": "hello"}], require_checkpoint=True
            )
        p.shutdown()

    def test_checkpoint_fails_closed_when_archive_is_not_committed(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        monkeypatch.setattr(
            p, "_store", lambda *a, **k: {"status": "skipped", "reason": "read-only"}
        )
        with pytest.raises(RuntimeError, match="not committed"):
            p.on_pre_compress(
                [{"role": "user", "content": "hello"}], require_checkpoint=True
            )
        p.shutdown()

    def test_checkpoint_writes_even_in_non_primary_context(
        self, plugin_module, monkeypatch, tmp_path
    ):
        """A checkpoint is a durability guarantee, not background turn traffic."""
        p = self._provider(plugin_module, monkeypatch, tmp_path, agent_context="cron")
        out = p.on_pre_compress(
            [{"role": "user", "content": "cron run evidence"}], require_checkpoint=True
        )
        assert "Sugar archived" in out
        assert self._recent(p, memory_type="research")
        p.shutdown()

    def test_memory_write_mirrors_user_profile_as_global_preference(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        p.on_memory_write("add", "user", "Prefers terse answers")
        entries = self._recent(p, memory_type="preference")
        assert any("terse" in e["content"] for e in entries)
        assert entries[0]["type"] == "preference"
        p.shutdown()

    def test_memory_write_mirrors_agent_notes(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        p.on_memory_write("add", "memory", "This repo uses uv, not pip")
        entries = self._recent(p, memory_type="research")
        assert any("uv, not pip" in e["content"] for e in entries)
        p.shutdown()

    def test_memory_write_dedupes_identical_content(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        p.on_memory_write("add", "user", "Prefers terse answers")
        p.on_memory_write("replace", "user", "Prefers terse answers")
        assert len(self._recent(p, memory_type="preference")) == 1
        p.shutdown()

    def test_memory_write_ignores_removals(self, plugin_module, monkeypatch, tmp_path):
        p = self._provider(plugin_module, monkeypatch, tmp_path)
        p.on_memory_write("remove", "user", "Prefers terse answers")
        assert self._recent(p) == []
        p.shutdown()

    def test_memory_write_respects_non_primary_context(
        self, plugin_module, monkeypatch, tmp_path
    ):
        p = self._provider(
            plugin_module, monkeypatch, tmp_path, agent_context="subagent"
        )
        p.on_memory_write("add", "user", "should not persist")
        assert self._recent(p) == []
        p.shutdown()

    def test_register_bundles_the_skill_when_host_supports_it(self, plugin_module):
        class Ctx:
            def __init__(self):
                self.provider = None
                self.skills = []

            def register_memory_provider(self, provider):
                self.provider = provider

            def register_skill(self, name, path, description):
                self.skills.append((name, path, description))

        ctx = Ctx()
        plugin_module.register(ctx)
        assert ctx.provider is not None
        assert len(ctx.skills) == 1
        name, path, description = ctx.skills[0]
        assert name == "memory"
        assert Path(path).is_file() and Path(path).name == "SKILL.md"
        assert description

    def test_register_without_skill_surface_still_registers_provider(
        self, plugin_module
    ):
        class Ctx:
            def __init__(self):
                self.provider = None

            def register_memory_provider(self, provider):
                self.provider = provider

        ctx = Ctx()
        plugin_module.register(ctx)
        assert ctx.provider is not None
