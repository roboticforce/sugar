"""
Quality Gates Coordinator - Orchestrates all quality gate checks

Coordinates test validation, success criteria verification, and truth enforcement
before allowing task completion.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

from .test_validator import TestExecutionValidator, TestExecutionResult
from .success_criteria import SuccessCriteriaVerifier, SuccessCriterion
from .truth_enforcer import TruthEnforcer
from .evidence import EvidenceCollector
from .functional_verifier import FunctionalVerifier
from .preflight_checks import PreFlightChecker
from .failure_handler import VerificationFailureHandler
from .diff_validator import DiffValidator
from .verification_gate import VerificationGate, VerificationResults, VerificationStatus

logger = logging.getLogger(__name__)


class QualityGateResult:
    """Result of quality gate checks"""

    def __init__(
        self,
        can_complete: bool,
        reason: str,
        tests_passed: bool = False,
        criteria_verified: bool = False,
        claims_proven: bool = False,
        preflight_passed: bool = True,
        functional_verified: bool = True,
        diff_validated: bool = True,
        self_verified: bool = True,
        verification_results: Optional[VerificationResults] = None,
        evidence_collector: Optional[EvidenceCollector] = None,
        failure_report: Optional[Any] = None,
    ):
        self.can_complete = can_complete
        self.reason = reason
        self.tests_passed = tests_passed
        self.criteria_verified = criteria_verified
        self.claims_proven = claims_proven
        self.preflight_passed = preflight_passed
        self.functional_verified = functional_verified
        self.diff_validated = diff_validated
        self.self_verified = self_verified
        self.verification_results = verification_results
        self.evidence_collector = evidence_collector
        self.failure_report = failure_report

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        result = {
            "can_complete": self.can_complete,
            "reason": self.reason,
            "tests_passed": self.tests_passed,
            "criteria_verified": self.criteria_verified,
            "claims_proven": self.claims_proven,
            "preflight_passed": self.preflight_passed,
            "functional_verified": self.functional_verified,
            "diff_validated": self.diff_validated,
            "self_verified": self.self_verified,
        }

        if self.verification_results:
            result["verification_results"] = self.verification_results.to_dict()

        if self.evidence_collector:
            result["evidence_summary"] = self.evidence_collector.get_evidence_summary()
            result["evidence_urls"] = self.evidence_collector.generate_evidence_urls()

        if self.failure_report:
            result["failure_report"] = (
                self.failure_report.to_dict()
                if hasattr(self.failure_report, "to_dict")
                else str(self.failure_report)
            )

        return result


class QualityGatesCoordinator:
    """
    Coordinates all quality gate checks before task completion
    """

    def __init__(self, config: dict):
        """
        Initialize quality gates coordinator

        Args:
            config: Full Sugar configuration dictionary
        """
        self.config = config
        self.gates_config = config.get("quality_gates", {})
        self.enabled = self.gates_config.get("enabled", False)

        # Initialize Phase 1 components
        self.test_validator = TestExecutionValidator(config)
        self.criteria_verifier = SuccessCriteriaVerifier(config)
        self.truth_enforcer = TruthEnforcer(config)

        # Initialize Phase 2 components
        self.functional_verifier = FunctionalVerifier(config)
        self.preflight_checker = PreFlightChecker(config)

        # Initialize Phase 3 components
        self.failure_handler = VerificationFailureHandler(config)
        self.diff_validator = DiffValidator(config)

        # Initialize Phase 4 components - Self-verification (AUTO-005)
        self.verification_gate = VerificationGate(config)

    def is_enabled(self) -> bool:
        """Check if quality gates are enabled"""
        return self.enabled

    async def validate_before_commit(
        self,
        task: Dict[str, Any],
        changed_files: List[str],
        claims: List[str] = None,
    ) -> Tuple[bool, QualityGateResult]:
        """
        Run all quality gate validations before allowing a git commit

        Args:
            task: Task dictionary
            changed_files: List of files that were changed
            claims: List of claims being made (e.g., "all tests pass")

        Returns:
            Tuple of (can_commit, quality_gate_result)
        """
        if not self.is_enabled():
            logger.debug("Quality gates disabled - allowing commit")
            return True, QualityGateResult(
                can_complete=True, reason="Quality gates disabled"
            )

        task_id = task.get("id", "unknown")
        logger.info(f"🔒 Running quality gates for task {task_id}")

        # Initialize evidence collector
        evidence_collector = EvidenceCollector(task_id)

        # Phase 1: Test Execution Validation (Feature 1)
        test_result = None
        tests_passed = False

        if self.test_validator.is_enabled():
            logger.info("📝 Phase 1: Validating test execution")
            can_commit, test_result, message = (
                await self.test_validator.validate_tests_before_commit(
                    task, changed_files
                )
            )

            if test_result:
                # Store test evidence
                evidence_collector.add_test_evidence(
                    command=test_result.command,
                    exit_code=test_result.exit_code,
                    stdout_path=f".sugar/test_evidence/{task_id}.txt",
                    failures=test_result.failures,
                    errors=test_result.errors,
                    pending=test_result.pending,
                    examples=test_result.examples,
                    duration=test_result.duration,
                )

                tests_passed = test_result.passed

            if not can_commit:
                logger.error(f"❌ Quality gate failed: {message}")
                return False, QualityGateResult(
                    can_complete=False,
                    reason=message,
                    tests_passed=False,
                    evidence_collector=evidence_collector,
                )

            logger.info(f"✅ Phase 1 passed: {message}")

        # Phase 2: Success Criteria Verification (Feature 3)
        criteria_verified = False
        success_criteria = task.get("success_criteria", [])

        if success_criteria and self.criteria_verifier:
            logger.info("📋 Phase 2: Verifying success criteria")
            all_verified, verified_criteria = (
                await self.criteria_verifier.verify_all_criteria(success_criteria)
            )

            # Store success criteria evidence
            for criterion in verified_criteria:
                evidence_collector.add_success_criteria_evidence(
                    criterion_id=f"criterion_{criterion.type}",
                    criterion_type=criterion.type,
                    expected=criterion.expected,
                    actual=criterion.actual,
                )

            criteria_verified = all_verified

            if not all_verified:
                failed = [c for c in verified_criteria if not c.verified]
                message = f"Success criteria not met: {len(failed)} criteria failed"
                logger.error(f"❌ Quality gate failed: {message}")
                return False, QualityGateResult(
                    can_complete=False,
                    reason=message,
                    tests_passed=tests_passed,
                    criteria_verified=False,
                    evidence_collector=evidence_collector,
                )

            logger.info(
                f"✅ Phase 2 passed: All {len(verified_criteria)} criteria verified"
            )

        # Phase 3: Truth Enforcement (Feature 8)
        claims_proven = False

        if claims and self.truth_enforcer.is_enabled():
            logger.info("🔍 Phase 3: Verifying claims with evidence")
            can_complete, reason = self.truth_enforcer.can_complete_task(
                claims, evidence_collector
            )

            claims_proven = can_complete

            if not can_complete:
                logger.error(f"❌ Quality gate failed: {reason}")

                # Generate unproven claims report
                report = self.truth_enforcer.get_unproven_claims_report(
                    claims, evidence_collector
                )
                logger.debug(f"Unproven claims report:\n{report}")

                return False, QualityGateResult(
                    can_complete=False,
                    reason=reason,
                    tests_passed=tests_passed,
                    criteria_verified=criteria_verified,
                    claims_proven=False,
                    evidence_collector=evidence_collector,
                )

            logger.info(f"✅ Phase 3 passed: All claims proven with evidence")

        # Save evidence report
        evidence_path = evidence_collector.save_evidence_report()
        logger.info(f"📄 Evidence report saved: {evidence_path}")

        # All gates passed
        logger.info("✅ All quality gates passed - allowing commit")
        return True, QualityGateResult(
            can_complete=True,
            reason="All quality gates passed",
            tests_passed=tests_passed,
            criteria_verified=criteria_verified,
            claims_proven=claims_proven,
            evidence_collector=evidence_collector,
        )

    async def validate_before_completion(
        self,
        work_item: Dict[str, Any],
        execution_result: Dict[str, Any],
        task_type_manager=None,
    ) -> Tuple[bool, QualityGateResult]:
        """
        Run self-verification before allowing task completion (AUTO-005).

        This method integrates the VerificationGate to ensure tasks
        self-verify before being marked as complete.

        Args:
            work_item: The work item being completed
            execution_result: The result from task execution
            task_type_manager: Optional TaskTypeManager for default criteria

        Returns:
            Tuple of (can_complete, quality_gate_result)
        """
        if not self.is_enabled():
            logger.debug("Quality gates disabled - allowing completion")
            return True, QualityGateResult(
                can_complete=True,
                reason="Quality gates disabled",
            )

        task_id = work_item.get("id", "unknown")
        logger.info(f"Running self-verification for task {task_id}")

        # Check if verification is required for this work item
        if not self.verification_gate.should_require_verification(work_item):
            logger.debug(f"Verification not required for task {task_id}")
            return True, QualityGateResult(
                can_complete=True,
                reason="Verification not required for this task type",
                self_verified=True,
            )

        # Run verification gate
        can_complete, verification_results = (
            await self.verification_gate.verify_task_completion(
                work_item=work_item,
                execution_result=execution_result,
                criteria_verifier=self.criteria_verifier,
                task_type_manager=task_type_manager,
            )
        )

        if can_complete:
            logger.info(f"✅ Self-verification passed for task {task_id}")
            return True, QualityGateResult(
                can_complete=True,
                reason=verification_results.reason,
                self_verified=True,
                verification_results=verification_results,
            )
        else:
            logger.warning(
                f"❌ Self-verification failed for task {task_id}: {verification_results.reason}"
            )
            return False, QualityGateResult(
                can_complete=False,
                reason=verification_results.reason,
                self_verified=False,
                verification_results=verification_results,
            )

    def get_commit_message_footer(self, quality_gate_result: QualityGateResult) -> str:
        """
        Generate commit message footer with quality gate evidence

        Args:
            quality_gate_result: Result from quality gate validation

        Returns:
            String to append to commit message
        """
        if not quality_gate_result.evidence_collector:
            return ""

        summary = quality_gate_result.evidence_collector.get_evidence_summary()

        footer = "\n\nQuality Gates:\n"
        footer += f"- Tests: {'✅ PASSED' if quality_gate_result.tests_passed else '❌ SKIPPED'}\n"
        footer += f"- Success Criteria: {'✅ VERIFIED' if quality_gate_result.criteria_verified else '⏭️ NONE'}\n"
        footer += f"- Claims Proven: {'✅ YES' if quality_gate_result.claims_proven else '⏭️ NONE'}\n"
        footer += f"- Self-Verified: {'✅ YES' if quality_gate_result.self_verified else '❌ NO'}\n"
        footer += f"- Total Evidence: {summary.get('total_evidence_items', 0)} items\n"

        evidence_urls = quality_gate_result.evidence_collector.generate_evidence_urls()
        if evidence_urls:
            footer += f"\nEvidence:\n"
            for url in evidence_urls[:3]:  # First 3 URLs
                footer += f"- {url}\n"

        return footer
