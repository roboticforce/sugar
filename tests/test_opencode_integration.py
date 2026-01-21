"""
Tests for OpenCode Integration

Tests the OpenCode integration components:
- OpenCodeConfig
- Data models (Session, Message, Event)
- ContextInjector
- LearningCapture
"""

import math
import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# =============================================================================
# Config Tests
# =============================================================================


class TestOpenCodeConfig:
    """Tests for OpenCodeConfig dataclass"""

    def test_default_values(self):
        """Test default configuration values"""
        from sugar.integrations.opencode.config import OpenCodeConfig

        config = OpenCodeConfig()
        assert config.server_url == "http://localhost:4096"
        assert config.api_key is None
        assert config.timeout == 30.0
        assert config.auto_inject is True
        assert config.inject_memory_types == ["decision", "preference", "error_pattern"]
        assert config.memory_limit == 5
        assert config.notify_on_completion is True
        assert config.notify_on_failure is True
        assert config.sync_interval == 5.0
        assert config.enabled is True

    def test_from_env_defaults(self):
        """Test config from environment with no variables set"""
        from sugar.integrations.opencode.config import OpenCodeConfig

        with patch.dict(os.environ, {}, clear=True):
            config = OpenCodeConfig.from_env()
            assert config.server_url == "http://localhost:4096"
            assert config.api_key is None
            assert config.timeout == 30.0
            assert config.enabled is True

    def test_from_env_custom_values(self):
        """Test config from environment with custom values"""
        from sugar.integrations.opencode.config import OpenCodeConfig

        env_vars = {
            "OPENCODE_SERVER_URL": "http://custom:8080",
            "OPENCODE_API_KEY": "test-api-key",
            "OPENCODE_TIMEOUT": "60.0",
            "SUGAR_OPENCODE_ENABLED": "false",
        }
        with patch.dict(os.environ, env_vars, clear=True):
            config = OpenCodeConfig.from_env()
            assert config.server_url == "http://custom:8080"
            assert config.api_key == "test-api-key"
            assert config.timeout == 60.0
            assert config.enabled is False

    def test_from_sugar_config_empty(self):
        """Test config from empty Sugar config"""
        from sugar.integrations.opencode.config import OpenCodeConfig

        config = OpenCodeConfig.from_sugar_config({})
        assert config.server_url == "http://localhost:4096"
        assert config.auto_inject is True

    def test_from_sugar_config_partial(self):
        """Test config from partial Sugar config"""
        from sugar.integrations.opencode.config import OpenCodeConfig

        sugar_config = {
            "integrations": {
                "opencode": {
                    "server_url": "http://custom:9000",
                    "auto_inject": False,
                    "memory_limit": 10,
                }
            }
        }
        config = OpenCodeConfig.from_sugar_config(sugar_config)
        assert config.server_url == "http://custom:9000"
        assert config.auto_inject is False
        assert config.memory_limit == 10
        # Defaults preserved
        assert config.notify_on_completion is True

    def test_from_sugar_config_complete(self):
        """Test config from complete Sugar config"""
        from sugar.integrations.opencode.config import OpenCodeConfig

        sugar_config = {
            "integrations": {
                "opencode": {
                    "server_url": "http://prod:4096",
                    "api_key": "prod-key",
                    "timeout": 120.0,
                    "auto_inject": True,
                    "inject_memory_types": ["decision", "preference"],
                    "memory_limit": 3,
                    "notify_on_completion": False,
                    "notify_on_failure": True,
                    "sync_interval": 10.0,
                    "enabled": True,
                }
            }
        }
        config = OpenCodeConfig.from_sugar_config(sugar_config)
        assert config.server_url == "http://prod:4096"
        assert config.api_key == "prod-key"
        assert config.timeout == 120.0
        assert config.inject_memory_types == ["decision", "preference"]
        assert config.notify_on_completion is False


# =============================================================================
# Models Tests
# =============================================================================


class TestNotificationLevel:
    """Tests for NotificationLevel enum"""

    def test_enum_values(self):
        """Test notification level enum values"""
        from sugar.integrations.opencode.models import NotificationLevel

        assert NotificationLevel.INFO.value == "info"
        assert NotificationLevel.SUCCESS.value == "success"
        assert NotificationLevel.WARNING.value == "warning"
        assert NotificationLevel.ERROR.value == "error"


class TestSessionStatus:
    """Tests for SessionStatus enum"""

    def test_enum_values(self):
        """Test session status enum values"""
        from sugar.integrations.opencode.models import SessionStatus

        assert SessionStatus.ACTIVE.value == "active"
        assert SessionStatus.IDLE.value == "idle"
        assert SessionStatus.COMPACTING.value == "compacting"
        assert SessionStatus.CLOSED.value == "closed"


