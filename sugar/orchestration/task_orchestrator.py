"""
Task Orchestrator - Manages complex tasks through staged execution

Provides intelligent decomposition and execution of complex features through:
- Research: Web search and codebase analysis
- Planning: Breaking down into subtasks with dependencies
- Implementation: Parallel execution with specialist agents
- Review: Code review and quality validation
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..agent.subagent_manager import SubAgentManager

logger = logging.getLogger(__name__)


class OrchestrationStage(Enum):
    """Orchestration workflow stages"""

    RESEARCH = "research"
    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    REVIEW = "review"


@dataclass
class StageResult:
    """Result from executing a single orchestration stage"""

    stage: OrchestrationStage
    success: bool
    output: str
    context_additions: Dict[str, Any] = field(default_factory=dict)
    subtasks_generated: List[Dict] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)
    execution_time: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "stage": self.stage.value,
            "success": self.success,
            "output": self.output,
            "context_additions": self.context_additions,
            "subtasks_generated": self.subtasks_generated,
            "files_modified": self.files_modified,
            "execution_time": self.execution_time,
            "error": self.error,
        }


@dataclass
class OrchestrationResult:
    """Result from full orchestration workflow"""

    task_id: str
    success: bool
    stages_completed: List[OrchestrationStage]
    subtasks: List[Dict]
    total_execution_time: float
    context_path: str
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "task_id": self.task_id,
            "success": self.success,
            "stages_completed": [s.value for s in self.stages_completed],
            "subtasks": self.subtasks,
            "total_execution_time": self.total_execution_time,
            "context_path": self.context_path,
            "error": self.error,
        }


class TaskOrchestrator:
    """
    Orchestrates complex tasks through staged execution.

    Manages the full workflow:
    1. Research - Gather context via web search and codebase analysis
    2. Planning - Create implementation plan and generate subtasks
    3. Implementation - Execute subtasks in parallel using specialist agents
    4. Review - Validate quality and completeness
    """

    def __init__(
        self,
        config: Dict[str, Any],
        work_queue=None,
        agent_executor=None,
    ):
        """
        Initialize the task orchestrator.

        Args:
            config: Full Sugar configuration dictionary
            work_queue: Optional WorkQueue instance for subtask management
            agent_executor: Optional agent executor for running tasks
        """
        self.config = config
        self.work_queue = work_queue
        self.agent_executor = agent_executor
        self.orchestration_config = self._load_orchestration_config()

        # Initialize agent router
        from .agent_router import AgentRouter

        self.router = AgentRouter(config)

        logger.debug("TaskOrchestrator initialized")

    def _load_orchestration_config(self) -> Dict[str, Any]:
        """
        Load orchestration configuration with defaults.

        Returns:
            Orchestration configuration dict
        """
        orchestration_config = self.config.get("orchestration", {})

        # Set defaults
        defaults = {
            "enabled": True,
            "auto_decompose": "auto",  # auto, explicit, disabled
            "detection": {
                "task_types": ["feature", "epic"],
                "keywords": [
                    "implement",
                    "build",
                    "create full",
                    "add complete",
                    "redesign",
                    "refactor entire",
                ],
                "min_complexity": "high",
            },
            "stages": {
                "research": {
                    "enabled": True,
                    "agent": "Explore",
                    "timeout": 600,
                    "actions": ["web_search", "codebase_analysis", "doc_gathering"],
                    "output_to_context": True,
                    "output_path": ".sugar/orchestration/{task_id}/research.md",
                },
                "planning": {
                    "enabled": True,
                    "agent": "Plan",
                    "timeout": 300,
                    "depends_on": ["research"],
                    "creates_subtasks": True,
                    "output_path": ".sugar/orchestration/{task_id}/plan.md",
                },
                "implementation": {
                    "parallel": True,
                    "max_concurrent": 3,
                    "timeout_per_task": 1800,
                    "agent_routing": {
                        "*ui*|*frontend*|*component*|*design*": "frontend-designer",
                        "*api*|*backend*|*endpoint*|*service*": "backend-developer",
                        "*test*|*spec*|*coverage*": "qa-engineer",
                        "*security*|*auth*|*permission*": "security-engineer",
                        "*devops*|*deploy*|*ci*|*docker*": "devops-engineer",
                        "*doc*|*readme*|*guide*": "general-purpose",
                        "default": "general-purpose",
                    },
                },
                "review": {
                    "enabled": True,
                    "depends_on": ["implementation"],
                    "agents": ["code-reviewer", "qa-engineer"],
                    "run_tests": True,
                    "require_passing": True,
                },
            },
        }

        # Merge with user config
        merged = {**defaults, **orchestration_config}

        # Ensure nested dicts are merged properly
        if "stages" in orchestration_config:
            for stage_name in ["research", "planning", "implementation", "review"]:
                if stage_name in orchestration_config.get("stages", {}):
                    merged["stages"][stage_name] = {
                        **defaults["stages"][stage_name],
                        **orchestration_config["stages"][stage_name],
                    }

        return merged

    async def should_orchestrate(self, task: Dict[str, Any]) -> bool:
        """
        Determine if a task should be orchestrated.

        Analyzes task type, keywords, and complexity to decide if the task
        needs the full orchestration workflow.

        Args:
            task: Task dictionary

        Returns:
            True if task should be orchestrated, False otherwise
        """
        if not self.orchestration_config.get("enabled", True):
            return False

        # Check explicit flag
        if task.get("orchestrate"):
            logger.info(
                f"Task {task.get('id', 'unknown')} has explicit orchestrate flag"
            )
            return True

        auto_mode = self.orchestration_config.get("auto_decompose", "auto")

        if auto_mode == "disabled":
            return False

        if auto_mode == "explicit":
            return task.get("orchestrate", False)

        # Auto detection mode
        detection = self.orchestration_config.get("detection", {})

        # Check task type
        task_type = task.get("type", "").lower()
        if task_type in detection.get("task_types", []):
            logger.info(f"Task type '{task_type}' triggers orchestration")
            return True

        # Check keywords
        title = task.get("title", "").lower()
        description = task.get("description", "").lower()
        search_text = f"{title} {description}"

        keywords = detection.get("keywords", [])
        for keyword in keywords:
            if keyword.lower() in search_text:
                logger.info(f"Keyword '{keyword}' triggers orchestration")
                return True

        # Check complexity (basic heuristic for now)
        # Could be enhanced with AI-based estimation
        complexity = self._estimate_complexity(task)
        min_complexity = detection.get("min_complexity", "high")

        complexity_levels = {"low": 1, "medium": 2, "high": 3}
        if complexity_levels.get(complexity, 0) >= complexity_levels.get(
            min_complexity, 3
        ):
            logger.info(f"Task complexity '{complexity}' triggers orchestration")
            return True

        return False

    def _estimate_complexity(self, task: Dict[str, Any]) -> str:
        """
        Estimate task complexity using heuristics.

        Args:
            task: Task dictionary

        Returns:
            Complexity level: 'low', 'medium', or 'high'
        """
        title = task.get("title", "")
        description = task.get("description", "")

        # High complexity indicators
        high_indicators = [
            "system",
            "architecture",
            "infrastructure",
            "migration",
            "redesign",
            "refactor entire",
            "complete rewrite",
            "full stack",
            "end-to-end",
            "multiple",
            "integrate",
            "build from scratch",
        ]

        # Medium complexity indicators
        medium_indicators = [
            "feature",
            "implement",
            "create",
            "add",
            "update",
            "enhance",
            "improve",
            "extend",
            "integrate",
            "connect",
        ]

        search_text = f"{title} {description}".lower()

        # Check for high complexity
        if any(indicator in search_text for indicator in high_indicators):
            return "high"

        # Check description length as a proxy for complexity
        if len(description) > 500:
            return "high"

        # Check for medium complexity
        if any(indicator in search_text for indicator in medium_indicators):
            if len(description) > 200:
                return "medium"

        return "low"

    async def orchestrate(self, task: Dict[str, Any]) -> OrchestrationResult:
        """
        Run full orchestration workflow for a task.

        Executes all enabled stages in sequence, accumulating context
        and generating subtasks for implementation.

        Args:
            task: Task dictionary to orchestrate

        Returns:
            OrchestrationResult with outcome and subtasks
        """
        task_id = task.get("id", "unknown")
        start_time = datetime.now(timezone.utc)

        logger.info(
            f"Starting orchestration for task {task_id}: {task.get('title', 'Untitled')}"
        )

        stages_completed = []
        context = self._initialize_context(task)
        subtasks = []
        error = None

        try:
            # Stage 1: Research
            if self.orchestration_config["stages"]["research"]["enabled"]:
                research_result = await self.run_stage(
                    OrchestrationStage.RESEARCH, task, context
                )
                stages_completed.append(OrchestrationStage.RESEARCH)

                if research_result.success:
                    context.update(research_result.context_additions)
                    await self._save_stage_output(task_id, research_result)
                else:
                    logger.warning(f"Research stage failed: {research_result.error}")
                    # Continue anyway - research is informational

            # Stage 2: Planning
            if self.orchestration_config["stages"]["planning"]["enabled"]:
                planning_result = await self.run_stage(
                    OrchestrationStage.PLANNING, task, context
                )
                stages_completed.append(OrchestrationStage.PLANNING)

                if planning_result.success:
                    context.update(planning_result.context_additions)
                    await self._save_stage_output(task_id, planning_result)

                    # Generate subtasks from plan
                    subtasks = await self.generate_subtasks(
                        planning_result.output, task
                    )
                    logger.info(f"Generated {len(subtasks)} subtasks from plan")
                else:
                    error = f"Planning stage failed: {planning_result.error}"
                    logger.error(error)
                    raise Exception(error)

            # Stage 3: Implementation
            if subtasks and self.orchestration_config["stages"]["implementation"].get(
                "parallel"
            ):
                impl_result = await self._run_implementation_stage(
                    subtasks, task, context
                )
                stages_completed.append(OrchestrationStage.IMPLEMENTATION)

                if not impl_result.success:
                    error = f"Implementation stage failed: {impl_result.error}"
                    logger.error(error)

            # Stage 4: Review
            if self.orchestration_config["stages"]["review"]["enabled"]:
                review_result = await self.run_stage(
                    OrchestrationStage.REVIEW, task, context
                )
                stages_completed.append(OrchestrationStage.REVIEW)

                if not review_result.success:
                    logger.warning(f"Review stage found issues: {review_result.error}")

            total_time = (datetime.now(timezone.utc) - start_time).total_seconds()

            return OrchestrationResult(
                task_id=task_id,
                success=error is None,
                stages_completed=stages_completed,
                subtasks=subtasks,
                total_execution_time=total_time,
                context_path=self._get_context_path(task_id),
                error=error,
            )

        except Exception as e:
            total_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.error(f"Orchestration failed for task {task_id}: {e}")

            return OrchestrationResult(
                task_id=task_id,
                success=False,
                stages_completed=stages_completed,
                subtasks=subtasks,
                total_execution_time=total_time,
                context_path=self._get_context_path(task_id),
                error=str(e),
            )

    async def run_stage(
        self, stage: OrchestrationStage, task: Dict[str, Any], context: Dict[str, Any]
    ) -> StageResult:
        """
        Execute a single orchestration stage.

        Args:
            stage: Stage to execute
            task: Original task dictionary
            context: Accumulated context from previous stages

        Returns:
            StageResult with stage outcome
        """
        start_time = datetime.now(timezone.utc)
        stage_config = self.orchestration_config["stages"][stage.value]

        logger.info(f"Running {stage.value} stage for task {task.get('id', 'unknown')}")

        try:
            # Get agent for this stage
            agent_name = self.router.get_stage_agent(stage)

            # Build stage-specific prompt
            prompt = self._build_stage_prompt(stage, task, context)

            # Execute using agent executor if available
            if self.agent_executor:
                result = await self._execute_with_agent(
                    agent_name, prompt, stage_config.get("timeout", 300)
                )

                execution_time = (
                    datetime.now(timezone.utc) - start_time
                ).total_seconds()

                return StageResult(
                    stage=stage,
                    success=result.get("success", False),
                    output=result.get("output", ""),
                    context_additions=self._extract_context_additions(stage, result),
                    subtasks_generated=[],  # Will be populated in planning stage
                    files_modified=result.get("files_changed", []),
                    execution_time=execution_time,
                    error=result.get("error"),
                )
            else:
                # Fallback: simulation mode
                logger.warning(
                    f"No agent executor available, simulating {stage.value} stage"
                )
                execution_time = (
                    datetime.now(timezone.utc) - start_time
                ).total_seconds()

                return StageResult(
                    stage=stage,
                    success=True,
                    output=f"Simulated {stage.value} stage output",
                    context_additions={},
                    subtasks_generated=[],
                    files_modified=[],
                    execution_time=execution_time,
                )

        except Exception as e:
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.error(f"Stage {stage.value} failed: {e}")

            return StageResult(
                stage=stage,
                success=False,
                output="",
                context_additions={},
                subtasks_generated=[],
                files_modified=[],
                execution_time=execution_time,
                error=str(e),
            )

    async def generate_subtasks(
        self, plan_output: str, task: Dict[str, Any]
    ) -> List[Dict]:
        """
        Parse planning output to generate subtasks with dependencies.

        Args:
            plan_output: Output from planning stage
            task: Original parent task

        Returns:
            List of subtask dictionaries
        """
        subtasks = []

        # Parse plan output for subtask definitions
        # Expected format:
        # ## Sub-tasks
        # 1. **Title** - Description (Agent: agent-name)
        #    Dependencies: task-1, task-2

        # Simple regex-based parsing
        subtask_pattern = (
            r"(\d+)\.\s+\*\*(.+?)\*\*\s*-?\s*(.+?)(?=\n(?:\d+\.|\Z)|Agent:)"
        )
        agent_pattern = r"Agent:\s*(\S+)"
        deps_pattern = r"Dependencies?:\s*(.+?)(?=\n|$)"

        matches = re.finditer(subtask_pattern, plan_output, re.DOTALL)

        for match in matches:
            task_num = match.group(1)
            title = match.group(2).strip()
            description = match.group(3).strip()

            # Extract agent if specified
            agent_match = re.search(agent_pattern, description)
            agent = agent_match.group(1) if agent_match else None

            # Extract dependencies if specified
            deps_match = re.search(deps_pattern, description)
            dependencies = []
            if deps_match:
                dep_text = deps_match.group(1)
                dependencies = [d.strip() for d in dep_text.split(",")]

            subtask = {
                "id": f"{task.get('id', 'unknown')}-sub-{task_num}",
                "parent_task_id": task.get("id"),
                "title": title,
                "description": description,
                "type": task.get("type", "feature"),
                "priority": task.get("priority", 3),
                "assigned_agent": agent,
                "blocked_by": dependencies,
                "status": "pending",
            }

            subtasks.append(subtask)
            logger.debug(f"Generated subtask {task_num}: {title}")

        # If parsing failed, create a single subtask
        if not subtasks:
            logger.warning(
                "No subtasks parsed from plan, creating single implementation task"
            )
            subtasks.append(
                {
                    "id": f"{task.get('id', 'unknown')}-sub-1",
                    "parent_task_id": task.get("id"),
                    "title": f"Implement: {task.get('title', 'Unknown')}",
                    "description": plan_output[:500],  # Use plan as context
                    "type": task.get("type", "feature"),
                    "priority": task.get("priority", 3),
                    "status": "pending",
                }
            )

        return subtasks

    async def _run_implementation_stage(
        self, subtasks: List[Dict], parent_task: Dict[str, Any], context: Dict[str, Any]
    ) -> StageResult:
        """
        Run implementation stage with parallel subtask execution.

        Args:
            subtasks: List of subtasks to execute
            parent_task: Parent task dictionary
            context: Accumulated context

        Returns:
            StageResult for implementation stage
        """
        start_time = datetime.now(timezone.utc)
        impl_config = self.orchestration_config["stages"]["implementation"]

        try:
            # Use SubAgentManager for parallel execution
            manager = SubAgentManager(
                parent_config=self._get_agent_config(),
                max_concurrent=impl_config.get("max_concurrent", 3),
                default_timeout=impl_config.get("timeout_per_task", 1800),
            )

            # Prepare tasks for parallel execution
            # For now, execute all without dependency resolution
            # TODO: Add dependency resolution for sequential execution
            tasks_to_execute = []
            for subtask in subtasks:
                # Route subtask to appropriate agent
                agent = self.router.route(subtask)

                task_data = {
                    "task_id": subtask["id"],
                    "prompt": self._build_subtask_prompt(subtask, context),
                    "context": json.dumps(context),
                    "timeout": impl_config.get("timeout_per_task", 1800),
                }

                tasks_to_execute.append(task_data)

            # Execute in parallel
            results = await manager.spawn_parallel(tasks_to_execute)

            # Aggregate results
            all_files = []
            all_success = True
            errors = []

            for result in results:
                if not result.success:
                    all_success = False
                    errors.append(f"{result.task_id}: {result.error}")
                all_files.extend(result.files_modified)

            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()

            return StageResult(
                stage=OrchestrationStage.IMPLEMENTATION,
                success=all_success,
                output=f"Completed {len(results)} subtasks",
                context_additions={"subtask_results": [r.to_dict() for r in results]},
                files_modified=all_files,
                execution_time=execution_time,
                error="; ".join(errors) if errors else None,
            )

        except Exception as e:
            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.error(f"Implementation stage failed: {e}")

            return StageResult(
                stage=OrchestrationStage.IMPLEMENTATION,
                success=False,
                output="",
                context_additions={},
                files_modified=[],
                execution_time=execution_time,
                error=str(e),
            )

    def _initialize_context(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Initialize context for orchestration.

        Args:
            task: Task dictionary

        Returns:
            Initial context dictionary
        """
        return {
            "task_id": task.get("id", "unknown"),
            "task_title": task.get("title", ""),
            "task_description": task.get("description", ""),
            "task_type": task.get("type", ""),
            "original_task": task.copy(),
        }

    def _build_stage_prompt(
        self, stage: OrchestrationStage, task: Dict[str, Any], context: Dict[str, Any]
    ) -> str:
        """
        Build prompt for a specific stage.

        Args:
            stage: Orchestration stage
            task: Task dictionary
            context: Current context

        Returns:
            Prompt string
        """
        base_prompt = f"""# Orchestration Stage: {stage.value.upper()}

## Task
**Title:** {task.get('title', 'Untitled')}
**Type:** {task.get('type', 'feature')}
**Description:**
{task.get('description', 'No description provided')}

"""

        if stage == OrchestrationStage.RESEARCH:
            return base_prompt + """
## Your Role
You are conducting research for this task. Your goals:
1. Search for relevant best practices and documentation
2. Analyze the existing codebase for patterns and related code
3. Identify technical requirements and constraints
4. Gather context that will help with implementation planning

## Output
Provide a research summary covering:
- Relevant best practices and patterns
- Existing codebase analysis
- Technical requirements
- Recommendations for implementation
"""

        elif stage == OrchestrationStage.PLANNING:
            research_context = ""
            if context.get("research_output"):
                research_context = (
                    f"\n## Research Findings\n{context['research_output']}\n"
                )

            return base_prompt + research_context + """
## Your Role
You are creating an implementation plan for this task. Your goals:
1. Break down the task into manageable subtasks
2. Identify which specialist agent is best for each subtask
3. Specify dependencies between subtasks
4. Create a clear execution plan

## Output Format
Create a plan with subtasks in this format:

## Sub-tasks

1. **Subtask Title** - Brief description
   Agent: agent-name
   Dependencies: (optional) task-1, task-2

2. **Next Subtask** - Description
   Agent: agent-name

...

## Dependencies
Explain the order of execution and why.
"""

        elif stage == OrchestrationStage.REVIEW:
            impl_results = context.get("subtask_results", [])
            files_modified = context.get("files_modified", [])

            return base_prompt + f"""
## Implementation Complete
The following subtasks have been completed:
{json.dumps(impl_results, indent=2)}

Files modified:
{json.dumps(files_modified, indent=2)}

## Your Role
You are reviewing the completed implementation. Your goals:
1. Review all code changes for quality and correctness
2. Verify the implementation meets the original requirements
3. Check for any issues or improvements needed
4. Validate that tests pass (if applicable)

## Output
Provide a review covering:
- Code quality assessment
- Requirements verification
- Issues found (if any)
- Recommendations for improvement
- Overall assessment (pass/fail)
"""

        else:
            return base_prompt

    def _build_subtask_prompt(
        self, subtask: Dict[str, Any], context: Dict[str, Any]
    ) -> str:
        """
        Build prompt for a subtask.

        Args:
            subtask: Subtask dictionary
            context: Orchestration context

        Returns:
            Prompt string
        """
        return f"""# Subtask: {subtask.get('title', 'Untitled')}

## Parent Task
{context.get('task_title', 'Unknown')}

## Your Task
{subtask.get('description', 'No description')}

## Context
This is part of a larger orchestrated task. Focus on completing your specific subtask.

## Instructions
1. Implement the required functionality
2. Follow existing code patterns
3. Test your changes if applicable
4. Provide a clear summary of what was done
"""

    def _get_context_path(self, task_id: str) -> str:
        """
        Get path to orchestration context directory.

        Args:
            task_id: Task ID

        Returns:
            Path string
        """
        return f".sugar/orchestration/{task_id}/"

    async def _save_stage_output(self, task_id: str, result: StageResult) -> None:
        """
        Save stage output to context directory.

        Args:
            task_id: Task ID
            result: Stage result to save
        """
        try:
            # Get output path from config
            stage_config = self.orchestration_config["stages"][result.stage.value]
            output_path_template = stage_config.get("output_path", "")

            if not output_path_template:
                return

            # Substitute task_id
            output_path = output_path_template.replace("{task_id}", task_id)

            # Create directory
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)

            # Write output
            path.write_text(result.output)
            logger.debug(f"Saved {result.stage.value} output to {output_path}")

        except Exception as e:
            logger.warning(f"Failed to save stage output: {e}")

    def _extract_context_additions(
        self, stage: OrchestrationStage, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Extract context additions from stage result.

        Args:
            stage: Stage that was executed
            result: Result dictionary from agent execution

        Returns:
            Context additions dictionary
        """
        additions = {
            f"{stage.value}_output": result.get("output", ""),
            f"{stage.value}_files": result.get("files_changed", []),
        }

        return additions

    async def _execute_with_agent(
        self, agent_name: str, prompt: str, timeout: int
    ) -> Dict[str, Any]:
        """
        Execute a task using the agent executor.

        Args:
            agent_name: Name of agent to use
            prompt: Task prompt
            timeout: Timeout in seconds

        Returns:
            Execution result dictionary
        """
        # Build a minimal work item for agent execution
        work_item = {
            "id": f"orchestration-{datetime.now(timezone.utc).timestamp()}",
            "title": f"Orchestration stage",
            "description": prompt,
            "type": "orchestration",
            "priority": 5,
        }

        # Execute using agent executor
        result = await self.agent_executor.execute_work_item(work_item)

        return result

    def _get_agent_config(self):
        """
        Get agent configuration for SubAgentManager.

        Returns:
            Agent configuration object
        """
        from ..agent.base import SugarAgentConfig

        # Extract relevant config
        return SugarAgentConfig(
            model=self.config.get("model", "claude-sonnet-4-20250514"),
            max_tokens=self.config.get("max_tokens", 8192),
            permission_mode=self.config.get("permission_mode", "acceptEdits"),
            quality_gates_enabled=self.config.get("quality_gates", {}).get(
                "enabled", True
            ),
            working_directory=self.config.get("working_directory"),
        )
