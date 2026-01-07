"""
Completion Signal Types and Detection

This module provides structured completion signal types for Ralph Wiggum
iterative execution. It supports multiple signal patterns:
- <promise>TEXT</promise> tags
- <complete>TEXT</complete> tags
- <done>TEXT</done> tags
- TASK_COMPLETE prefix pattern

These signals allow agents to explicitly indicate task completion in a
consistent, detectable manner.
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Pattern, Tuple

logger = logging.getLogger(__name__)


class CompletionType(Enum):
    """
    Types of completion signals that can be detected.

    These represent different mechanisms by which task completion
    can be signaled or determined.
    """

    # Explicit signal types (agent output signals)
    PROMISE = auto()  # <promise>TEXT</promise>
    COMPLETE = auto()  # <complete>TEXT</complete>
    DONE = auto()  # <done>TEXT</done>
    TASK_COMPLETE_PREFIX = auto()  # TASK_COMPLETE: description

    # Configuration-based completion
    MAX_ITERATIONS = auto()  # Safety limit reached
    CRITERIA = auto()  # Success criteria from "When complete:" section
    IMPLICIT = auto()  # Weak signal from completion phrases

    # Terminal states
    STUCK = auto()  # Agent detected it cannot proceed
    ERROR = auto()  # Execution error terminated the task

    def is_explicit_signal(self) -> bool:
        """Check if this is an explicit completion signal from agent output."""
        return self in (
            CompletionType.PROMISE,
            CompletionType.COMPLETE,
            CompletionType.DONE,
            CompletionType.TASK_COMPLETE_PREFIX,
        )

    def is_success_signal(self) -> bool:
        """Check if this represents successful completion."""
        return self in (
            CompletionType.PROMISE,
            CompletionType.COMPLETE,
            CompletionType.DONE,
            CompletionType.TASK_COMPLETE_PREFIX,
            CompletionType.CRITERIA,
        )


@dataclass
class CompletionSignal:
    """
    Represents a detected completion signal.

    This dataclass captures all relevant information about a detected
    completion signal, including its type, extracted text, and context.
    """

    # Whether a completion signal was detected
    detected: bool = False

    # Type of completion signal detected
    signal_type: Optional[CompletionType] = None

    # Extracted signal text (content within tags or after prefix)
    signal_text: Optional[str] = None

    # The raw matched text (including tags/prefix)
    raw_match: Optional[str] = None

    # Confidence level for the detection (0.0 to 1.0)
    confidence: float = 1.0

    # Additional context or metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        """Allow using CompletionSignal directly in boolean context."""
        return self.detected

    def is_successful(self) -> bool:
        """Check if this signal indicates successful task completion."""
        if not self.detected or self.signal_type is None:
            return False
        return self.signal_type.is_success_signal()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "detected": self.detected,
            "signal_type": self.signal_type.name if self.signal_type else None,
            "signal_text": self.signal_text,
            "raw_match": self.raw_match,
            "confidence": self.confidence,
            "is_successful": self.is_successful(),
            "metadata": self.metadata,
        }


class CompletionSignalDetector:
    """
    Detects completion signals in agent output.

    This class provides pattern-based detection for multiple completion
    signal formats, with configurable patterns and priority ordering.
    """

    # Pattern definitions for each signal type
    # Order matters - first match wins (highest priority first)
    DEFAULT_PATTERNS: List[Tuple[CompletionType, Pattern[str]]] = [
        # <promise>TEXT</promise> - original/primary pattern
        (
            CompletionType.PROMISE,
            re.compile(r"<promise>\s*(.+?)\s*</promise>", re.IGNORECASE | re.DOTALL),
        ),
        # <complete>TEXT</complete> - alternative explicit signal
        (
            CompletionType.COMPLETE,
            re.compile(r"<complete>\s*(.+?)\s*</complete>", re.IGNORECASE | re.DOTALL),
        ),
        # <done>TEXT</done> - shorthand explicit signal
        (
            CompletionType.DONE,
            re.compile(r"<done>\s*(.+?)\s*</done>", re.IGNORECASE | re.DOTALL),
        ),
        # TASK_COMPLETE: description - prefix-based signal
        (
            CompletionType.TASK_COMPLETE_PREFIX,
            re.compile(
                r"TASK_COMPLETE[:\s]+(.+?)(?:\n|$)", re.IGNORECASE | re.MULTILINE
            ),
        ),
    ]

    def __init__(
        self,
        patterns: Optional[List[Tuple[CompletionType, Pattern[str]]]] = None,
        custom_patterns: Optional[List[Tuple[str, str]]] = None,
    ):
        """
        Initialize the signal detector.

        Args:
            patterns: Override default patterns with custom list
            custom_patterns: Additional patterns as (name, regex) tuples
                            to add to defaults
        """
        self.patterns = list(patterns or self.DEFAULT_PATTERNS)

        # Add any custom patterns
        if custom_patterns:
            for name, regex in custom_patterns:
                pattern = re.compile(regex, re.IGNORECASE | re.DOTALL)
                # Custom patterns use PROMISE type by default
                self.patterns.append((CompletionType.PROMISE, pattern))
                logger.debug(f"Added custom completion pattern: {name}")

    def detect(self, text: str) -> CompletionSignal:
        """
        Detect completion signal in text.

        Searches for completion signals in priority order (first match wins).

        Args:
            text: The text to search for completion signals

        Returns:
            CompletionSignal with detection results
        """
        if not text:
            return CompletionSignal(detected=False)

        for signal_type, pattern in self.patterns:
            match = pattern.search(text)
            if match:
                signal_text = match.group(1).strip() if match.groups() else ""
                raw_match = match.group(0)

                logger.debug(
                    f"Completion signal detected: type={signal_type.name}, "
                    f"text='{signal_text[:50]}...'"
                )

                return CompletionSignal(
                    detected=True,
                    signal_type=signal_type,
                    signal_text=signal_text,
                    raw_match=raw_match,
                    confidence=1.0,
                    metadata={
                        "pattern_index": self.patterns.index((signal_type, pattern)),
                        "match_start": match.start(),
                        "match_end": match.end(),
                    },
                )

        return CompletionSignal(detected=False)

    def detect_all(self, text: str) -> List[CompletionSignal]:
        """
        Detect all completion signals in text.

        Unlike detect(), this finds all matches, not just the first.
        Useful for validation or when multiple signals might be present.

        Args:
            text: The text to search for completion signals

        Returns:
            List of all detected CompletionSignals
        """
        if not text:
            return []

        signals = []
        for signal_type, pattern in self.patterns:
            for match in pattern.finditer(text):
                signal_text = match.group(1).strip() if match.groups() else ""
                raw_match = match.group(0)

                signals.append(
                    CompletionSignal(
                        detected=True,
                        signal_type=signal_type,
                        signal_text=signal_text,
                        raw_match=raw_match,
                        confidence=1.0,
                        metadata={
                            "pattern_index": self.patterns.index(
                                (signal_type, pattern)
                            ),
                            "match_start": match.start(),
                            "match_end": match.end(),
                        },
                    )
                )

        return signals

    def has_signal(self, text: str) -> bool:
        """
        Quick check if text contains any completion signal.

        Args:
            text: The text to check

        Returns:
            True if any completion signal is found
        """
        return self.detect(text).detected

    @classmethod
    def create_pattern(
        cls, tag_name: str, case_insensitive: bool = True
    ) -> Pattern[str]:
        """
        Create a tag-based completion pattern.

        Args:
            tag_name: The tag name (e.g., "promise", "complete")
            case_insensitive: Whether to match case-insensitively

        Returns:
            Compiled regex pattern
        """
        flags = re.DOTALL
        if case_insensitive:
            flags |= re.IGNORECASE

        return re.compile(rf"<{tag_name}>\s*(.+?)\s*</{tag_name}>", flags)


# Module-level convenience functions
_default_detector = CompletionSignalDetector()


def detect_completion(text: str) -> CompletionSignal:
    """
    Detect completion signal in text using default detector.

    This is a convenience function for simple use cases.

    Args:
        text: The text to search for completion signals

    Returns:
        CompletionSignal with detection results
    """
    return _default_detector.detect(text)


def has_completion_signal(text: str) -> bool:
    """
    Quick check if text contains any completion signal.

    Args:
        text: The text to check

    Returns:
        True if any completion signal is found
    """
    return _default_detector.has_signal(text)


def extract_signal_text(text: str) -> Optional[str]:
    """
    Extract just the signal text from completion signal.

    Args:
        text: The text to search

    Returns:
        The extracted signal text, or None if not found
    """
    signal = _default_detector.detect(text)
    return signal.signal_text if signal.detected else None
