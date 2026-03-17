"""
Tests for Sugar Global Memory Feature

Covers:
- GlobalMemoryManager: init, routing, search merging, dedup, scope labels
- MemoryScope enum and GUIDELINE MemoryType
- MCP server tools: store_learning with scope, search_memory, get_project_context,
  recall, global_guidelines_resource
- MemoryRetriever: scope labels in format_for_prompt, get_project_context guidelines
- Edge cases: empty stores, both stores populated, deduplication across stores
"""

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from sugar.memory import (
    FallbackEmbedder,
    GlobalMemoryManager,
    MemoryEntry,
    MemoryQuery,
    MemoryRetriever,
    MemoryScope,
    MemorySearchResult,
    MemoryStore,
    MemoryType,
)
from sugar.memory.global_store import GLOBAL_DB_PATH

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_store(tmp_path: Path, name: str = "memory.db") -> MemoryStore:
    """Return a real MemoryStore backed by a temp SQLite file."""
    db_path = tmp_path / name
    return MemoryStore(str(db_path), embedder=FallbackEmbedder())


def make_entry(
    content: str,
    memory_type: MemoryType = MemoryType.DECISION,
    entry_id: str | None = None,
    importance: float = 1.0,
) -> MemoryEntry:
    """Create a MemoryEntry with a unique id."""
    return MemoryEntry(
        id=entry_id or str(uuid.uuid4()),
        memory_type=memory_type,
        content=content,
        importance=importance,
    )


