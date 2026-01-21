"""
Embedding generation for Sugar memory system.

Uses sentence-transformers for local embeddings with FTS5 fallback.
"""

import logging
from abc import ABC, abstractmethod
from typing import List, Optional

logger = logging.getLogger(__name__)

# Embedding dimension for all-MiniLM-L6-v2
EMBEDDING_DIM = 384


class BaseEmbedder(ABC):
    """Abstract base class for embedders."""

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        pass

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return embedding dimension."""
        pass


class SentenceTransformerEmbedder(BaseEmbedder):
    """Embedder using sentence-transformers (local, no API calls)."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        """Lazy load the model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                logger.info(f"Loading embedding model: {self.model_name}")
                self._model = SentenceTransformer(self.model_name)
                logger.info("Embedding model loaded successfully")
            except ImportError:
                raise ImportError(
                    "sentence-transformers not installed. "
                    "Install with: pip install 'sugarai[memory]'"
                )
        return self._model

    def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        model = self._load_model()
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        if not texts:
            return []
        model = self._load_model()
        embeddings = model.encode(texts, convert_to_numpy=True)
        return embeddings.tolist()

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIM


class FallbackEmbedder(BaseEmbedder):
    """
    Fallback embedder that returns None embeddings.

    Used when sentence-transformers is not available.
    Memory store will use FTS5 keyword search instead.
    """

    def __init__(self):
        logger.warning(
            "Using fallback embedder - semantic search disabled. "
            "Install sentence-transformers for better search: pip install 'sugarai[memory]'"
        )

    def embed(self, text: str) -> List[float]:
        """Return empty embedding (triggers FTS5 fallback)."""
        return []

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Return empty embeddings."""
        return [[] for _ in texts]

    @property
    def dimension(self) -> int:
        return 0


def create_embedder(prefer_local: bool = True) -> BaseEmbedder:
    """
    Create the best available embedder.

    Args:
        prefer_local: If True, prefer local sentence-transformers over API

    Returns:
        An embedder instance
    """
    if prefer_local:
        try:
            embedder = SentenceTransformerEmbedder()
            # Try to load model to verify it works
            embedder._load_model()
            return embedder
        except ImportError:
            logger.info("sentence-transformers not available, using fallback")
        except Exception as e:
            logger.warning(f"Failed to load sentence-transformers: {e}")

    return FallbackEmbedder()


def is_semantic_search_available() -> bool:
    """Check if semantic search (embeddings) is available."""
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401

        return True
    except ImportError:
        return False
