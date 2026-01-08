"""
Thinking Display - Real-time capture and display of Claude's thinking during execution

Captures thinking blocks from Claude Agent SDK execution and provides:
- Real-time logging of thinking progress
- Task-specific thinking logs stored as markdown
- Summary generation for task results
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ThinkingBlock:
    """A captured thinking block from execution."""

    timestamp: datetime
    content: str
    tool_use: Optional[str] = None  # What tool was being considered
    signature: Optional[str] = None  # Thinking block signature from SDK


class ThinkingCapture:
    """
    Captures and processes thinking blocks during Claude Agent SDK execution.

    This class hooks into the execution stream to capture thinking blocks in real-time,
    providing visibility into Claude's reasoning process during autonomous task execution.
    """

    def __init__(
        self,
        task_id: str,
        task_title: str = "",
        display_callback: Optional[Callable[[ThinkingBlock], None]] = None,
        log_to_file: bool = True,
    ):
        """
        Initialize thinking capture for a task.

        Args:
            task_id: Unique identifier for the task
            task_title: Human-readable task title for display
            display_callback: Optional custom callback for displaying thinking blocks
            log_to_file: Whether to write thinking to task-specific log file
        """
        self.task_id = task_id
        self.task_title = task_title
        self.thinking_blocks: List[ThinkingBlock] = []
        self.display_callback = display_callback or self._default_display
        self.log_to_file = log_to_file
        self._log_file_path: Optional[str] = None

        if self.log_to_file:
            self._setup_log_file()

    def _setup_log_file(self) -> None:
        """Setup task-specific thinking log file."""
        log_dir = ".sugar/thinking"
        os.makedirs(log_dir, exist_ok=True)

        self._log_file_path = os.path.join(log_dir, f"{self.task_id}.md")

        # Write header
        with open(self._log_file_path, "w") as f:
            f.write(f"# Thinking Log: {self.task_title or self.task_id}\n\n")
            f.write(f"**Task ID:** {self.task_id}\n")
            f.write(f"**Started:** {datetime.now().isoformat()}\n\n")
            f.write("---\n\n")

    def capture(
        self,
        thinking_content: str,
        tool_use: Optional[str] = None,
        signature: Optional[str] = None,
    ) -> None:
        """
        Capture a thinking block from execution.

        This should be called for each ThinkingBlock received from the SDK's
        streaming response.

        Args:
            thinking_content: The thinking content text
            tool_use: Optional tool being considered (e.g., "Read", "Write")
            signature: Optional signature from ThinkingBlock
        """
        if not thinking_content.strip():
            return  # Skip empty thinking

        block = ThinkingBlock(
            timestamp=datetime.now(),
            content=thinking_content,
            tool_use=tool_use,
            signature=signature,
        )

        self.thinking_blocks.append(block)
        self.display_callback(block)

        if self.log_to_file and self._log_file_path:
            self._write_to_task_log(block)

    def _default_display(self, block: ThinkingBlock) -> None:
        """
        Default display handler - logs to stdout/stderr.

        Truncates long thinking for console display while preserving
        full content in log files.
        """
        # Truncate for console display
        max_display_length = 200
        preview = (
            block.content[:max_display_length] + "..."
            if len(block.content) > max_display_length
            else block.content
        )

        # Log with context
        if block.tool_use:
            logger.info(
                f"[THINKING → {block.tool_use}] {preview}",
                extra={"task_id": self.task_id},
            )
        else:
            logger.info(f"[THINKING] {preview}", extra={"task_id": self.task_id})

    def _write_to_task_log(self, block: ThinkingBlock) -> None:
        """
        Write thinking block to task-specific log file.

        Creates a markdown-formatted log with full thinking content,
        timestamps, and metadata.
        """
        if not self._log_file_path:
            return

        try:
            with open(self._log_file_path, "a") as f:
                # Timestamp header
                f.write(f"\n## {block.timestamp.strftime('%H:%M:%S')}\n\n")

                # Tool use context
                if block.tool_use:
                    f.write(f"*Considering tool: `{block.tool_use}`*\n\n")

                # Thinking content
                f.write(block.content)
                f.write("\n\n---\n")

        except IOError as e:
            logger.warning(f"Failed to write thinking to log file: {e}")

    def get_summary(self) -> str:
        """
        Get summary of all thinking for task results.

        Returns:
            Summary string describing thinking capture
        """
        if not self.thinking_blocks:
            return "No thinking captured"

        total_chars = sum(len(block.content) for block in self.thinking_blocks)
        return (
            f"Captured {len(self.thinking_blocks)} thinking blocks "
            f"({total_chars:,} characters). "
            f"See .sugar/thinking/{self.task_id}.md for full log"
        )

    def get_thinking_log_path(self) -> Optional[str]:
        """Get the path to the thinking log file if it exists."""
        return self._log_file_path

    def get_thinking_blocks(self) -> List[ThinkingBlock]:
        """Get all captured thinking blocks."""
        return self.thinking_blocks.copy()

    def get_stats(self) -> dict:
        """
        Get statistics about captured thinking.

        Returns:
            Dictionary with thinking statistics
        """
        if not self.thinking_blocks:
            return {
                "count": 0,
                "total_characters": 0,
                "average_length": 0,
                "tool_uses_considered": [],
            }

        total_chars = sum(len(block.content) for block in self.thinking_blocks)
        tool_uses = [
            block.tool_use for block in self.thinking_blocks if block.tool_use
        ]

        return {
            "count": len(self.thinking_blocks),
            "total_characters": total_chars,
            "average_length": total_chars // len(self.thinking_blocks),
            "tool_uses_considered": list(set(tool_uses)),
            "first_thinking": self.thinking_blocks[0].timestamp.isoformat(),
            "last_thinking": self.thinking_blocks[-1].timestamp.isoformat(),
        }

    def finalize(self) -> None:
        """
        Finalize the thinking capture session.

        Writes summary information to the log file.
        """
        if not self.log_to_file or not self._log_file_path:
            return

        try:
            with open(self._log_file_path, "a") as f:
                f.write("\n\n## Summary\n\n")
                stats = self.get_stats()
                f.write(f"- **Total thinking blocks:** {stats['count']}\n")
                f.write(f"- **Total characters:** {stats['total_characters']:,}\n")
                f.write(f"- **Average length:** {stats['average_length']} chars\n")

                if stats.get("tool_uses_considered"):
                    f.write(
                        f"- **Tools considered:** {', '.join(stats['tool_uses_considered'])}\n"
                    )

                f.write(f"\n**Completed:** {datetime.now().isoformat()}\n")

        except IOError as e:
            logger.warning(f"Failed to write thinking summary: {e}")


def read_thinking_log(task_id: str) -> Optional[str]:
    """
    Read the thinking log file for a task.

    Args:
        task_id: Task ID to read thinking log for

    Returns:
        Thinking log content as string, or None if not found
    """
    log_path = os.path.join(".sugar/thinking", f"{task_id}.md")

    if not os.path.exists(log_path):
        return None

    try:
        with open(log_path, "r") as f:
            return f.read()
    except IOError as e:
        logger.error(f"Failed to read thinking log: {e}")
        return None


def list_thinking_logs() -> List[tuple[str, str, datetime]]:
    """
    List all available thinking logs.

    Returns:
        List of tuples: (task_id, file_path, modified_time)
    """
    log_dir = ".sugar/thinking"

    if not os.path.exists(log_dir):
        return []

    logs = []
    for filename in os.listdir(log_dir):
        if filename.endswith(".md"):
            file_path = os.path.join(log_dir, filename)
            task_id = filename[:-3]  # Remove .md extension
            modified_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            logs.append((task_id, file_path, modified_time))

    # Sort by modification time, newest first
    logs.sort(key=lambda x: x[2], reverse=True)

    return logs
