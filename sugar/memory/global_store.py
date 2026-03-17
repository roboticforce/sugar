"""
Global memory manager that combines project-local and global memory stores.

Reads from both stores on every search, writes to the appropriate store
based on scope. The global store lives at ~/.sugar/memory.db and is
available regardless of whether the current directory is a Sugar project.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .store import MemoryStore
from .types import MemoryEntry, MemoryQuery, MemoryScope, MemorySearchResult, MemoryType

logger = logging.getLogger(__name__)

GLOBAL_DB_PATH = Path.home() / ".sugar" / "memory.db"


class GlobalMemoryManager:
    """
    Manages both project-local and global memory stores.

    All search/recall operations query both stores and merge results by
    relevance score. Writes are routed to the appropriate store based on
    the requested scope.

    Works outside of Sugar projects - when no project store is provided,
    global memory is still fully available.
    """

    def __init__(self, project_store: Optional[MemoryStore] = None):
        """
        Args:
            project_store: Project-local store. Pass None when not inside a
                Sugar project - global memory will still be available.
        """
        self.project_store = project_store
        self.global_store = MemoryStore(str(GLOBAL_DB_PATH))

    def store(self, entry: MemoryEntry, scope: MemoryScope = MemoryScope.PROJECT) -> str:
        """
        Store a memory entry in the appropriate database.

        Args:
            entry: The memory entry to store.
            scope: Where to store it - PROJECT for .sugar/memory.db,
                GLOBAL for ~/.sugar/memory.db.

        Returns:
            The entry ID.

        Raises:
            RuntimeError: If scope is PROJECT but no project store is available.
        """
        if scope == MemoryScope.GLOBAL:
            return self.global_store.store(entry)

        if self.project_store is None:
            raise RuntimeError(
                "Not in a Sugar project. Use scope=global or run 'sugar init'."
            )
        return self.project_store.store(entry)

    def search(self, query: MemoryQuery, limit: int = 10) -> List[MemorySearchResult]:
        """
        Search both stores and return merged, deduplicated results.

        Results are tagged with their scope and sorted by relevance score.
        Near-duplicate content across stores is deduplicated, preferring
        project-scoped results (more specific context).

        Args:
            query: Search query parameters.
            limit: Maximum number of results to return.

        Returns:
            Merged list of results sorted by score descending.
        """
        results: List[MemorySearchResult] = []

        if self.project_store:
            project_results = self.project_store.search(query)
            for r in project_results:
                r.scope = MemoryScope.PROJECT.value
            results.extend(project_results)

        global_results = self.global_store.search(query)
        for r in global_results:
            r.scope = MemoryScope.GLOBAL.value
        results.extend(global_results)

        results.sort(key=lambda r: r.score, reverse=True)
        results = self._deduplicate(results)

        return results[:limit]

    def get_by_type(self, memory_type: MemoryType, limit: int = 50) -> List[MemoryEntry]:
        """
        Get memories of a specific type from both stores.

        Args:
            memory_type: The type to filter by.
            limit: Maximum total results.

        Returns:
            Combined list capped at limit.
        """
        entries: List[MemoryEntry] = []

        if self.project_store:
            entries.extend(self.project_store.get_by_type(memory_type, limit))

        entries.extend(self.global_store.get_by_type(memory_type, limit))

        return entries[:limit]

    def list_memories(self, **kwargs) -> List[MemoryEntry]:
        """
        List memories from both stores, sorted by importance then recency.

        Accepts the same keyword arguments as MemoryStore.list_memories
        (memory_type, limit, offset, since_days).

        Returns:
            Combined list sorted by importance descending, then created_at descending.
        """
        entries: List[MemoryEntry] = []

        if self.project_store:
            entries.extend(self.project_store.list_memories(**kwargs))

        entries.extend(self.global_store.list_memories(**kwargs))

        entries.sort(
            key=lambda e: (e.importance, e.created_at or datetime.min),
            reverse=True,
        )

        limit = kwargs.get("limit", 50)
        return entries[:limit]

    def delete(self, entry_id: str) -> bool:
        """
        Delete a memory entry from whichever store contains it.

        Tries the project store first, then the global store.

        Args:
            entry_id: ID of the entry to delete.

        Returns:
            True if the entry was found and deleted, False otherwise.
        """
        if self.project_store and self.project_store.delete(entry_id):
            return True
        return self.global_store.delete(entry_id)

    def count(self, memory_type: Optional[MemoryType] = None) -> int:
        """
        Count memories across both stores.

        Args:
            memory_type: If provided, count only this type.

        Returns:
            Total count from project store + global store.
        """
        total = 0

        if self.project_store:
            total += self.project_store.count(memory_type)

        total += self.global_store.count(memory_type)

        return total

    def close(self):
        """Close both database connections."""
        if self.project_store:
            self.project_store.close()
        self.global_store.close()

    def _deduplicate(self, results: List[MemorySearchResult]) -> List[MemorySearchResult]:
        """
        Remove near-duplicate results based on content similarity.

        Normalizes whitespace and compares the first 200 characters. When
        duplicates exist, the first occurrence is kept - since results are
        pre-sorted by score, higher-scoring (and project-scoped) results
        are naturally preferred.

        Args:
            results: Pre-sorted list of search results.

        Returns:
            Deduplicated list preserving original order.
        """
        seen: set = set()
        deduped: List[MemorySearchResult] = []

        for r in results:
            key = " ".join(r.entry.content[:200].lower().split())
            if key not in seen:
                seen.add(key)
                deduped.append(r)

        return deduped
