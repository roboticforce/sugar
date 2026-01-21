"""
Memory retrieval for task execution context injection.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from .store import MemoryStore
from .types import MemoryEntry, MemoryQuery, MemorySearchResult, MemoryType

logger = logging.getLogger(__name__)


class MemoryRetriever:
    """
    Retrieves relevant memories for task execution.

    Handles:
    - Semantic search for relevant context
    - Combining different memory types
    - Formatting memories for prompt injection
    """

    def __init__(self, store: MemoryStore):
        self.store = store

    def get_relevant(
        self,
        query: str,
        memory_types: Optional[List[MemoryType]] = None,
        limit: int = 5,
    ) -> List[MemorySearchResult]:
        """
        Get memories relevant to a query.

        Args:
            query: Natural language query (e.g., task description)
            memory_types: Filter by types (default: decision, preference, file_context)
            limit: Maximum results

        Returns:
            List of relevant memories with scores
        """
        if memory_types is None:
            memory_types = [
                MemoryType.DECISION,
                MemoryType.PREFERENCE,
                MemoryType.FILE_CONTEXT,
                MemoryType.ERROR_PATTERN,
            ]

        search_query = MemoryQuery(
            query=query,
            memory_types=memory_types,
            limit=limit,
        )

        return self.store.search(search_query)

    def get_project_context(self, limit: int = 10) -> dict:
        """
        Get overall project context for session injection.

        Returns:
            Dictionary with organized memories by type
        """
        context = {
            "preferences": [],
            "recent_decisions": [],
            "file_context": [],
            "error_patterns": [],
        }

        # Get all preferences (permanent, high value)
        preferences = self.store.get_by_type(MemoryType.PREFERENCE, limit=20)
        context["preferences"] = [self._entry_to_dict(e) for e in preferences]

        # Get recent decisions (last 30 days)
        decisions = self.store.list_memories(
            memory_type=MemoryType.DECISION,
            limit=limit,
            since_days=30,
        )
        context["recent_decisions"] = [self._entry_to_dict(e) for e in decisions]

        # Get file context
        file_context = self.store.get_by_type(MemoryType.FILE_CONTEXT, limit=limit)
        context["file_context"] = [self._entry_to_dict(e) for e in file_context]

        # Get recent error patterns
        patterns = self.store.list_memories(
            memory_type=MemoryType.ERROR_PATTERN,
            limit=5,
            since_days=60,
        )
        context["error_patterns"] = [self._entry_to_dict(e) for e in patterns]

        return context

    def format_for_prompt(
        self,
        memories: List[MemorySearchResult],
        max_tokens: int = 2000,
    ) -> str:
        """
        Format memories for injection into a prompt.

        Args:
            memories: List of memory search results
            max_tokens: Approximate max tokens (chars / 4)

        Returns:
            Markdown-formatted context string
        """
        if not memories:
            return ""

        lines = ["## Relevant Context from Previous Work", ""]
        char_count = 100  # Header chars
        max_chars = max_tokens * 4

        for result in memories:
            entry = result.entry
            age = self._format_age(entry.created_at)

            # Build memory block
            type_label = entry.memory_type.value.replace("_", " ").title()
            block_lines = [
                f"### {type_label} ({age})",
                entry.content,
            ]

            # Add file path if present
            if entry.metadata.get("file_paths"):
                files = ", ".join(f"`{f}`" for f in entry.metadata["file_paths"][:3])
                block_lines.append(f"Files: {files}")

            block_lines.append("")
            block = "\n".join(block_lines)

            # Check if we'd exceed limit
            if char_count + len(block) > max_chars:
                break

            lines.extend(block_lines)
            char_count += len(block)

        if len(lines) <= 2:  # Only header
            return ""

        lines.append("---")
        return "\n".join(lines)

    def format_context_markdown(self, context: dict) -> str:
        """
        Format project context as markdown for export.

        Args:
            context: Dictionary from get_project_context()

        Returns:
            Markdown string
        """
        lines = ["## Recent Context from Sugar Memory", ""]

        # Decisions
        if context.get("recent_decisions"):
            lines.append("### Decisions (last 30 days)")
            for d in context["recent_decisions"][:5]:
                summary = d.get("summary") or d.get("content", "")[:100]
                lines.append(f"- **{summary}**")
            lines.append("")

        # Preferences
        if context.get("preferences"):
            lines.append("### Preferences")
            for p in context["preferences"][:5]:
                lines.append(f"- {p.get('content', '')}")
            lines.append("")

        # Error patterns
        if context.get("error_patterns"):
            lines.append("### Recent Error Patterns")
            for e in context["error_patterns"][:3]:
                summary = e.get("summary") or e.get("content", "")[:100]
                lines.append(f"- {summary}")
            lines.append("")

        if len(lines) <= 2:  # Only header
            return ""

        return "\n".join(lines)

    def _entry_to_dict(self, entry: MemoryEntry) -> dict:
        """Convert entry to simple dict for context."""
        return {
            "id": entry.id,
            "content": entry.content,
            "summary": entry.summary,
            "type": (
                entry.memory_type.value
                if isinstance(entry.memory_type, MemoryType)
                else entry.memory_type
            ),
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
            "metadata": entry.metadata,
        }

    def _format_age(self, created_at: Optional[datetime]) -> str:
        """Format age as human-readable string."""
        if not created_at:
            return "unknown"

        now = datetime.now(created_at.tzinfo) if created_at.tzinfo else datetime.now()
        delta = now - created_at

        if delta < timedelta(hours=1):
            return "just now"
        elif delta < timedelta(days=1):
            hours = int(delta.total_seconds() / 3600)
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        elif delta < timedelta(days=7):
            days = delta.days
            return f"{days} day{'s' if days > 1 else ''} ago"
        elif delta < timedelta(days=30):
            weeks = delta.days // 7
            return f"{weeks} week{'s' if weeks > 1 else ''} ago"
        else:
            return created_at.strftime("%Y-%m-%d")
