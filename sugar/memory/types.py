"""
Memory types and dataclasses for Sugar memory system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class MemoryType(str, Enum):
    """Types of memories that Sugar can store."""

    DECISION = "decision"  # Architectural/implementation decisions
    PREFERENCE = "preference"  # User coding preferences (permanent)
    FILE_CONTEXT = "file_context"  # What files do what
    ERROR_PATTERN = "error_pattern"  # Bug patterns and fixes
    RESEARCH = "research"  # API docs, library findings
    OUTCOME = "outcome"  # Task outcomes and learnings


@dataclass
class MemoryEntry:
    """A single memory entry."""

    id: str
    memory_type: MemoryType
    content: str
    summary: Optional[str] = None
    source_id: Optional[str] = None  # Related work_item id
    metadata: Dict[str, Any] = field(default_factory=dict)
    importance: float = 1.0
    created_at: Optional[datetime] = None
    last_accessed_at: Optional[datetime] = None
    access_count: int = 0
    expires_at: Optional[datetime] = None
    embedding: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "memory_type": (
                self.memory_type.value
                if isinstance(self.memory_type, MemoryType)
                else self.memory_type
            ),
            "content": self.content,
            "summary": self.summary,
            "source_id": self.source_id,
            "metadata": self.metadata,
            "importance": self.importance,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_accessed_at": (
                self.last_accessed_at.isoformat() if self.last_accessed_at else None
            ),
            "access_count": self.access_count,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        """Create from dictionary."""
        memory_type = data.get("memory_type", "decision")
        if isinstance(memory_type, str):
            try:
                memory_type = MemoryType(memory_type)
            except ValueError:
                memory_type = MemoryType.DECISION

        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        last_accessed_at = data.get("last_accessed_at")
        if isinstance(last_accessed_at, str):
            last_accessed_at = datetime.fromisoformat(last_accessed_at)

        expires_at = data.get("expires_at")
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)

        return cls(
            id=data["id"],
            memory_type=memory_type,
            content=data["content"],
            summary=data.get("summary"),
            source_id=data.get("source_id"),
            metadata=data.get("metadata", {}),
            importance=data.get("importance", 1.0),
            created_at=created_at,
            last_accessed_at=last_accessed_at,
            access_count=data.get("access_count", 0),
            expires_at=expires_at,
        )


@dataclass
class MemorySearchResult:
    """Result from a memory search."""

    entry: MemoryEntry
    score: float  # Similarity score (0-1)
    match_type: str = "semantic"  # "semantic" or "keyword"


@dataclass
class MemoryQuery:
    """Query parameters for memory search."""

    query: str
    memory_types: Optional[List[MemoryType]] = None
    tags: Optional[List[str]] = None
    file_paths: Optional[List[str]] = None
    limit: int = 10
    min_importance: float = 0.0
    include_expired: bool = False
