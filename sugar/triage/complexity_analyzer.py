"""
Task Complexity Analyzer

Intelligently analyzes task complexity and determines optimal execution strategy.
Decides whether to use Ralph mode (iterative) or single-pass execution based on
task characteristics, type, scope, and keywords.
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class ComplexityLevel(Enum):
    """Task complexity levels (1=simple, 5=very complex)"""

    SIMPLE = 1  # Single file, simple change
    MODERATE = 2  # Few files, straightforward logic
    INTERMEDIATE = 3  # Multiple files or moderate logic
    COMPLEX = 4  # Many files or complex logic
    VERY_COMPLEX = 5  # System-wide or highly complex


@dataclass
class ComplexityScore:
    """Detailed complexity scoring breakdown"""

    # Overall complexity (1-5)
    level: ComplexityLevel

    # Contributing factors (0.0-1.0 each)
    scope_factor: float = 0.5  # How many files/modules affected
    logic_factor: float = 0.5  # How complex the logic changes are
    integration_factor: float = 0.5  # How many system integrations
    risk_factor: float = 0.5  # How risky the changes are

    # Confidence in the assessment (0.0-1.0)
    confidence: float = 0.5

    # Reasoning for the score
    reasoning: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "level": self.level.value,
            "level_name": self.level.name,
            "scope_factor": self.scope_factor,
            "logic_factor": self.logic_factor,
            "integration_factor": self.integration_factor,
            "risk_factor": self.risk_factor,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


@dataclass
class ExecutionRecommendation:
    """Recommendation for how to execute a task"""

    # Whether to use Ralph mode (iterative execution)
    use_ralph_mode: bool

    # Estimated number of iterations needed
    estimated_iterations: int = 3

    # Maximum iterations to allow
    max_iterations: int = 10

    # Auto-generated completion criteria
    completion_criteria: List[str] = field(default_factory=list)

    # Suggested quality gates
    quality_gates: List[str] = field(default_factory=list)

    # Reasoning for recommendation
    reasoning: List[str] = field(default_factory=list)

    # Confidence in recommendation (0.0-1.0)
    confidence: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "use_ralph_mode": self.use_ralph_mode,
            "estimated_iterations": self.estimated_iterations,
            "max_iterations": self.max_iterations,
            "completion_criteria": self.completion_criteria,
            "quality_gates": self.quality_gates,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
        }


@dataclass
class TaskAnalysis:
    """Complete task analysis result"""

    # Complexity assessment
    complexity: ComplexityScore

    # Execution recommendation
    recommendation: ExecutionRecommendation

    # Detected keywords/indicators
    detected_keywords: Dict[str, List[str]] = field(default_factory=dict)

    # Task characteristics
    characteristics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "complexity": self.complexity.to_dict(),
            "recommendation": self.recommendation.to_dict(),
            "detected_keywords": self.detected_keywords,
            "characteristics": self.characteristics,
        }


class ComplexityAnalyzer:
    """
    Analyzes task complexity and determines optimal execution strategy.

    This analyzer:
    1. Scores task complexity (1-5) based on multiple factors
    2. Determines if Ralph mode (iterative) should be used
    3. Estimates iteration requirements
    4. Generates appropriate completion criteria
    """

    # Keywords that indicate complexity level
    SIMPLE_KEYWORDS = {
        "fix typo",
        "update comment",
        "rename",
        "format",
        "style",
        "add comment",
        "remove comment",
        "update docs",
        "quick fix",
        "simple change",
        "minor fix",
        "trivial",
        "straightforward",
    }

    MODERATE_KEYWORDS = {
        "add",
        "create",
        "implement",
        "update",
        "modify",
        "enhance",
        "improve",
        "fix bug",
        "add test",
        "update test",
        "add feature",
    }

    COMPLEX_KEYWORDS = {
        "refactor",
        "migrate",
        "redesign",
        "rewrite",
        "restructure",
        "optimize",
        "performance",
        "integrate",
        "system-wide",
        "architecture",
        "multi-step",
        "complex",
        "comprehensive",
        "overhaul",
        "transform",
    }

    VERY_COMPLEX_KEYWORDS = {
        "migrate entire",
        "complete overhaul",
        "full rewrite",
        "major refactor",
        "system redesign",
        "breaking change",
        "architectural change",
        "complete integration",
        "end-to-end",
        "full implementation",
    }

    # Task type complexity baselines
    TASK_TYPE_COMPLEXITY = {
        "documentation": 1.5,  # Usually simpler
        "test": 2.0,  # Moderate
        "bug_fix": 2.5,  # Varies widely
        "feature": 3.0,  # Often complex
        "refactor": 3.5,  # Usually complex
        "chore": 2.0,  # Moderate
        "style": 1.5,  # Usually simple
        "perf": 3.5,  # Usually complex
        "security": 3.0,  # Can be complex
        "ci": 2.5,  # Moderate
    }

    # Scope indicators (suggest multi-file changes)
    SCOPE_INDICATORS = {
        "multi-file": 1.0,
        "system-wide": 1.0,
        "across": 0.8,
        "throughout": 0.8,
        "all files": 1.0,
        "multiple": 0.7,
        "several": 0.6,
        "many": 0.7,
        "entire": 0.9,
        "whole": 0.8,
        "codebase": 0.9,
    }

    # Integration complexity indicators
    INTEGRATION_INDICATORS = {
        "integrate",
        "connect",
        "api",
        "endpoint",
        "service",
        "database",
        "external",
        "third-party",
        "webhook",
        "oauth",
        "authentication",
        "authorization",
        "payment",
        "queue",
    }

    # Risk indicators (suggest need for testing/validation)
    RISK_INDICATORS = {
        "breaking": 1.0,
        "critical": 0.9,
        "production": 0.8,
        "security": 0.9,
        "data migration": 0.9,
        "schema change": 0.8,
        "backwards incompatible": 1.0,
        "destructive": 1.0,
        "irreversible": 0.9,
    }

    # Task types that benefit from iterative execution
    ITERATIVE_TASK_TYPES = {"refactor", "feature", "perf", "security", "bug_fix"}

    # Task types that rarely need iteration
    SINGLE_PASS_TASK_TYPES = {"documentation", "style", "chore", "ci"}

    def __init__(self, strict_mode: bool = False):
        """
        Initialize the analyzer.

        Args:
            strict_mode: If True, be more conservative about recommending Ralph mode
        """
        self.strict_mode = strict_mode

    def analyze(self, work_item: Dict[str, Any]) -> TaskAnalysis:
        """
        Analyze a work item and provide complexity assessment and execution recommendation.

        Args:
            work_item: Work item dictionary with type, title, description, etc.

        Returns:
            TaskAnalysis with complexity score and execution recommendation
        """
        task_type = work_item.get("type", "feature")
        title = work_item.get("title", "")
        description = work_item.get("description", "")
        priority = work_item.get("priority", 3)

        # Combine title and description for analysis
        full_text = f"{title}\n{description}".lower()

        # Analyze complexity
        complexity = self._assess_complexity(task_type, title, description, priority)

        # Generate execution recommendation
        recommendation = self._recommend_execution_strategy(
            task_type, full_text, complexity, priority
        )

        # Detect keywords
        detected_keywords = self._detect_keywords(full_text)

        # Extract characteristics
        characteristics = self._extract_characteristics(task_type, full_text, work_item)

        return TaskAnalysis(
            complexity=complexity,
            recommendation=recommendation,
            detected_keywords=detected_keywords,
            characteristics=characteristics,
        )

    def _assess_complexity(
        self,
        task_type: str,
        title: str,
        description: str,
        priority: int,
    ) -> ComplexityScore:
        """Assess the complexity of a task"""
        full_text = f"{title}\n{description}".lower()
        reasoning = []

        # Start with baseline from task type
        base_complexity = self.TASK_TYPE_COMPLEXITY.get(task_type, 2.5)
        reasoning.append(f"Base complexity for {task_type}: {base_complexity}")

        # Assess scope factor
        scope_factor = self._assess_scope(full_text)
        reasoning.append(f"Scope factor: {scope_factor:.2f}")

        # Assess logic complexity
        logic_factor = self._assess_logic_complexity(full_text)
        reasoning.append(f"Logic complexity: {logic_factor:.2f}")

        # Assess integration complexity
        integration_factor = self._assess_integration_complexity(full_text)
        reasoning.append(f"Integration complexity: {integration_factor:.2f}")

        # Assess risk
        risk_factor = self._assess_risk(full_text)
        reasoning.append(f"Risk factor: {risk_factor:.2f}")

        # Calculate weighted complexity score
        # Risk and scope are weighted more heavily than logic and integration
        # Formula: base * multiplier based on weighted factors
        weighted_avg = (
            scope_factor * 0.3
            + logic_factor * 0.2
            + integration_factor * 0.2
            + risk_factor * 0.3  # Risk is weighted heavily
        )

        # Determine multiplier based on weighted average
        if weighted_avg < 0.20:
            raw_score = base_complexity * 0.6  # Significant reduction for simple tasks
        elif weighted_avg < 0.35:
            raw_score = base_complexity * 0.85  # Moderate reduction
        elif weighted_avg < 0.55:
            raw_score = base_complexity  # Keep base complexity
        else:
            raw_score = base_complexity * (
                1.0 + (weighted_avg - 0.45)
            )  # Increase for complex tasks

        # Clamp to 1-5 range and round to nearest level
        complexity_value = max(1, min(5, round(raw_score)))
        complexity_level = ComplexityLevel(complexity_value)

        # Assess confidence based on text length and specificity
        confidence = self._assess_confidence(title, description)

        reasoning.append(
            f"Final complexity: {complexity_level.name} ({complexity_value})"
        )

        return ComplexityScore(
            level=complexity_level,
            scope_factor=scope_factor,
            logic_factor=logic_factor,
            integration_factor=integration_factor,
            risk_factor=risk_factor,
            confidence=confidence,
            reasoning=reasoning,
        )

    def _assess_scope(self, text: str) -> float:
        """Assess scope factor (0.0-1.0) based on scope indicators"""
        max_score = 0.0
        for indicator, weight in self.SCOPE_INDICATORS.items():
            if indicator in text:
                max_score = max(max_score, weight)
        return max_score

    def _assess_logic_complexity(self, text: str) -> float:
        """Assess logic complexity (0.0-1.0) based on keywords"""
        score = 0.0

        # Check for complexity keywords
        if any(kw in text for kw in self.VERY_COMPLEX_KEYWORDS):
            score = max(score, 1.0)
        elif any(kw in text for kw in self.COMPLEX_KEYWORDS):
            score = max(score, 0.75)
        elif any(kw in text for kw in self.SIMPLE_KEYWORDS):
            # Simple keywords should result in LOWER complexity
            score = max(score, 0.1)
        elif any(kw in text for kw in self.MODERATE_KEYWORDS):
            score = max(score, 0.5)
        else:
            score = 0.4  # Default slightly below moderate

        return score

    def _assess_integration_complexity(self, text: str) -> float:
        """Assess integration complexity (0.0-1.0)"""
        integration_count = sum(
            1 for indicator in self.INTEGRATION_INDICATORS if indicator in text
        )
        # Each integration indicator adds complexity
        return min(1.0, integration_count * 0.3)

    def _assess_risk(self, text: str) -> float:
        """Assess risk factor (0.0-1.0)"""
        max_risk = 0.0
        for indicator, weight in self.RISK_INDICATORS.items():
            if indicator in text:
                max_risk = max(max_risk, weight)
        return max_risk

    def _assess_confidence(self, title: str, description: str) -> float:
        """Assess confidence in the complexity assessment"""
        # More detailed descriptions lead to higher confidence
        desc_length = len(description)
        title_length = len(title)

        confidence = 0.5  # Base confidence

        # Longer descriptions increase confidence
        if desc_length > 200:
            confidence += 0.2
        elif desc_length > 100:
            confidence += 0.1
        elif desc_length < 20:
            confidence -= 0.2

        # Presence of specific details increases confidence
        if any(char in description for char in [":", "-", "*", "1.", "2."]):
            confidence += 0.1

        return max(0.1, min(1.0, confidence))

    def _recommend_execution_strategy(
        self,
        task_type: str,
        text: str,
        complexity: ComplexityScore,
        priority: int,
    ) -> ExecutionRecommendation:
        """Recommend whether to use Ralph mode and estimate iterations"""
        reasoning = []
        use_ralph = False
        estimated_iterations = 1
        max_iterations = 10

        # Decision factors
        complexity_level = complexity.level.value

        # 1. Task type influence
        if task_type in self.SINGLE_PASS_TASK_TYPES:
            reasoning.append(f"Task type '{task_type}' typically single-pass")
            use_ralph = False
        elif task_type in self.ITERATIVE_TASK_TYPES:
            reasoning.append(f"Task type '{task_type}' benefits from iteration")
            use_ralph = True

        # 2. Complexity influence (override task type for very complex tasks)
        if complexity_level >= 4:
            reasoning.append(f"High complexity ({complexity_level}) suggests iteration")
            use_ralph = True
            estimated_iterations = complexity_level
        elif complexity_level >= 3:
            reasoning.append(
                f"Moderate complexity ({complexity_level}) may need iteration"
            )
            if task_type in self.ITERATIVE_TASK_TYPES:
                use_ralph = True
                estimated_iterations = 3
        else:
            if complexity_level <= 2:
                reasoning.append(
                    f"Low complexity ({complexity_level}) suggests single-pass"
                )
                use_ralph = False

        # 3. Risk influence (high risk = more validation needed)
        if complexity.risk_factor > 0.7:
            reasoning.append("High risk requires iterative validation")
            use_ralph = True
            estimated_iterations = max(estimated_iterations, 4)

        # 4. Scope influence (wide scope = more iterations)
        if complexity.scope_factor > 0.7:
            reasoning.append("Wide scope benefits from iterative approach")
            use_ralph = True
            estimated_iterations = max(estimated_iterations, 3)

        # 5. Integration complexity (lots of integrations = iterative testing)
        if complexity.integration_factor > 0.7 and complexity_level >= 3:
            reasoning.append("High integration complexity needs iterative testing")
            use_ralph = True
            estimated_iterations = max(estimated_iterations, 4)

        # 6. Strict mode adjustment
        if self.strict_mode and use_ralph:
            reasoning.append("Strict mode: reducing max iterations")
            max_iterations = min(max_iterations, 7)

        # 7. Priority influence
        if priority >= 4:  # High priority
            reasoning.append("High priority: conservative iteration limit")
            max_iterations = min(max_iterations, 8)

        # Generate completion criteria
        completion_criteria = self._generate_completion_criteria(
            task_type, text, complexity, use_ralph
        )

        # Generate quality gates
        quality_gates = self._generate_quality_gates(task_type, complexity)

        # Assess confidence
        confidence = self._assess_recommendation_confidence(
            complexity, use_ralph, len(completion_criteria)
        )

        return ExecutionRecommendation(
            use_ralph_mode=use_ralph,
            estimated_iterations=estimated_iterations,
            max_iterations=max_iterations,
            completion_criteria=completion_criteria,
            quality_gates=quality_gates,
            reasoning=reasoning,
            confidence=confidence,
        )

    def _generate_completion_criteria(
        self,
        task_type: str,
        text: str,
        complexity: ComplexityScore,
        use_ralph: bool,
    ) -> List[str]:
        """Generate appropriate completion criteria based on task characteristics"""
        criteria = []

        # Type-specific criteria
        if task_type == "bug_fix":
            criteria.extend(
                [
                    "Bug is reproducibly fixed",
                    "Tests added to prevent regression",
                    "No new errors introduced",
                ]
            )
        elif task_type == "feature":
            criteria.extend(
                [
                    "Feature implementation complete",
                    "Tests pass for new functionality",
                    "Documentation updated",
                ]
            )
        elif task_type == "refactor":
            criteria.extend(
                [
                    "Code refactored to target structure",
                    "All existing tests still pass",
                    "No functionality changes",
                    "Code quality improved",
                ]
            )
        elif task_type == "test":
            criteria.extend(
                [
                    "Tests written and passing",
                    "Coverage meets requirements",
                ]
            )
        elif task_type == "documentation":
            criteria.extend(
                [
                    "Documentation complete and accurate",
                    "No broken links or formatting issues",
                ]
            )
        elif task_type == "perf":
            criteria.extend(
                [
                    "Performance improvements measurable",
                    "No functionality regressions",
                    "Benchmarks show improvement",
                ]
            )
        else:
            # Generic criteria
            criteria.extend(
                [
                    "Implementation complete",
                    "Tests passing",
                    "No errors or warnings",
                ]
            )

        # Add criteria based on complexity
        if complexity.level.value >= 3:
            criteria.append("Code review quality checks pass")

        if complexity.risk_factor > 0.7:
            criteria.append("Risk mitigation verified")

        if complexity.integration_factor > 0.5:
            criteria.append("Integration tests pass")

        # Add Ralph-specific criteria
        if use_ralph:
            criteria.append("Output: <promise>TASK_COMPLETE</promise>")

        return criteria

    def _generate_quality_gates(
        self,
        task_type: str,
        complexity: ComplexityScore,
    ) -> List[str]:
        """Generate quality gates appropriate for the task"""
        gates = []

        # Universal gates
        gates.append("syntax_check")

        # Type-specific gates
        if task_type in ["bug_fix", "feature", "refactor", "test"]:
            gates.append("test_execution")

        if task_type in ["feature", "refactor", "perf"]:
            gates.append("linting")

        # Complexity-based gates
        if complexity.level.value >= 3:
            gates.append("type_checking")

        if complexity.level.value >= 4:
            gates.append("integration_tests")

        return gates

    def _assess_recommendation_confidence(
        self,
        complexity: ComplexityScore,
        use_ralph: bool,
        criteria_count: int,
    ) -> float:
        """Assess confidence in the recommendation"""
        # Start with complexity confidence
        confidence = complexity.confidence

        # Clear criteria increase confidence
        if criteria_count >= 3:
            confidence += 0.1
        elif criteria_count < 2:
            confidence -= 0.1

        # Moderate complexity (neither very simple nor very complex) is easier to recommend
        if 2 <= complexity.level.value <= 4:
            confidence += 0.05

        return max(0.1, min(1.0, confidence))

    def _detect_keywords(self, text: str) -> Dict[str, List[str]]:
        """Detect and categorize keywords in the text"""
        detected = {
            "simple": [],
            "moderate": [],
            "complex": [],
            "very_complex": [],
            "scope": [],
            "integration": [],
            "risk": [],
        }

        for kw in self.SIMPLE_KEYWORDS:
            if kw in text:
                detected["simple"].append(kw)

        for kw in self.MODERATE_KEYWORDS:
            if kw in text:
                detected["moderate"].append(kw)

        for kw in self.COMPLEX_KEYWORDS:
            if kw in text:
                detected["complex"].append(kw)

        for kw in self.VERY_COMPLEX_KEYWORDS:
            if kw in text:
                detected["very_complex"].append(kw)

        for indicator in self.SCOPE_INDICATORS:
            if indicator in text:
                detected["scope"].append(indicator)

        for indicator in self.INTEGRATION_INDICATORS:
            if indicator in text:
                detected["integration"].append(indicator)

        for indicator in self.RISK_INDICATORS:
            if indicator in text:
                detected["risk"].append(indicator)

        return detected

    def _extract_characteristics(
        self,
        task_type: str,
        text: str,
        work_item: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Extract task characteristics for analysis"""
        return {
            "task_type": task_type,
            "priority": work_item.get("priority", 3),
            "has_context": bool(work_item.get("context")),
            "text_length": len(text),
            "word_count": len(text.split()),
            "has_code_references": bool(
                re.search(r"\b\w+\.(py|js|ts|go|rs|java)\b", text)
            ),
            "has_file_paths": bool(re.search(r"[/\\]\w+", text)),
            "has_numbered_steps": bool(re.search(r"^\s*\d+\.", text, re.MULTILINE)),
            "has_bullet_points": bool(re.search(r"^\s*[-*]\s", text, re.MULTILINE)),
        }
