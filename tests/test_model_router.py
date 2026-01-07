"""
Tests for Model Router (AUTO-001)

Tests the model routing functionality covering:
- ModelRouter: Dynamic model selection based on task complexity
- ModelTier: Tier mapping (simple, standard, complex)
- Integration with AgentSDKExecutor
"""

import pytest
from typing import Dict, Any, Optional
from unittest.mock import MagicMock, AsyncMock, patch

from sugar.orchestration.model_router import (
    ModelRouter,
    ModelTier,
    ModelSelection,
    create_model_router,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def default_config():
    """Default configuration without custom model mappings."""
    return {}


@pytest.fixture
def custom_config():
    """Configuration with custom model mappings."""
    return {
        "models": {
            "simple": "claude-haiku-3-5-20241022",
            "standard": "claude-sonnet-4-20250514",
            "complex": "claude-sonnet-4-20250514",
            "dynamic_routing": True,
        }
    }


@pytest.fixture
def disabled_routing_config():
    """Configuration with dynamic routing disabled."""
    return {
        "models": {
            "dynamic_routing": False,
        }
    }


@pytest.fixture
def model_router(default_config):
    """ModelRouter instance with default config."""
    return ModelRouter(default_config)


@pytest.fixture
def custom_model_router(custom_config):
    """ModelRouter instance with custom config."""
    return ModelRouter(custom_config)


# ============================================================================
# Test ModelTier Enum
# ============================================================================


class TestModelTier:
    """Tests for ModelTier enum."""

    def test_tier_values(self):
        """Verify tier enum values."""
        assert ModelTier.SIMPLE.value == "simple"
        assert ModelTier.STANDARD.value == "standard"
        assert ModelTier.COMPLEX.value == "complex"

    def test_tier_creation_from_string(self):
        """Tier can be created from string value."""
        assert ModelTier("simple") == ModelTier.SIMPLE
        assert ModelTier("standard") == ModelTier.STANDARD
        assert ModelTier("complex") == ModelTier.COMPLEX

    def test_invalid_tier_raises(self):
        """Invalid tier string raises ValueError."""
        with pytest.raises(ValueError):
            ModelTier("invalid")


# ============================================================================
# Test ModelRouter Initialization
# ============================================================================


class TestModelRouterInit:
    """Tests for ModelRouter initialization."""

    def test_default_mappings(self, model_router):
        """Default model mappings are set correctly."""
        mappings = model_router.model_mappings

        assert ModelTier.SIMPLE in mappings
        assert ModelTier.STANDARD in mappings
        assert ModelTier.COMPLEX in mappings

    def test_custom_mappings_override_defaults(self, custom_model_router):
        """Custom config overrides default mappings."""
        mappings = custom_model_router.model_mappings

        assert mappings[ModelTier.SIMPLE] == "claude-haiku-3-5-20241022"
        assert mappings[ModelTier.STANDARD] == "claude-sonnet-4-20250514"
        assert mappings[ModelTier.COMPLEX] == "claude-sonnet-4-20250514"

    def test_partial_custom_mappings(self):
        """Partial custom config only overrides specified tiers."""
        config = {
            "models": {
                "simple": "custom-haiku-model",
                # standard and complex not specified
            }
        }
        router = ModelRouter(config)

        assert router.model_mappings[ModelTier.SIMPLE] == "custom-haiku-model"
        # Others should use defaults
        assert ModelTier.STANDARD in router.model_mappings
        assert ModelTier.COMPLEX in router.model_mappings


# ============================================================================
# Test Model Selection
# ============================================================================


class TestModelSelection:
    """Tests for ModelSelection dataclass."""

    def test_model_selection_creation(self):
        """ModelSelection can be created with all fields."""
        selection = ModelSelection(
            model="claude-sonnet-4-20250514",
            tier=ModelTier.STANDARD,
            reason="Test reason",
            task_type="feature",
            complexity_level=3,
            override_applied=False,
        )

        assert selection.model == "claude-sonnet-4-20250514"
        assert selection.tier == ModelTier.STANDARD
        assert selection.reason == "Test reason"
        assert selection.task_type == "feature"
        assert selection.complexity_level == 3
        assert selection.override_applied is False


# ============================================================================
# Test Routing Logic
# ============================================================================


class TestModelRouting:
    """Tests for model routing logic."""

    def test_route_simple_task_type(self, model_router):
        """Simple task types route to simple tier."""
        task = {
            "type": "docs",
            "title": "Update README",
            "description": "Fix typo in documentation",
        }

        selection = model_router.route(task)

        assert selection.tier == ModelTier.SIMPLE
        assert "docs" in selection.reason

    def test_route_standard_task_type(self, model_router):
        """Standard task types route to standard tier."""
        task = {
            "type": "bug_fix",
            "title": "Fix login issue",
            "description": "Users cannot login with special characters",
        }

        selection = model_router.route(task)

        assert selection.tier == ModelTier.STANDARD
        assert "bug_fix" in selection.reason

    def test_route_complex_task_type(self, model_router):
        """Complex task types route to complex tier."""
        task = {
            "type": "refactor",
            "title": "Refactor authentication system",
            "description": "Complete overhaul of auth module",
        }

        selection = model_router.route(task)

        assert selection.tier == ModelTier.COMPLEX
        assert "refactor" in selection.reason

    def test_route_with_task_type_info(self, model_router):
        """Routing uses task_type_info when provided."""
        task = {
            "type": "feature",
            "title": "Add new feature",
            "description": "Simple feature",
        }
        task_type_info = {
            "model_tier": "simple",
            "complexity_level": 1,
        }

        selection = model_router.route(task, task_type_info)

        assert selection.tier == ModelTier.SIMPLE
        assert selection.complexity_level == 1

    def test_route_explicit_model_override(self, model_router):
        """Explicit model override in context is respected."""
        task = {
            "type": "feature",
            "title": "Complex feature",
            "description": "Multi-step implementation",
            "context": {
                "model_override": "claude-opus-4-20250514",
            },
        }

        selection = model_router.route(task)

        assert selection.model == "claude-opus-4-20250514"
        assert selection.override_applied is True
        assert "override" in selection.reason.lower()

    def test_route_explicit_tier_override(self, model_router):
        """Explicit tier override in context is respected."""
        task = {
            "type": "docs",  # Would normally be simple
            "title": "Update docs",
            "description": "Simple docs update",
            "context": {
                "model_tier": "complex",  # Override to complex
            },
        }

        selection = model_router.route(task)

        assert selection.tier == ModelTier.COMPLEX
        assert selection.override_applied is True


# ============================================================================
# Test Runtime Complexity Analysis
# ============================================================================


class TestRuntimeComplexityAnalysis:
    """Tests for runtime complexity analysis."""

    def test_complexity_keywords_upgrade_tier(self, model_router):
        """Complex keywords in task content upgrade tier."""
        task = {
            "type": "bug_fix",  # Standard tier (not already complex)
            "title": "Refactor and migrate entire database layer",
            "description": "Major system-wide architecture change with breaking changes",
        }

        selection = model_router.route(task)

        # Should be upgraded to complex due to complexity keywords
        assert selection.tier == ModelTier.COMPLEX
        assert "complexity indicators" in selection.reason.lower()

    def test_simple_keywords_downgrade_tier(self, model_router):
        """Simple keywords in task content can downgrade tier."""
        task = {
            "type": "feature",  # Would be complex
            "title": "Fix typo in variable name",
            "description": "Simple rename, trivial change",
        }

        selection = model_router.route(task)

        # Should be downgraded due to simple keywords
        assert selection.tier in [ModelTier.SIMPLE, ModelTier.STANDARD]

    def test_multi_file_scope_detected(self, model_router):
        """Multi-file scope affects tier selection."""
        task = {
            "type": "chore",  # Simple tier
            "title": "Update imports across multiple files",
            "description": "Change import paths throughout the codebase",
        }

        selection = model_router.route(task)

        # Should be upgraded due to scope
        assert selection.tier in [ModelTier.STANDARD, ModelTier.COMPLEX]


# ============================================================================
# Test Helper Methods
# ============================================================================


class TestModelRouterHelpers:
    """Tests for ModelRouter helper methods."""

    def test_get_model_for_tier(self, custom_model_router):
        """get_model_for_tier returns correct model."""
        assert (
            custom_model_router.get_model_for_tier(ModelTier.SIMPLE)
            == "claude-haiku-3-5-20241022"
        )
        assert (
            custom_model_router.get_model_for_tier(ModelTier.STANDARD)
            == "claude-sonnet-4-20250514"
        )
        assert (
            custom_model_router.get_model_for_tier(ModelTier.COMPLEX)
            == "claude-sonnet-4-20250514"
        )

    def test_get_tier_for_complexity(self, model_router):
        """get_tier_for_complexity maps levels to tiers."""
        assert model_router.get_tier_for_complexity(1) == ModelTier.SIMPLE
        assert model_router.get_tier_for_complexity(2) == ModelTier.SIMPLE
        assert model_router.get_tier_for_complexity(3) == ModelTier.STANDARD
        assert model_router.get_tier_for_complexity(4) == ModelTier.COMPLEX
        assert model_router.get_tier_for_complexity(5) == ModelTier.COMPLEX

    def test_get_available_models(self, custom_model_router):
        """get_available_models returns all configured models."""
        models = custom_model_router.get_available_models()

        assert "simple" in models
        assert "standard" in models
        assert "complex" in models
        assert models["simple"] == "claude-haiku-3-5-20241022"

    def test_validate_model(self, model_router):
        """validate_model checks if model is configured."""
        # Valid models
        for tier in ModelTier:
            model = model_router.get_model_for_tier(tier)
            assert model_router.validate_model(model) is True

        # Invalid model
        assert model_router.validate_model("invalid-model-name") is False

    def test_get_default_tier_for_type(self, model_router):
        """Default tier for unknown task types is standard."""
        # Private method test
        assert model_router._get_default_tier_for_type("docs") == "simple"
        assert model_router._get_default_tier_for_type("feature") == "complex"
        assert model_router._get_default_tier_for_type("unknown") == "standard"

    def test_get_default_complexity_for_type(self, model_router):
        """Default complexity for unknown task types is 3."""
        # Private method test
        assert model_router._get_default_complexity_for_type("docs") == 1
        assert model_router._get_default_complexity_for_type("refactor") == 4
        assert model_router._get_default_complexity_for_type("unknown") == 3

    def test_infer_tier_from_model(self, model_router):
        """Infer tier from model name."""
        # Private method test
        assert (
            model_router._infer_tier_from_model("claude-haiku-3-5-20241022")
            == ModelTier.SIMPLE
        )
        assert (
            model_router._infer_tier_from_model("claude-sonnet-4-20250514")
            == ModelTier.STANDARD
        )
        assert (
            model_router._infer_tier_from_model("claude-opus-4-20250514")
            == ModelTier.COMPLEX
        )


# ============================================================================
# Test Factory Function
# ============================================================================


class TestCreateModelRouter:
    """Tests for create_model_router factory function."""

    def test_factory_creates_router(self, default_config):
        """Factory function creates ModelRouter instance."""
        router = create_model_router(default_config)

        assert isinstance(router, ModelRouter)

    def test_factory_passes_config(self, custom_config):
        """Factory function passes config to router."""
        router = create_model_router(custom_config)

        assert router.model_mappings[ModelTier.SIMPLE] == "claude-haiku-3-5-20241022"


# ============================================================================
# Test Task Type Mappings
# ============================================================================


class TestTaskTypeMappings:
    """Tests for default task type to tier mappings."""

    @pytest.mark.parametrize(
        "task_type,expected_tier",
        [
            ("docs", "simple"),
            ("style", "simple"),
            ("chore", "simple"),
            ("test", "standard"),
            ("bug_fix", "standard"),
            ("ci", "standard"),
            ("feature", "complex"),
            ("refactor", "complex"),
            ("perf", "complex"),
            ("security", "complex"),
        ],
    )
    def test_task_type_default_tier(self, model_router, task_type, expected_tier):
        """Each task type has correct default tier."""
        tier = model_router._get_default_tier_for_type(task_type)
        assert tier == expected_tier

    @pytest.mark.parametrize(
        "task_type,expected_level",
        [
            ("docs", 1),
            ("style", 1),
            ("chore", 2),
            ("test", 2),
            ("bug_fix", 3),
            ("ci", 2),
            ("feature", 3),
            ("refactor", 4),
            ("perf", 4),
            ("security", 4),
        ],
    )
    def test_task_type_default_complexity(
        self, model_router, task_type, expected_level
    ):
        """Each task type has correct default complexity level."""
        level = model_router._get_default_complexity_for_type(task_type)
        assert level == expected_level


# ============================================================================
# Integration Tests
# ============================================================================


class TestModelRouterIntegration:
    """Integration tests for ModelRouter with other components."""

    def test_full_routing_workflow(self, custom_model_router):
        """Test complete routing workflow."""
        # Create various tasks
        tasks = [
            {
                "type": "docs",
                "title": "Update README",
                "description": "Fix installation instructions",
            },
            {
                "type": "feature",
                "title": "Add user authentication",
                "description": "Implement OAuth 2.0 flow",
            },
            {
                "type": "refactor",
                "title": "Refactor entire codebase",
                "description": "Major architectural change across all modules",
            },
        ]

        expected_tiers = [ModelTier.SIMPLE, ModelTier.COMPLEX, ModelTier.COMPLEX]

        for task, expected_tier in zip(tasks, expected_tiers):
            selection = custom_model_router.route(task)
            assert (
                selection.tier == expected_tier
            ), f"Task '{task['title']}' should route to {expected_tier}"
            assert selection.model is not None
            assert len(selection.reason) > 0

    def test_routing_with_database_task_info(self, model_router):
        """Routing uses database task type info when available."""
        task = {
            "type": "custom_type",
            "title": "Custom task",
            "description": "Task with custom type",
        }

        # Simulate task type info from database
        task_type_info = {
            "model_tier": "complex",
            "complexity_level": 5,
        }

        selection = model_router.route(task, task_type_info)

        assert selection.tier == ModelTier.COMPLEX
        assert selection.complexity_level == 5

    def test_routing_handles_missing_fields(self, model_router):
        """Routing handles tasks with missing fields gracefully."""
        minimal_task = {
            "type": "feature",
        }

        # Should not raise exception
        selection = model_router.route(minimal_task)

        assert selection.tier is not None
        assert selection.model is not None

    def test_routing_handles_empty_context(self, model_router):
        """Routing handles empty context gracefully."""
        task = {
            "type": "feature",
            "title": "Test task",
            "description": "Test description",
            "context": {},
        }

        selection = model_router.route(task)

        assert selection.override_applied is False
