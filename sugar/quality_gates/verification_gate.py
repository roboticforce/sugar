"""
Verification Gate - Self-verification before task completion (AUTO-005)

This gate ensures that tasks self-verify their completion before being
marked as complete. It integrates with the completion signal detection
(AUTO-004) and acceptance criteria (AUTO-003) to provide comprehensive
verification before allowing task completion.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class VerificationStatus(Enum):
    """Status of verification for a task"""

    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class VerificationResult:
    """Result of a single verification check"""

    check_name: str
    passed: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "check_name": self.check_name,
            "passed": self.passed,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
        }


@dataclass
class VerificationResults:
    """Aggregated verification results for a task"""

    status: VerificationStatus
    overall_passed: bool
    checks: List[VerificationResult] = field(default_factory=list)
    reason: str = ""
    verified_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "status": self.status.value,
            "overall_passed": self.overall_passed,
            "reason": self.reason,
            "checks": [check.to_dict() for check in self.checks],
            "verified_at": self.verified_at,
        }


class VerificationGate:
    """
    Self-verification gate for task completion.

    This gate performs multiple verification checks before allowing a task
    to be marked as complete:

    1. Completion Signal Verification - Checks if the task produced a valid
       completion signal (from AUTO-004)

    2. Execution Success Verification - Verifies the execution result indicates
       success

    3. Acceptance Criteria Verification - Verifies against task-specific
       acceptance criteria (from AUTO-003)

    4. Output Validity Verification - Checks that the task produced valid output

    5. Quality Gate Consistency - Ensures no quality gate failures occurred
    """

    def __init__(self, config: dict):
        """
        Initialize verification gate.

        Args:
            config: Full Sugar configuration dictionary
        """
        self.config = config
        self.gates_config = config.get("quality_gates", {})
        self.verification_config = self.gates_config.get("verification", {})

        # Default enabled for verification
        self.enabled = self.verification_config.get("enabled", True)

        # Configurable requirements
        self.require_completion_signal = self.verification_config.get(
            "require_completion_signal", False
        )
        self.require_explicit_success = self.verification_config.get(
            "require_explicit_success", True
        )
        self.require_acceptance_criteria = self.verification_config.get(
            "require_acceptance_criteria", False
        )
        self.strict_mode = self.verification_config.get("strict_mode", False)

        logger.debug(f"VerificationGate initialized: enabled={self.enabled}")

    def is_enabled(self) -> bool:
        """Check if verification gate is enabled"""
        return self.enabled

    async def verify_task_completion(
        self,
        work_item: Dict[str, Any],
        execution_result: Dict[str, Any],
        criteria_verifier=None,
        task_type_manager=None,
    ) -> Tuple[bool, VerificationResults]:
        """
        Verify that a task can be marked as complete.

        This is the main verification method that runs all verification checks
        and determines if the task should be allowed to complete.

        Args:
            work_item: The work item being verified
            execution_result: The result from task execution
            criteria_verifier: Optional SuccessCriteriaVerifier for acceptance criteria
            task_type_manager: Optional TaskTypeManager for default criteria

        Returns:
            Tuple of (can_complete, verification_results)
        """
        if not self.is_enabled():
            logger.debug("Verification gate disabled - allowing completion")
            return True, VerificationResults(
                status=VerificationStatus.SKIPPED,
                overall_passed=True,
                reason="Verification gate disabled",
            )

        task_id = work_item.get("id", "unknown")
        logger.info(f"Running verification checks for task {task_id}")

        checks: List[VerificationResult] = []
        failed_checks: List[str] = []

        # Check 1: Execution Success Verification
        exec_check = self._verify_execution_success(execution_result)
        checks.append(exec_check)
        if not exec_check.passed and self.require_explicit_success:
            failed_checks.append(exec_check.check_name)

        # Check 2: Completion Signal Verification
        signal_check = self._verify_completion_signal(execution_result)
        checks.append(signal_check)
        if not signal_check.passed and self.require_completion_signal:
            failed_checks.append(signal_check.check_name)

        # Check 3: Output Validity Verification
        output_check = self._verify_output_validity(execution_result)
        checks.append(output_check)
        if not output_check.passed and self.strict_mode:
            failed_checks.append(output_check.check_name)

        # Check 4: Acceptance Criteria Verification (if configured)
        if criteria_verifier and (
            work_item.get("acceptance_criteria") or self.require_acceptance_criteria
        ):
            criteria_check = await self._verify_acceptance_criteria(
                work_item, criteria_verifier, task_type_manager
            )
            checks.append(criteria_check)
            if not criteria_check.passed and self.require_acceptance_criteria:
                failed_checks.append(criteria_check.check_name)

        # Check 5: Quality Gate Consistency
        gate_check = self._verify_quality_gate_consistency(work_item, execution_result)
        checks.append(gate_check)
        if not gate_check.passed:
            failed_checks.append(gate_check.check_name)

        # Determine overall result
        all_passed = len(failed_checks) == 0

        if all_passed:
            status = VerificationStatus.VERIFIED
            reason = f"All {len(checks)} verification checks passed"
            logger.info(f"Verification passed for task {task_id}: {reason}")
        else:
            status = VerificationStatus.FAILED
            reason = f"Failed checks: {', '.join(failed_checks)}"
            logger.warning(f"Verification failed for task {task_id}: {reason}")

        return all_passed, VerificationResults(
            status=status,
            overall_passed=all_passed,
            checks=checks,
            reason=reason,
        )

    def _verify_execution_success(
        self, execution_result: Dict[str, Any]
    ) -> VerificationResult:
        """
        Verify that the execution result indicates success.

        Args:
            execution_result: The execution result dictionary

        Returns:
            VerificationResult for this check
        """
        success = execution_result.get("success", False)
        error = execution_result.get("error")

        if success:
            return VerificationResult(
                check_name="execution_success",
                passed=True,
                message="Execution completed successfully",
                details={"success": True},
            )
        else:
            return VerificationResult(
                check_name="execution_success",
                passed=False,
                message=f"Execution did not report success: {error or 'Unknown reason'}",
                details={"success": False, "error": error},
            )

    def _verify_completion_signal(
        self, execution_result: Dict[str, Any]
    ) -> VerificationResult:
        """
        Verify that a completion signal was detected.

        This checks for completion signals from AUTO-004:
        - <promise>TEXT</promise>
        - <complete>TEXT</complete>
        - <done>TEXT</done>
        - TASK_COMPLETE: description

        Args:
            execution_result: The execution result dictionary

        Returns:
            VerificationResult for this check
        """
        completion_detected = execution_result.get("completion_detected", False)
        completion_signal = execution_result.get("completion_signal")
        completion_type = execution_result.get("completion_type")

        if completion_detected and completion_signal:
            return VerificationResult(
                check_name="completion_signal",
                passed=True,
                message=f"Completion signal detected: {completion_type}",
                details={
                    "detected": True,
                    "signal_type": completion_type,
                    "signal_details": completion_signal,
                },
            )
        else:
            return VerificationResult(
                check_name="completion_signal",
                passed=False,
                message="No completion signal detected in output",
                details={"detected": False},
            )

    def _verify_output_validity(
        self, execution_result: Dict[str, Any]
    ) -> VerificationResult:
        """
        Verify that the task produced valid output.

        Checks for:
        - Non-empty output or content
        - Presence of actions taken
        - Valid summary

        Args:
            execution_result: The execution result dictionary

        Returns:
            VerificationResult for this check
        """
        output = execution_result.get("output", "") or execution_result.get(
            "content", ""
        )
        actions = execution_result.get("actions_taken", [])
        summary = execution_result.get("summary", "")
        files_changed = execution_result.get("files_changed", [])

        # At least one of these should be present
        has_output = bool(output and len(output.strip()) > 0)
        has_actions = bool(actions and len(actions) > 0)
        has_summary = bool(summary and len(summary.strip()) > 0)
        has_files = bool(files_changed and len(files_changed) > 0)

        valid = has_output or has_actions or has_summary or has_files

        details = {
            "has_output": has_output,
            "has_actions": has_actions,
            "has_summary": has_summary,
            "has_files_changed": has_files,
            "output_length": len(output) if output else 0,
            "actions_count": len(actions) if actions else 0,
        }

        if valid:
            return VerificationResult(
                check_name="output_validity",
                passed=True,
                message="Task produced valid output",
                details=details,
            )
        else:
            return VerificationResult(
                check_name="output_validity",
                passed=False,
                message="Task did not produce any valid output",
                details=details,
            )

    async def _verify_acceptance_criteria(
        self,
        work_item: Dict[str, Any],
        criteria_verifier,
        task_type_manager=None,
    ) -> VerificationResult:
        """
        Verify that acceptance criteria are met.

        Uses the SuccessCriteriaVerifier from AUTO-003 to verify
        task-specific acceptance criteria.

        Args:
            work_item: The work item with acceptance_criteria field
            criteria_verifier: SuccessCriteriaVerifier instance
            task_type_manager: Optional TaskTypeManager for default criteria

        Returns:
            VerificationResult for this check
        """
        try:
            all_verified, verified_criteria = (
                await criteria_verifier.verify_task_acceptance_criteria(
                    work_item, task_type_manager
                )
            )

            if all_verified:
                return VerificationResult(
                    check_name="acceptance_criteria",
                    passed=True,
                    message=f"All {len(verified_criteria)} acceptance criteria verified",
                    details={
                        "total_criteria": len(verified_criteria),
                        "all_verified": True,
                        "criteria": [c.to_dict() for c in verified_criteria],
                    },
                )
            else:
                failed = [c for c in verified_criteria if not c.verified]
                return VerificationResult(
                    check_name="acceptance_criteria",
                    passed=False,
                    message=f"{len(failed)} acceptance criteria failed verification",
                    details={
                        "total_criteria": len(verified_criteria),
                        "failed_count": len(failed),
                        "all_verified": False,
                        "failed_criteria": [c.to_dict() for c in failed],
                    },
                )

        except Exception as e:
            logger.error(f"Error verifying acceptance criteria: {e}")
            return VerificationResult(
                check_name="acceptance_criteria",
                passed=False,
                message=f"Error verifying acceptance criteria: {e}",
                details={"error": str(e)},
            )

    def _verify_quality_gate_consistency(
        self,
        work_item: Dict[str, Any],
        execution_result: Dict[str, Any],
    ) -> VerificationResult:
        """
        Verify that no quality gate failures occurred.

        Checks for any quality gate failure flags in the work item
        or execution result.

        Args:
            work_item: The work item dictionary
            execution_result: The execution result dictionary

        Returns:
            VerificationResult for this check
        """
        # Check for quality gate failures in work item
        gate_status = work_item.get("quality_gate_status")
        gate_reason = work_item.get("quality_gate_reason")

        # Check for gate failures in execution result
        result_gate_status = execution_result.get("quality_gate_status")

        if gate_status == "failed" or result_gate_status == "failed":
            return VerificationResult(
                check_name="quality_gate_consistency",
                passed=False,
                message=f"Quality gate previously failed: {gate_reason or 'Unknown reason'}",
                details={
                    "work_item_gate_status": gate_status,
                    "result_gate_status": result_gate_status,
                    "gate_reason": gate_reason,
                },
            )

        return VerificationResult(
            check_name="quality_gate_consistency",
            passed=True,
            message="No quality gate failures detected",
            details={
                "work_item_gate_status": gate_status,
                "result_gate_status": result_gate_status,
            },
        )

    def should_require_verification(self, work_item: Dict[str, Any]) -> bool:
        """
        Determine if a work item requires verification before completion.

        Args:
            work_item: The work item to check

        Returns:
            True if verification is required
        """
        # Check explicit flag on work item
        if work_item.get("verification_required"):
            return True

        # Check task type
        task_type = work_item.get("type", "")
        high_risk_types = self.verification_config.get(
            "high_risk_task_types",
            ["bug_fix", "feature", "refactor"],
        )
        if task_type in high_risk_types:
            return True

        # Check priority - higher priority tasks require verification
        priority = work_item.get("priority", 3)
        priority_threshold = self.verification_config.get("priority_threshold", 4)
        if priority >= priority_threshold:
            return True

        # Check if task has acceptance criteria
        if work_item.get("acceptance_criteria"):
            return True

        # Default based on config
        return self.verification_config.get("require_all", False)

    def to_dict(self) -> dict:
        """Serialize gate configuration to dictionary"""
        return {
            "enabled": self.enabled,
            "require_completion_signal": self.require_completion_signal,
            "require_explicit_success": self.require_explicit_success,
            "require_acceptance_criteria": self.require_acceptance_criteria,
            "strict_mode": self.strict_mode,
        }
