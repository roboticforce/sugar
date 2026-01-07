"""
Learnings Writer - Persist learning insights to LEARNINGS.md progress log
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class LearningsWriter:
    """Write learning insights and session summaries to .sugar/LEARNINGS.md"""

    def __init__(self, sugar_dir: str = ".sugar"):
        self.sugar_dir = Path(sugar_dir)
        self.learnings_file = self.sugar_dir / "LEARNINGS.md"

    def _ensure_file_exists(self) -> None:
        """Ensure the LEARNINGS.md file exists with a header"""
        if not self.learnings_file.exists():
            self.sugar_dir.mkdir(parents=True, exist_ok=True)
            header = """# Sugar Learning Progress Log 🧠

This file contains automated learning insights from Sugar's execution history.
It tracks session summaries, success/failure patterns, and recommendations.

---

"""
            with open(self.learnings_file, "w") as f:
                f.write(header)
            logger.info(f"📝 Created new learnings file: {self.learnings_file}")

    def write_session_summary(self, insights: Dict[str, Any]) -> bool:
        """
        Write a session summary from FeedbackProcessor insights.

        Args:
            insights: Dictionary containing feedback processor insights

        Returns:
            True if write was successful, False otherwise
        """
        try:
            self._ensure_file_exists()

            timestamp = insights.get(
                "timestamp", datetime.now(timezone.utc).isoformat()
            )

            # Build the session summary markdown
            summary_parts = []
            summary_parts.append(f"\n## Session Summary - {timestamp}\n")

            # Performance Metrics
            metrics = insights.get("performance_metrics", {})
            if metrics:
                summary_parts.append("### 📊 Performance Metrics\n")
                summary_parts.append(
                    f"- **Total Tasks Processed:** {metrics.get('total_tasks_processed', 0)}"
                )
                summary_parts.append(
                    f"- **Success Rate:** {metrics.get('success_rate_percent', 0):.1f}%"
                )
                summary_parts.append(
                    f"- **Completed Tasks:** {metrics.get('completed_tasks', 0)}"
                )
                summary_parts.append(
                    f"- **Failed Tasks:** {metrics.get('failed_tasks', 0)}"
                )
                velocity = metrics.get("task_completion_velocity_per_day", 0)
                summary_parts.append(f"- **Velocity:** {velocity:.1f} tasks/day")

                time_stats = metrics.get("execution_time_statistics", {})
                if time_stats:
                    avg_time = time_stats.get("average_execution_time", 0)
                    summary_parts.append(
                        f"- **Average Execution Time:** {self._format_duration(avg_time)}"
                    )
                summary_parts.append("")

            # Success Patterns
            success_patterns = insights.get("success_patterns", {})
            if success_patterns:
                summary_parts.append("### ✅ Success Patterns\n")

                task_types = success_patterns.get("successful_task_types", {})
                if task_types:
                    summary_parts.append("**Successful Task Types:**")
                    for task_type, count in sorted(
                        task_types.items(), key=lambda x: x[1], reverse=True
                    ):
                        summary_parts.append(f"- {task_type}: {count} tasks")

                sources = success_patterns.get("successful_sources", {})
                if sources:
                    summary_parts.append("\n**Successful Sources:**")
                    for source, count in sorted(
                        sources.items(), key=lambda x: x[1], reverse=True
                    ):
                        summary_parts.append(f"- {source}: {count} tasks")
                summary_parts.append("")

            # Failure Patterns
            failure_patterns = insights.get("failure_patterns", {})
            if failure_patterns:
                summary_parts.append("### ❌ Failure Patterns\n")

                failure_reasons = failure_patterns.get("common_failure_reasons", {})
                if failure_reasons:
                    summary_parts.append("**Common Failure Reasons:**")
                    for reason, count in sorted(
                        failure_reasons.items(), key=lambda x: x[1], reverse=True
                    ):
                        summary_parts.append(f"- {reason}: {count} occurrences")

                failed_types = failure_patterns.get("failed_task_types", {})
                if failed_types:
                    summary_parts.append("\n**Failed Task Types:**")
                    for task_type, count in sorted(
                        failed_types.items(), key=lambda x: x[1], reverse=True
                    ):
                        summary_parts.append(f"- {task_type}: {count} tasks")
                summary_parts.append("")

            # Recommendations
            recommendations = insights.get("recommendations", [])
            if recommendations:
                summary_parts.append("### 💡 Recommendations\n")
                for rec in recommendations:
                    rec_type = rec.get("type", "info")
                    message = rec.get("message", "")
                    action = rec.get("action", "")

                    type_emoji = {
                        "priority_adjustment": "🎯",
                        "optimization": "⚡",
                        "focus_area": "🔍",
                        "discovery_optimization": "🔎",
                        "failure_prevention": "🛡️",
                        "info": "ℹ️",
                    }.get(rec_type, "📌")

                    summary_parts.append(f"- {type_emoji} **{rec_type}:** {message}")
                    if action:
                        summary_parts.append(f"  - Action: `{action}`")
                summary_parts.append("")

            # Write to file
            summary_parts.append("---\n")
            content = "\n".join(summary_parts)

            with open(self.learnings_file, "a") as f:
                f.write(content)

            logger.info(f"📊 Saved session summary to {self.learnings_file}")
            return True

        except Exception as e:
            logger.error(f"Error writing session summary: {e}")
            return False

    def write_custom_entry(
        self, title: str, content: str, entry_type: str = "note"
    ) -> bool:
        """
        Write a custom entry to the learnings log.

        Args:
            title: Entry title
            content: Entry content (markdown supported)
            entry_type: Type of entry (note, insight, warning, etc.)

        Returns:
            True if write was successful, False otherwise
        """
        try:
            self._ensure_file_exists()

            timestamp = datetime.now(timezone.utc).isoformat()

            type_emoji = {
                "note": "📝",
                "insight": "💡",
                "warning": "⚠️",
                "success": "✅",
                "error": "❌",
            }.get(entry_type, "📌")

            entry = f"""
