"""
Model Router - Routes tasks to appropriate Claude models based on complexity

Maps task types and complexity levels to Claude model tiers (simple, standard, complex)
for cost-effective model selection based on task requirements.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ModelTier(Enum):
    """Model tiers for cost/capability trade-offs"""

    SIMPLE = "simple"  # Low complexity - fast, cheap model (e.g., Haiku)
    STANDARD = "standard"  # Medium complexity - balanced model (e.g., Sonnet)
    COMPLEX = "complex"  # High complexity - most capable model (e.g., Opus)


@dataclass
class ModelSelection:
    """Result of model routing decision"""

    model: str  # The actual Claude model name
    tier: ModelTier  # The tier that was selected
    reason: str  # Reason for the selection
    task_type: Optional[str]  # Task type that influenced the decision
    complexity_level: int  # Complexity level (1-5)
    override_applied: bool  # Whether an override was applied


class ModelRouter:
    """
    Routes tasks to appropriate Claude models based on complexity.

    This router:
    1. Maps model tiers (simple, standard, complex) to actual Claude model names
    2. Determines the appropriate tier based on task type and complexity
    3. Supports runtime complexity analysis for dynamic routing
    4. Allows configuration overrides via config.yaml
    """

    # Default model mappings (can be overridden via config)
    DEFAULT_MODEL_MAPPINGS = {
        ModelTier.SIMPLE: "claude-haiku-3-5-20241022",
        ModelTier.STANDARD: "claude-sonnet-4-20250514",
        ModelTier.COMPLEX: "claude-sonnet-4-20250514",  # Opus when available
    }

    # Complexity level to tier mapping
    COMPLEXITY_TO_TIER = {
        1: ModelTier.SIMPLE,
        2: ModelTier.SIMPLE,
        3: ModelTier.STANDARD,
        4: ModelTier.COMPLEX,
        5: ModelTier.COMPLEX,
    }

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the model router.

        Args:
            config: Configuration dictionary containing optional model mappings
        """
        self.config = config
        self.model_mappings = self._load_model_mappings()
        self._task_type_manager = None  # Lazy loaded

        logger.debug(f"ModelRouter initialized with mappings: {self.model_mappings}")

    def _load_model_mappings(self) -> Dict[ModelTier, str]:
        """
        Load model mappings from configuration.

        Returns:
            Dict mapping ModelTier to actual model names
        """
        mappings = self.DEFAULT_MODEL_MAPPINGS.copy()

        # Load custom mappings from config
        models_config = self.config.get("models", {})

        if "simple" in models_config:
            mappings[ModelTier.SIMPLE] = models_config["simple"]
        if "standard" in models_config:
            mappings[ModelTier.STANDARD] = models_config["standard"]
        if "complex" in models_config:
            mappings[ModelTier.COMPLEX] = models_config["complex"]

        return mappings

    def get_model_for_tier(self, tier: ModelTier) -> str:
        """
        Get the actual model name for a tier.

        Args:
            tier: ModelTier enum value

        Returns:
            Claude model name string
        """
        return self.model_mappings.get(tier, self.model_mappings[ModelTier.STANDARD])

    def route(
        self,
        task: Dict[str, Any],
        task_type_info: Optional[Dict[str, Any]] = None,
    ) -> ModelSelection:
        """
        Route a task to the appropriate model.

        Analyzes task type, stored complexity level, and runtime analysis
        to determine the best model tier.

        Args:
            task: Task dictionary with type, title, description, etc.
            task_type_info: Optional task type information from database

        Returns:
            ModelSelection with model name, tier, and reasoning
        """
        task_type = task.get("type", "feature")
        context = task.get("context", {})

        # Check for explicit model override in task context
        if context.get("model_override"):
            model = context["model_override"]
            return ModelSelection(
                model=model,
                tier=self._infer_tier_from_model(model),
                reason="Explicit model override in task context",
                task_type=task_type,
                complexity_level=3,
                override_applied=True,
            )

        # Check for explicit tier override in task context
        if context.get("model_tier"):
            tier_str = context["model_tier"]
            tier = (
                ModelTier(tier_str)
                if tier_str in [t.value for t in ModelTier]
                else ModelTier.STANDARD
            )
            model = self.get_model_for_tier(tier)
            return ModelSelection(
                model=model,
                tier=tier,
                reason=f"Explicit tier override in task context: {tier_str}",
                task_type=task_type,
                complexity_level=3,
                override_applied=True,
            )

        # Get tier and complexity from task type info (from database)
        if task_type_info:
            tier_str = task_type_info.get("model_tier", "standard")
            complexity_level = task_type_info.get("complexity_level", 3)
        else:
            # Use defaults for unknown task types
            tier_str = self._get_default_tier_for_type(task_type)
            complexity_level = self._get_default_complexity_for_type(task_type)

        # Convert string tier to enum
        try:
            tier = ModelTier(tier_str)
        except ValueError:
            tier = ModelTier.STANDARD

        # Perform runtime complexity analysis to potentially upgrade tier
        analyzed_tier, analysis_reason = self._analyze_runtime_complexity(
            task, tier, complexity_level
        )

        model = self.get_model_for_tier(analyzed_tier)

        reason = f"Task type '{task_type}' with complexity level {complexity_level}"
        if analyzed_tier != tier:
            reason += f". {analysis_reason}"

        return ModelSelection(
            model=model,
            tier=analyzed_tier,
            reason=reason,
            task_type=task_type,
            complexity_level=complexity_level,
            override_applied=False,
        )

    def _analyze_runtime_complexity(
        self,
        task: Dict[str, Any],
        base_tier: ModelTier,
        complexity_level: int,
    ) -> tuple[ModelTier, str]:
        """
        Analyze task content at runtime to potentially upgrade tier.

        Args:
            task: Task dictionary
            base_tier: Initial tier based on task type
            complexity_level: Initial complexity level

        Returns:
            Tuple of (adjusted tier, reason for adjustment)
        """
        title = task.get("title", "").lower()
        description = task.get("description", "").lower()
        full_text = f"{title} {description}"

        # Keywords that suggest higher complexity
        complex_indicators = [
            "refactor",
            "migrate",
            "redesign",
            "rewrite",
            "architecture",
            "system-wide",
            "multi-file",
            "complex",
            "comprehensive",
            "integrate",
            "breaking change",
            "major",
            "overhaul",
        ]

        # Keywords that suggest simpler tasks
        simple_indicators = [
            "typo",
            "comment",
            "formatting",
            "style",
            "rename",
            "trivial",
            "minor",
            "quick",
            "simple",
            "update docs",
        ]

        # Count indicators
        complex_count = sum(1 for kw in complex_indicators if kw in full_text)
        simple_count = sum(1 for kw in simple_indicators if kw in full_text)

        # Adjust tier based on indicators
        if complex_count >= 2 and base_tier != ModelTier.COMPLEX:
            return (
                ModelTier.COMPLEX,
                f"Runtime analysis detected {complex_count} complexity indicators",
            )

        if simple_count >= 2 and base_tier != ModelTier.SIMPLE:
            return (
                ModelTier.SIMPLE,
                f"Runtime analysis detected {simple_count} simplicity indicators",
            )

        # Check task scope (multiple files mentioned)
        if (
            "multiple files" in full_text
            or "several files" in full_text
            or "across" in full_text
        ):
            if base_tier == ModelTier.SIMPLE:
                return ModelTier.STANDARD, "Multi-file scope detected"

        return base_tier, ""

    def _get_default_tier_for_type(self, task_type: str) -> str:
        """Get default tier for a task type when not in database"""
        defaults = {
            "docs": "simple",
            "style": "simple",
            "chore": "simple",
            "test": "standard",
            "bug_fix": "standard",
            "ci": "standard",
            "feature": "complex",
            "refactor": "complex",
            "perf": "complex",
            "security": "complex",
        }
        return defaults.get(task_type, "standard")

    def _get_default_complexity_for_type(self, task_type: str) -> int:
        """Get default complexity level for a task type when not in database"""
        defaults = {
            "docs": 1,
            "style": 1,
            "chore": 2,
            "test": 2,
            "bug_fix": 3,
            "ci": 2,
            "feature": 3,
            "refactor": 4,
            "perf": 4,
            "security": 4,
        }
        return defaults.get(task_type, 3)

    def _infer_tier_from_model(self, model: str) -> ModelTier:
        """Infer tier from a model name"""
        model_lower = model.lower()

        if "haiku" in model_lower:
            return ModelTier.SIMPLE
        elif "opus" in model_lower:
            return ModelTier.COMPLEX
        else:
            return ModelTier.STANDARD

    def get_tier_for_complexity(self, complexity_level: int) -> ModelTier:
        """
        Get the appropriate tier for a complexity level.

        Args:
            complexity_level: Complexity level (1-5)

        Returns:
            Appropriate ModelTier
        """
        return self.COMPLEXITY_TO_TIER.get(complexity_level, ModelTier.STANDARD)

    def get_available_models(self) -> Dict[str, str]:
        """
        Get all configured models.

        Returns:
            Dict mapping tier names to model names
        """
        return {tier.value: model for tier, model in self.model_mappings.items()}

    def validate_model(self, model: str) -> bool:
        """
        Validate that a model name is known.

        Args:
            model: Model name to validate

        Returns:
            True if model is in configured mappings
        """
        return model in self.model_mappings.values()


def create_model_router(config: Dict[str, Any]) -> ModelRouter:
    """
    Factory function to create a ModelRouter instance.

    Args:
        config: Configuration dictionary

    Returns:
        Configured ModelRouter instance
    """
    return ModelRouter(config)
