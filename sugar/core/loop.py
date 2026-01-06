"""
Sugar Core Loop - The heart of autonomous development
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List
import yaml
from pathlib import Path

from ..discovery.error_monitor import ErrorLogMonitor
from ..discovery.github_watcher import GitHubWatcher
from ..discovery.code_quality import CodeQualityScanner
from ..discovery.test_coverage import TestCoverageAnalyzer
from ..executor.claude_wrapper import ClaudeWrapper
from ..executor.agent_sdk_executor import AgentSDKExecutor
from ..storage.work_queue import WorkQueue
from ..learning.feedback_processor import FeedbackProcessor
from ..learning.adaptive_scheduler import AdaptiveScheduler
from ..utils.git_operations import GitOperations
from ..workflow.orchestrator import WorkflowOrchestrator
from ..__version__ import get_version_info

logger = logging.getLogger(__name__)


class SugarLoop:
    """Sugar - AI-powered autonomous development system - Main orchestrator"""

    def __init__(self, config_path: str = ".sugar/config.yaml"):
        self.config = self._load_config(config_path)
        self.running = False
        self.work_queue = WorkQueue(self.config["sugar"]["storage"]["database"])

        # Initialize executor based on config
        self.executor = self._create_executor()

        # Initialize learning components
        self.feedback_processor = FeedbackProcessor(self.work_queue)
        self.adaptive_scheduler = AdaptiveScheduler(
            self.work_queue, self.feedback_processor
        )

        # Initialize git operations
        self.git_ops = GitOperations()

        # Initialize workflow orchestrator
        self.workflow_orchestrator = WorkflowOrchestrator(
            self.config, self.git_ops, self.work_queue
        )

        # Initialize work discovery modules
        self.discovery_modules = []

        # Error log monitoring
        if self.config["sugar"]["discovery"]["error_logs"]["enabled"]:
            error_monitor = ErrorLogMonitor(
                self.config["sugar"]["discovery"]["error_logs"]
            )
            error_monitor.work_queue = self.work_queue  # Pass work_queue reference
            self.discovery_modules.append(error_monitor)

        # GitHub integration
        if self.config["sugar"]["discovery"].get("github", {}).get("enabled", False):
            self.discovery_modules.append(
                GitHubWatcher(self.config["sugar"]["discovery"]["github"])
            )

        # Code quality scanning
        if (
            self.config["sugar"]["discovery"]
            .get("code_quality", {})
            .get("enabled", True)
        ):
            quality_config = self.config["sugar"]["discovery"].get("code_quality", {})
            quality_config.setdefault("root_path", ".")
            self.discovery_modules.append(CodeQualityScanner(quality_config))

        # Test coverage analysis
        if (
            self.config["sugar"]["discovery"]
            .get("test_coverage", {})
            .get("enabled", True)
        ):
            coverage_config = self.config["sugar"]["discovery"].get("test_coverage", {})
            coverage_config.setdefault("root_path", ".")
            self.discovery_modules.append(TestCoverageAnalyzer(coverage_config))

    def _load_config(self, config_path: str) -> dict:
        """Load Sugar configuration"""
        try:
            with open(config_path, "r") as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.error(f"Config file not found: {config_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML config: {e}")
            raise

    def _create_executor(self):
        """Create the appropriate executor based on configuration"""
        claude_config = self.config["sugar"]["claude"].copy()
        claude_config["dry_run"] = self.config["sugar"]["dry_run"]
        claude_config["database_path"] = self.config["sugar"]["storage"]["database"]

        # Check which executor to use (default to sdk for v3.0+)
        executor_type = claude_config.get("executor", "sdk")

        if executor_type == "sdk":
            logger.info("🚀 Using Agent SDK executor (v3.0)")
            return AgentSDKExecutor(claude_config)
        elif executor_type == "legacy":
            logger.info("📦 Using legacy Claude wrapper executor")
            return ClaudeWrapper(claude_config)
        else:
            logger.warning(
                f"Unknown executor type '{executor_type}', defaulting to SDK"
            )
            return AgentSDKExecutor(claude_config)

    async def start(self):
        """Start the autonomous loop"""
        logger.info(f"🤖 Starting {get_version_info()}")

        # Initialize storage
        await self.work_queue.initialize()

        self.running = True

        # Start main loop
        await self._main_loop()

    async def start_with_shutdown(self, shutdown_event):
        """Start the autonomous loop with shutdown event monitoring"""
        logger.info(f"🤖 Starting {get_version_info()}")

        # Initialize storage
        await self.work_queue.initialize()

        self.running = True

        # Start main loop with shutdown monitoring
        await self._main_loop_with_shutdown(shutdown_event)

    async def stop(self):
        """Stop the autonomous loop gracefully"""
        logger.info("🛑 Stopping Sugar...")
        self.running = False

    async def _main_loop(self):
        """Main autonomous development loop"""
        loop_interval = self.config["sugar"]["loop_interval"]

        while self.running:
            try:
                cycle_start = datetime.now(timezone.utc)
                logger.info(f"🔄 Starting Sugar cycle at {cycle_start}")

                # Phase 1: Discover new work
                await self._discover_work()

                # Phase 2: Execute highest priority work
                await self._execute_work()

                # Phase 3: Process results and learn
                await self._process_feedback()

                # Wait for next cycle
                cycle_duration = (
                    datetime.now(timezone.utc) - cycle_start
                ).total_seconds()
                sleep_time = max(0, loop_interval - cycle_duration)

                logger.info(
                    f"✅ Cycle completed in {cycle_duration:.1f}s, sleeping {sleep_time:.1f}s"
                )
                await asyncio.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error

    async def _main_loop_with_shutdown(self, shutdown_event):
        """Main autonomous development loop with shutdown event monitoring"""
        loop_interval = self.config["sugar"]["loop_interval"]

        while self.running and not shutdown_event.is_set():
            try:
                cycle_start = datetime.now(timezone.utc)
                logger.info(f"🔄 Starting Sugar cycle at {cycle_start}")

                # Phase 1: Discover new work
                await self._discover_work()

                # Check for shutdown before execution
                if shutdown_event.is_set():
                    logger.info("🛑 Shutdown requested, finishing current cycle...")
                    break

                # Phase 2: Execute highest priority work
                await self._execute_work(shutdown_event)

                # Check for shutdown after execution
                if shutdown_event.is_set():
                    logger.info("🛑 Shutdown requested, finishing current cycle...")
                    break

                # Phase 3: Process results and learn
                await self._process_feedback()

                # Wait for next cycle or shutdown
                cycle_duration = (
                    datetime.now(timezone.utc) - cycle_start
                ).total_seconds()
                sleep_time = max(0, loop_interval - cycle_duration)

                logger.info(
                    f"✅ Cycle completed in {cycle_duration:.1f}s, sleeping {sleep_time:.1f}s"
                )

                # Sleep with frequent shutdown checks (check every 1 second)
                remaining_sleep = sleep_time
                while remaining_sleep > 0 and not shutdown_event.is_set():
                    sleep_chunk = min(1.0, remaining_sleep)  # Sleep in 1-second chunks
                    try:
                        await asyncio.wait_for(
                            shutdown_event.wait(), timeout=sleep_chunk
                        )
                        # If we get here, shutdown was requested
                        logger.info("🛑 Shutdown detected in sleep loop, exiting...")
                        return  # Exit the main loop immediately
                    except asyncio.TimeoutError:
                        # Normal timeout, continue sleeping
                        remaining_sleep -= sleep_chunk
                        # Check if shutdown was set during the chunk
                        if shutdown_event.is_set():
                            logger.info(
                                "🛑 Shutdown detected after sleep chunk, exiting..."
                            )
                            return

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                # Even during error recovery, check for shutdown frequently
                remaining_recovery = 60.0  # 60 second error recovery
                while remaining_recovery > 0 and not shutdown_event.is_set():
                    recovery_chunk = min(1.0, remaining_recovery)
                    try:
                        await asyncio.wait_for(
                            shutdown_event.wait(), timeout=recovery_chunk
                        )
                        logger.info("🛑 Shutdown requested during error recovery")
                        return  # Exit immediately
                    except asyncio.TimeoutError:
                        remaining_recovery -= recovery_chunk

    async def _discover_work(self):
        """Discover new work from all enabled sources"""
        logger.debug("🔍 Discovering work...")

        discovered_work = []

        for module in self.discovery_modules:
            try:
                work_items = await module.discover()
                discovered_work.extend(work_items)
                logger.debug(
                    f"📋 {module.__class__.__name__} found {len(work_items)} work items"
                )
            except Exception as e:
                logger.error(f"Error in {module.__class__.__name__}: {e}")

        # Add discovered work to queue (with deduplication)
        added_count = 0
        skipped_count = 0

        for work_item in discovered_work:
            source_file = work_item.get("source_file", "")

            # Smart deduplication: different logic for different sources
            should_skip = False
            if source_file:
                if work_item.get("source") == "github_watcher":
                    # For GitHub issues, only skip if pending/in_progress (not completed)
                    should_skip = await self.work_queue.work_exists(
                        source_file, exclude_statuses=["failed", "completed"]
                    )
                else:
                    # For other sources, use default logic (skip all except failed)
                    should_skip = await self.work_queue.work_exists(source_file)

            if should_skip:
                skipped_count += 1
                logger.debug(f"⏭️ Skipping duplicate work item: {work_item['title']}")
                continue

            await self.work_queue.add_work(work_item)
            added_count += 1

        if added_count > 0:
            logger.info(f"➕ Added {added_count} new work items to queue")
        if skipped_count > 0:
            logger.info(f"⏭️ Skipped {skipped_count} duplicate work items")
        if added_count == 0 and skipped_count == 0:
            logger.info("No new work discovered this cycle")

    async def _execute_work(self, shutdown_event=None):
        """Execute the highest priority work item"""
        max_concurrent = self.config["sugar"]["max_concurrent_work"]

        for _ in range(max_concurrent):
            # Check for shutdown before starting new work
            if shutdown_event and shutdown_event.is_set():
                logger.info("🛑 Shutdown requested, not starting new work")
                break

            work_item = await self.work_queue.get_next_work()
            if not work_item:
                logger.info("No work items ready for execution")
                break

            logger.info(f"⚡ Executing work [{work_item['id']}]: {work_item['title']}")

            # Prepare unified workflow (replaces GitHub-specific workflow)
            workflow = await self.workflow_orchestrator.prepare_work_execution(
                work_item
            )

            # Track execution timing
            from datetime import datetime

            start_time = datetime.now(timezone.utc)
            execution_time = 0.0

            try:
                # Execute with Claude Code
                result = await self.executor.execute_work(work_item)

                # Calculate execution time
                execution_time = (
                    datetime.now(timezone.utc) - start_time
                ).total_seconds()

                # Check if execution actually succeeded
                # Default to False as fail-safe - all executors should set success explicitly
                if "success" not in result:
                    logger.warning(
                        f"⚠️ Missing 'success' field in result for [{work_item['id']}]"
                    )
                execution_success = result.get("success", False)
                if not execution_success:
                    error_msg = result.get("error") or result.get(
                        "summary", "Execution returned failure"
                    )
                    logger.warning(
                        f"⚠️ Task execution failed [{work_item['id']}]: {error_msg}"
                    )
                    await self.work_queue.fail_work(
                        work_item["id"], error_msg, execution_time=execution_time
                    )
                    await self._handle_failed_workflow(work_item, workflow, error_msg)
                    return

                # Complete unified workflow (commit, branch, PR, issues)
                workflow_success = (
                    await self.workflow_orchestrator.complete_work_execution(
                        work_item, workflow, result
                    )
                )

                if not workflow_success:
                    logger.warning(
                        f"⚠️ Workflow completion had issues for [{work_item['id']}]"
                    )

                # Update work item with result
                await self.work_queue.complete_work(work_item["id"], result)

                # Handle GitHub issue updates if needed (for GitHub-sourced work)
                if work_item.get("source_type") == "github_watcher":
                    await self._update_github_issue(work_item, result)

                logger.info(
                    f"✅ Work completed [{work_item['id']}]: {work_item['title']}"
                )

            except Exception as e:
                # Calculate execution time even on failure
                execution_time = (
                    datetime.now(timezone.utc) - start_time
                ).total_seconds()

                logger.error(f"❌ Work execution failed [{work_item['id']}]: {e}")
                await self.work_queue.fail_work(
                    work_item["id"], str(e), execution_time=execution_time
                )

                # Handle failed workflow cleanup
                await self._handle_failed_workflow(work_item, workflow, str(e))

    async def _process_feedback(self):
        """Process execution results and learn from them"""
        try:
            # Process feedback and generate insights
            insights = await self.feedback_processor.process_feedback()

            # Apply adaptive changes based on learning
            adaptations = await self.adaptive_scheduler.adapt_system_behavior()

            # Log learning summary
            stats = await self.work_queue.get_stats()
            logger.info(
                f"📊 Queue stats: {stats['pending']} pending, "
                f"{stats['completed']} completed, {stats['failed']} failed"
            )

            if insights.get("recommendations"):
                rec_count = len(insights["recommendations"])
                logger.info(
                    f"🧠 Generated {rec_count} recommendations for system improvement"
                )

            if adaptations:
                adapt_count = len(adaptations)
                logger.info(f"🎯 Applied {adapt_count} adaptive improvements")

        except Exception as e:
            logger.error(f"Error processing feedback: {e}")

    async def _comment_on_github_issue(self, work_item: dict, result: dict):
        """Comment on GitHub issue if work item originated from GitHub"""
        try:
            # Check if this work came from GitHub
            if work_item.get("source") != "github_watcher" or not work_item.get(
                "context", {}
            ).get("github_issue"):
                return

            # Find GitHub watcher module
            github_watcher = None
            for module in self.discovery_modules:
                if isinstance(module, GitHubWatcher):
                    github_watcher = module
                    break

            if not github_watcher or not github_watcher.enabled:
                logger.debug("GitHub watcher not available for commenting")
                return

            # Extract issue details
            github_issue = work_item["context"]["github_issue"]
            issue_number = github_issue.get("number")

            if not issue_number:
                logger.warning("No issue number found in GitHub work item")
                return

            # Create comment body
            comment_body = self._format_completion_comment(work_item, result)

            # Post comment
            success = await github_watcher.comment_on_issue(issue_number, comment_body)

            if success:
                logger.info(
                    f"💬 Posted completion comment to GitHub issue #{issue_number} for task [{work_item['id']}]"
                )
            else:
                logger.warning(f"Failed to comment on GitHub issue #{issue_number}")

        except Exception as e:
            logger.error(f"Error commenting on GitHub issue: {e}")

    async def _assign_github_issue(self, work_item: dict):
        """Assign GitHub issue if work item originated from GitHub"""
        try:
            # Check if this work came from GitHub
            if work_item.get("source") != "github_watcher" or not work_item.get(
                "context", {}
            ).get("github_issue"):
                return

            # Find GitHub watcher module
            github_watcher = None
            for module in self.discovery_modules:
                if isinstance(module, GitHubWatcher):
                    github_watcher = module
                    break

            if not github_watcher or not github_watcher.enabled:
                logger.debug("GitHub watcher not available for assignment")
                return

            # Extract issue details
            github_issue = work_item["context"]["github_issue"]
            issue_number = github_issue.get("number")

            if not issue_number:
                logger.warning("No issue number found in GitHub work item")
                return

            # Assign the issue
            success = await github_watcher.assign_issue(issue_number)

            if success:
                logger.info(f"👤 Assigned GitHub issue #{issue_number} to current user")

                # Add "working on it" comment
                working_comment = "👀 Sugar is now working on this issue..."
                comment_success = await github_watcher.comment_on_issue(
                    issue_number, working_comment
                )

                if comment_success:
                    logger.info(
                        f"💬 Posted working status to GitHub issue #{issue_number}"
                    )
                else:
                    logger.debug(
                        f"Could not post working status to GitHub issue #{issue_number}"
                    )
            else:
                logger.debug(f"Could not assign GitHub issue #{issue_number}")

        except Exception as e:
            logger.error(f"Error assigning GitHub issue: {e}")

    def _format_completion_comment(self, work_item: dict, result: dict) -> str:
        """Format concise completion comment for GitHub issue"""
        task_id = work_item.get("id", "unknown")
        task_title = work_item.get("title", "Unknown task")

        # Determine the actual work type and create appropriate header
        actual_work_type = self._determine_actual_work_type(result, work_item)

        # Create header based on actual work type
        header_map = {
            "verification": "🔍 Issue Verified",
            "documentation_verification": "📋 Documentation Verified",
            "bug_fix": "🐛 Bug Fixed",
            "feature": "✨ Feature Added",
            "update": "📝 Updated",
            "documentation": "📋 Documentation Updated",
        }

        header = header_map.get(actual_work_type, "✅ Issue Resolved")

        lines = [f"## {header} (`{task_id}`)", ""]

        # Add concise summary from Claude's response or actions
        summary = self._extract_concise_summary(result)

        # Get meaningful actions
        meaningful_actions = []
        if result.get("actions_taken"):
            key_actions = [
                action.lstrip("✅✓ ").strip() for action in result["actions_taken"][:8]
            ]  # Get up to 8 actions
            meaningful_actions = [
                action
                for action in key_actions
                if action and action != "Task completed successfully"
            ]  # Don't filter by summary yet

        # Show summary if it's different from actions
        if summary and summary != "Task completed successfully":
            # Check if summary is substantially different from first action
            first_action = meaningful_actions[0] if meaningful_actions else ""
            if not first_action or not self._are_similar_strings(summary, first_action):
                lines.extend([summary, ""])

        # Add actions if we have meaningful ones (prioritize detailed explanations)
        actions_added = False
        if meaningful_actions:
            # Filter out only the most generic messages, be less aggressive
            detailed_actions = []
            for action in meaningful_actions:
                # Skip only very generic messages
                if (
                    any(
                        generic in action.lower()
                        for generic in [
                            "task completed successfully",
                            "work completed",
                            "task done",
                        ]
                    )
                    and len(action.strip()) < 30
                ):  # Only skip if both generic AND short
                    continue

                # For longer, detailed explanations, be much less restrictive
                if len(action.strip()) > 50:  # Detailed explanations - keep them
                    detailed_actions.append(action)
                    continue

                # For shorter actions, check similarity more carefully
                if summary and len(action.strip()) < 50:
                    # Only skip if very similar AND both are short
                    if (
                        self._are_similar_strings(action, summary)
                        and len(summary.strip()) < 50
                    ):
                        continue

                # Skip very short actions (less than 15 chars)
                if len(action.strip()) < 15:
                    continue

                detailed_actions.append(action)

            # If we have meaningful actions, show them
            if detailed_actions:
                lines.extend(
                    [
                        "**What was done:**",
                        *[f"- {action}" for action in detailed_actions],
                        "",
                    ]
                )
                actions_added = True
            # If no detailed actions but we have some original actions, show the most substantial one
            elif meaningful_actions:
                # Find the most substantial action (prioritize length and detail)
                best_action = ""
                for action in meaningful_actions:
                    if len(action.strip()) > len(best_action.strip()) and not any(
                        generic in action.lower()
                        for generic in ["task completed successfully", "work completed"]
                    ):
                        best_action = action

                if best_action and len(best_action.strip()) > 10:
                    lines.extend(["**What was done:**", f"- {best_action}", ""])
                    actions_added = True

        # Add files changed if available (most important info)
        if result.get("files_changed"):
            lines.extend(
                [
                    f"**Files updated:** {', '.join(f'`{file}`' for file in result['files_changed'])}",
                    "",
                ]
            )

        # If no meaningful actions were found, show the task context with correct type
        if not actions_added and not result.get("files_changed"):
            task_clean = task_title.replace("Address GitHub issue: ", "")
            lines.extend(
                [f"**Task:** {task_clean}", f"**Type:** {actual_work_type}", ""]
            )

        # Execution details in compact format
        exec_time = result.get("execution_time", 0)
        lines.append(
            f"*Completed in {exec_time:.1f}s by [Sugar AI](https://github.com/roboticforce/sugar)*"
        )

        return "\n".join(lines)

    def _extract_concise_summary(self, result: dict) -> str:
        """Extract a concise summary from Claude's output"""
        # Try to get Claude's actual response first
        claude_response = result.get("claude_response", "")
        output = result.get("output", "")

        # Look for Claude's key findings or conclusions
        summary_candidates = []

        # Parse Claude's response for meaningful statements
        if claude_response:
            lines = claude_response.split("\n")
            for line in lines:
                line = line.strip()
                # Look for conclusion or finding statements
                if any(
                    phrase in line.lower()
                    for phrase in [
                        "already",
                        "found",
                        "confirmed",
                        "verified",
                        "checked",
                        "analysis",
                        "shows",
                        "indicates",
                        "discovered",
                        "exists",
                        "no changes needed",
                        "requirement satisfied",
                        "properly",
                        "includes a comprehensive",
                        "author section",
                        "lines",
                    ]
                ):
                    if 20 < len(line) < 300:  # Good summary length
                        summary_candidates.append(line.rstrip("."))

                # Look for detailed explanations
                elif any(
                    phrase in line.lower()
                    for phrase in [
                        "the readme.md file includes",
                        "comprehensive author section",
                        "requesting to",
                        "ensure you add",
                        "steven leggett",
                    ]
                ):
                    if 20 < len(line) < 300:
                        summary_candidates.append(line.rstrip("."))

                # Also look for action statements
                elif any(
                    word in line.lower()
                    for word in [
                        "updated",
                        "added",
                        "created",
                        "modified",
                        "fixed",
                        "implemented",
                    ]
                ):
                    if 20 < len(line) < 300:
                        summary_candidates.append(line.rstrip("."))

        # If no Claude response, try parsing the full output
        if not summary_candidates and output:
            lines = output.split("\n")
            for line in lines:
                line = line.strip()
                # Look for key findings in output
                if any(
                    phrase in line.lower()
                    for phrase in [
                        "the readme",
                        "author section",
                        "already includes",
                        "properly listed",
                        "verified that",
                        "analysis shows",
                        "file contains",
                    ]
                ):
                    if 20 < len(line) < 300:
                        summary_candidates.append(line.rstrip("."))
                        break

        # Return the best candidate
        if summary_candidates:
            return summary_candidates[0]

        # Fallback to summary field
        summary = result.get("summary", "")
        if summary and len(summary) < 200 and summary != "Task completed successfully":
            return summary.rstrip(".")

        # Fallback to first meaningful action
        actions = result.get("actions_taken", [])
        if actions:
            first_action = actions[0].lstrip("✅✓ ").strip()
            if (
                len(first_action) < 200
                and first_action != "Task completed successfully"
            ):
                return first_action.rstrip(".")

        return ""

    def _are_similar_strings(self, str1: str, str2: str) -> bool:
        """Check if two strings are substantially similar (to avoid duplication)"""
        if not str1 or not str2:
            return False

        # Normalize strings for comparison
        s1 = str1.lower().strip()
        s2 = str2.lower().strip()

        # If one contains the other or they're very similar, consider them duplicates
        if s1 in s2 or s2 in s1:
            return True

        # Check for substantial overlap in words
        words1 = set(s1.split())
        words2 = set(s2.split())

        if len(words1) == 0 or len(words2) == 0:
            return False

        overlap = len(words1.intersection(words2))
        total_unique = len(words1.union(words2))

        # If more than 70% overlap, consider similar
        similarity = overlap / total_unique if total_unique > 0 else 0
        return similarity > 0.7

    def _determine_actual_work_type(self, result: dict, work_item: dict) -> str:
        """Determine the actual work type based on Claude's analysis and actions"""
        output = result.get("output", "")
        summary = result.get("summary", "")
        actions = result.get("actions_taken", [])
        original_type = work_item.get("type", "unknown")

        # Combine all text for analysis
        all_text = f"{output} {summary} {' '.join(actions)}".lower()

        # Check for verification/analysis patterns
        if any(
            phrase in all_text
            for phrase in [
                "already",
                "already exists",
                "already includes",
                "already proper",
                "verified that",
                "confirmed that",
                "found that",
                "checked",
                "no changes needed",
                "requirement satisfied",
                "properly listed",
                "comprehensive",
                "analysis shows",
                "reviewing the current",
            ]
        ):
            return "verification"

        # Check for actual file changes/updates
        if any(
            phrase in all_text
            for phrase in [
                "updated",
                "modified",
                "added new",
                "created",
                "implemented",
                "wrote to",
                "changed",
                "fixed",
            ]
        ) and result.get("files_changed"):
            if "bug" in all_text or "fix" in all_text or "error" in all_text:
                return "bug_fix"
            elif "feature" in all_text or "enhancement" in all_text:
                return "feature"
            else:
                return "update"

        # Check for documentation work
        if any(
            phrase in all_text
            for phrase in ["readme", "documentation", "docs", "author section"]
        ):
            if "already" in all_text or "verified" in all_text:
                return "documentation_verification"
            else:
                return "documentation"

        # Fallback to original type if we can't determine better
        return original_type

    async def _start_github_workflow(self, work_item: dict) -> dict:
        """Start GitHub workflow for issue-based work"""
        branch_info = {
            "created_branch": False,
            "branch_name": None,
            "original_branch": None,
        }

        try:
            # Check if this work came from GitHub
            if work_item.get("source") != "github_watcher" or not work_item.get(
                "context", {}
            ).get("github_issue"):
                return branch_info

            # Get GitHub configuration
            github_config = self.config["sugar"]["discovery"].get("github", {})
            workflow_config = github_config.get("workflow", {})

            if not workflow_config.get("auto_close_issues", True):
                # Just assign and comment if workflow is disabled
                await self._assign_github_issue(work_item)
                return branch_info

            # Assign GitHub issue and add working comment
            await self._assign_github_issue(work_item)

            # Handle branching workflow
            git_workflow = workflow_config.get("git_workflow", "direct_commit")
            if git_workflow == "pull_request":
                branch_info = await self._create_feature_branch(
                    work_item, workflow_config
                )

            return branch_info

        except Exception as e:
            logger.error(f"Error starting GitHub workflow: {e}")
            return branch_info

    async def _create_feature_branch(
        self, work_item: dict, workflow_config: dict
    ) -> dict:
        """Create a feature branch for the work item"""
        branch_info = {
            "created_branch": False,
            "branch_name": None,
            "original_branch": None,
        }

        try:
            # Get current branch
            original_branch = await self.git_ops.get_current_branch()
            branch_info["original_branch"] = original_branch

            # Get issue details
            github_issue = work_item["context"]["github_issue"]
            issue_number = github_issue.get("number")
            issue_title = work_item.get("title", "").replace(
                "Address GitHub issue: ", ""
            )

            # Format branch name
            branch_config = workflow_config.get("branch", {})
            pattern = branch_config.get("name_pattern", "sugar/issue-{issue_number}")
            variables = {
                "issue_number": issue_number,
                "issue_title_slug": self.git_ops.slugify_title(issue_title),
            }
            branch_name = self.git_ops.format_branch_name(pattern, variables)

            # Create and checkout branch
            base_branch = branch_config.get("base_branch", "main")
            success = await self.git_ops.create_branch(branch_name, base_branch)

            if success:
                branch_info["created_branch"] = True
                branch_info["branch_name"] = branch_name
                logger.info(
                    f"🌿 Created feature branch '{branch_name}' for issue #{issue_number}"
                )

            return branch_info

        except Exception as e:
            logger.error(f"Error creating feature branch: {e}")
            return branch_info

    async def _complete_github_workflow(
        self, work_item: dict, result: dict, branch_info: dict
    ):
        """Complete GitHub workflow after successful execution"""
        try:
            # Check if this work came from GitHub
            if work_item.get("source") != "github_watcher" or not work_item.get(
                "context", {}
            ).get("github_issue"):
                return

            # Get GitHub configuration
            github_config = self.config["sugar"]["discovery"].get("github", {})
            workflow_config = github_config.get("workflow", {})

            if not workflow_config.get("auto_close_issues", True):
                # Just comment if workflow is disabled
                await self._comment_on_github_issue(work_item, result)
                return

            # Commit changes if enabled
            if workflow_config.get("commit", {}).get("auto_commit", True):
                await self._commit_work_changes(work_item, result, workflow_config)

            # Handle workflow type
            git_workflow = workflow_config.get("git_workflow", "direct_commit")

            if git_workflow == "pull_request" and branch_info.get("created_branch"):
                await self._handle_pull_request_workflow(
                    work_item, result, branch_info, workflow_config
                )
            else:
                await self._handle_direct_commit_workflow(
                    work_item, result, workflow_config
                )

        except Exception as e:
            logger.error(f"Error completing GitHub workflow: {e}")

    async def _commit_work_changes(
        self, work_item: dict, result: dict, workflow_config: dict
    ):
        """Commit changes made during work execution"""
        try:
            # Check if there are changes to commit
            if not await self.git_ops.has_uncommitted_changes():
                logger.debug("No changes to commit")
                return

            # Get issue details
            github_issue = work_item["context"]["github_issue"]
            issue_number = github_issue.get("number")

            # Format commit message
            commit_config = workflow_config.get("commit", {})
            if commit_config.get("include_issue_ref", True):
                pattern = commit_config.get(
                    "message_pattern", "Fix #{issue_number}: {work_summary}"
                )
                work_summary = self._extract_work_summary(result)
                variables = {"issue_number": issue_number, "work_summary": work_summary}
                commit_message = self.git_ops.format_commit_message(pattern, variables)
            else:
                commit_message = self._extract_work_summary(result)

            # Commit changes
            success = await self.git_ops.commit_changes(commit_message)
            if success:
                logger.info(f"📝 Committed changes for issue #{issue_number}")

        except Exception as e:
            logger.error(f"Error committing changes: {e}")

    async def _handle_pull_request_workflow(
        self, work_item: dict, result: dict, branch_info: dict, workflow_config: dict
    ):
        """Handle pull request workflow"""
        try:
            github_issue = work_item["context"]["github_issue"]
            issue_number = github_issue.get("number")
            branch_name = branch_info.get("branch_name")

            if not branch_name:
                logger.error("No branch name available for PR creation")
                return

            # Push branch
            push_success = await self.git_ops.push_branch(branch_name)
            if not push_success:
                logger.error(f"Failed to push branch {branch_name}")
                return

            # Create pull request if configured
            pr_config = workflow_config.get("pull_request", {})
            if pr_config.get("auto_create", True):
                pr_url = await self._create_pull_request(
                    work_item, result, branch_info, workflow_config
                )

                if pr_url:
                    # Update issue with PR link
                    completion_comment = self._format_completion_comment(
                        work_item, result
                    )
                    completion_comment += f"\n\n🔀 **Pull Request:** {pr_url}"

                    # Find GitHub watcher and comment
                    github_watcher = self._get_github_watcher()
                    if github_watcher:
                        await github_watcher.comment_on_issue(
                            issue_number, completion_comment
                        )

                        # Close issue if not auto-merging PR
                        if not pr_config.get("auto_merge", False):
                            await github_watcher.close_issue(issue_number)

        except Exception as e:
            logger.error(f"Error in pull request workflow: {e}")

    async def _handle_direct_commit_workflow(
        self, work_item: dict, result: dict, workflow_config: dict
    ):
        """Handle direct commit workflow"""
        try:
            # Comment and close issue
            github_watcher = self._get_github_watcher()
            if not github_watcher:
                return

            github_issue = work_item["context"]["github_issue"]
            issue_number = github_issue.get("number")

            # Create completion comment
            completion_comment = self._format_completion_comment(work_item, result)

            # Close issue with comment
            await github_watcher.close_issue(issue_number, completion_comment)

        except Exception as e:
            logger.error(f"Error in direct commit workflow: {e}")

    async def _create_pull_request(
        self, work_item: dict, result: dict, branch_info: dict, workflow_config: dict
    ) -> Optional[str]:
        """Create a pull request"""
        try:
            github_watcher = self._get_github_watcher()
            if not github_watcher:
                return None

            github_issue = work_item["context"]["github_issue"]
            issue_number = github_issue.get("number")
            issue_title = work_item.get("title", "").replace(
                "Address GitHub issue: ", ""
            )

            # Format PR title
            pr_config = workflow_config.get("pull_request", {})
            title_pattern = pr_config.get(
                "title_pattern", "Fix #{issue_number}: {issue_title}"
            )
            variables = {"issue_number": issue_number, "issue_title": issue_title}
            pr_title = self.git_ops.format_pr_title(title_pattern, variables)

            # Create PR body
            pr_body = f"Fixes #{issue_number}\n\n"
            if pr_config.get("include_work_summary", True):
                work_summary = self._extract_work_summary(result)
                pr_body += f"## Summary\n{work_summary}\n\n"

            pr_body += "## Changes\n"
            if result.get("files_changed"):
                pr_body += f"- Modified files: {', '.join(result['files_changed'])}\n"

            pr_body += "\n---\n*This PR was automatically created by Sugar AI*"

            # Create PR
            base_branch = workflow_config.get("branch", {}).get("base_branch", "main")
            return await github_watcher.create_pull_request(
                branch_info["branch_name"], pr_title, pr_body, base_branch
            )

        except Exception as e:
            logger.error(f"Error creating pull request: {e}")
            return None

    async def _update_github_issue(self, work_item: dict, result: dict):
        """Update GitHub issue for completed work (replaces full GitHub workflow)"""
        try:
            if not work_item.get("context", {}).get("github_issue"):
                return

            github_watcher = self._get_github_watcher()
            if not github_watcher:
                logger.warning("⚠️ No GitHub watcher available for issue update")
                return

            # Use existing GitHub comment functionality
            await self._comment_on_github_issue(work_item, result)

            # Check if we should auto-close the issue
            github_config = self.config["sugar"]["discovery"].get("github", {})
            if github_config.get("workflow", {}).get("auto_close_issues", True):
                issue_number = work_item["context"]["github_issue"].get("number")
                if issue_number:
                    try:
                        await github_watcher.close_issue(issue_number)
                        logger.info(f"🔒 Closed GitHub issue #{issue_number}")
                    except Exception as e:
                        logger.warning(
                            f"⚠️ Could not close GitHub issue #{issue_number}: {e}"
                        )

        except Exception as e:
            logger.error(f"❌ Failed to update GitHub issue: {e}")

    async def _handle_failed_workflow(
        self, work_item: dict, workflow: dict, error: str
    ):
        """Handle cleanup when unified workflow fails"""
        try:
            # If we created a branch, switch back to main/master
            if workflow.get("branch_name"):
                current_branch = await self.git_ops.get_current_branch()
                if current_branch == workflow["branch_name"]:
                    # Switch back to main branch
                    base_branch = (
                        self.config["sugar"]["discovery"]
                        .get("github", {})
                        .get("workflow", {})
                        .get("base_branch", "main")
                    )
                    await self.git_ops._run_git_command(["checkout", base_branch])
                    logger.info(f"🔄 Switched back to {base_branch} after failure")

            # Comment on GitHub issue if this was GitHub-sourced work
            if work_item.get("source_type") == "github_watcher" and work_item.get(
                "context", {}
            ).get("github_issue"):

                github_watcher = self._get_github_watcher()
                if github_watcher:
                    issue_number = work_item["context"]["github_issue"].get("number")
                    failure_comment = f"❌ **Sugar AI encountered an error while working on this issue:**\n\n```\n{error}\n```\n\nI'll retry this work item in the next cycle."
                    await github_watcher.comment_on_issue(issue_number, failure_comment)

        except Exception as e:
            logger.error(f"Error handling failed workflow: {e}")

    def _extract_work_summary(self, result: dict) -> str:
        """Extract a concise work summary for commits and PRs"""
        # Try to get the most descriptive summary
        summary = result.get("summary", "")
        if summary and len(summary) > 10:
            return summary

        # Fallback to first meaningful action
        actions = result.get("actions_taken", [])
        if actions:
            return actions[0].lstrip("✅✓ ").strip()

        return "Completed work via Sugar AI"

    def _get_github_watcher(self) -> Optional[GitHubWatcher]:
        """Get the GitHub watcher module"""
        for module in self.discovery_modules:
            if isinstance(module, GitHubWatcher):
                return module
        return None

    async def health_check(self) -> dict:
        """Return system health status"""
        return {
            "status": "running" if self.running else "stopped",
            "queue_stats": await self.work_queue.get_stats(),
            "last_cycle": datetime.now(timezone.utc).isoformat(),
            "discovery_modules": len(self.discovery_modules),
            "config_loaded": bool(self.config),
        }