## {type_emoji} {title} - {timestamp}

{content}

---
"""
            with open(self.learnings_file, "a") as f:
                f.write(entry)

            logger.info(f"📝 Added custom entry to {self.learnings_file}: {title}")
            return True

        except Exception as e:
            logger.error(f"Error writing custom entry: {e}")
            return False

    def get_learnings(self, lines: Optional[int] = None) -> str:
        """
        Read the learnings log content.

        Args:
            lines: Number of most recent lines to return (None for all)

        Returns:
            Content of the learnings file
        """
        try:
            if not self.learnings_file.exists():
                return "No learnings recorded yet. Run Sugar to generate insights."

            with open(self.learnings_file, "r") as f:
                content = f.read()

            if lines is not None and lines > 0:
                all_lines = content.split("\n")
                return "\n".join(all_lines[-lines:])

            return content

        except Exception as e:
            logger.error(f"Error reading learnings: {e}")
            return f"Error reading learnings: {e}"

    def get_recent_sessions(self, count: int = 5) -> List[Dict[str, Any]]:
        """
        Get the most recent session summaries.

        Args:
            count: Number of sessions to return

        Returns:
            List of session summary dictionaries
        """
        try:
            if not self.learnings_file.exists():
                return []

            with open(self.learnings_file, "r") as f:
                content = f.read()

            # Parse sessions from markdown (split by session headers)
            sessions = []
            current_session = None
            current_lines = []

            for line in content.split("\n"):
                if line.startswith("## Session Summary - "):
                    if current_session:
                        sessions.append(
                            {
                                "timestamp": current_session,
                                "content": "\n".join(current_lines),
                            }
                        )
                    current_session = line.replace("## Session Summary - ", "").strip()
                    current_lines = [line]
                elif current_session:
                    if line.startswith("---"):
                        sessions.append(
                            {
                                "timestamp": current_session,
                                "content": "\n".join(current_lines),
                            }
                        )
                        current_session = None
                        current_lines = []
                    else:
                        current_lines.append(line)

            # Return most recent sessions
            return sessions[-count:] if len(sessions) > count else sessions

        except Exception as e:
            logger.error(f"Error getting recent sessions: {e}")
            return []

    def clear_learnings(self, backup: bool = True) -> bool:
        """
        Clear the learnings log, optionally creating a backup.

        Args:
            backup: Whether to create a backup before clearing

        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.learnings_file.exists():
                return True

            if backup:
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                backup_file = self.sugar_dir / f"LEARNINGS_backup_{timestamp}.md"

                with open(self.learnings_file, "r") as f:
                    content = f.read()
                with open(backup_file, "w") as f:
                    f.write(content)

                logger.info(f"📦 Created backup: {backup_file}")

            # Remove the file so it can be recreated fresh
            self.learnings_file.unlink()
            self._ensure_file_exists()

            logger.info(f"🗑️ Cleared learnings log")
            return True

        except Exception as e:
            logger.error(f"Error clearing learnings: {e}")
            return False

    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds to human-readable format"""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            remaining_seconds = seconds % 60
            return f"{minutes}m {remaining_seconds:.0f}s"
        else:
            hours = int(seconds / 3600)
            remaining_minutes = int((seconds % 3600) / 60)
            return f"{hours}h {remaining_minutes}m"
