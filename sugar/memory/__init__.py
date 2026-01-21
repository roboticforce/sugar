"""
Sugar Memory System

Persistent semantic memory for AI coding sessions.
"""

from .embedder import (
    BaseEmbedder,
    FallbackEmbedder,
    SentenceTransformerEmbedder,
    create_embedder,
    is_semantic_search_available,
)
from .retriever import MemoryRetriever
from .store import MemoryStore
from .types import MemoryEntry, MemoryQuery, MemorySearchResult, MemoryType

__all__ = [
    # Types
    "MemoryEntry",
    "MemoryQuery",
    "MemorySearchResult",
    "MemoryType",
    # Store
    "MemoryStore",
    # Retriever
    "MemoryRetriever",
    # Embedder
    "BaseEmbedder",
    "FallbackEmbedder",
    "SentenceTransformerEmbedder",
    "create_embedder",
    "is_semantic_search_available",
]