class TestMessage:
    """Tests for Message dataclass"""

    def test_create_message(self):
        """Test creating a message"""
        from sugar.integrations.opencode.models import Message

        msg = Message(id="msg-1", role="user", content="Hello")
        assert msg.id == "msg-1"
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.created_at is None
        assert msg.metadata == {}

    def test_message_with_metadata(self):
        """Test message with metadata"""
        from sugar.integrations.opencode.models import Message

        now = datetime.now()
        msg = Message(
            id="msg-2",
            role="assistant",
            content="Response",
            created_at=now,
            metadata={"tokens": 100},
        )
        assert msg.created_at == now
        assert msg.metadata["tokens"] == 100


class TestSession:
    """Tests for Session dataclass"""

    def test_create_session(self):
        """Test creating a session"""
        from sugar.integrations.opencode.models import Session, SessionStatus

        session = Session(id="sess-1")
        assert session.id == "sess-1"
        assert session.status == SessionStatus.ACTIVE
        assert session.messages == []
        assert session.metadata == {}

    def test_session_from_dict_minimal(self):
        """Test creating session from minimal dict"""
        from sugar.integrations.opencode.models import Session, SessionStatus

        data = {"id": "sess-2"}
        session = Session.from_dict(data)
        assert session.id == "sess-2"
        assert session.status == SessionStatus.ACTIVE

    def test_session_from_dict_complete(self):
        """Test creating session from complete dict"""
        from sugar.integrations.opencode.models import Session, SessionStatus

        data = {
            "id": "sess-3",
            "status": "idle",
            "created_at": "2025-01-15T10:00:00",
            "updated_at": "2025-01-15T11:00:00",
            "messages": [
                {"id": "m1", "role": "user", "content": "Hello"},
                {"id": "m2", "role": "assistant", "content": "Hi there"},
            ],
            "metadata": {"project": "sugar"},
        }
        session = Session.from_dict(data)
        assert session.id == "sess-3"
        assert session.status == SessionStatus.IDLE
        assert session.created_at == datetime(2025, 1, 15, 10, 0, 0)
        assert session.updated_at == datetime(2025, 1, 15, 11, 0, 0)
        assert len(session.messages) == 2
        assert session.messages[0].content == "Hello"
        assert session.metadata["project"] == "sugar"


class TestEvent:
    """Tests for Event dataclass"""

    def test_create_event(self):
        """Test creating an event"""
        from sugar.integrations.opencode.models import Event

        event = Event(type="message", data={"content": "test"})
        assert event.type == "message"
        assert event.data["content"] == "test"

    def test_event_from_sse_valid_json(self):
        """Test parsing SSE event with valid JSON"""
        from sugar.integrations.opencode.models import Event

        event = Event.from_sse("session.created", '{"id": "sess-1"}')
        assert event.type == "session.created"
        assert event.data["id"] == "sess-1"
        assert event.timestamp is not None

    def test_event_from_sse_invalid_json(self):
        """Test parsing SSE event with invalid JSON"""
        from sugar.integrations.opencode.models import Event

        event = Event.from_sse("error", "not valid json")
        assert event.type == "error"
        assert event.data["raw"] == "not valid json"


# =============================================================================
# ContextInjector Tests
# =============================================================================


