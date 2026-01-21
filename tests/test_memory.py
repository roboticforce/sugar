"""
Tests for Sugar Memory System

Tests cover:
- MemoryStore CRUD operations
- MemoryStore search (FTS5 fallback)
- MemoryRetriever context formatting
- MemoryEntry serialization
- CLI commands (remember, recall, memories, forget, export-context)
"""

import json
import pytest
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

# ============================================================================
# Unit Tests - Memory Types
# ============================================================================


class TestMemoryTypes:
    """Test MemoryEntry and related types."""

    def test_memory_entry_creation(self):
        """Test creating a MemoryEntry."""
        from sugar.memory import MemoryEntry, MemoryType

        entry = MemoryEntry(
            id="test-123",
            memory_type=MemoryType.DECISION,
            content="Use JWT for authentication",
        )

        assert entry.id == "test-123"
        assert entry.memory_type == MemoryType.DECISION
        assert entry.content == "Use JWT for authentication"
        assert entry.importance == 1.0
        assert entry.access_count == 0

    def test_memory_entry_to_dict(self):
        """Test MemoryEntry serialization."""
        from sugar.memory import MemoryEntry, MemoryType

        now = datetime.now(timezone.utc)
        entry = MemoryEntry(
            id="test-123",
            memory_type=MemoryType.PREFERENCE,
            content="Always use async/await",
            summary="Prefer async",
            metadata={"tags": ["coding-style"]},
            importance=1.5,
            created_at=now,
        )

        data = entry.to_dict()

        assert data["id"] == "test-123"
        assert data["memory_type"] == "preference"
        assert data["content"] == "Always use async/await"
        assert data["summary"] == "Prefer async"
        assert data["metadata"] == {"tags": ["coding-style"]}
        assert data["importance"] == 1.5

    def test_memory_entry_from_dict(self):
        """Test MemoryEntry deserialization."""
        from sugar.memory import MemoryEntry, MemoryType

        data = {
            "id": "test-456",
            "memory_type": "research",
            "content": "Stripe API requires idempotency keys",
            "summary": None,
            "metadata": {"source": "docs"},
            "importance": 1.0,
            "created_at": "2025-01-15T10:00:00+00:00",
            "last_accessed_at": None,
            "access_count": 0,
            "expires_at": None,
        }

        entry = MemoryEntry.from_dict(data)

        assert entry.id == "test-456"
        assert entry.memory_type == MemoryType.RESEARCH
        assert entry.content == "Stripe API requires idempotency keys"
        assert entry.metadata == {"source": "docs"}

    def test_memory_type_enum(self):
        """Test MemoryType enum values."""
        from sugar.memory import MemoryType

        assert MemoryType.DECISION.value == "decision"
        assert MemoryType.PREFERENCE.value == "preference"
        assert MemoryType.FILE_CONTEXT.value == "file_context"
        assert MemoryType.ERROR_PATTERN.value == "error_pattern"
        assert MemoryType.RESEARCH.value == "research"
        assert MemoryType.OUTCOME.value == "outcome"


# ============================================================================
# Unit Tests - Memory Store
# ============================================================================


