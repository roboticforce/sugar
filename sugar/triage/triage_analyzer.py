"""
Task Triage Analyzer

Main orchestrator for intelligent task triage. Combines complexity analysis
and codebase capability detection to determine optimal execution strategy.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .capability_scanner import CodebaseCapabilities, CodebaseCapabilityScanner
from .complexity_analyzer import (
    ComplexityAnalyzer,
    ComplexityLevel,
    ExecutionRecommendation,
    TaskAnalysis,
)

logger = logging.getLogger(__name__)


@dataclass
class TriageResult:
    """Complete triage result for a task"""

    # Task analysis (complexity, recommendation)
    analysis: TaskAnalysis

    # Whether Ralph mode should be used
    use_ralph_mode: bool

    # Auto-generated completion criteria
    completion_criteria: List[str] = field(default_factory=list)

    # Suggested completion promise for Ralph mode
    completion_promise: str = "TASK_COMPLETE"

    # Recommended max iterations
    max_iterations: int = 10

    # Quality gates to apply
    quality_gates: List[str] = field(default_factory=list)

    # Context enrichments to add to task
    context_enrichments: Dict[str, Any] = field(default_factory=dict)

    # Confidence in the triage decision (0.0-1.0)
    confidence: float = 0.5

    # Reasoning for the triage decision
    reasoning: List[str] = field(default_factory=list)

    # Triage timestamp
    triaged_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "analysis": self.analysis.to_dict(),
            "use_ralph_mode": self.use_ralph_mode,
            "completion_criteria": self.completion_criteria,
            "completion_promise": self.completion_promise,
            "max_iterations": self.max_iterations,
            "quality_gates": self.quality_gates,
            "context_enrichments": self.context_enrichments,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "triaged_at": self.triaged_at.isoformat() if self.triaged_at else None,
        }


class TaskTriageAnalyzer:
    """
    Intelligent task triage analyzer.

    Analyzes tasks to determine:
    1. Whether Ralph mode (iterative execution) should be used
    2. Appropriate completion criteria based on task type and codebase
    3. Quality gates to apply based on available tools
    4. Context enrichments for better task execution

    This analyzer integrates:
    - Complexity analysis (task text, type, scope)
    - Codebase capabilities (test frameworks, linters, etc.)
    """

    def __init__(
        self,
        root_path: Optional[str] = None,
        strict_mode: bool = False,
        auto_detect_capabilities: bool = True,
    ):
        """
        Initialize the triage analyzer.

        Args:
            root_path: Project root directory for capability scanning
            strict_mode: Be more conservative about Ralph mode recommendations
            auto_detect_capabilities: Automatically detect codebase capabilities
        """
        self.root_path = root_path
        self.strict_mode = strict_mode
        self.auto_detect_capabilities = auto_detect_capabilities

        # Initialize analyzers
        self.complexity_analyzer = ComplexityAnalyzer(strict_mode=strict_mode)
        self.capability_scanner = CodebaseCapabilityScanner(root_path=root_path)

        # Cached capabilities (scanned once per session)
        self._capabilities: Optional[CodebaseCapabilities] = None
        self._capabilities_scanned = False

    async def triage(self, work_item: Dict[str, Any]) -> TriageResult:
        """
        Perform intelligent triage on a work item.

        Args:
            work_item: Work item with type, title, description, etc.

        Returns:
            TriageResult with execution recommendations
        """
        try:
            # Ensure capabilities are scanned (cached for session)
            if self.auto_detect_capabilities and not self._capabilities_scanned:
                await self._scan_capabilities()

            # Analyze task complexity
            analysis = self.complexity_analyzer.analyze(work_item)

            # Build triage result
            result = self._build_triage_result(work_item, analysis)

            logger.debug(
                f"Triage complete for '{work_item.get('title', 'unknown')}': "
                f"ralph={result.use_ralph_mode}, confidence={result.confidence:.2f}"
            )

            return result

        except Exception as e:
            logger.error(f"Triage failed: {e}")
            # Return safe default
            return self._default_triage_result(work_item)

    async def _scan_capabilities(self):
        """Scan codebase capabilities (cached for session)."""
        try:
            self._capabilities = await self.capability_scanner.scan()
            self._capabilities_scanned = True
            logger.debug(f"Scanned capabilities: {self._capabilities.language}")
        except Exception as e:
            logger.warning(f"Failed to scan capabilities: {e}")
            self._capabilities = None
            self._capabilities_scanned = True  # Mark as scanned to avoid retries

    def _build_triage_result(
        self,
        work_item: Dict[str, Any],
        analysis: TaskAnalysis,
    ) -> TriageResult:
        """Build the triage result from analysis and capabilities."""
        reasoning = list(analysis.recommendation.reasoning)

        # Determine Ralph mode
        use_ralph = analysis.recommendation.use_ralph_mode

        # Check for explicit user override in context
        context = work_item.get("context", {})
        if context.get("ralph_enabled") is not None:
            use_ralph = context["ralph_enabled"]
            reasoning.append(f"User override: ralph_enabled={use_ralph}")

        # Generate completion criteria based on task type and capabilities
        completion_criteria = self._generate_completion_criteria(
            work_item, analysis, use_ralph
        )

        # Generate completion promise
        completion_promise = self._generate_completion_promise(work_item)

        # Determine max iterations
        max_iterations = self._determine_max_iterations(analysis, work_item)

        # Generate quality gates based on capabilities
        quality_gates = self._generate_quality_gates(analysis)

        # Build context enrichments
        context_enrichments = self._build_context_enrichments(
            analysis, use_ralph, completion_criteria, completion_promise
        )

        # Calculate overall confidence
        confidence = self._calculate_confidence(analysis, completion_criteria)

        return TriageResult(
            analysis=analysis,
            use_ralph_mode=use_ralph,
            completion_criteria=completion_criteria,
            completion_promise=completion_promise,
            max_iterations=max_iterations,
            quality_gates=quality_gates,
            context_enrichments=context_enrichments,
            confidence=confidence,
            reasoning=reasoning,
            triaged_at=datetime.now(timezone.utc),
        )

    def _generate_completion_criteria(
        self,
        work_item: Dict[str, Any],
        analysis: TaskAnalysis,
        use_ralph: bool,
    ) -> List[str]:
        """Generate completion criteria based on task and capabilities."""
        criteria = list(analysis.recommendation.completion_criteria)

        # Add capability-based criteria
        if self._capabilities:
            if self._capabilities.has_tests:
                if "test" not in str(criteria).lower():
                    criteria.append(f"Tests pass: {self._capabilities.test_command}")

            if self._capabilities.has_linting and analysis.complexity.level.value >= 3:
                criteria.append(f"Linting passes: {self._capabilities.lint_command}")

            if (
                self._capabilities.has_type_checking
                and analysis.complexity.level.value >= 3
            ):
                criteria.append(
                    f"Type checking passes: {self._capabilities.type_check_command}"
                )

        # Add Ralph promise if needed
        if use_ralph:
            promise = self._generate_completion_promise(work_item)
            promise_criteria = f"Output: <promise>{promise}</promise>"
            if promise_criteria not in criteria:
                # Remove any existing promise criteria and add new one
                criteria = [c for c in criteria if "<promise>" not in c]
                criteria.append(promise_criteria)

        return criteria

    def _generate_completion_promise(self, work_item: Dict[str, Any]) -> str:
        """Generate appropriate completion promise based on task type."""
        task_type = work_item.get("type", "feature")
        title = work_item.get("title", "")

        # Check if user provided explicit promise
        context = work_item.get("context", {})
        if context.get("completion_promise"):
            return context["completion_promise"]

        # Generate based on task type
        promises = {
            "bug_fix": "BUG_FIXED",
            "feature": "FEATURE_COMPLETE",
            "refactor": "REFACTOR_COMPLETE",
            "test": "TESTS_COMPLETE",
            "documentation": "DOCS_COMPLETE",
            "perf": "OPTIMIZATION_COMPLETE",
            "security": "SECURITY_FIXED",
            "chore": "CHORE_COMPLETE",
            "ci": "CI_CONFIGURED",
        }

        return promises.get(task_type, "TASK_COMPLETE")

    def _determine_max_iterations(
        self,
        analysis: TaskAnalysis,
        work_item: Dict[str, Any],
    ) -> int:
        """Determine appropriate max iterations."""
        # Check user override
        context = work_item.get("context", {})
        if context.get("max_iterations"):
            return context["max_iterations"]

        # Use analysis recommendation
        max_iter = analysis.recommendation.max_iterations

        # Adjust based on priority (high priority = fewer iterations for faster delivery)
        priority = work_item.get("priority", 3)
        if priority >= 4:
            max_iter = min(max_iter, 8)

        return max_iter

    def _generate_quality_gates(self, analysis: TaskAnalysis) -> List[str]:
        """Generate quality gates based on capabilities and complexity."""
        gates = list(analysis.recommendation.quality_gates)

        # Add capability-based gates
        if self._capabilities:
            if self._capabilities.has_tests and "test_execution" not in gates:
                gates.append("test_execution")

            if self._capabilities.has_linting and "linting" not in gates:
                gates.append("linting")

            if (
                self._capabilities.has_type_checking
                and analysis.complexity.level.value >= 3
            ):
                if "type_checking" not in gates:
                    gates.append("type_checking")

        return gates

    def _build_context_enrichments(
        self,
        analysis: TaskAnalysis,
        use_ralph: bool,
        completion_criteria: List[str],
        completion_promise: str,
    ) -> Dict[str, Any]:
        """Build context enrichments to add to task."""
        enrichments = {
            "triage": {
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
                "complexity_level": analysis.complexity.level.value,
                "complexity_name": analysis.complexity.level.name,
                "use_ralph_mode": use_ralph,
                "completion_promise": completion_promise if use_ralph else None,
                "estimated_iterations": analysis.recommendation.estimated_iterations,
                "confidence": analysis.recommendation.confidence,
            },
            "quality_gates": analysis.recommendation.quality_gates,
            "completion_criteria": completion_criteria,
        }

        # Add capability information if available
        if self._capabilities:
            enrichments["codebase"] = {
                "language": self._capabilities.language,
                "has_tests": self._capabilities.has_tests,
                "has_linting": self._capabilities.has_linting,
                "has_type_checking": self._capabilities.has_type_checking,
                "test_command": self._capabilities.test_command,
                "lint_command": self._capabilities.lint_command,
            }

        return enrichments

    def _calculate_confidence(
        self,
        analysis: TaskAnalysis,
        completion_criteria: List[str],
    ) -> float:
        """Calculate overall confidence in triage decision."""
        confidence = analysis.recommendation.confidence

        # More completion criteria = higher confidence
        if len(completion_criteria) >= 4:
            confidence += 0.1
        elif len(completion_criteria) < 2:
            confidence -= 0.1

        # Codebase capabilities increase confidence
        if self._capabilities:
            if self._capabilities.has_tests:
                confidence += 0.05
            if self._capabilities.has_ci:
                confidence += 0.05

        return max(0.1, min(1.0, confidence))

    def _default_triage_result(self, work_item: Dict[str, Any]) -> TriageResult:
        """Return safe default triage result when analysis fails."""
        return TriageResult(
            analysis=TaskAnalysis(
                complexity=self.complexity_analyzer._assess_complexity(
                    work_item.get("type", "feature"),
                    work_item.get("title", ""),
                    work_item.get("description", ""),
                    work_item.get("priority", 3),
                ),
                recommendation=ExecutionRecommendation(
                    use_ralph_mode=False,
                    estimated_iterations=1,
                    completion_criteria=["Implementation complete"],
                    reasoning=["Default triage - analysis failed"],
                ),
            ),
            use_ralph_mode=False,
            completion_criteria=["Implementation complete"],
            completion_promise="DONE",
            max_iterations=10,
            quality_gates=["syntax_check"],
            confidence=0.3,
            reasoning=["Fallback to default triage"],
            triaged_at=datetime.now(timezone.utc),
        )

    async def enrich_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich a task with triage information.

        This is the main entry point for integrating triage into the task workflow.
        Returns the task_data with triage enrichments added to context.

        Args:
            task_data: Task data dictionary

        Returns:
            Enriched task_data with triage context
        """
        triage_result = await self.triage(task_data)

        # Update task context with triage enrichments
        if "context" not in task_data:
            task_data["context"] = {}

        task_data["context"].update(triage_result.context_enrichments)

        # Add Ralph settings if recommended
        if triage_result.use_ralph_mode:
            if not task_data["context"].get("ralph_enabled"):
                task_data["context"]["ralph_enabled"] = True
            if not task_data["context"].get("completion_promise"):
                task_data["context"][
                    "completion_promise"
                ] = triage_result.completion_promise
            if not task_data["context"].get("max_iterations"):
                task_data["context"]["max_iterations"] = triage_result.max_iterations

        return task_data

    def get_capabilities(self) -> Optional[CodebaseCapabilities]:
        """Get cached codebase capabilities."""
        return self._capabilities

    def clear_cache(self):
        """Clear cached capabilities (force re-scan on next triage)."""
        self._capabilities = None
        self._capabilities_scanned = False