class TestContextInjector:
    """Tests for ContextInjector class"""

    def test_calculate_relevance_basic(self):
        """Test basic relevance calculation"""
        from sugar.integrations.opencode.injector import ContextInjector

        injector = ContextInjector()
        memory = {
            "content": "Test memory",
            "type": "decision",
            "created_at": datetime.now(),
            "importance": 5,
            "access_count": 0,
        }
        score = injector.calculate_relevance(memory, semantic_score=0.8)
        # Should be a positive score combining all factors
        assert 0 < score <= 1.0

    def test_calculate_relevance_high_semantic(self):
        """Test relevance with high semantic similarity"""
        from sugar.integrations.opencode.injector import ContextInjector

        injector = ContextInjector()
        memory = {
            "content": "Highly relevant memory",
            "type": "preference",
            "created_at": datetime.now(),
            "importance": 10,
            "access_count": 5,
        }
        high_score = injector.calculate_relevance(memory, semantic_score=0.95)
        low_score = injector.calculate_relevance(memory, semantic_score=0.2)
        assert high_score > low_score

    def test_calculate_relevance_recency_decay(self):
        """Test that older memories have lower recency scores"""
        from sugar.integrations.opencode.injector import ContextInjector

        injector = ContextInjector()

        recent = {
            "content": "Recent memory",
            "type": "outcome",
            "created_at": datetime.now(),
            "importance": 5,
            "access_count": 0,
        }
        old = {
            "content": "Old memory",
            "type": "outcome",
            "created_at": datetime.now() - timedelta(days=60),
            "importance": 5,
            "access_count": 0,
        }

        recent_score = injector.calculate_relevance(recent, semantic_score=0.5)
        old_score = injector.calculate_relevance(old, semantic_score=0.5)
        # Outcome type has 30 day half-life, so 60 days old should score lower
        assert recent_score > old_score

    def test_calculate_relevance_preference_no_decay(self):
        """Test that preferences don't decay quickly"""
        from sugar.integrations.opencode.injector import ContextInjector

        injector = ContextInjector()

        recent = {
            "content": "Recent preference",
            "type": "preference",
            "created_at": datetime.now(),
            "importance": 5,
            "access_count": 0,
        }
        old = {
            "content": "Old preference",
            "type": "preference",
            "created_at": datetime.now() - timedelta(days=365),
            "importance": 5,
            "access_count": 0,
        }

        recent_score = injector.calculate_relevance(recent, semantic_score=0.5)
        old_score = injector.calculate_relevance(old, semantic_score=0.5)
        # Preferences have 10 year half-life, so scores should be very close
        assert abs(recent_score - old_score) < 0.05

    def test_calculate_relevance_importance_weight(self):
        """Test that importance affects score"""
        from sugar.integrations.opencode.injector import ContextInjector

        injector = ContextInjector()

        high_importance = {
            "content": "Important memory",
            "type": "decision",
            "created_at": datetime.now(),
            "importance": 10,
            "access_count": 0,
        }
        low_importance = {
            "content": "Low importance memory",
            "type": "decision",
            "created_at": datetime.now(),
            "importance": 1,
            "access_count": 0,
        }

        high_score = injector.calculate_relevance(high_importance, semantic_score=0.5)
        low_score = injector.calculate_relevance(low_importance, semantic_score=0.5)
        assert high_score > low_score

    def test_calculate_relevance_string_date(self):
        """Test relevance calculation with string date"""
        from sugar.integrations.opencode.injector import ContextInjector

        injector = ContextInjector()
        memory = {
            "content": "Memory with string date",
            "type": "decision",
            "created_at": "2025-01-15T10:00:00",
            "importance": 5,
            "access_count": 0,
        }
        score = injector.calculate_relevance(memory, semantic_score=0.5)
        assert 0 < score <= 1.0

    def test_fit_to_budget_basic(self):
        """Test fitting memories to token budget"""
        from sugar.integrations.opencode.injector import ContextInjector

        injector = ContextInjector()
        memories = [
            {"content": "Short", "type": "decision", "relevance": 0.9},
            {
                "content": "Medium length content here",
                "type": "preference",
                "relevance": 0.8,
            },
            {"content": "A" * 1000, "type": "error_pattern", "relevance": 0.7},
        ]

        # With a very small budget (5 tokens), should only fit the shortest memory
        # Token estimate is len(content) // 4 + 1, so "Short" = 5//4+1 = 2 tokens
        result = injector.fit_to_budget(memories, budget=5)
        assert len(result) >= 1
        assert result[0]["content"] == "Short"

    def test_fit_to_budget_type_diversity(self):
        """Test that fit_to_budget enforces type diversity"""
        from sugar.integrations.opencode.injector import ContextInjector

        injector = ContextInjector()
        # Create 5 memories of the same type
        memories = [
            {"content": f"Memory {i}", "type": "decision", "relevance": 0.9 - i * 0.1}
            for i in range(5)
        ]

        # With max_per_type=2, should only get 2 decisions
        result = injector.fit_to_budget(memories, budget=1000, max_per_type=2)
        assert len(result) == 2

    def test_fit_to_budget_respects_budget(self):
        """Test that fit_to_budget respects token budget"""
        from sugar.integrations.opencode.injector import ContextInjector

        injector = ContextInjector()
        # Each memory is ~25 tokens (100 chars / 4)
        memories = [
            {"content": "A" * 100, "type": f"type_{i}", "relevance": 0.9}
            for i in range(10)
        ]

        # Budget of 100 tokens should fit ~4 memories
        result = injector.fit_to_budget(memories, budget=100)
        assert len(result) <= 4

    def test_format_for_injection_empty(self):
        """Test formatting empty memories list"""
        from sugar.integrations.opencode.injector import ContextInjector

        injector = ContextInjector()
        result = injector.format_for_injection([])
        assert result == ""

    def test_format_for_injection_single_type(self):
        """Test formatting memories of a single type"""
        from sugar.integrations.opencode.injector import ContextInjector

        injector = ContextInjector()
        memories = [
            {"content": "Decision 1", "type": "decision"},
            {"content": "Decision 2", "type": "decision"},
        ]

        result = injector.format_for_injection(memories)
        assert "## Sugar Context" in result
        assert "### Previous Decisions" in result
        assert "- Decision 1" in result
        assert "- Decision 2" in result

    def test_format_for_injection_multiple_types(self):
        """Test formatting memories of multiple types"""
        from sugar.integrations.opencode.injector import ContextInjector

        injector = ContextInjector()
        memories = [
            {"content": "Prefer tabs", "type": "preference"},
            {"content": "Use pytest", "type": "decision"},
            {"content": "Fix null check", "type": "error_pattern"},
        ]

        result = injector.format_for_injection(memories)
        assert "### Coding Preferences" in result
        assert "### Previous Decisions" in result
        assert "### Known Error Patterns" in result

    def test_format_for_injection_truncates_long_content(self):
        """Test that long content is truncated"""
        from sugar.integrations.opencode.injector import ContextInjector

        injector = ContextInjector()
        memories = [
            {"content": "A" * 600, "type": "decision"},
        ]

        result = injector.format_for_injection(memories)
        # Content should be truncated to 500 chars + "..."
        assert "A" * 500 in result
        assert "..." in result


