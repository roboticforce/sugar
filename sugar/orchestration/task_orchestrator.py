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
        and generating subtasks for implementation. The parent task's
        ``stage`` and ``context_path`` are persisted to the work queue so
        ``sugar orchestrate`` / ``sugar context`` show live progress.

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

        # Stages the user asked to skip (set via `sugar add --skip-stages`)
        task_context = task.get("context") or {}
        if not isinstance(task_context, dict):
            task_context = {}
        skip_stages = task_context.get("skip_stages", []) or []

        # Persist the context_path (a file) and initial stage on the parent
        context_path = self._get_context_path(task_id)
        if self.work_queue:
            await self.work_queue.update_work(task_id, {"context_path": context_path})
            await self.work_queue.update_orchestration_stage(task_id, "research")

        stages_completed = []
        context = self._initialize_context(task)
        subtasks = []
        error = None

        try:
            # Stage 1: Research
            if (
                self.orchestration_config["stages"]["research"]["enabled"]
                and "research" not in skip_stages
            ):
                if self.work_queue:
                    await self.work_queue.update_orchestration_stage(
                        task_id, OrchestrationStage.RESEARCH.value
                    )
                research_result = await self.run_stage(
                    OrchestrationStage.RESEARCH, task, context
                )
                stages_completed.append(OrchestrationStage.RESEARCH)

                if research_result.success:
                    context.update(research_result.context_additions)
                    await self._save_stage_output(task_id, research_result)
                    await self._append_to_context_file(
                        task_id, OrchestrationStage.RESEARCH, research_result
                    )
                else:
                    logger.warning(f"Research stage failed: {research_result.error}")
                    # Continue anyway - research is informational

            # Stage 2: Planning
            if (
                self.orchestration_config["stages"]["planning"]["enabled"]
                and "planning" not in skip_stages
            ):
                if self.work_queue:
                    await self.work_queue.update_orchestration_stage(
                        task_id, OrchestrationStage.PLANNING.value
                    )
                planning_result = await self.run_stage(
                    OrchestrationStage.PLANNING, task, context
                )
                stages_completed.append(OrchestrationStage.PLANNING)

                if planning_result.success:
                    context.update(planning_result.context_additions)
                    await self._save_stage_output(task_id, planning_result)
                    await self._append_to_context_file(
                        task_id, OrchestrationStage.PLANNING, planning_result
                    )

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
            impl_config = self.orchestration_config["stages"]["implementation"]
            if (
                subtasks
                and impl_config.get("enabled", True)
                and "implementation" not in skip_stages
            ):
                if self.work_queue:
                    await self.work_queue.update_orchestration_stage(
                        task_id, OrchestrationStage.IMPLEMENTATION.value
                    )
                impl_result = await self._run_implementation_stage(
                    subtasks, task, context
                )
                stages_completed.append(OrchestrationStage.IMPLEMENTATION)
                context.update(impl_result.context_additions)
                await self._append_to_context_file(
                    task_id, OrchestrationStage.IMPLEMENTATION, impl_result
                )

                if not impl_result.success:
                    error = f"Implementation stage failed: {impl_result.error}"
                    logger.error(error)

            # Stage 4: Review
            review_config = self.orchestration_config["stages"]["review"]
            if review_config["enabled"] and "review" not in skip_stages:
                if self.work_queue:
                    await self.work_queue.update_orchestration_stage(
                        task_id, OrchestrationStage.REVIEW.value
                    )
                review_result = await self.run_stage(
                    OrchestrationStage.REVIEW, task, context
                )
                stages_completed.append(OrchestrationStage.REVIEW)
                await self._append_to_context_file(
                    task_id, OrchestrationStage.REVIEW, review_result
                )

                # Run the project test suite if configured
                if review_config.get("run_tests"):
                    tests_pass, test_detail = await self._run_project_tests()
                    if not tests_pass:
                        review_result.output = (
                            review_result.output or ""
                        ) + f"\n\n## Test Results (FAILED)\n{test_detail}"
                        if review_config.get("require_passing"):
                            review_result.success = False
                            review_result.error = f"Review tests failed:\n{test_detail}"
                            logger.warning("Review stage failed: tests did not pass")
                        else:
                            logger.warning(
                                "Review tests failed but require_passing is false"
                            )

                if not review_result.success:
                    if review_config.get("require_passing") and review_result.error:
                        error = f"Review stage failed: {review_result.error}"
                        logger.error(error)
                    else:
                        logger.warning(
                            f"Review stage found issues: {review_result.error}"
                        )

            total_time = (datetime.now(timezone.utc) - start_time).total_seconds()

            return OrchestrationResult(
                task_id=task_id,
                success=error is None,
                stages_completed=stages_completed,
                subtasks=subtasks,
                total_execution_time=total_time,
                context_path=context_path,
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
                context_path=context_path,
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

        # Simple regex-based parsing. The description group extends until the
        # next numbered subtask (or end of input) so the Agent: and Dependencies:
        # lines that belong to this subtask are captured within it.
        subtask_pattern = r"(\d+)\.\s+\*\*(.+?)\*\*\s*-?\s*(.+?)(?=\n\d+\.\s+\*\*|\Z)"
        agent_pattern = r"Agent:\s*(\S+)"
        deps_pattern = r"Dependencies?:\s*(.+?)(?=\n|$)"

        matches = re.finditer(subtask_pattern, plan_output, re.DOTALL)

        parent_id = task.get("id", "unknown")

        # First pass: parse raw subtask rows and capture their numbers so
        # dependency references can be normalized to placeholder ids.
        raw_rows = []
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

            raw_rows.append(
                {
                    "num": task_num,
                    "title": title,
                    "description": description,
                    "agent": agent,
                    "deps": dependencies,
                }
            )

        # Map a subtask number (and common reference forms) to its placeholder
        # id so blocked_by can be resolved by _run_implementation_stage's
        # placeholder -> real id remap.
        num_to_placeholder = {
            row["num"]: f"{parent_id}-sub-{row['num']}" for row in raw_rows
        }

        # Sentinel values a planning agent may write to mean "no dependency".
        # If stored verbatim these would block the subtask forever (the wave
        # executor waits for a task with that id to complete, and it never
        # will), so they are dropped entirely.
        _NO_DEP_SENTINELS = {
            "",
            "none",
            "n/a",
            "na",
            "nil",
            "null",
            "-",
            "0",
            "no",
            "none specified",
            "no dependencies",
            "none required",
        }

        def _normalize_dep(dep: str) -> Optional[str]:
            dep = dep.strip().lower()
            if dep in _NO_DEP_SENTINELS:
                return None
            if dep in num_to_placeholder:
                return num_to_placeholder[dep]
            # "task-1" / "sub-1" / "{parent}-sub-1" style references -> try the
            # trailing number against the parsed subtasks.
            m = re.search(r"(\d+)$", dep)
            if m and m.group(1) in num_to_placeholder:
                return num_to_placeholder[m.group(1)]
            return dep

        for row in raw_rows:
            subtask = {
                "id": f"{parent_id}-sub-{row['num']}",
                "parent_task_id": parent_id,
                "title": row["title"],
                "description": row["description"],
                "type": task.get("type", "feature"),
                "priority": task.get("priority", 3),
                "assigned_agent": row["agent"],
                "blocked_by": [
                    d for d in (_normalize_dep(x) for x in row["deps"]) if d
                ],
                "status": "pending",
            }
            subtasks.append(subtask)
            logger.debug(f"Generated subtask {row['num']}: {row['title']}")

        # If parsing failed, create a single subtask
        if not subtasks:
            logger.warning(
                "No subtasks parsed from plan, creating single implementation task"
            )
            subtasks.append(
                {
                    "id": f"{parent_id}-sub-1",
                    "parent_task_id": parent_id,
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
        Run implementation stage executing subtasks in dependency order.

        Subtasks are persisted to the work queue with ``status='hold'`` so the
        main loop's ``get_next_work`` (which only claims ``pending`` items) never
        picks them up - the orchestrator executes them itself. Subtasks run in
        dependency waves: each wave executes all subtasks whose blockers are
        complete, sequentially (the cached SugarAgent mutates shared instance
        state during execution, so intra-wave parallelism is unsafe without
        per-subtask agent isolation).

        Args:
            subtasks: List of subtasks to execute
            parent_task: Parent task dictionary
            context: Accumulated context

        Returns:
            StageResult for implementation stage
        """
        start_time = datetime.now(timezone.utc)
        parent_id = parent_task.get("id")
        context_path = self._get_context_path(parent_id)

        try:
            if not self.work_queue:
                raise RuntimeError(
                    "A work_queue is required to persist and execute subtasks"
                )
            if not self.agent_executor:
                raise RuntimeError("An agent_executor is required to execute subtasks")

            # 1. Persist subtasks as hold-status rows.
            #    generate_subtasks emits placeholder ids like "{parent}-sub-{n}";
            #    map those to the real DB ids returned by add_work so blocked_by
            #    can be remapped before the first wave (else nothing unblocks).
            placeholder_to_real: Dict[str, str] = {}
            for subtask in subtasks:
                agent_name = subtask.get("assigned_agent") or self.router.route(subtask)
                subtask["assigned_agent"] = agent_name

                subtask_work = {
                    "type": subtask.get("type", parent_task.get("type", "feature")),
                    "title": subtask.get("title", "Untitled subtask"),
                    "description": self._build_subtask_prompt(subtask, context),
                    "priority": subtask.get("priority", parent_task.get("priority", 3)),
                    "status": "hold",
                    "orchestrate": False,
                    "parent_task_id": parent_id,
                    "stage": "implementation",
                    "blocked_by": [],
                    "context_path": context_path,
                    "assigned_agent": agent_name,
                    "context": {
                        "orchestration_agent": agent_name,
                        "parent_task_id": parent_id,
                        "subtask_placeholder_id": subtask.get("id"),
                    },
                }
                real_id = await self.work_queue.add_work(subtask_work)
                placeholder_to_real[subtask["id"]] = real_id
                subtask["_real_id"] = real_id

            # 2. Remap blocked_by placeholder ids -> real DB ids now that all
            #    rows exist. Must happen before wave 1 or get_ready_subtasks
            #    never resolves a blocker.
            for subtask in subtasks:
                real_blocked_by = [
                    placeholder_to_real.get(dep, dep)
                    for dep in (subtask.get("blocked_by") or [])
                ]
                if real_blocked_by:
                    await self.work_queue.update_work(
                        subtask["_real_id"], {"blocked_by": real_blocked_by}
                    )

            # 3. Wave loop: execute ready subtasks sequentially until none remain.
            all_files: List[str] = []
            all_success = True
            errors: List[str] = []
            subtask_results: List[Dict[str, Any]] = []
            executed_ids: set = set()

            while True:
                ready = await self.work_queue.get_ready_subtasks(parent_id)
                # get_ready_subtasks ignores status, so filter to unexecuted
                # subtasks still waiting to run.
                ready = [
                    s
                    for s in ready
                    if s["id"] not in executed_ids
                    and s.get("status") in ("hold", "pending")
                ]
                if not ready:
                    break

                for subtask_row in ready:
                    sid = subtask_row["id"]
                    executed_ids.add(sid)
                    await self.work_queue.update_work(sid, {"status": "active"})

                    agent_name = subtask_row.get("assigned_agent") or "general-purpose"
                    # Prime the role header into the description for routing.
                    desc = subtask_row.get("description", "") or ""
                    if agent_name and agent_name != "general-purpose":
                        if not desc.startswith("## Acting as:"):
                            desc = f"## Acting as: {agent_name}\n\n{desc}"
                        subtask_row["description"] = desc
                    row_ctx = subtask_row.get("context") or {}
                    if isinstance(row_ctx, dict):
                        row_ctx["orchestration_agent"] = agent_name
                    subtask_row["context"] = row_ctx

                    try:
                        result = await self.agent_executor.execute_work(subtask_row)
                    except Exception as e:  # noqa: BLE001
                        result = {"success": False, "error": str(e)}

                    if result.get("success", False):
                        await self.work_queue.complete_work(sid, result)
                        subtask_results.append(
                            {
                                "subtask_id": sid,
                                "title": subtask_row.get("title"),
                                "agent": agent_name,
                                "success": True,
                                "output": result.get("output", ""),
                                "files_changed": result.get("files_changed", []),
                            }
                        )
                        all_files.extend(result.get("files_changed", []) or [])
                    else:
                        all_success = False
                        err = result.get("error") or "Subtask execution failed"
                        errors.append(f"{subtask_row.get('title', sid)}: {err}")
                        # max_retries=0 -> permanent failure (attempts starts at
                        # 0 and is never incremented for hold subtasks). Avoids
                        # recycling the subtask back to 'pending' where the main
                        # loop could claim it.
                        await self.work_queue.fail_work(sid, err, max_retries=0)
                        subtask_results.append(
                            {
                                "subtask_id": sid,
                                "title": subtask_row.get("title"),
                                "agent": agent_name,
                                "success": False,
                                "error": err,
                            }
                        )

            # 4. Orphan sweep. get_ready_subtasks only unblocks a dependent
            # when its blocker is 'completed' - a 'failed' blocker leaves
            # dependents stranded in 'hold' forever. Cascade-fail any remaining
            # non-terminal subtask whose direct blocker failed (to a fixed
            # point), then fail any still-stranded subtasks as a dependency
            # cycle so they don't ghost in `sugar orchestrate <id>`.
            stranded = await self._cascade_fail_orphans(
                parent_id, executed_ids, subtask_results, errors
            )
            if stranded:
                all_success = False

            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()

            return StageResult(
                stage=OrchestrationStage.IMPLEMENTATION,
                success=all_success,
                output=f"Completed {len(subtask_results)} subtasks",
                context_additions={"subtask_results": subtask_results},
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

    async def _cascade_fail_orphans(
        self,
        parent_id: str,
        executed_ids: set,
        subtask_results: List[Dict[str, Any]],
        errors: List[str],
    ) -> bool:
        """Fail subtasks stranded by a failed blocker or a dependency cycle.

        After the wave loop, any subtask still in a non-terminal status
        ('hold'/'pending') is either blocked by a failed subtask or part of a
        dependency cycle. Cascade-fail blocked-by-failed subtasks to a fixed
        point, then fail any still-stranded subtasks as a cycle. Returns True
        if any subtask was stranded (indicating the stage did not fully
        succeed).
        """
        stranded_any = False

        # Fixed-point cascade: a subtask whose direct blocker failed becomes
        # failed, which may in turn strand its own dependents.
        while True:
            remaining = await self.work_queue.get_subtasks(parent_id)
            orphans = [
                s
                for s in remaining
                if s.get("status") in ("hold", "pending")
                and s["id"] not in executed_ids
            ]
            if not orphans:
                break

            progressed = False
            for st in orphans:
                blockers = st.get("blocked_by", []) or []
                if not blockers:
                    # No blockers but still unexecuted and not ready - shouldn't
                    # happen post-wave, but fail defensively rather than ghost.
                    continue
                # Look up blocker statuses; bail on the first failed blocker.
                failed_blocker = None
                for bid in blockers:
                    blocker = await self.work_queue.get_work_by_id(bid)
                    if blocker and blocker.get("status") == "failed":
                        failed_blocker = bid
                        break
                if failed_blocker:
                    reason = f"skipped: blocked by failed subtask {failed_blocker}"
                    await self.work_queue.fail_work(st["id"], reason, max_retries=0)
                    errors.append(f"{st.get('title', st['id'])}: {reason}")
                    subtask_results.append(
                        {
                            "subtask_id": st["id"],
                            "title": st.get("title"),
                            "agent": st.get("assigned_agent") or "general-purpose",
                            "success": False,
                            "error": reason,
                        }
                    )
                    stranded_any = True
                    progressed = True

            if not progressed:
                break

        # Anything still stranded is a dependency cycle (no failed blocker to
        # blame, yet never becomes ready). Fail it so it doesn't ghost.
        remaining = await self.work_queue.get_subtasks(parent_id)
        cycle_orphans = [
            s
            for s in remaining
            if s.get("status") in ("hold", "pending") and s["id"] not in executed_ids
        ]
        for st in cycle_orphans:
            reason = "skipped: dependency cycle detected"
            await self.work_queue.fail_work(st["id"], reason, max_retries=0)
            errors.append(f"{st.get('title', st['id'])}: {reason}")
            subtask_results.append(
                {
                    "subtask_id": st["id"],
                    "title": st.get("title"),
                    "agent": st.get("assigned_agent") or "general-purpose",
                    "success": False,
                    "error": reason,
                }
            )
            stranded_any = True

        return stranded_any

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
        Get path to the orchestration context file for a task.

        The ``sugar context`` command opens this path as a file, so it must be
        a file (context.md), not a directory.

        Args:
            task_id: Task ID

        Returns:
            Path string to context.md
        """
        return f".sugar/orchestration/{task_id}/context.md"

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

    async def _append_to_context_file(
        self, task_id: str, stage: OrchestrationStage, result: StageResult
    ) -> None:
        """
        Append a stage's output to the accumulated context.md file.

        This is what ``sugar context <id>`` displays, so each stage's output
        is appended in order to build up the full orchestration context.

        Args:
            task_id: Task ID
            stage: Stage that produced the output
            result: Stage result to append
        """
        try:
            context_path = Path(self._get_context_path(task_id))
            context_path.parent.mkdir(parents=True, exist_ok=True)

            header = f"\n\n## Stage: {stage.value}\n\n"
            body = result.output or "(no output)"
            if result.error:
                body += f"\n\n**Error:** {result.error}"

            # Append (create if missing)
            mode = "a" if context_path.exists() else "w"
            with open(context_path, mode) as f:
                f.write(header + body)

            logger.debug(f"Appended {stage.value} output to {context_path}")
        except Exception as e:
            logger.warning(f"Failed to append to context file: {e}")

    async def _run_project_tests(self) -> tuple:
        """
        Run the project test suite via pytest and capture pass/fail.

        Returns:
            Tuple of (passed: bool, detail: str)
        """
        try:
            process = await asyncio.create_subprocess_exec(
                "pytest",
                "-q",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""

            if process.returncode == 0:
                return True, stdout_text.strip() or "Tests passed"
            else:
                detail = (stdout_text + "\n" + stderr_text).strip()
                return False, detail or "Tests failed"
        except FileNotFoundError:
            # pytest not installed - treat as a pass to avoid blocking on
            # projects without a pytest suite
            logger.warning("pytest not found, skipping test run")
            return True, "pytest not installed - skipped"
        except Exception as e:
            logger.warning(f"Failed to run tests: {e}")
            return False, str(e)

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

        The agent name primes the prompt with a role header and is recorded in
        the work item context for visibility. Specialist names (e.g.
        backend-developer) are not built-in Claude Code subagent types, so they
        influence behaviour through the prompt rather than subagent dispatch.

        Args:
            agent_name: Name of agent to use (role primer)
            prompt: Task prompt
            timeout: Timeout in seconds

        Returns:
            Execution result dictionary
        """
        # Prime the prompt with a role header when a specialist agent is routed
        if agent_name and agent_name != "general-purpose":
            primed_prompt = f"## Acting as: {agent_name}\n\n{prompt}"
        else:
            primed_prompt = prompt

        # Build a minimal work item for agent execution
        work_item = {
            "id": f"orchestration-{datetime.now(timezone.utc).timestamp()}",
            "title": f"Orchestration stage ({agent_name or 'general-purpose'})",
            "description": primed_prompt,
            "type": "orchestration",
            "priority": 5,
            "context": ({"orchestration_agent": agent_name} if agent_name else {}),
        }

        # Execute using agent executor (AgentSDKExecutor exposes execute_work)
        result = await self.agent_executor.execute_work(work_item)

        return result
