"""
Ralph Wiggum Integration - Iterative AI loop support for Sugar

This module provides:
- CompletionCriteriaValidator: Validates tasks have clear exit conditions
- RalphWiggumProfile: Profile for iterative task execution
- RalphConfig: Configuration for Ralph Wiggum loops
"""

from .validator import CompletionCriteriaValidator, ValidationResult
from .profile import RalphWiggumProfile
from .config import RalphConfig

__all__ = [
    "CompletionCriteriaValidator",
    "ValidationResult",
    "RalphWiggumProfile",
    "RalphConfig",
]