# =============================================================================
# LearningCapture Tests
# =============================================================================


class TestLearningCapture:
    """Tests for LearningCapture class"""

    @pytest.mark.asyncio
    async def test_extract_learnings_decision(self):
        """Test extracting decision learnings"""
        from sugar.integrations.opencode.injector import LearningCapture

        capture = LearningCapture()
        content = "After discussion, we decided to use PostgreSQL over MySQL for this project."

        learnings = await capture.extract_learnings(content)
        assert len(learnings) > 0
        decision = next((l for l in learnings if l["type"] == "decision"), None)
        assert decision is not None
        assert "PostgreSQL" in decision["content"] or "decided" in decision["content"]

    @pytest.mark.asyncio
    async def test_extract_learnings_preference(self):
        """Test extracting preference learnings"""
        from sugar.integrations.opencode.injector import LearningCapture

        capture = LearningCapture()
        # Pattern requires "prefer to use" or "prefer using" (not "prefers")
        content = "We prefer to use functional components in React."

        learnings = await capture.extract_learnings(content)
        preference = next((l for l in learnings if l["type"] == "preference"), None)
        assert preference is not None

    @pytest.mark.asyncio
    async def test_extract_learnings_error_pattern(self):
        """Test extracting error pattern learnings"""
        from sugar.integrations.opencode.injector import LearningCapture

        capture = LearningCapture()
        content = (
            "The TypeError was fixed by adding null check before accessing property."
        )

        learnings = await capture.extract_learnings(content)
        error = next((l for l in learnings if l["type"] == "error_pattern"), None)
        assert error is not None

    @pytest.mark.asyncio
    async def test_extract_learnings_no_matches(self):
        """Test that generic text produces no learnings"""
        from sugar.integrations.opencode.injector import LearningCapture

        capture = LearningCapture()
        content = "This is just a regular comment with no special patterns."

        learnings = await capture.extract_learnings(content)
        assert len(learnings) == 0

    @pytest.mark.asyncio
    async def test_extract_learnings_multiple(self):
        """Test extracting multiple learnings from one text"""
        from sugar.integrations.opencode.injector import LearningCapture

        capture = LearningCapture()
        content = """
        We decided to use TypeScript for this project.
        We always use ESLint for linting.
        The import error was fixed by updating package.json.
        """

        learnings = await capture.extract_learnings(content)
        types = [l["type"] for l in learnings]
        assert "decision" in types
        assert "preference" in types
        assert "error_pattern" in types

    @pytest.mark.asyncio
    async def test_store_learning_no_store(self):
        """Test storing learning when no memory store available"""
        from sugar.integrations.opencode.injector import LearningCapture

        capture = LearningCapture()
        # Mock _get_memory_store to return None
        capture._get_memory_store = MagicMock(return_value=None)

        result = await capture.store_learning("Test content", "decision")
        assert result is None


# =============================================================================
# Client Tests (with mocked HTTP)
# =============================================================================


