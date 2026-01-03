"""
Ralph Wiggum Profile - Iterative task execution profile

This profile implements the Ralph Wiggum technique for Sugar:
- Tasks are executed iteratively until completion criteria are met
- Each iteration builds on the previous work visible in files
- Quality gates run between iterations
- Completion is signaled via <promise> tags
"""

import logging
import re
from typing import Any, Dict, List, Optional

from ..profiles.base import BaseProfile, ProfileConfig
from .config import RalphConfig
from .validator import CompletionCriteriaValidator, ValidationResult

logger = logging.getLogger(__name__)


class RalphWiggumProfile(BaseProfile):
    """
    Profile for iterative task execution using the Ralph Wiggum technique.

    The Ralph Wiggum technique:
    1. Same prompt is fed repeatedly
    2. Agent sees previous work in files/git history
    3. Iteratively improves until completion criteria met
    4. <promise> tag signals successful completion

    This profile:
    - Validates completion criteria before execution
    - Manages iteration state
    - Checks for completion after each iteration
    - Enforces safety limits (max iterations)
    """

    def __init__(
        self,
        config: Optional[ProfileConfig] = None,
        ralph_config: Optional[RalphConfig] = None,
    ):
        """
        Initialize the Ralph Wiggum profile.

        Args:
            config: Base profile configuration
            ralph_config: Ralph-specific configuration
        """
        if config is None:
            config = ProfileConfig(
                name="ralph_wiggum",
                description="Iterative task execution with self-correction",
                allowed_tools=[
                    "Read",
                    "Write",
                    "Edit",
                    "Glob",
                    "Grep",
                    "Bash",
                    "Task",
                ],
                settings={
                    "iterative": True,
                    "self_correcting": True,
                },
                quality_gates_enabled=True,
            )
        super().__init__(config)

        self.ralph_config = ralph_config or RalphConfig()
        self.validator = CompletionCriteriaValidator(
            strict=self.ralph_config.require_completion_criteria
        )

        # Iteration state
        self._current_iteration = 0
        self._iteration_history: List[Dict[str, Any]] = []
        self._is_complete = False
        self._completion_reason: Optional[str] = None

    @property
    def current_iteration(self) -> int:
        """Get the current iteration number (0-indexed)."""
        return self._current_iteration

    def get_system_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """Get the system prompt for Ralph Wiggum execution"""
        context = context or {}

        iteration = context.get("iteration", self._current_iteration)
        max_iterations = context.get("max_iterations", self.ralph_config.max_iterations)
        completion_promise = context.get(
            "completion_promise", self.ralph_config.completion_promise
        )

        prompt = f"""# Ralph Wiggum Iterative Execution

You are executing a task iteratively. This is iteration {iteration + 1} of up to {max_iterations}.

## How This Works

1. You receive the same task prompt each iteration
2. Your previous work persists in files and git history
3. Review what you've done before and continue from there
4. When the task is complete, output: <promise>{completion_promise}</promise>

## Iteration Guidelines

### Starting an Iteration
1. Check git status and recent changes to understand current state
2. Review any test results or error logs from previous iterations
3. Identify what remains to be done

### During Execution
1. Make incremental progress toward the goal
2. Run tests to verify your changes
3. If stuck, try a different approach
4. Document your reasoning in comments if helpful

### Completing the Task
1. All success criteria must be met
2. Tests must pass (if applicable)
3. Output the completion signal: <promise>{completion_promise}</promise>
4. Do NOT output the promise until truly complete

### If You Cannot Complete
- If blocked by external factors, explain what's needed
- If the task is impossible, explain why
- Do NOT output the completion promise if not actually done

## Quality Standards

- All changes must pass quality gates
- Tests must pass before marking complete
- Code must follow project conventions
- No regressions in existing functionality

## Current State

- Iteration: {iteration + 1} / {max_iterations}
- Previous iterations: {len(self._iteration_history)}
"""

        # Add iteration history summary if available
        if self._iteration_history:
            prompt += "\n### Previous Iteration Summary\n"
            for i, hist in enumerate(self._iteration_history[-3:], 1):  # Last 3
                status = "completed" if hist.get("success") else "incomplete"
                prompt += f"- Iteration {hist.get('iteration', i)}: {status}\n"
                if hist.get("summary"):
                    prompt += f"  {hist['summary']}\n"

        return prompt

    async def process_input(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process and validate input before Ralph execution.

        Args:
            input_data: Input containing:
                - prompt: The task prompt
                - config: Optional Ralph configuration overrides

        Returns:
            Processed input with validation results
        """
        prompt = input_data.get("prompt", "")
        config = input_data.get("config", {})

        # Merge config with ralph_config
        max_iterations = config.get("max_iterations", self.ralph_config.max_iterations)
        completion_promise = config.get(
            "completion_promise", self.ralph_config.completion_promise
        )

        # Validate completion criteria
        validation = self.validator.validate(prompt, {"max_iterations": max_iterations})

        if not validation.is_valid:
            logger.warning(
                f"Task failed completion criteria validation: {validation.errors}"
            )
            return {
                "valid": False,
                "validation": validation.to_dict(),
                "error": self.validator.format_validation_error(validation),
                "prompt": prompt,
            }

        # Extract or use default promise
        if validation.promise_text:
            completion_promise = validation.promise_text

        # Build the execution prompt
        execution_prompt = self._build_execution_prompt(
            prompt, completion_promise, max_iterations, validation.success_criteria
        )

        return {
            "valid": True,
            "validation": validation.to_dict(),
            "prompt": execution_prompt,
            "original_prompt": prompt,
            "completion_promise": completion_promise,
            "max_iterations": max_iterations,
            "success_criteria": validation.success_criteria,
        }

    def _build_execution_prompt(
        self,
        prompt: str,
        completion_promise: str,
        max_iterations: int,
        success_criteria: List[str],
    ) -> str:
        """Build the full execution prompt with Ralph context"""
        lines = [prompt]

        # Add completion reminder if not already in prompt
        if "<promise>" not in prompt.lower():
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("## Completion Signal")
            lines.append("")
            lines.append(
                f"When all criteria are met, output: <promise>{completion_promise}</promise>"
            )

        # Add success criteria reminder if we extracted them
        if success_criteria and "when complete" not in prompt.lower():
            lines.append("")
            lines.append("## Success Criteria")
            lines.append("")
            for criterion in success_criteria:
                lines.append(f"- {criterion}")

        return "\n".join(lines)

    async def process_output(self, output_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process output from a Ralph iteration.

        Args:
            output_data: Output containing:
                - content: The agent's output
                - iteration: Current iteration number
                - files_changed: List of changed files

        Returns:
            Processed output with completion status
        """
        content = output_data.get("content", "")

        # Increment iteration counter at start of each process_output call
        self._current_iteration += 1
        iteration = self._current_iteration

        # Check for completion signal
        is_complete, promise_text = self.validator.extract_completion_signal(content)

        # Check for stuck patterns
        is_stuck = self._check_stuck_patterns(content)

        # Record iteration state
        iteration_record = {
            "iteration": iteration,
            "success": is_complete,
            "stuck": is_stuck,
            "promise_text": promise_text,
            "content_length": len(content),
            "files_changed": output_data.get("files_changed", []),
        }

        # Extract summary from content (first substantial line or paragraph)
        summary = self._extract_summary(content)
        iteration_record["summary"] = summary

        self._iteration_history.append(iteration_record)

        # Determine completion status
        if is_complete:
            self._is_complete = True
            self._completion_reason = f"Promise signal received: {promise_text}"
        elif is_stuck:
            self._is_complete = True
            self._completion_reason = "Task appears stuck, stopping iterations"

        return {
            "success": is_complete,
            "complete": self._is_complete,
            "completion_reason": self._completion_reason,
            "promise_text": promise_text,
            "stuck": is_stuck,
            "iteration": iteration,
            "iteration_history": self._iteration_history,
            "summary": summary,
            "content": content,
        }

    def _check_stuck_patterns(self, content: str) -> bool:
        """Check if output indicates the task is stuck"""
        content_lower = content.lower()
        for pattern in self.ralph_config.stuck_patterns:
            if re.search(pattern, content_lower):
                logger.warning(f"Stuck pattern detected: {pattern}")
                return True
        return False

    def _extract_summary(self, content: str) -> str:
        """Extract a brief summary from the output"""
        # Look for explicit summary markers
        summary_match = re.search(
            r"(?:summary|status|progress)[:\s]+(.+?)(?:\n\n|\n-|\n#|$)",
            content,
            re.IGNORECASE | re.DOTALL,
        )
        if summary_match:
            return summary_match.group(1).strip()[:200]

        # Otherwise use first non-empty line
        for line in content.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and len(line) > 10:
                return line[:200]

        return "No summary available"

    def should_continue(self) -> bool:
        """Check if iterations should continue"""
        if self._is_complete:
            return False

        if self._current_iteration >= self.ralph_config.max_iterations:
            self._completion_reason = "Max iterations reached"
            return False

        return True

    def reset(self) -> None:
        """Reset iteration state for a new task"""
        self._current_iteration = 0
        self._iteration_history = []
        self._is_complete = False
        self._completion_reason = None

    def get_iteration_stats(self) -> Dict[str, Any]:
        """Get statistics about the iterations"""
        return {
            "current_iteration": self._current_iteration,
            "total_iterations": len(self._iteration_history),
            "max_iterations": self.ralph_config.max_iterations,
            "is_complete": self._is_complete,
            "completion_reason": self._completion_reason,
            "successful_iterations": sum(
                1 for h in self._iteration_history if h.get("success")
            ),
        }

    def validate_output(self, output: Dict[str, Any]) -> bool:
        """Validate that iteration output meets requirements"""
        # For Ralph, we mainly care about completion status
        return output.get("complete", False) or output.get("success", False)
