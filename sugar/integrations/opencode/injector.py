"""
Sugar Context Injector for OpenCode

Automatically injects relevant Sugar memories into OpenCode sessions
to provide persistent context across sessions.
"""

import asyncio
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from .client import OpenCodeClient
from .config import OpenCodeConfig
from .models import NotificationLevel

logger = logging.getLogger(__name__)


class ContextInjector:
    """
    Manages automatic context injection from Sugar memory into OpenCode sessions.

    Features:
    - Relevance scoring for memory selection
    - Token budget management
    - Learning capture from session outcomes
    """

    # Token budget defaults
    SESSION_START_BUDGET = 2000
    PER_PROMPT_BUDGET = 800
    ERROR_CONTEXT_BUDGET = 300

    # Relevance scoring weights
    SEMANTIC_WEIGHT = 0.40
    RECENCY_WEIGHT = 0.20
    IMPORTANCE_WEIGHT = 0.15
    FREQUENCY_WEIGHT = 0.10
    TYPE_WEIGHT = 0.15

    # Memory type priorities (higher = more relevant for general queries)
    TYPE_PRIORITIES = {
        "preference": 0.9,
        "decision": 0.8,
        "error_pattern": 0.7,
        "file_context": 0.6,
        "research": 0.5,
        "outcome": 0.4,
    }

    # Type-specific similarity thresholds
    SIMILARITY_THRESHOLDS = {
        "preference": 0.30,
        "decision": 0.35,
        "file_context": 0.40,
        "error_pattern": 0.45,
        "research": 0.50,
        "outcome": 0.55,
    }

    # Recency decay half-life in days
    HALF_LIFE = {
        "preference": 365 * 10,  # Never expires practically
        "decision": 365 * 10,
        "file_context": 365 * 10,
        "error_pattern": 90,
        "research": 60,
        "outcome": 30,
    }

    def __init__(
        self,
        config: Optional[OpenCodeConfig] = None,
    ):
        """
        Initialize context injector.

        Args:
            config: OpenCode configuration
        """
        self.config = config or OpenCodeConfig.from_env()
        self._memory_store = None
        self._embedding_cache: Dict[str, Any] = {}

    def _get_memory_store(self):
        """Lazy load memory store."""
        if self._memory_store is None:
            from pathlib import Path
            from sugar.memory import MemoryStore

            # Find .sugar directory
            cwd = Path.cwd()
            sugar_dir = cwd / ".sugar"

            if not sugar_dir.exists():
                for parent in cwd.parents:
                    potential = parent / ".sugar"
                    if potential.exists():
                        sugar_dir = potential
                        break

            if not sugar_dir.exists():
                raise RuntimeError("Not in a Sugar project. Run 'sugar init' first.")

            memory_db = sugar_dir / "memory.db"
            self._memory_store = MemoryStore(str(memory_db))

        return self._memory_store

    def calculate_relevance(
        self,
        memory: Dict[str, Any],
        query_embedding: Optional[Any] = None,
        semantic_score: float = 0.5,
    ) -> float:
        """
        Calculate relevance score for a memory.

        Args:
            memory: Memory dict with content, type, created_at, importance, access_count
            query_embedding: Optional query embedding for semantic similarity
            semantic_score: Pre-computed semantic similarity (0-1)

        Returns:
            Relevance score (0-1)
        """
        score = 0.0
        mem_type = memory.get("type", "outcome")

        # Semantic similarity (40% weight)
        score += semantic_score * self.SEMANTIC_WEIGHT

        # Recency (20% weight)
        created_at = memory.get("created_at")
        if created_at:
            if isinstance(created_at, str):
                try:
                    created_at = datetime.fromisoformat(created_at)
                except ValueError:
                    created_at = None

            if created_at:
                days_old = (datetime.now() - created_at).days
                half_life = self.HALF_LIFE.get(mem_type, 30)
                recency = math.exp(-days_old / half_life * math.log(2))
                score += recency * self.RECENCY_WEIGHT
            else:
                score += 0.5 * self.RECENCY_WEIGHT
        else:
            score += 0.5 * self.RECENCY_WEIGHT

        # Importance (15% weight)
        importance = memory.get("importance", 5) / 10.0
        score += importance * self.IMPORTANCE_WEIGHT

        # Access frequency (10% weight)
        access_count = memory.get("access_count", 0)
        frequency = min(access_count / 10.0, 1.0)
        score += frequency * self.FREQUENCY_WEIGHT

        # Type priority (15% weight)
        type_priority = self.TYPE_PRIORITIES.get(mem_type, 0.5)
        score += type_priority * self.TYPE_WEIGHT

        return score

    def fit_to_budget(
        self,
        memories: List[Dict[str, Any]],
        budget: int,
        max_per_type: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Select memories that fit within token budget.

        Args:
            memories: List of memories sorted by relevance
            budget: Maximum tokens to use
            max_per_type: Maximum memories per type (for diversity)

        Returns:
            List of memories fitting budget
        """
        result = []
        used_tokens = 0
        type_counts: Dict[str, int] = {}

        # Estimate tokens (rough: 4 chars per token)
        def estimate_tokens(text: str) -> int:
            return len(text) // 4 + 1

        for memory in memories:
            content = memory.get("content", "")
            tokens = estimate_tokens(content)

            # Check budget
            if used_tokens + tokens > budget:
                continue

            # Check type diversity
            mem_type = memory.get("type", "other")
            if type_counts.get(mem_type, 0) >= max_per_type:
                continue

            result.append(memory)
            used_tokens += tokens
            type_counts[mem_type] = type_counts.get(mem_type, 0) + 1

        return result

    def format_for_injection(self, memories: List[Dict[str, Any]]) -> str:
        """
        Format memories for LLM consumption.

        Args:
            memories: List of memory dicts

        Returns:
            Formatted markdown string
        """
        if not memories:
            return ""

        lines = ["## Sugar Context", ""]

        # Group by type
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for m in memories:
            mem_type = m.get("type", "other")
            by_type.setdefault(mem_type, []).append(m)

        type_headers = {
            "decision": "Previous Decisions",
            "preference": "Coding Preferences",
            "file_context": "File Context",
            "error_pattern": "Known Error Patterns",
            "research": "Research Notes",
            "outcome": "Past Outcomes",
        }

        for mem_type, items in by_type.items():
            header = type_headers.get(mem_type, mem_type.title())
            lines.append(f"### {header}")
            lines.append("")
            for item in items:
                content = item.get("content", "")
                # Truncate very long content
                if len(content) > 500:
                    content = content[:500] + "..."
                lines.append(f"- {content}")
            lines.append("")

        return "\n".join(lines)

    async def get_context_for_prompt(
        self,
        prompt: str,
        budget: int = PER_PROMPT_BUDGET,
    ) -> str:
        """
        Get relevant context for a prompt.

        Args:
            prompt: User prompt text
            budget: Token budget

        Returns:
            Formatted context string
        """
        try:
            from sugar.memory import MemoryQuery, MemoryType

            store = self._get_memory_store()

            # Convert string types to MemoryType enums
            memory_types = None
            if self.config.inject_memory_types:
                memory_types = [
                    MemoryType(t) if isinstance(t, str) else t
                    for t in self.config.inject_memory_types
                ]

            # Search for relevant memories
            query = MemoryQuery(
                query=prompt,
                limit=20,
                memory_types=memory_types,
            )
            results = store.search(query)

            if not results:
                return ""

            # Convert to dicts with relevance scores
            memories = []
            for r in results:
                mem_dict = {
                    "content": r.entry.content,
                    "type": r.entry.memory_type.value,
                    "created_at": r.entry.created_at,
                    "importance": getattr(r.entry, "importance", 5),
                    "access_count": getattr(r.entry, "access_count", 0),
                }

                # Use search score as semantic similarity
                relevance = self.calculate_relevance(mem_dict, semantic_score=r.score)

                # Check type-specific threshold
                threshold = self.SIMILARITY_THRESHOLDS.get(mem_dict["type"], 0.4)
                if r.score >= threshold:
                    mem_dict["relevance"] = relevance
                    memories.append(mem_dict)

            # Sort by relevance and fit to budget
            memories.sort(key=lambda m: m.get("relevance", 0), reverse=True)
            selected = self.fit_to_budget(memories, budget)

            return self.format_for_injection(selected)

        except Exception as e:
            logger.error(f"Error getting context for prompt: {e}")
            return ""

    async def get_session_start_context(self) -> str:
        """
        Get context for session start (broader, more tokens).

        Returns:
            Formatted context string
        """
        return await self.get_context_for_prompt(
            "project context preferences decisions",
            budget=self.SESSION_START_BUDGET,
        )

    async def get_error_context(self, error_message: str) -> str:
        """
        Get context for an error (focused on error patterns).

        Args:
            error_message: The error that occurred

        Returns:
            Formatted context string
        """
        try:
            from sugar.memory import MemoryQuery, MemoryType

            store = self._get_memory_store()

            # Search specifically for error patterns
            query = MemoryQuery(
                query=error_message,
                limit=5,
                memory_types=[MemoryType.ERROR_PATTERN],
            )
            results = store.search(query)

            if not results:
                return ""

            memories = [
                {
                    "content": r.entry.content,
                    "type": "error_pattern",
                }
                for r in results
                if r.score >= 0.3
            ]

            return self.format_for_injection(memories)

        except Exception as e:
            logger.error(f"Error getting error context: {e}")
            return ""

    async def inject_into_session(
        self,
        session_id: str,
        context: str,
    ) -> bool:
        """
        Inject context into an OpenCode session.

        Args:
            session_id: Target session ID
            context: Context to inject

        Returns:
            True if successful
        """
        if not context:
            return True

        try:
            async with OpenCodeClient(self.config) as client:
                return await client.inject_context(session_id, context)
        except Exception as e:
            logger.error(f"Failed to inject context: {e}")
            return False


class LearningCapture:
    """
    Captures learnings from OpenCode sessions and stores them in Sugar memory.
    """

    # Patterns to detect learnings
    DECISION_PATTERNS = [
        r"decided to (use|implement|go with)",
        r"chose .+ (over|instead of)",
        r"will (use|implement|adopt)",
        r"architecture.+will be",
    ]

    PREFERENCE_PATTERNS = [
        r"prefer (to use|using)",
        r"always (use|include|add)",
        r"never (use|include)",
        r"style.+(should|must)",
    ]

    ERROR_PATTERNS = [
        r"fixed by",
        r"solution was",
        r"resolved.+by",
        r"error.+caused by",
    ]

    def __init__(self):
        """Initialize learning capture."""
        self._memory_store = None

    def _get_memory_store(self):
        """Lazy load memory store."""
        if self._memory_store is None:
            from pathlib import Path
            from sugar.memory import MemoryStore

            cwd = Path.cwd()
            sugar_dir = cwd / ".sugar"

            if not sugar_dir.exists():
                for parent in cwd.parents:
                    potential = parent / ".sugar"
                    if potential.exists():
                        sugar_dir = potential
                        break

            if sugar_dir.exists():
                memory_db = sugar_dir / "memory.db"
                self._memory_store = MemoryStore(str(memory_db))

        return self._memory_store

    async def extract_learnings(
        self,
        content: str,
    ) -> List[Dict[str, Any]]:
        """
        Extract learnings from content.

        Args:
            content: Text content to analyze

        Returns:
            List of learning dicts with type and content
        """
        import re

        learnings = []

        # Check decision patterns
        for pattern in self.DECISION_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                # Extract context around match
                start = max(0, match.start() - 50)
                end = min(len(content), match.end() + 100)
                context = content[start:end].strip()

                learnings.append(
                    {
                        "type": "decision",
                        "content": context,
                        "importance": 7,
                    }
                )

        # Check preference patterns
        for pattern in self.PREFERENCE_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                start = max(0, match.start() - 50)
                end = min(len(content), match.end() + 100)
                context = content[start:end].strip()

                learnings.append(
                    {
                        "type": "preference",
                        "content": context,
                        "importance": 8,
                    }
                )

        # Check error patterns
        for pattern in self.ERROR_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                start = max(0, match.start() - 50)
                end = min(len(content), match.end() + 100)
                context = content[start:end].strip()

                learnings.append(
                    {
                        "type": "error_pattern",
                        "content": context,
                        "importance": 6,
                    }
                )

        return learnings

    async def store_learning(
        self,
        content: str,
        memory_type: str,
        importance: int = 5,
    ) -> Optional[str]:
        """
        Store a learning in Sugar memory.

        Args:
            content: Learning content
            memory_type: Type of memory
            importance: Importance score (1-10)

        Returns:
            Memory ID if stored, None otherwise
        """
        try:
            import uuid
            from sugar.memory import MemoryEntry, MemoryType

            store = self._get_memory_store()
            if not store:
                return None

            # Validate memory type
            try:
                mem_type = MemoryType(memory_type)
            except ValueError:
                mem_type = MemoryType.OUTCOME

            entry = MemoryEntry(
                id=str(uuid.uuid4()),
                memory_type=mem_type,
                content=content,
                summary=content[:100] if len(content) > 100 else None,
                metadata={"source": "opencode", "importance": importance},
            )

            entry_id = store.store(entry)
            logger.debug(f"Stored learning: {entry_id[:8]} ({memory_type})")
            return entry_id

        except Exception as e:
            logger.error(f"Failed to store learning: {e}")
            return None

    async def store_if_unique(
        self,
        content: str,
        memory_type: str,
        similarity_threshold: float = 0.85,
    ) -> Optional[str]:
        """
        Store a learning only if it's unique enough.

        Args:
            content: Learning content
            memory_type: Type of memory
            similarity_threshold: Threshold for considering duplicate

        Returns:
            Memory ID if stored, None if duplicate or error
        """
        try:
            from sugar.memory import MemoryQuery, MemoryType

            store = self._get_memory_store()
            if not store:
                return None

            # Search for similar existing memories
            query = MemoryQuery(
                query=content,
                limit=5,
                memory_types=[MemoryType(memory_type)] if memory_type else None,
            )
            similar = store.search(query)

            # Check for duplicates
            for result in similar:
                if result.score >= similarity_threshold:
                    logger.debug(
                        f"Skipping duplicate learning (similarity: {result.score:.2f})"
                    )
                    return None

            return await self.store_learning(content, memory_type)

        except Exception as e:
            logger.error(f"Error checking uniqueness: {e}")
            return await self.store_learning(content, memory_type)