class TestMemoryStore:
    """Test MemoryStore database operations."""

    @pytest.fixture
    def memory_store(self, temp_dir):
        """Create a memory store with temporary database."""
        from sugar.memory import MemoryStore
        from sugar.memory.embedder import FallbackEmbedder

        db_path = temp_dir / "test_memory.db"
        # Use FallbackEmbedder to avoid requiring sentence-transformers
        store = MemoryStore(str(db_path), embedder=FallbackEmbedder())
        yield store
        store.close()

    def test_store_and_retrieve(self, memory_store):
        """Test storing and retrieving a memory."""
        from sugar.memory import MemoryEntry, MemoryType

        entry = MemoryEntry(
            id="store-test-1",
            memory_type=MemoryType.DECISION,
            content="Decided to use PostgreSQL for the database",
        )

        memory_store.store(entry)
        retrieved = memory_store.get("store-test-1")

        assert retrieved is not None
        assert retrieved.id == "store-test-1"
        assert retrieved.content == "Decided to use PostgreSQL for the database"
        assert retrieved.memory_type == MemoryType.DECISION

    def test_store_with_metadata(self, memory_store):
        """Test storing memory with metadata."""
        from sugar.memory import MemoryEntry, MemoryType

        entry = MemoryEntry(
            id="meta-test-1",
            memory_type=MemoryType.FILE_CONTEXT,
            content="payment_processor.py handles Stripe webhooks",
            metadata={
                "file_paths": ["src/payment_processor.py"],
                "tags": ["stripe", "payments"],
            },
        )

        memory_store.store(entry)
        retrieved = memory_store.get("meta-test-1")

        assert retrieved.metadata["file_paths"] == ["src/payment_processor.py"]
        assert "stripe" in retrieved.metadata["tags"]

    def test_delete_memory(self, memory_store):
        """Test deleting a memory."""
        from sugar.memory import MemoryEntry, MemoryType

        entry = MemoryEntry(
            id="delete-test-1",
            memory_type=MemoryType.RESEARCH,
            content="Temporary research note",
        )

        memory_store.store(entry)
        assert memory_store.get("delete-test-1") is not None

        deleted = memory_store.delete("delete-test-1")
        assert deleted is True
        assert memory_store.get("delete-test-1") is None

    def test_list_memories(self, memory_store):
        """Test listing memories."""
        from sugar.memory import MemoryEntry, MemoryType

        entries = [
            MemoryEntry(
                id="list-1", memory_type=MemoryType.PREFERENCE, content="Prefer async"
            ),
            MemoryEntry(
                id="list-2", memory_type=MemoryType.DECISION, content="Use JWT"
            ),
            MemoryEntry(
                id="list-3", memory_type=MemoryType.PREFERENCE, content="No callbacks"
            ),
        ]

        for entry in entries:
            memory_store.store(entry)

        # List all
        all_memories = memory_store.list_memories()
        assert len(all_memories) == 3

        # Filter by type
        preferences = memory_store.list_memories(memory_type=MemoryType.PREFERENCE)
        assert len(preferences) == 2

    def test_count_memories(self, memory_store):
        """Test counting memories."""
        from sugar.memory import MemoryEntry, MemoryType

        entries = [
            MemoryEntry(
                id="count-1", memory_type=MemoryType.PREFERENCE, content="Pref 1"
            ),
            MemoryEntry(
                id="count-2", memory_type=MemoryType.PREFERENCE, content="Pref 2"
            ),
            MemoryEntry(id="count-3", memory_type=MemoryType.DECISION, content="Dec 1"),
        ]

        for entry in entries:
            memory_store.store(entry)

        assert memory_store.count() == 3
        assert memory_store.count(MemoryType.PREFERENCE) == 2
        assert memory_store.count(MemoryType.DECISION) == 1

    def test_search_keyword_fallback(self, memory_store):
        """Test FTS5 keyword search."""
        from sugar.memory import MemoryEntry, MemoryQuery, MemoryType

        entries = [
            MemoryEntry(
                id="search-1",
                memory_type=MemoryType.DECISION,
                content="Use JWT tokens for authentication",
            ),
            MemoryEntry(
                id="search-2",
                memory_type=MemoryType.DECISION,
                content="PostgreSQL for the main database",
            ),
            MemoryEntry(
                id="search-3",
                memory_type=MemoryType.ERROR_PATTERN,
                content="Auth redirect loop fixed by return",
            ),
        ]

        for entry in entries:
            memory_store.store(entry)

        # Search for authentication-related memories
        query = MemoryQuery(query="authentication", limit=10)
        results = memory_store.search(query)

        assert len(results) >= 1
        # The JWT entry should be in results
        result_ids = [r.entry.id for r in results]
        assert "search-1" in result_ids

    def test_prune_expired(self, memory_store):
        """Test pruning expired memories."""
        from sugar.memory import MemoryEntry, MemoryType

        # Create an expired entry
        expired_entry = MemoryEntry(
            id="expired-1",
            memory_type=MemoryType.RESEARCH,
            content="Expired research",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        # Create a valid entry
        valid_entry = MemoryEntry(
            id="valid-1",
            memory_type=MemoryType.PREFERENCE,
            content="Valid preference",
        )

        memory_store.store(expired_entry)
        memory_store.store(valid_entry)

        assert memory_store.count() == 2

        pruned = memory_store.prune_expired()
        assert pruned == 1
        assert memory_store.count() == 1
        assert memory_store.get("valid-1") is not None
        assert memory_store.get("expired-1") is None


# ============================================================================
# Unit Tests - Memory Retriever
# ============================================================================


class TestMemoryRetriever:
    """Test MemoryRetriever context formatting."""

    @pytest.fixture
    def populated_store(self, temp_dir):
        """Create a memory store with sample data."""
        from sugar.memory import MemoryEntry, MemoryStore, MemoryType
        from sugar.memory.embedder import FallbackEmbedder

        db_path = temp_dir / "retriever_test.db"
        store = MemoryStore(str(db_path), embedder=FallbackEmbedder())

        entries = [
            MemoryEntry(
                id="ret-1",
                memory_type=MemoryType.PREFERENCE,
                content="Always use async/await, never callbacks",
            ),
            MemoryEntry(
                id="ret-2",
                memory_type=MemoryType.DECISION,
                content="Chose JWT with RS256 for authentication tokens",
            ),
            MemoryEntry(
                id="ret-3",
                memory_type=MemoryType.ERROR_PATTERN,
                content="Login redirect loop was caused by missing return statement",
            ),
        ]

        for entry in entries:
            store.store(entry)

        yield store
        store.close()

    def test_get_project_context(self, populated_store):
        """Test retrieving project context."""
        from sugar.memory import MemoryRetriever

        retriever = MemoryRetriever(populated_store)
        context = retriever.get_project_context()

        assert "preferences" in context
        assert "recent_decisions" in context
        assert "error_patterns" in context
        assert len(context["preferences"]) >= 1

    def test_format_context_markdown(self, populated_store):
        """Test formatting context as markdown."""
        from sugar.memory import MemoryRetriever

        retriever = MemoryRetriever(populated_store)
        context = retriever.get_project_context()
        markdown = retriever.format_context_markdown(context)

        assert "## Recent Context from Sugar Memory" in markdown
        assert "Preferences" in markdown

    def test_format_for_prompt(self, populated_store):
        """Test formatting search results for prompt injection."""
        from sugar.memory import MemoryQuery, MemoryRetriever

        retriever = MemoryRetriever(populated_store)

        # Search and format
        query = MemoryQuery(query="authentication", limit=5)
        results = populated_store.search(query)
        formatted = retriever.format_for_prompt(results)

        if results:
            assert "## Relevant Context" in formatted


# ============================================================================
# Integration Tests - CLI Commands
# ============================================================================


class TestMemoryCLI:
    """Test memory CLI commands."""

    @pytest.fixture
    def cli_setup(self, temp_dir):
        """Setup CLI test environment with Sugar project."""
        from click.testing import CliRunner

        project_dir = temp_dir / "test_project"
        project_dir.mkdir()

        sugar_dir = project_dir / ".sugar"
        sugar_dir.mkdir()

        # Create minimal config
        config = {
            "sugar": {
                "storage": {"database": str(sugar_dir / "sugar.db")},
            }
        }

        import yaml

        with open(sugar_dir / "config.yaml", "w") as f:
            yaml.dump(config, f)

        runner = CliRunner()
        return runner, project_dir

    def test_remember_command(self, cli_setup):
        """Test sugar remember command."""
        from sugar.main import cli

        runner, project_dir = cli_setup

        with runner.isolated_filesystem(temp_dir=project_dir.parent):
            import os

            os.chdir(project_dir)

            result = runner.invoke(
                cli,
                [
                    "--config",
                    str(project_dir / ".sugar" / "config.yaml"),
                    "remember",
                    "Always use type hints in Python",
                    "--type",
                    "preference",
                ],
            )

            # May fail if memory dependencies not installed, which is fine
            if "Memory dependencies not installed" not in result.output:
                assert "Remembered" in result.output or result.exit_code == 0

    def test_memories_command(self, cli_setup):
        """Test sugar memories command."""
        from sugar.main import cli

        runner, project_dir = cli_setup

        with runner.isolated_filesystem(temp_dir=project_dir.parent):
            import os

            os.chdir(project_dir)

            result = runner.invoke(
                cli,
                ["--config", str(project_dir / ".sugar" / "config.yaml"), "memories"],
            )

            # Should work even with no memories
            if "Memory dependencies not installed" not in result.output:
                assert result.exit_code == 0 or "No memories found" in result.output

    def test_recall_command(self, cli_setup):
        """Test sugar recall command."""
        from sugar.main import cli

        runner, project_dir = cli_setup

        with runner.isolated_filesystem(temp_dir=project_dir.parent):
            import os

            os.chdir(project_dir)

            result = runner.invoke(
                cli,
                [
                    "--config",
                    str(project_dir / ".sugar" / "config.yaml"),
                    "recall",
                    "authentication",
                ],
            )

            if "Memory dependencies not installed" not in result.output:
                # With empty store, should report no results
                assert result.exit_code == 0 or "No memories found" in result.output

    def test_export_context_command(self, cli_setup):
        """Test sugar export-context command."""
        from sugar.main import cli

        runner, project_dir = cli_setup

        with runner.isolated_filesystem(temp_dir=project_dir.parent):
            import os

            os.chdir(project_dir)

            result = runner.invoke(
                cli,
                [
                    "--config",
                    str(project_dir / ".sugar" / "config.yaml"),
                    "export-context",
                ],
            )

            # Should not crash, even without memories
            assert result.exit_code == 0

    def test_memory_stats_command(self, cli_setup):
        """Test sugar memory-stats command."""
        from sugar.main import cli

        runner, project_dir = cli_setup

        with runner.isolated_filesystem(temp_dir=project_dir.parent):
            import os

            os.chdir(project_dir)

            result = runner.invoke(
                cli,
                [
                    "--config",
                    str(project_dir / ".sugar" / "config.yaml"),
                    "memory-stats",
                ],
            )

            if "Memory dependencies not installed" not in result.output:
                assert "Memory Statistics" in result.output or result.exit_code == 0


# ============================================================================
# Unit Tests - Embedder
# ============================================================================


class TestEmbedder:
    """Test embedding functionality."""

    def test_fallback_embedder(self):
        """Test FallbackEmbedder returns empty embeddings."""
        from sugar.memory.embedder import FallbackEmbedder

        embedder = FallbackEmbedder()

        embedding = embedder.embed("test text")
        assert embedding == []

        embeddings = embedder.embed_batch(["text1", "text2"])
        assert embeddings == [[], []]

        assert embedder.dimension == 0

    def test_create_embedder_fallback(self):
        """Test create_embedder falls back gracefully."""
        from sugar.memory.embedder import create_embedder, FallbackEmbedder

        # Should return an embedder without crashing
        embedder = create_embedder(prefer_local=True)
        assert embedder is not None

        # If sentence-transformers not available, should be FallbackEmbedder
        # If available, should be SentenceTransformerEmbedder
        # Either way, it should work

    def test_is_semantic_search_available(self):
        """Test semantic search availability check."""
        from sugar.memory.embedder import is_semantic_search_available

        # Should return True or False without crashing
        result = is_semantic_search_available()
        assert isinstance(result, bool)


# ============================================================================
# Integration Tests - Full Workflow
# ============================================================================


class TestMemoryWorkflow:
    """Test complete memory workflows."""

    @pytest.fixture
    def full_setup(self, temp_dir):
        """Create a complete memory setup."""
        from sugar.memory import MemoryEntry, MemoryRetriever, MemoryStore, MemoryType
        from sugar.memory.embedder import FallbackEmbedder

        db_path = temp_dir / "workflow_test.db"
        store = MemoryStore(str(db_path), embedder=FallbackEmbedder())
        retriever = MemoryRetriever(store)

        yield store, retriever
        store.close()

    def test_store_search_retrieve_workflow(self, full_setup):
        """Test complete store -> search -> retrieve workflow."""
        from sugar.memory import MemoryEntry, MemoryQuery, MemoryType

        store, retriever = full_setup

        # Store some memories
        memories = [
            MemoryEntry(
                id="wf-1",
                memory_type=MemoryType.PREFERENCE,
                content="Steve prefers minimal comments and descriptive variable names",
            ),
            MemoryEntry(
                id="wf-2",
                memory_type=MemoryType.DECISION,
                content="Using Redis for session storage due to horizontal scaling needs",
                metadata={"tags": ["architecture", "redis"]},
            ),
            MemoryEntry(
                id="wf-3",
                memory_type=MemoryType.ERROR_PATTERN,
                content="Database connection timeout: fixed by increasing pool size",
            ),
        ]

        for m in memories:
            store.store(m)

        # Search for relevant context
        query = MemoryQuery(query="redis", limit=5)
        results = store.search(query)

        # Get project context
        context = retriever.get_project_context()

        assert len(context["preferences"]) >= 1
        assert len(context["recent_decisions"]) >= 1

        # Format for prompt
        formatted = retriever.format_context_markdown(context)
        assert len(formatted) > 0

    def test_memory_lifecycle(self, full_setup):
        """Test memory creation, update, and deletion."""
        from sugar.memory import MemoryEntry, MemoryType

        store, retriever = full_setup

        # Create
        entry = MemoryEntry(
            id="lifecycle-1",
            memory_type=MemoryType.RESEARCH,
            content="Initial research finding",
        )
        store.store(entry)

        # Verify created
        retrieved = store.get("lifecycle-1")
        assert retrieved is not None
        assert retrieved.content == "Initial research finding"

        # Update (store with same ID)
        updated_entry = MemoryEntry(
            id="lifecycle-1",
            memory_type=MemoryType.RESEARCH,
            content="Updated research finding with more details",
            importance=1.5,
        )
        store.store(updated_entry)

        # Verify updated
        retrieved = store.get("lifecycle-1")
        assert retrieved.content == "Updated research finding with more details"
        assert retrieved.importance == 1.5

        # Delete
        store.delete("lifecycle-1")
        assert store.get("lifecycle-1") is None
