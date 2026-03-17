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

    def store(
        self, entry: MemoryEntry, scope: MemoryScope = MemoryScope.PROJECT
    ) -> str:
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

    def search(
        self,
        query: MemoryQuery,
        limit: int = 10,
        guideline_slots: int = 2,
    ) -> List[MemorySearchResult]:
        """
        Search with project-first strategy and reserved guideline slots.

        Strategy:
        1. Search project store first (most specific context wins).
        2. Reserve slots for global guidelines so cross-project standards
           always surface, even when the project store has plenty of hits.
        3. Fill any remaining slots with other global results.
        4. Deduplicate across all results.

        This ensures:
        - A mature project's local context dominates search results.
        - A new project with no local memories still gets global knowledge.
        - Cross-project guidelines (SEO rules, deploy standards, etc.)
          always appear regardless of how many project results exist.
        - A project-level decision naturally overrides a global guideline
          on the same topic because it occupies a higher-priority slot.

        Args:
            query: Search query parameters.
            limit: Maximum number of results to return.
            guideline_slots: How many result slots to reserve for global
                guidelines (default 2). Set to 0 to disable.

        Returns:
            List of results: project results first, then guidelines,
            then remaining global results - all deduplicated.
        """
        results: List[MemorySearchResult] = []

        # --- Step 1: project results (highest priority) ---
        if self.project_store:
            project_results = self.project_store.search(query)
            for r in project_results:
                r.scope = MemoryScope.PROJECT.value
            results.extend(project_results)

        # --- Step 2: global guideline results (reserved slots) ---
        guideline_results: List[MemorySearchResult] = []
        if guideline_slots > 0:
            guideline_query = MemoryQuery(
                query=query.query,
                memory_types=[MemoryType.GUIDELINE],
                limit=guideline_slots,
                min_importance=query.min_importance,
                include_expired=query.include_expired,
            )
            guideline_results = self.global_store.search(guideline_query)
            for r in guideline_results:
                r.scope = MemoryScope.GLOBAL.value
            results.extend(guideline_results)

        # --- Step 3: fill remaining slots with other global results ---
        slots_used = len(self._deduplicate(results))
        remaining_slots = limit - slots_used
        if remaining_slots > 0:
            global_results = self.global_store.search(query)
            for r in global_results:
                r.scope = MemoryScope.GLOBAL.value
            # Exclude guidelines already added in step 2
            guideline_ids = {r.entry.id for r in guideline_results}
            global_results = [
                r for r in global_results if r.entry.id not in guideline_ids
            ]
            results.extend(global_results)

        # --- Step 4: deduplicate and cap ---
        results = self._deduplicate(results)
        return results[:limit]

    def get_by_type(
        self, memory_type: MemoryType, limit: int = 50
    ) -> List[MemoryEntry]:
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

    def _deduplicate(
        self, results: List[MemorySearchResult]
    ) -> List[MemorySearchResult]:
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
