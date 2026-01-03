"""
Completion Criteria Validator

Validates that tasks have clear exit conditions before spawning Ralph Wiggum loops.
This prevents infinite loops by ensuring every task has:
- A <promise> tag pattern, OR
- A max_iterations limit, OR
- Clear success criteria that can be detected

Without completion criteria, Ralph loops can run indefinitely, consuming resources
and potentially causing unintended side effects.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of completion criteria validation"""

    # Whether the task has valid completion criteria
    is_valid: bool

    # Detected completion mechanism
    completion_type: Optional[str] = None  # "promise", "max_iterations", "criteria", None

    # Extracted promise text (if any)
    promise_text: Optional[str] = None

    # Extracted success criteria (if any)
    success_criteria: List[str] = field(default_factory=list)

    # Max iterations (if specified)
    max_iterations: Optional[int] = None

    # Validation errors or warnings
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # Suggestions for improving the prompt
    suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "is_valid": self.is_valid,
            "completion_type": self.completion_type,
            "promise_text": self.promise_text,
            "success_criteria": self.success_criteria,
            "max_iterations": self.max_iterations,
            "errors": self.errors,
            "warnings": self.warnings,
            "suggestions": self.suggestions,
        }


class CompletionCriteriaValidator:
    """
    Validates that tasks have clear completion criteria before Ralph execution.

    This validator checks for:
    1. <promise>TEXT</promise> tags that signal completion
    2. Explicit max_iterations settings
    3. Clear success criteria in "When complete:" sections
    4. Common completion patterns

    Without at least one of these, the validator rejects the task to prevent
    infinite loops.
    """

    # Pattern to detect <promise> tags
    PROMISE_PATTERN = re.compile(
        r"<promise>\s*(.+?)\s*</promise>",
        re.IGNORECASE | re.DOTALL
    )

    # Pattern to detect "Output: <promise>..." instructions
    OUTPUT_PROMISE_PATTERN = re.compile(
        r"output[:\s]+<promise>\s*(.+?)\s*</promise>",
        re.IGNORECASE | re.DOTALL
    )

    # Pattern to detect "When complete:" sections
    # Handles optional leading whitespace on each line
    WHEN_COMPLETE_PATTERN = re.compile(
        r"when\s+complete[:\s]*\n((?:\s*[-*]\s+[^\n]+\n?)+)",
        re.IGNORECASE | re.MULTILINE
    )

    # Pattern to detect max_iterations in various formats
    MAX_ITERATIONS_PATTERNS = [
        re.compile(r"--max-iterations\s+(\d+)", re.IGNORECASE),
        re.compile(r"max_iterations[:\s]+(\d+)", re.IGNORECASE),
        re.compile(r"maximum\s+(?:of\s+)?(\d+)\s+iterations", re.IGNORECASE),
        re.compile(r"(\d+)\s+iterations?\s+max", re.IGNORECASE),
    ]

    # Common completion phrases to look for
    COMPLETION_PHRASES = [
        "all tests pass",
        "tests pass",
        "build succeeds",
        "build passes",
        "no errors",
        "no warnings",
        "coverage",
        "complete when",
        "done when",
        "finished when",
        "success when",
    ]

    def __init__(self, strict: bool = True):
        """
        Initialize the validator.

        Args:
            strict: If True, require explicit completion criteria.
                    If False, allow tasks with implicit criteria.
        """
        self.strict = strict

    def validate(
        self,
        prompt: str,
        config: Optional[Dict[str, Any]] = None
    ) -> ValidationResult:
        """
        Validate that a prompt has clear completion criteria.

        Args:
            prompt: The task prompt to validate
            config: Optional configuration dict (may contain max_iterations)

        Returns:
            ValidationResult with validation status and details
        """
        config = config or {}
        result = ValidationResult(is_valid=False)

        # Check for empty prompt
        if not prompt or not prompt.strip():
            result.errors.append("Prompt is empty")
            return result

        # 1. Check for <promise> tags
        promise_match = self.PROMISE_PATTERN.search(prompt)
        if promise_match:
            result.promise_text = promise_match.group(1).strip()
            result.completion_type = "promise"
            result.is_valid = True
            logger.debug(f"Found promise tag: {result.promise_text}")

        # 2. Check for max_iterations
        max_iters = config.get("max_iterations")
        if max_iters is None:
            # Check in prompt text
            for pattern in self.MAX_ITERATIONS_PATTERNS:
                match = pattern.search(prompt)
                if match:
                    max_iters = int(match.group(1))
                    break

        if max_iters is not None:
            result.max_iterations = max_iters
            if not result.is_valid:
                result.completion_type = "max_iterations"
                result.is_valid = True
            logger.debug(f"Found max_iterations: {max_iters}")

        # 3. Check for "When complete:" section
        when_complete = self.WHEN_COMPLETE_PATTERN.search(prompt)
        if when_complete:
            criteria_text = when_complete.group(1)
            # Extract individual criteria
            criteria = [
                line.strip().lstrip("-*").strip()
                for line in criteria_text.split("\n")
                if line.strip() and not line.strip().startswith("#")
            ]
            result.success_criteria = [c for c in criteria if c]

            if result.success_criteria:
                if not result.is_valid:
                    result.completion_type = "criteria"
                    result.is_valid = True
                logger.debug(f"Found success criteria: {result.success_criteria}")

        # 4. Check for completion phrases (less reliable)
        if not result.is_valid:
            prompt_lower = prompt.lower()
            found_phrases = [
                phrase for phrase in self.COMPLETION_PHRASES
                if phrase in prompt_lower
            ]
            if found_phrases:
                result.warnings.append(
                    f"Found implicit completion hints ({', '.join(found_phrases)}) "
                    "but no explicit criteria. Consider adding explicit completion criteria."
                )
                if not self.strict:
                    result.is_valid = True
                    result.completion_type = "implicit"

        # Generate suggestions if not valid
        if not result.is_valid:
            result.suggestions = self._generate_suggestions(prompt)
            result.errors.append(
                "No clear completion criteria found. Ralph loops require explicit "
                "exit conditions to prevent infinite execution."
            )

        # Validate promise text isn't too generic
        if result.promise_text:
            generic_promises = ["done", "complete", "finished", "ok", "yes"]
            if result.promise_text.lower() in generic_promises:
                result.warnings.append(
                    f"Promise text '{result.promise_text}' is very generic. "
                    "Consider using a more specific completion signal."
                )

        # Warn if no max_iterations as safety backup
        if result.is_valid and result.max_iterations is None:
            result.warnings.append(
                "No max_iterations specified. Consider adding a safety limit "
                "(e.g., --max-iterations 10) to prevent runaway loops."
            )

        return result

    def _generate_suggestions(self, prompt: str) -> List[str]:
        """Generate suggestions for improving the prompt"""
        suggestions = []

        # Suggest adding promise tag
        suggestions.append(
            "Add a completion signal: 'Output: <promise>DONE</promise>' when all criteria are met"
        )

        # Suggest adding When complete section
        suggestions.append(
            "Add explicit criteria:\n"
            "When complete:\n"
            "- [Success criterion 1]\n"
            "- [Success criterion 2]\n"
            "- Output: <promise>DONE</promise>"
        )

        # Suggest max_iterations
        suggestions.append(
            "Add a safety limit: --max-iterations 10"
        )

        return suggestions

    def extract_completion_signal(self, output: str) -> Tuple[bool, Optional[str]]:
        """
        Check if output contains a completion signal.

        Args:
            output: The output text to check

        Returns:
            Tuple of (is_complete, promise_text)
        """
        match = self.PROMISE_PATTERN.search(output)
        if match:
            return True, match.group(1).strip()
        return False, None

    def format_validation_error(self, result: ValidationResult) -> str:
        """Format validation result as human-readable error message"""
        if result.is_valid:
            return "Validation passed"

        lines = ["Ralph Wiggum validation failed:"]
        lines.append("")

        for error in result.errors:
            lines.append(f"  ERROR: {error}")

        if result.warnings:
            lines.append("")
            for warning in result.warnings:
                lines.append(f"  WARNING: {warning}")

        if result.suggestions:
            lines.append("")
            lines.append("Suggestions to fix:")
            for i, suggestion in enumerate(result.suggestions, 1):
                lines.append(f"  {i}. {suggestion}")

        return "\n".join(lines)
