"""
Completion Criteria Validator

Validates that tasks have clear exit conditions before spawning Ralph Wiggum loops.
This prevents infinite loops by ensuring every task has:
- A <promise> tag pattern, OR
- A <complete> tag pattern, OR
- A <done> tag pattern, OR
- A TASK_COMPLETE prefix pattern, OR
- A max_iterations limit, OR
- Clear success criteria that can be detected

Without completion criteria, Ralph loops can run indefinitely, consuming resources
and potentially causing unintended side effects.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .signals import (
    CompletionSignal,
    CompletionSignalDetector,
    CompletionType,
)

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of completion criteria validation"""

    # Whether the task has valid completion criteria
    is_valid: bool

    # Detected completion mechanism
    # Legacy string type for backward compatibility
    completion_type: Optional[str] = (
        None  # "promise", "complete", "done", "task_complete", "max_iterations", "criteria", None
    )

    # Structured completion signal (new)
    completion_signal: Optional[CompletionSignal] = None

    # Extracted promise text (if any) - kept for backward compatibility
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
            "completion_signal": (
                self.completion_signal.to_dict() if self.completion_signal else None
            ),
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
    2. <complete>TEXT</complete> tags that signal completion
    3. <done>TEXT</done> tags that signal completion
    4. TASK_COMPLETE: prefix pattern
    5. Explicit max_iterations settings
    6. Clear success criteria in "When complete:" sections
    7. Common completion patterns

    Without at least one of these, the validator rejects the task to prevent
    infinite loops.
    """

    # Legacy pattern kept for backward compatibility
    # New code should use CompletionSignalDetector
    PROMISE_PATTERN = re.compile(
        r"<promise>\s*(.+?)\s*</promise>", re.IGNORECASE | re.DOTALL
    )

    # Pattern to detect "Output: <promise>..." instructions
    OUTPUT_PROMISE_PATTERN = re.compile(
        r"output[:\s]+<promise>\s*(.+?)\s*</promise>", re.IGNORECASE | re.DOTALL
    )

    # Pattern to detect "When complete:" sections
    # Handles optional leading whitespace on each line
    WHEN_COMPLETE_PATTERN = re.compile(
        r"when\s+complete[:\s]*\n((?:\s*[-*]\s+[^\n]+\n?)+)",
        re.IGNORECASE | re.MULTILINE,
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

    # Mapping from CompletionType to string representation (for backward compatibility)
    _COMPLETION_TYPE_MAP = {
        CompletionType.PROMISE: "promise",
        CompletionType.COMPLETE: "complete",
        CompletionType.DONE: "done",
        CompletionType.TASK_COMPLETE_PREFIX: "task_complete",
        CompletionType.MAX_ITERATIONS: "max_iterations",
        CompletionType.CRITERIA: "criteria",
        CompletionType.IMPLICIT: "implicit",
        CompletionType.STUCK: "stuck",
        CompletionType.ERROR: "error",
    }

    def __init__(self, strict: bool = True):
        """
        Initialize the validator.

        Args:
            strict: If True, require explicit completion criteria.
                    If False, allow tasks with implicit criteria.
        """
        self.strict = strict
        self.signal_detector = CompletionSignalDetector()

    def validate(
        self, prompt: str, config: Optional[Dict[str, Any]] = None
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

        # 1. Check for completion signal tags using the new signal detector
        # This detects <promise>, <complete>, <done>, and TASK_COMPLETE: patterns
        signal = self.signal_detector.detect(prompt)
        if signal.detected:
            result.completion_signal = signal
            result.promise_text = signal.signal_text
            result.completion_type = self._COMPLETION_TYPE_MAP.get(
                signal.signal_type, "promise"
            )
            result.is_valid = True
            logger.debug(
                f"Found completion signal: type={signal.signal_type.name}, "
                f"text={result.promise_text}"
            )

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
                phrase for phrase in self.COMPLETION_PHRASES if phrase in prompt_lower
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
        suggestions.append("Add a safety limit: --max-iterations 10")

        return suggestions

    def extract_completion_signal(self, output: str) -> Tuple[bool, Optional[str]]:
        """
        Check if output contains a completion signal.

        This method is kept for backward compatibility. For new code,
        use detect_completion_signal() which returns a CompletionSignal.

        Args:
            output: The output text to check

        Returns:
            Tuple of (is_complete, promise_text)
        """
        signal = self.signal_detector.detect(output)
        if signal.detected:
            return True, signal.signal_text
        return False, None

    def detect_completion_signal(self, output: str) -> CompletionSignal:
        """
        Detect and return structured completion signal from output.

        This is the preferred method for detecting completion signals.
        It returns a CompletionSignal with full type information.

        Args:
            output: The output text to check

        Returns:
            CompletionSignal with detection results
        """
        return self.signal_detector.detect(output)

    def detect_all_completion_signals(self, output: str) -> List[CompletionSignal]:
        """
        Detect all completion signals in output.

        Useful when multiple signals might be present.

        Args:
            output: The output text to check

        Returns:
            List of all detected CompletionSignals
        """
        return self.signal_detector.detect_all(output)

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