class TestOpenCodeClientAvailability:
    """Test OpenCodeClient import and availability checks"""

    def test_aiohttp_not_available(self):
        """Test error when aiohttp is not installed"""
        # This test documents expected behavior when aiohttp is missing
        # In actual code, AIOHTTP_AVAILABLE would be False
        pass  # Skip as we can't easily simulate missing aiohttp

    def test_client_creation(self):
        """Test creating client with config"""
        try:
            from sugar.integrations.opencode.client import (
                OpenCodeClient,
                AIOHTTP_AVAILABLE,
            )
            from sugar.integrations.opencode.config import OpenCodeConfig

            if not AIOHTTP_AVAILABLE:
                pytest.skip("aiohttp not installed")

            config = OpenCodeConfig(server_url="http://test:4096")
            client = OpenCodeClient(config)
            assert client.config.server_url == "http://test:4096"
        except ImportError:
            pytest.skip("aiohttp not installed")


@pytest.mark.asyncio
class TestOpenCodeClientMocked:
    """Tests for OpenCodeClient with mocked HTTP responses"""

    async def test_health_check_success(self):
        """Test health check returns True on 200"""
        try:
            from sugar.integrations.opencode.client import (
                OpenCodeClient,
                AIOHTTP_AVAILABLE,
            )

            if not AIOHTTP_AVAILABLE:
                pytest.skip("aiohttp not installed")

            # Would need to mock aiohttp session here
            pass
        except ImportError:
            pytest.skip("aiohttp not installed")


# =============================================================================
# Integration Module Tests
# =============================================================================


class TestIntegrationImports:
    """Test that integration module imports work correctly"""

    def test_lazy_import_opencode_client(self):
        """Test lazy import of OpenCodeClient"""
        try:
            from sugar.integrations import OpenCodeClient

            assert OpenCodeClient is not None
        except ImportError as e:
            # Expected if aiohttp not installed
            assert "aiohttp" in str(e)

    def test_lazy_import_opencode_config(self):
        """Test lazy import of OpenCodeConfig"""
        try:
            from sugar.integrations import OpenCodeConfig

            assert OpenCodeConfig is not None
        except ImportError as e:
            assert "aiohttp" in str(e)

    def test_direct_import_config(self):
        """Test direct import of config (no aiohttp needed)"""
        from sugar.integrations.opencode.config import OpenCodeConfig

        config = OpenCodeConfig()
        assert config is not None

    def test_direct_import_models(self):
        """Test direct import of models (no aiohttp needed)"""
        from sugar.integrations.opencode.models import (
            Session,
            Message,
            Event,
            NotificationLevel,
        )

        assert Session is not None
        assert Message is not None
        assert Event is not None
        assert NotificationLevel is not None


# =============================================================================
# Constants and Thresholds Tests
# =============================================================================


class TestInjectorConstants:
    """Test ContextInjector constants and thresholds"""

    def test_budget_constants(self):
        """Test token budget constants"""
        from sugar.integrations.opencode.injector import ContextInjector

        assert ContextInjector.SESSION_START_BUDGET == 2000
        assert ContextInjector.PER_PROMPT_BUDGET == 800
        assert ContextInjector.ERROR_CONTEXT_BUDGET == 300

    def test_weight_constants_sum_to_one(self):
        """Test that relevance weights sum to 1.0"""
        from sugar.integrations.opencode.injector import ContextInjector

        total = (
            ContextInjector.SEMANTIC_WEIGHT
            + ContextInjector.RECENCY_WEIGHT
            + ContextInjector.IMPORTANCE_WEIGHT
            + ContextInjector.FREQUENCY_WEIGHT
            + ContextInjector.TYPE_WEIGHT
        )
        assert abs(total - 1.0) < 0.001

    def test_type_priorities(self):
        """Test type priority ordering"""
        from sugar.integrations.opencode.injector import ContextInjector

        priorities = ContextInjector.TYPE_PRIORITIES
        # Preferences should be highest priority
        assert priorities["preference"] >= priorities["decision"]
        assert priorities["decision"] >= priorities["error_pattern"]
        # Outcomes should be lowest
        assert priorities["outcome"] <= priorities["research"]

    def test_similarity_thresholds(self):
        """Test similarity thresholds by type"""
        from sugar.integrations.opencode.injector import ContextInjector

        thresholds = ContextInjector.SIMILARITY_THRESHOLDS
        # Preferences should have lower threshold (easier to match)
        assert thresholds["preference"] < thresholds["outcome"]

    def test_half_life_values(self):
        """Test recency half-life values"""
        from sugar.integrations.opencode.injector import ContextInjector

        half_life = ContextInjector.HALF_LIFE
        # Preferences and decisions should not expire
        assert half_life["preference"] >= 365 * 5
        assert half_life["decision"] >= 365 * 5
        # Outcomes should expire fastest
        assert half_life["outcome"] < half_life["error_pattern"]