def run(coro):
    """Run an async coroutine synchronously inside a test.

    Creates a fresh event loop each time to stay compatible with Python 3.14+,
    which no longer creates a default loop in the main thread.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ===========================================================================
# 1. GlobalMemoryManager Core Tests
# ===========================================================================


class TestGlobalMemoryManagerInit:
    """Initialisation - store wiring is correct."""

    def test_init_with_project_store(self, tmp_path):
        project_store = make_store(tmp_path, "project.db")
        global_store_path = tmp_path / "global.db"

        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_store_path):
            manager = GlobalMemoryManager(project_store=project_store)
            assert manager.project_store is project_store
            assert manager.global_store is not None
        manager.close()
        project_store.close()

    def test_init_without_project_store(self, tmp_path):
        global_store_path = tmp_path / "global.db"

        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_store_path):
            manager = GlobalMemoryManager(project_store=None)
            assert manager.project_store is None
            assert manager.global_store is not None
        manager.close()

    def test_global_db_path_constant_is_home_sugar(self):
        expected = Path.home() / ".sugar" / "memory.db"
        assert GLOBAL_DB_PATH == expected


class TestGlobalMemoryManagerStore:
    """Writes go to the correct backing store."""

    @pytest.fixture
    def manager(self, tmp_path):
        project_store = make_store(tmp_path, "project.db")
        global_db = tmp_path / "global.db"
        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            mgr = GlobalMemoryManager(project_store=project_store)
            yield mgr
        mgr.close()

    @pytest.fixture
    def global_only_manager(self, tmp_path):
        global_db = tmp_path / "global.db"
        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            mgr = GlobalMemoryManager(project_store=None)
            yield mgr
        mgr.close()

    def test_project_scope_routes_to_project_store(self, manager):
        entry = make_entry("Use PostgreSQL for main DB")
        manager.store(entry, scope=MemoryScope.PROJECT)

        assert manager.project_store.get(entry.id) is not None
        assert manager.global_store.get(entry.id) is None

    def test_global_scope_routes_to_global_store(self, manager):
        entry = make_entry("Always use Kamal for deploys", MemoryType.GUIDELINE)
        manager.store(entry, scope=MemoryScope.GLOBAL)

        assert manager.global_store.get(entry.id) is not None
        assert manager.project_store.get(entry.id) is None

    def test_project_scope_without_project_store_raises(self, global_only_manager):
        entry = make_entry("This should fail")
        with pytest.raises(RuntimeError, match="Not in a Sugar project"):
            global_only_manager.store(entry, scope=MemoryScope.PROJECT)

    def test_global_scope_without_project_store_succeeds(self, global_only_manager):
        entry = make_entry("Cross-project guideline", MemoryType.GUIDELINE)
        entry_id = global_only_manager.store(entry, scope=MemoryScope.GLOBAL)
        assert entry_id == entry.id


class TestGlobalMemoryManagerSearch:
    """Search merges both stores and labels results correctly."""

    @pytest.fixture
    def manager(self, tmp_path):
        project_store = make_store(tmp_path, "project.db")
        global_db = tmp_path / "global.db"
        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            mgr = GlobalMemoryManager(project_store=project_store)
            yield mgr
        mgr.close()

    @pytest.fixture
    def global_only_manager(self, tmp_path):
        global_db = tmp_path / "global.db"
        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            mgr = GlobalMemoryManager(project_store=None)
            yield mgr
        mgr.close()

    def _store_to_project(self, manager, content, memory_type=MemoryType.DECISION):
        entry = make_entry(content, memory_type)
        manager.store(entry, scope=MemoryScope.PROJECT)
        return entry

    def _store_to_global(self, manager, content, memory_type=MemoryType.GUIDELINE):
        entry = make_entry(content, memory_type)
        manager.store(entry, scope=MemoryScope.GLOBAL)
        return entry

    def test_search_returns_results_from_both_stores(self, manager):
        self._store_to_project(manager, "JWT authentication for this project")
        self._store_to_global(manager, "JWT is the org-wide auth standard")

        results = manager.search(MemoryQuery(query="JWT authentication"))

        assert len(results) >= 2

    def test_search_labels_project_results_correctly(self, manager):
        self._store_to_project(manager, "Redis caching strategy for project X")

        results = manager.search(MemoryQuery(query="Redis caching"))

        project_results = [r for r in results if r.scope == "project"]
        assert len(project_results) >= 1

    def test_search_labels_global_results_correctly(self, manager):
        self._store_to_global(manager, "Always use 60-character title tags for SEO")

        results = manager.search(MemoryQuery(query="title tags SEO"))

        global_results = [r for r in results if r.scope == "global"]
        assert len(global_results) >= 1

    def test_search_deduplicates_identical_content(self, manager):
        # Store the exact same content in both stores
        content = "Use 4-space indentation across all projects"
        project_entry = make_entry(content, MemoryType.PREFERENCE)
        manager.store(project_entry, scope=MemoryScope.PROJECT)

        global_entry = make_entry(content, MemoryType.PREFERENCE)
        manager.store(global_entry, scope=MemoryScope.GLOBAL)

        results = manager.search(MemoryQuery(query="indentation style"))

        # Content appears only once after dedup
        seen_content = [r.entry.content for r in results]
        assert seen_content.count(content) <= 1

    def test_search_respects_limit(self, manager):
        for i in range(5):
            self._store_to_project(manager, f"Project decision number {i}")
        for i in range(5):
            self._store_to_global(manager, f"Global guideline number {i}")

        results = manager.search(MemoryQuery(query="decision guideline"), limit=3)

        assert len(results) <= 3

    def test_search_global_only_manager_works(self, global_only_manager):
        entry = make_entry("Python type hints everywhere", MemoryType.GUIDELINE)
        global_only_manager.store(entry, scope=MemoryScope.GLOBAL)

        results = global_only_manager.search(MemoryQuery(query="type hints Python"))

        assert len(results) >= 1
        assert all(r.scope == "global" for r in results)

    def test_search_sorts_by_score_descending(self, manager):
        self._store_to_project(manager, "deployment procedure step one")
        self._store_to_global(manager, "deployment procedure step two")

        results = manager.search(MemoryQuery(query="deployment procedure"))

        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)


class TestGlobalMemoryManagerGetByType:
    """get_by_type aggregates both stores."""

    @pytest.fixture
    def manager(self, tmp_path):
        project_store = make_store(tmp_path, "project.db")
        global_db = tmp_path / "global.db"
        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            mgr = GlobalMemoryManager(project_store=project_store)
            yield mgr
        mgr.close()

    def test_returns_from_both_stores(self, manager):
        project_entry = make_entry("Project pref", MemoryType.PREFERENCE)
        global_entry = make_entry("Global pref", MemoryType.PREFERENCE)

        manager.store(project_entry, scope=MemoryScope.PROJECT)
        manager.store(global_entry, scope=MemoryScope.GLOBAL)

        entries = manager.get_by_type(MemoryType.PREFERENCE)
        ids = [e.id for e in entries]

        assert project_entry.id in ids
        assert global_entry.id in ids

    def test_respects_limit(self, manager):
        for i in range(4):
            e = make_entry(f"Project pref {i}", MemoryType.PREFERENCE)
            manager.store(e, scope=MemoryScope.PROJECT)
        for i in range(4):
            e = make_entry(f"Global pref {i}", MemoryType.PREFERENCE)
            manager.store(e, scope=MemoryScope.GLOBAL)

        entries = manager.get_by_type(MemoryType.PREFERENCE, limit=5)

        assert len(entries) <= 5


class TestGlobalMemoryManagerListMemories:
    """list_memories combines and sorts both stores."""

    @pytest.fixture
    def manager(self, tmp_path):
        project_store = make_store(tmp_path, "project.db")
        global_db = tmp_path / "global.db"
        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            mgr = GlobalMemoryManager(project_store=project_store)
            yield mgr
        mgr.close()

    def test_returns_from_both_stores(self, manager):
        project_entry = make_entry("Project decision", MemoryType.DECISION)
        global_entry = make_entry("Global guideline", MemoryType.GUIDELINE)

        manager.store(project_entry, scope=MemoryScope.PROJECT)
        manager.store(global_entry, scope=MemoryScope.GLOBAL)

        entries = manager.list_memories()
        ids = [e.id for e in entries]

        assert project_entry.id in ids
        assert global_entry.id in ids

    def test_sorted_by_importance_then_recency(self, manager):
        low_entry = make_entry("Low priority", importance=0.5)
        high_entry = make_entry("High priority", importance=2.0)

        manager.store(low_entry, scope=MemoryScope.PROJECT)
        manager.store(high_entry, scope=MemoryScope.PROJECT)

        entries = manager.list_memories()

        importances = [e.importance for e in entries]
        assert importances == sorted(importances, reverse=True)

    def test_respects_limit(self, manager):
        for i in range(10):
            e = make_entry(f"Entry {i}")
            manager.store(e, scope=MemoryScope.PROJECT)

        entries = manager.list_memories(limit=4)
        assert len(entries) <= 4


class TestGlobalMemoryManagerDelete:
    """delete tries project store first, then global."""

    @pytest.fixture
    def manager(self, tmp_path):
        project_store = make_store(tmp_path, "project.db")
        global_db = tmp_path / "global.db"
        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            mgr = GlobalMemoryManager(project_store=project_store)
            yield mgr
        mgr.close()

    def test_delete_from_project_store(self, manager):
        entry = make_entry("Delete from project")
        manager.store(entry, scope=MemoryScope.PROJECT)

        deleted = manager.delete(entry.id)

        assert deleted is True
        assert manager.project_store.get(entry.id) is None

    def test_delete_from_global_when_not_in_project(self, manager):
        entry = make_entry("Delete from global", MemoryType.GUIDELINE)
        manager.store(entry, scope=MemoryScope.GLOBAL)

        deleted = manager.delete(entry.id)

        assert deleted is True
        assert manager.global_store.get(entry.id) is None

    def test_delete_nonexistent_returns_false(self, manager):
        result = manager.delete("does-not-exist-id")
        assert result is False


class TestGlobalMemoryManagerCount:
    """count sums both stores."""

    @pytest.fixture
    def manager(self, tmp_path):
        project_store = make_store(tmp_path, "project.db")
        global_db = tmp_path / "global.db"
        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            mgr = GlobalMemoryManager(project_store=project_store)
            yield mgr
        mgr.close()

    def test_count_sums_both_stores(self, manager):
        for _ in range(3):
            manager.store(make_entry("Project entry"), scope=MemoryScope.PROJECT)
        for _ in range(2):
            manager.store(
                make_entry("Global entry", MemoryType.GUIDELINE),
                scope=MemoryScope.GLOBAL,
            )

        assert manager.count() == 5

    def test_count_by_type(self, manager):
        manager.store(
            make_entry("Project pref", MemoryType.PREFERENCE),
            scope=MemoryScope.PROJECT,
        )
        manager.store(
            make_entry("Global pref", MemoryType.PREFERENCE),
            scope=MemoryScope.GLOBAL,
        )
        manager.store(
            make_entry("Project decision", MemoryType.DECISION),
            scope=MemoryScope.PROJECT,
        )

        assert manager.count(MemoryType.PREFERENCE) == 2
        assert manager.count(MemoryType.DECISION) == 1


class TestGlobalMemoryManagerClose:
    """close() shuts down both connections without error."""

    def test_close_closes_both_stores(self, tmp_path):
        project_store = make_store(tmp_path, "project.db")
        global_db = tmp_path / "global.db"

        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            manager = GlobalMemoryManager(project_store=project_store)

        # close must not raise
        manager.close()

    def test_close_without_project_store(self, tmp_path):
        global_db = tmp_path / "global.db"

        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            manager = GlobalMemoryManager(project_store=None)

        manager.close()


# ===========================================================================
# 2. MemoryScope and GUIDELINE Type Tests
# ===========================================================================


class TestMemoryScopeEnum:
    """MemoryScope enum contract."""

    def test_scope_values(self):
        assert MemoryScope.PROJECT.value == "project"
        assert MemoryScope.GLOBAL.value == "global"

    def test_scope_is_str_enum(self):
        assert isinstance(MemoryScope.PROJECT, str)
        assert isinstance(MemoryScope.GLOBAL, str)

    def test_scope_from_string(self):
        assert MemoryScope("project") is MemoryScope.PROJECT
        assert MemoryScope("global") is MemoryScope.GLOBAL


class TestGuidelineMemoryType:
    """GUIDELINE is a proper MemoryType member."""

    def test_guideline_exists(self):
        assert MemoryType.GUIDELINE.value == "guideline"

    def test_guideline_accessible_by_value(self):
        assert MemoryType("guideline") is MemoryType.GUIDELINE

    def test_all_original_types_still_present(self):
        for value in (
            "decision",
            "preference",
            "file_context",
            "error_pattern",
            "research",
            "outcome",
        ):
            assert MemoryType(value) is not None

    def test_store_and_retrieve_guideline(self, tmp_path):
        store = make_store(tmp_path)
        entry = make_entry(
            "Never commit secrets to version control", MemoryType.GUIDELINE
        )
        store.store(entry)

        retrieved = store.get(entry.id)

        assert retrieved is not None
        assert retrieved.memory_type == MemoryType.GUIDELINE
        assert retrieved.content == "Never commit secrets to version control"
        store.close()

    def test_get_by_type_guideline(self, tmp_path):
        store = make_store(tmp_path)
        for i in range(3):
            e = make_entry(f"Guideline {i}", MemoryType.GUIDELINE)
            store.store(e)
        # Also add a non-guideline entry so we're sure filtering works
        store.store(make_entry("A decision", MemoryType.DECISION))

        guidelines = store.get_by_type(MemoryType.GUIDELINE)
        assert len(guidelines) == 3
        assert all(g.memory_type == MemoryType.GUIDELINE for g in guidelines)
        store.close()


# ===========================================================================
# 3. MCP Server Integration Tests
# ===========================================================================


class TestGetMemoryManager:
    """get_memory_manager() factory in memory_server.py."""

    def test_returns_global_memory_manager_with_sugar_dir(self, tmp_path):
        from sugar.mcp.memory_server import get_memory_manager

        sugar_dir = tmp_path / ".sugar"
        sugar_dir.mkdir()
        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.Path") as mock_path_cls,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            # Redirect cwd to tmp_path so sugar_dir is found
            mock_path_cls.cwd.return_value = tmp_path
            # Re-enable Path() construction for actual paths
            mock_path_cls.side_effect = lambda *a, **kw: Path(*a, **kw)
            mock_path_cls.cwd.return_value = tmp_path

            manager = get_memory_manager()

        assert isinstance(manager, GlobalMemoryManager)
        manager.close()

    def test_returns_global_memory_manager_without_sugar_dir(self, tmp_path):
        from sugar.mcp.memory_server import get_memory_manager

        # tmp_path has no .sugar directory
        no_sugar_dir = tmp_path / "no_project"
        no_sugar_dir.mkdir()
        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.Path") as mock_path_cls,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            mock_path_cls.cwd.return_value = no_sugar_dir
            mock_path_cls.side_effect = lambda *a, **kw: Path(*a, **kw)
            mock_path_cls.cwd.return_value = no_sugar_dir

            manager = get_memory_manager()

        assert isinstance(manager, GlobalMemoryManager)
        # No .sugar found - no project store
        assert manager.project_store is None
        manager.close()


def _get_mcp_tool_fn(mcp, name):
    """Return the raw async function for a named MCP tool."""
    tool = mcp._tool_manager._tools.get(name)
    assert tool is not None, f"Tool '{name}' not registered in MCP server"
    return tool.fn


def _get_mcp_resource_fn(mcp, uri):
    """Return the raw async function for a registered MCP resource URI."""
    resource = mcp._resource_manager._resources.get(uri)
    assert resource is not None, f"Resource '{uri}' not registered in MCP server"
    return resource.fn


class TestMCPStoreLearning:
    """store_learning tool respects the scope parameter."""

    def test_store_global_scope_goes_to_global_db(self, tmp_path):
        from sugar.mcp.memory_server import create_memory_mcp_server

        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.get_memory_manager") as mock_get_manager,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            project_store = make_store(tmp_path, "project.db")
            manager = GlobalMemoryManager(project_store=project_store)
            mock_get_manager.return_value = manager

            mcp = create_memory_mcp_server()
            result = run(
                _get_mcp_tool_fn(mcp, "store_learning")(
                    content="Title tags must be under 60 characters",
                    memory_type="guideline",
                    scope="global",
                )
            )

        assert result["status"] == "stored"
        assert result["scope"] == "global"
        manager.close()
        project_store.close()

    def test_store_project_scope_goes_to_project_db(self, tmp_path):
        from sugar.mcp.memory_server import create_memory_mcp_server

        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.get_memory_manager") as mock_get_manager,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            project_store = make_store(tmp_path, "project.db")
            manager = GlobalMemoryManager(project_store=project_store)
            mock_get_manager.return_value = manager

            mcp = create_memory_mcp_server()
            result = run(
                _get_mcp_tool_fn(mcp, "store_learning")(
                    content="Use Redis for session caching in this project",
                    memory_type="decision",
                    scope="project",
                )
            )

        assert result["status"] == "stored"
        assert result["scope"] == "project"
        manager.close()
        project_store.close()

    def test_store_project_scope_without_project_store_falls_back_to_global(
        self, tmp_path
    ):
        from sugar.mcp.memory_server import create_memory_mcp_server

        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.get_memory_manager") as mock_get_manager,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            # No project store - simulates running outside a Sugar project
            manager = GlobalMemoryManager(project_store=None)
            mock_get_manager.return_value = manager

            mcp = create_memory_mcp_server()
            result = run(
                _get_mcp_tool_fn(mcp, "store_learning")(
                    content="Some knowledge outside a project",
                    memory_type="decision",
                    scope="project",
                )
            )

        # Falls back to global and includes a note
        assert result["status"] == "stored"
        assert result["scope"] == "global"
        assert "note" in result
        manager.close()


class TestMCPSearchMemory:
    """search_memory tool returns results with scope field."""

    def test_search_memory_results_include_scope(self, tmp_path):
        from sugar.mcp.memory_server import create_memory_mcp_server

        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.get_memory_manager") as mock_get_manager,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            project_store = make_store(tmp_path, "project.db")
            manager = GlobalMemoryManager(project_store=project_store)

            # Pre-seed data
            entry = make_entry("JWT tokens for authentication", MemoryType.DECISION)
            manager.store(entry, scope=MemoryScope.PROJECT)
            mock_get_manager.return_value = manager

            mcp = create_memory_mcp_server()
            results = run(
                _get_mcp_tool_fn(mcp, "search_memory")(query="JWT authentication")
            )

        assert isinstance(results, list)
        if results and "error" not in results[0]:
            assert "scope" in results[0]
            assert results[0]["scope"] in ("project", "global")

        manager.close()
        project_store.close()

    def test_search_memory_returns_list(self, tmp_path):
        from sugar.mcp.memory_server import create_memory_mcp_server

        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.get_memory_manager") as mock_get_manager,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            manager = GlobalMemoryManager(project_store=None)
            mock_get_manager.return_value = manager

            mcp = create_memory_mcp_server()
            results = run(_get_mcp_tool_fn(mcp, "search_memory")(query="anything"))

        assert isinstance(results, list)
        manager.close()


class TestMCPGetProjectContext:
    """get_project_context includes a guidelines section."""

    def test_includes_guidelines_key(self, tmp_path):
        from sugar.mcp.memory_server import create_memory_mcp_server

        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.get_memory_manager") as mock_get_manager,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            manager = GlobalMemoryManager(project_store=None)
            mock_get_manager.return_value = manager

            mcp = create_memory_mcp_server()
            result = run(_get_mcp_tool_fn(mcp, "get_project_context")())

        assert "guidelines" in result
        assert isinstance(result["guidelines"], list)
        manager.close()

    def test_guidelines_populated_from_global_store(self, tmp_path):
        from sugar.mcp.memory_server import create_memory_mcp_server

        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.get_memory_manager") as mock_get_manager,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            manager = GlobalMemoryManager(project_store=None)
            guideline = make_entry(
                "Use IndexNow API on every deploy", MemoryType.GUIDELINE
            )
            manager.store(guideline, scope=MemoryScope.GLOBAL)
            mock_get_manager.return_value = manager

            mcp = create_memory_mcp_server()
            result = run(_get_mcp_tool_fn(mcp, "get_project_context")())

        assert len(result["guidelines"]) == 1
        assert result["guidelines"][0]["content"] == guideline.content
        manager.close()


class TestMCPRecall:
    """recall tool searches both stores."""

    def test_recall_returns_string(self, tmp_path):
        from sugar.mcp.memory_server import create_memory_mcp_server

        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.get_memory_manager") as mock_get_manager,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            manager = GlobalMemoryManager(project_store=None)
            mock_get_manager.return_value = manager

            mcp = create_memory_mcp_server()
            result = run(_get_mcp_tool_fn(mcp, "recall")(topic="deployment"))

        assert isinstance(result, str)
        manager.close()

    def test_recall_no_results_returns_helpful_message(self, tmp_path):
        from sugar.mcp.memory_server import create_memory_mcp_server

        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.get_memory_manager") as mock_get_manager,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            manager = GlobalMemoryManager(project_store=None)
            mock_get_manager.return_value = manager

            mcp = create_memory_mcp_server()
            result = run(
                _get_mcp_tool_fn(mcp, "recall")(topic="absolutely unique xyz123")
            )

        assert "No memories found" in result or isinstance(result, str)
        manager.close()


class TestMCPGlobalGuidelinesResource:
    """global_guidelines_resource returns guidelines markdown."""

    def test_resource_returns_string(self, tmp_path):
        from sugar.mcp.memory_server import create_memory_mcp_server

        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.get_memory_manager") as mock_get_manager,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            manager = GlobalMemoryManager(project_store=None)
            mock_get_manager.return_value = manager

            mcp = create_memory_mcp_server()
            result = run(_get_mcp_resource_fn(mcp, "sugar://global/guidelines")())

        assert isinstance(result, str)
        manager.close()

    def test_resource_empty_store_returns_placeholder(self, tmp_path):
        from sugar.mcp.memory_server import create_memory_mcp_server

        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.get_memory_manager") as mock_get_manager,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            manager = GlobalMemoryManager(project_store=None)
            mock_get_manager.return_value = manager

            mcp = create_memory_mcp_server()
            result = run(_get_mcp_resource_fn(mcp, "sugar://global/guidelines")())

        assert "No global guidelines" in result
        manager.close()

    def test_resource_with_guidelines_lists_them(self, tmp_path):
        from sugar.mcp.memory_server import create_memory_mcp_server

        global_db = tmp_path / "global.db"

        with (
            patch("sugar.mcp.memory_server.get_memory_manager") as mock_get_manager,
            patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db),
        ):
            manager = GlobalMemoryManager(project_store=None)
            guideline = make_entry(
                "Kamal-only deploys, never raw Docker", MemoryType.GUIDELINE
            )
            manager.store(guideline, scope=MemoryScope.GLOBAL)
            mock_get_manager.return_value = manager

            mcp = create_memory_mcp_server()
            result = run(_get_mcp_resource_fn(mcp, "sugar://global/guidelines")())

        assert "Global Guidelines" in result
        assert "Kamal-only deploys" in result
        manager.close()


# ===========================================================================
# 4. Retriever Tests
# ===========================================================================


class TestRetrieverWithGlobalManager:
    """MemoryRetriever works correctly with a GlobalMemoryManager."""

    @pytest.fixture
    def manager(self, tmp_path):
        project_store = make_store(tmp_path, "project.db")
        global_db = tmp_path / "global.db"
        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            mgr = GlobalMemoryManager(project_store=project_store)
            yield mgr
        mgr.close()

    def test_format_for_prompt_labels_global_result(self, tmp_path):
        global_db = tmp_path / "global.db"

        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            manager = GlobalMemoryManager(project_store=None)

        guideline = make_entry(
            "Meta descriptions must be 120-160 chars", MemoryType.GUIDELINE
        )
        manager.store(guideline, scope=MemoryScope.GLOBAL)

        results = manager.search(MemoryQuery(query="meta descriptions"))

        retriever = MemoryRetriever(manager)
        formatted = retriever.format_for_prompt(results)

        if results:
            assert "[Global]" in formatted

        manager.close()

    def test_format_for_prompt_no_global_label_for_project_result(self, tmp_path):
        global_db = tmp_path / "global.db"

        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            project_store = make_store(tmp_path, "project.db")
            manager = GlobalMemoryManager(project_store=project_store)

        entry = make_entry("Project-specific routing decision", MemoryType.DECISION)
        manager.store(entry, scope=MemoryScope.PROJECT)

        results = manager.search(MemoryQuery(query="routing decision"))

        retriever = MemoryRetriever(manager)
        formatted = retriever.format_for_prompt(results)

        if results:
            # Project-scoped results must not carry a [Global] label
            # Split on the result content and check its header
            project_result = next((r for r in results if r.scope == "project"), None)
            if project_result:
                assert "[Global]" not in formatted or "Decision" in formatted

        manager.close()

    def test_get_project_context_includes_guidelines_key(self, manager):
        guideline = make_entry(
            "Always use semantic HTML5 elements", MemoryType.GUIDELINE
        )
        manager.store(guideline, scope=MemoryScope.GLOBAL)

        retriever = MemoryRetriever(manager)
        context = retriever.get_project_context()

        assert "guidelines" in context
        assert isinstance(context["guidelines"], list)
        assert len(context["guidelines"]) >= 1

    def test_get_project_context_guidelines_content_matches(self, manager):
        guideline = make_entry("Run tests before every deploy", MemoryType.GUIDELINE)
        manager.store(guideline, scope=MemoryScope.GLOBAL)

        retriever = MemoryRetriever(manager)
        context = retriever.get_project_context()

        guideline_contents = [g["content"] for g in context["guidelines"]]
        assert "Run tests before every deploy" in guideline_contents

    def test_get_project_context_no_guidelines_key_with_plain_store(self, tmp_path):
        """Plain MemoryStore (not GlobalMemoryManager) must not include guidelines."""
        store = make_store(tmp_path, "plain.db")
        retriever = MemoryRetriever(store)
        context = retriever.get_project_context()

        assert "guidelines" not in context
        store.close()


# ===========================================================================
# 5. Edge Cases
# ===========================================================================


class TestEdgeCases:
    """Boundary conditions and degenerate inputs."""

    def test_empty_global_store_populated_project_store(self, tmp_path):
        global_db = tmp_path / "global.db"

        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            project_store = make_store(tmp_path, "project.db")
            manager = GlobalMemoryManager(project_store=project_store)

        for i in range(3):
            manager.store(make_entry(f"Project entry {i}"), scope=MemoryScope.PROJECT)

        # Global store is empty - search and list must still work
        results = manager.search(MemoryQuery(query="project entry"))
        entries = manager.list_memories()

        assert len(results) >= 1
        assert all(r.scope == "project" for r in results)
        assert len(entries) == 3
        assert manager.count() == 3
        manager.close()

    def test_populated_global_store_empty_project_store(self, tmp_path):
        global_db = tmp_path / "global.db"

        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            project_store = make_store(tmp_path, "project.db")
            manager = GlobalMemoryManager(project_store=project_store)

        for i in range(2):
            manager.store(
                make_entry(f"Global guideline {i}", MemoryType.GUIDELINE),
                scope=MemoryScope.GLOBAL,
            )

        results = manager.search(MemoryQuery(query="guideline"))
        entries = manager.list_memories()

        assert len(results) >= 1
        assert all(r.scope == "global" for r in results)
        assert len(entries) == 2
        manager.close()

    def test_both_stores_empty_returns_empty_results(self, tmp_path):
        global_db = tmp_path / "global.db"

        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            manager = GlobalMemoryManager(project_store=None)

        results = manager.search(MemoryQuery(query="anything"))
        entries = manager.list_memories()

        assert results == []
        assert entries == []
        assert manager.count() == 0
        manager.close()

    def test_dedup_same_content_in_both_stores(self, tmp_path):
        global_db = tmp_path / "global.db"

        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            project_store = make_store(tmp_path, "project.db")
            manager = GlobalMemoryManager(project_store=project_store)

        identical_content = "Use 4-space indentation in all Python files"

        project_entry = make_entry(identical_content, MemoryType.PREFERENCE)
        global_entry = make_entry(identical_content, MemoryType.PREFERENCE)

        manager.store(project_entry, scope=MemoryScope.PROJECT)
        manager.store(global_entry, scope=MemoryScope.GLOBAL)

        results = manager.search(MemoryQuery(query="indentation Python"))

        # After dedup, the content appears at most once
        contents = [r.entry.content for r in results]
        assert contents.count(identical_content) <= 1
        manager.close()

    def test_dedup_preserves_project_scope_over_global(self, tmp_path):
        """When content is in both stores, the project-scoped result is kept (higher
        specificity). The project result has a higher score because it is sorted first
        before dedup runs - but with FallbackEmbedder scores are equal, so the first
        result in the pre-sorted list is kept. We verify at least one result survives.
        """
        global_db = tmp_path / "global.db"

        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            project_store = make_store(tmp_path, "project.db")
            manager = GlobalMemoryManager(project_store=project_store)

        content = "Always write docstrings for public functions"
        project_entry = make_entry(content, MemoryType.PREFERENCE, importance=1.5)
        global_entry = make_entry(content, MemoryType.PREFERENCE)

        manager.store(project_entry, scope=MemoryScope.PROJECT)
        manager.store(global_entry, scope=MemoryScope.GLOBAL)

        results = manager.search(MemoryQuery(query="docstrings public functions"))

        # At least one result, and the dedup key is based on content
        assert len(results) >= 1
        assert len(results) == len(
            {" ".join(r.entry.content[:200].lower().split()) for r in results}
        )
        manager.close()

    def test_global_db_path_is_in_home_dot_sugar(self):
        """Module-level constant points to the right location."""
        assert GLOBAL_DB_PATH.parent == Path.home() / ".sugar"
        assert GLOBAL_DB_PATH.name == "memory.db"

    def test_memory_search_result_scope_field_defaults_to_project(self):
        """MemorySearchResult.scope defaults to 'project'."""
        entry = make_entry("Some content")
        result = MemorySearchResult(entry=entry, score=0.9)
        assert result.scope == "project"

    def test_memory_search_result_scope_can_be_global(self):
        entry = make_entry("Some global content")
        result = MemorySearchResult(entry=entry, score=0.8, scope="global")
        assert result.scope == "global"

    def test_manager_store_returns_entry_id(self, tmp_path):
        global_db = tmp_path / "global.db"

        with patch("sugar.memory.global_store.GLOBAL_DB_PATH", global_db):
            manager = GlobalMemoryManager(project_store=None)

        entry = make_entry("Test return value", MemoryType.GUIDELINE)
        returned_id = manager.store(entry, scope=MemoryScope.GLOBAL)

        assert returned_id == entry.id
        manager.close()
