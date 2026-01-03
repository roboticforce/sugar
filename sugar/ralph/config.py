"""
Ralph Wiggum Configuration

Configuration for Ralph Wiggum iterative loops.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RalphConfig:
    """Configuration for Ralph Wiggum iterative execution"""

    # Maximum iterations before auto-stop (safety limit)
    max_iterations: int = 10

    # Promise text to look for to signal completion
    completion_promise: str = "DONE"

    # Whether to require explicit completion criteria
    require_completion_criteria: bool = True

    # Minimum confidence score to consider task complete
    min_confidence: float = 0.8

    # Timeout per iteration in seconds
    iteration_timeout: int = 300

    # Delay between iterations in seconds
    iteration_delay: float = 1.0

    # Quality gates to run between iterations
    quality_gates_enabled: bool = True

    # Stop on first quality gate failure or continue iterating
    stop_on_gate_failure: bool = False

    # Patterns that indicate completion (besides promise tag)
    completion_patterns: List[str] = field(
        default_factory=lambda: [
            r"<promise>.*?</promise>",
            r"all tests pass",
            r"task complete",
            r"implementation complete",
        ]
    )

    # Patterns that indicate the task is stuck and should stop
    stuck_patterns: List[str] = field(
        default_factory=lambda: [
            r"cannot proceed",
            r"blocked by",
            r"need human intervention",
            r"unable to resolve",
        ]
    )

    def validate(self) -> List[str]:
        """Validate configuration, return list of errors"""
        errors = []

        if self.max_iterations < 1:
            errors.append("max_iterations must be at least 1")

        if self.max_iterations > 100:
            errors.append("max_iterations should not exceed 100 (safety limit)")

        if self.iteration_timeout < 30:
            errors.append("iteration_timeout must be at least 30 seconds")

        if not 0.0 <= self.min_confidence <= 1.0:
            errors.append("min_confidence must be between 0.0 and 1.0")

        return errors
