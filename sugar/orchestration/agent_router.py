"""
Agent Router - Routes tasks to specialist agents based on content analysis

Maps task content to appropriate specialist agents using pattern matching
and task type analysis.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from ..orchestration.task_orchestrator import OrchestrationStage

logger = logging.getLogger(__name__)


class AgentRouter:
    """
    Routes tasks to specialist agents based on content analysis.

    Analyzes task title, description, and type to determine the most
    appropriate specialist agent for execution.
    """

    # Available specialist agents (mapped to Claude Code agent types)
    AVAILABLE_AGENTS = [
        "general-purpose",
        "tech-lead",
        "code-reviewer",
        "frontend-designer",
        "backend-developer",
        "qa-engineer",
        "security-engineer",
        "devops-engineer",
        "Explore",
        "Plan",
    ]

    # Default routing patterns (can be overridden by config)
    DEFAULT_PATTERNS = {
        # Frontend patterns
        r"\b(ui|frontend|component|design|css|html|react|vue|angular|interface|styling|layout)\b": "frontend-designer",
        # Backend patterns
        r"\b(api|backend|endpoint|service|database|db|server|route|middleware|orm|query)\b": "backend-developer",
        # Testing patterns
        r"\b(test|spec|coverage|qa|quality|e2e|integration|unit test|testing)\b": "qa-engineer",
        # Security patterns
        r"\b(security|auth|authentication|authorization|permission|oauth|jwt|token|vulnerability|encrypt)\b": "security-engineer",
        # DevOps patterns
        r"\b(devops|deploy|deployment|ci|cd|docker|kubernetes|k8s|container|pipeline|infrastructure)\b": "devops-engineer",
        # Documentation patterns
        r"\b(doc|documentation|readme|guide|tutorial|manual)\b": "general-purpose",
        # Architecture/planning patterns
        r"\b(architecture|design|planning|refactor|restructure|redesign)\b": "tech-lead",
        # Code review patterns
        r"\b(review|audit|quality|lint|analyze|inspect)\b": "code-reviewer",
    }

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the agent router.

        Args:
            config: Configuration dictionary with optional routing patterns
        """
        self.config = config
        self.patterns = self._load_routing_patterns()

        logger.debug(
            f"AgentRouter initialized with {len(self.patterns)} routing patterns"
        )

    def _load_routing_patterns(self) -> Dict[str, str]:
        """
        Load routing patterns from configuration.

        Returns:
            Dict mapping regex patterns to agent names
        """
        # Start with defaults
        patterns = {}

        # Load custom patterns from config if available
        orchestration_config = self.config.get("orchestration", {})
        stages_config = orchestration_config.get("stages", {})
        impl_config = stages_config.get("implementation", {})
        agent_routing = impl_config.get("agent_routing", {})

        if agent_routing:
            # Convert config glob patterns to word-boundary regexes.
            # "*ui*|*frontend*" -> r"\b(?:ui|frontend)\b" so that "ui" matches
            # as a word but does NOT match the "ui" inside "build" (which a
            # naive .*ui.* substring conversion would, mis-routing every
            # "Build ..." task to the frontend agent).
            for pattern, agent in agent_routing.items():
                if pattern == "default":
                    continue
                terms = []
                for alt in pattern.split("|"):
                    cleaned = alt.strip().replace("*", "")
                    if cleaned:
                        terms.append(re.escape(cleaned))
                if terms:
                    regex_pattern = r"\b(?:" + "|".join(terms) + r")\b"
                    patterns[regex_pattern] = agent
        else:
            # Use defaults
            patterns = self.DEFAULT_PATTERNS.copy()

        return patterns

    def route(self, task: Dict[str, Any]) -> str:
        """
        Route a task to the appropriate specialist agent.

        Analyzes task content and returns the best-fit agent name.

        Args:
            task: Task dictionary with title, description, type, etc.

        Returns:
            Agent name (from AVAILABLE_AGENTS)
        """
        title = task.get("title", "").lower()
        description = task.get("description", "").lower()
        task_type = task.get("type", "").lower()

        # Combine all searchable content
        search_text = f"{title} {description} {task_type}"

        # Check explicit agent assignment first
        if "assigned_agent" in task and task["assigned_agent"]:
            agent = task["assigned_agent"]
            if agent in self.AVAILABLE_AGENTS:
                logger.debug(f"Using explicitly assigned agent: {agent}")
                return agent

        # Try pattern matching
        for pattern, agent in self.patterns.items():
            if re.search(pattern, search_text, re.IGNORECASE):
                logger.debug(
                    f"Matched pattern '{pattern}' -> {agent} for task: {title[:50]}"
                )
                return agent

        # Check task type mapping
        type_agent = self._get_agent_for_type(task_type)
        if type_agent:
            logger.debug(f"Using type-based routing: {task_type} -> {type_agent}")
            return type_agent

        # Default fallback
        default = self._get_default_agent()
        logger.debug(f"Using default agent {default} for task: {title[:50]}")
        return default

    def _get_agent_for_type(self, task_type: str) -> Optional[str]:
        """
        Get agent based on task type.

        Args:
            task_type: Task type string

        Returns:
            Agent name or None
        """
        type_mapping = {
            "bug_fix": "tech-lead",
            "feature": "general-purpose",
            "refactor": "code-reviewer",
            "test": "qa-engineer",
            "documentation": "general-purpose",
            "security": "security-engineer",
            "devops": "devops-engineer",
        }

        return type_mapping.get(task_type)

    def _get_default_agent(self) -> str:
        """
        Get the default agent from config or use fallback.

        Returns:
            Default agent name
        """
        orchestration_config = self.config.get("orchestration", {})
        stages_config = orchestration_config.get("stages", {})
        impl_config = stages_config.get("implementation", {})
        agent_routing = impl_config.get("agent_routing", {})

        return agent_routing.get("default", "general-purpose")

    def get_stage_agent(self, stage: OrchestrationStage) -> str:
        """
        Get the default agent for an orchestration stage.

        Args:
            stage: OrchestrationStage enum value

        Returns:
            Agent name for the stage
        """
        # Load stage configuration
        orchestration_config = self.config.get("orchestration", {})
        stages_config = orchestration_config.get("stages", {})

        # Stage-specific defaults
        stage_defaults = {
            OrchestrationStage.RESEARCH: "Explore",
            OrchestrationStage.PLANNING: "Plan",
            OrchestrationStage.IMPLEMENTATION: "general-purpose",  # Will be routed per subtask
            OrchestrationStage.REVIEW: "code-reviewer",
        }

        # Try to get from config
        stage_name = stage.value
        stage_config = stages_config.get(stage_name, {})

        if "agent" in stage_config:
            agent = stage_config["agent"]
            if agent in self.AVAILABLE_AGENTS:
                return agent

        # Use default for stage
        return stage_defaults.get(stage, "general-purpose")

    def get_available_agents(self) -> List[str]:
        """
        Get list of available specialist agents.

        Returns:
            List of agent names
        """
        return self.AVAILABLE_AGENTS.copy()

    def validate_agent(self, agent_name: str) -> bool:
        """
        Validate that an agent name is available.

        Args:
            agent_name: Agent name to validate

        Returns:
            True if agent is available, False otherwise
        """
        return agent_name in self.AVAILABLE_AGENTS
