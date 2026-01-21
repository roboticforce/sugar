"""
Memory storage backend using SQLite + sqlite-vec for vector search.

Falls back to FTS5 keyword search if sqlite-vec is not available.
"""

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .embedder import EMBEDDING_DIM, BaseEmbedder, FallbackEmbedder, create_embedder
from .types import MemoryEntry, MemoryQuery, MemorySearchResult, MemoryType

logger = logging.getLogger(__name__)


def _serialize_embedding(embedding: List[float]) -> bytes:
    """Serialize embedding to bytes for sqlite-vec."""
    import struct

    return struct.pack(f"{len(embedding)}f", *embedding)


def _deserialize_embedding(data: bytes) -> List[float]:
    """Deserialize embedding from bytes."""
    import struct

    count = len(data) // 4  # 4 bytes per float
    return list(struct.unpack(f"{count}f", data))


class MemoryStore:
    """
    SQLite-based memory store with vector search support.

    Uses sqlite-vec for vector similarity search when available,
    falls back to FTS5 keyword search otherwise.
    """

    def __init__(
        self,
        db_path: str,
        embedder: Optional[BaseEmbedder] = None,
    ):
        """
        Initialize memory store.

        Args:
            db_path: Path to SQLite database file
            embedder: Embedder for generating vectors (auto-created if None)
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.embedder = embedder or create_embedder()
        self._has_vec = self._check_sqlite_vec()
        self._conn: Optional[sqlite3.Connection] = None

        self._init_db()

    def _check_sqlite_vec(self) -> bool:
        """Check if sqlite-vec extension is available."""
        try:
            import sqlite_vec  # noqa: F401

            return True
        except ImportError:
            logger.info("sqlite-vec not available, using FTS5 fallback")
            return False

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row

            if self._has_vec:
                try:
                    import sqlite_vec

                    self._conn.enable_load_extension(True)
                    sqlite_vec.load(self._conn)
                    self._conn.enable_load_extension(False)
                except Exception as e:
                    logger.warning(f"Failed to load sqlite-vec: {e}")
                    self._has_vec = False

        return self._conn

    def _init_db(self):
        """Initialize database schema."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Main memory entries table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_entries (
                id TEXT PRIMARY KEY,
                memory_type TEXT NOT NULL,
                source_id TEXT,
                content TEXT NOT NULL,
                summary TEXT,
                metadata TEXT,
                importance REAL DEFAULT 1.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed_at TIMESTAMP,
                access_count INTEGER DEFAULT 0,
                expires_at TIMESTAMP
            )
        """)

        # Indexes
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_type
            ON memory_entries(memory_type)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_importance
            ON memory_entries(importance DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_created
            ON memory_entries(created_at DESC)
        """)

        # FTS5 for keyword search (always available)
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                id,
                content,
                summary,
                content='memory_entries',
                content_rowid='rowid'
            )
        """)

        # Triggers to keep FTS in sync
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory_entries BEGIN
                INSERT INTO memory_fts(rowid, id, content, summary)
                VALUES (new.rowid, new.id, new.content, new.summary);
            END
        """)
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory_entries BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, id, content, summary)
                VALUES ('delete', old.rowid, old.id, old.content, old.summary);
            END
        """)
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory_entries BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, id, content, summary)
                VALUES ('delete', old.rowid, old.id, old.content, old.summary);
                INSERT INTO memory_fts(rowid, id, content, summary)
                VALUES (new.rowid, new.id, new.content, new.summary);
            END
        """)

        # Vector storage table (if sqlite-vec available)
        if self._has_vec:
            try:
                cursor.execute(f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_vectors USING vec0(
                        id TEXT PRIMARY KEY,
                        embedding float[{EMBEDDING_DIM}]
                    )
                """)
            except Exception as e:
                logger.warning(f"Failed to create vector table: {e}")
                self._has_vec = False

        conn.commit()

    def store(self, entry: MemoryEntry) -> str:
        """
        Store a memory entry.

        Args:
            entry: The memory entry to store

        Returns:
            The entry ID
        """
        if not entry.id:
            entry.id = str(uuid.uuid4())

        if entry.created_at is None:
            entry.created_at = datetime.now(timezone.utc)

        conn = self._get_connection()
        cursor = conn.cursor()

        # Store main entry
        cursor.execute(
            """
            INSERT OR REPLACE INTO memory_entries
            (id, memory_type, source_id, content, summary, metadata,
             importance, created_at, last_accessed_at, access_count, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                entry.id,
                (
                    entry.memory_type.value
                    if isinstance(entry.memory_type, MemoryType)
                    else entry.memory_type
                ),
                entry.source_id,
                entry.content,
                entry.summary,
                json.dumps(entry.metadata) if entry.metadata else None,
                entry.importance,
                entry.created_at.isoformat() if entry.created_at else None,
                entry.last_accessed_at.isoformat() if entry.last_accessed_at else None,
                entry.access_count,
                entry.expires_at.isoformat() if entry.expires_at else None,
            ),
        )

        # Generate and store embedding if we have semantic search
        if self._has_vec and not isinstance(self.embedder, FallbackEmbedder):
            try:
                embedding = self.embedder.embed(entry.content)
                if embedding:
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO memory_vectors (id, embedding)
                        VALUES (?, ?)
                    """,
                        (entry.id, _serialize_embedding(embedding)),
                    )
            except Exception as e:
                logger.warning(f"Failed to store embedding: {e}")

        conn.commit()
        return entry.id

    def get(self, entry_id: str) -> Optional[MemoryEntry]:
        """Get a memory entry by ID."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT * FROM memory_entries WHERE id = ?
        """,
            (entry_id,),
        )

        row = cursor.fetchone()
        if row:
            return self._row_to_entry(row)
        return None

    def delete(self, entry_id: str) -> bool:
        """Delete a memory entry."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("DELETE FROM memory_entries WHERE id = ?", (entry_id,))

        if self._has_vec:
            try:
                cursor.execute("DELETE FROM memory_vectors WHERE id = ?", (entry_id,))
            except Exception:
                pass

        conn.commit()
        return cursor.rowcount > 0

    def search(self, query: MemoryQuery) -> List[MemorySearchResult]:
        """
        Search memories.

        Uses vector similarity if available, falls back to FTS5.
        """
        if self._has_vec and not isinstance(self.embedder, FallbackEmbedder):
            return self._search_semantic(query)
        return self._search_keyword(query)

    def _search_semantic(self, query: MemoryQuery) -> List[MemorySearchResult]:
        """Search using vector similarity."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Generate query embedding
        try:
            query_embedding = self.embedder.embed(query.query)
            if not query_embedding:
                return self._search_keyword(query)
        except Exception as e:
            logger.warning(f"Failed to embed query: {e}")
            return self._search_keyword(query)

        # Build WHERE clause for filters
        where_clauses = []
        params: List[Any] = []

        if query.memory_types:
            placeholders = ",".join("?" * len(query.memory_types))
            where_clauses.append(f"e.memory_type IN ({placeholders})")
            params.extend(
                [
                    t.value if isinstance(t, MemoryType) else t
                    for t in query.memory_types
                ]
            )

        if query.min_importance > 0:
            where_clauses.append("e.importance >= ?")
            params.append(query.min_importance)

        if not query.include_expired:
            where_clauses.append(
                "(e.expires_at IS NULL OR e.expires_at > datetime('now'))"
            )

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # Vector search with filters
        try:
            sql = f"""
                SELECT e.*, v.distance
                FROM memory_entries e
                JOIN memory_vectors v ON e.id = v.id
                {where_sql}
                ORDER BY v.embedding <-> ?
                LIMIT ?
            """
            params_with_query = params + [
                _serialize_embedding(query_embedding),
                query.limit,
            ]
            cursor.execute(sql, params_with_query)
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return self._search_keyword(query)

        results = []
        for row in cursor.fetchall():
            entry = self._row_to_entry(row)
            # Convert distance to similarity score (0-1)
            distance = row["distance"] if "distance" in row.keys() else 0
            score = max(0, 1 - distance / 2)  # Normalize
            results.append(
                MemorySearchResult(entry=entry, score=score, match_type="semantic")
            )

            # Update access stats
            self._update_access(entry.id)

        return results

    def _search_keyword(self, query: MemoryQuery) -> List[MemorySearchResult]:
        """Search using FTS5 keyword matching."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Build WHERE clause for filters
        where_clauses = []
        params: List[Any] = []

        if query.memory_types:
            placeholders = ",".join("?" * len(query.memory_types))
            where_clauses.append(f"e.memory_type IN ({placeholders})")
            params.extend(
                [
                    t.value if isinstance(t, MemoryType) else t
                    for t in query.memory_types
                ]
            )

        if query.min_importance > 0:
            where_clauses.append("e.importance >= ?")
            params.append(query.min_importance)

        if not query.include_expired:
            where_clauses.append(
                "(e.expires_at IS NULL OR e.expires_at > datetime('now'))"
            )

        where_sql = f"AND {' AND '.join(where_clauses)}" if where_clauses else ""

        # FTS5 search
        # Escape special FTS5 characters
        safe_query = query.query.replace('"', '""')

        sql = f"""
            SELECT e.*, bm25(memory_fts) as score
            FROM memory_entries e
            JOIN memory_fts f ON e.id = f.id
            WHERE memory_fts MATCH ?
            {where_sql}
            ORDER BY bm25(memory_fts)
            LIMIT ?
        """

        try:
            cursor.execute(sql, [f'"{safe_query}"', *params, query.limit])
        except sqlite3.OperationalError:
            # If FTS query fails, fall back to LIKE
            sql = f"""
                SELECT e.*, 0.5 as score
                FROM memory_entries e
                WHERE (e.content LIKE ? OR e.summary LIKE ?)
                {where_sql.replace('AND', 'AND' if where_sql else '')}
                ORDER BY e.importance DESC, e.created_at DESC
                LIMIT ?
            """
            like_pattern = f"%{query.query}%"
            cursor.execute(sql, [like_pattern, like_pattern, *params, query.limit])

        results = []
        for row in cursor.fetchall():
            entry = self._row_to_entry(row)
            score = abs(row["score"]) if "score" in row.keys() else 0.5
            # Normalize BM25 score to 0-1 range
            normalized_score = min(1.0, score / 10)
            results.append(
                MemorySearchResult(
                    entry=entry, score=normalized_score, match_type="keyword"
                )
            )

            # Update access stats
            self._update_access(entry.id)

        return results

    def list_memories(
        self,
        memory_type: Optional[MemoryType] = None,
        limit: int = 50,
        offset: int = 0,
        since_days: Optional[int] = None,
    ) -> List[MemoryEntry]:
        """List memories with optional filtering."""
        conn = self._get_connection()
        cursor = conn.cursor()

        where_clauses = []
        params: List[Any] = []

        if memory_type:
            where_clauses.append("memory_type = ?")
            params.append(
                memory_type.value
                if isinstance(memory_type, MemoryType)
                else memory_type
            )

        if since_days:
            where_clauses.append("created_at >= datetime('now', ?)")
            params.append(f"-{since_days} days")

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        sql = f"""
            SELECT * FROM memory_entries
            {where_sql}
            ORDER BY importance DESC, created_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        cursor.execute(sql, params)
        return [self._row_to_entry(row) for row in cursor.fetchall()]

    def get_by_type(
        self, memory_type: MemoryType, limit: int = 50
    ) -> List[MemoryEntry]:
        """Get all memories of a specific type."""
        return self.list_memories(memory_type=memory_type, limit=limit)

    def count(self, memory_type: Optional[MemoryType] = None) -> int:
        """Count memories."""
        conn = self._get_connection()
        cursor = conn.cursor()

        if memory_type:
            cursor.execute(
                "SELECT COUNT(*) FROM memory_entries WHERE memory_type = ?",
                (
                    (
                        memory_type.value
                        if isinstance(memory_type, MemoryType)
                        else memory_type
                    ),
                ),
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM memory_entries")

        return cursor.fetchone()[0]

    def _update_access(self, entry_id: str):
        """Update access statistics for an entry."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE memory_entries
            SET last_accessed_at = datetime('now'),
                access_count = access_count + 1
            WHERE id = ?
        """,
            (entry_id,),
        )
        conn.commit()

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        """Convert database row to MemoryEntry."""
        metadata = {}
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"])
            except json.JSONDecodeError:
                pass

        memory_type = row["memory_type"]
        try:
            memory_type = MemoryType(memory_type)
        except ValueError:
            memory_type = MemoryType.DECISION

        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        last_accessed_at = row["last_accessed_at"]
        if isinstance(last_accessed_at, str):
            last_accessed_at = datetime.fromisoformat(last_accessed_at)

        expires_at = row["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)

        return MemoryEntry(
            id=row["id"],
            memory_type=memory_type,
            content=row["content"],
            summary=row["summary"],
            source_id=row["source_id"],
            metadata=metadata,
            importance=row["importance"],
            created_at=created_at,
            last_accessed_at=last_accessed_at,
            access_count=row["access_count"],
            expires_at=expires_at,
        )

    def prune_expired(self) -> int:
        """Remove expired memories. Returns count of removed entries."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Get IDs to delete (for vector cleanup)
        cursor.execute("""
            SELECT id FROM memory_entries
            WHERE expires_at IS NOT NULL AND expires_at < datetime('now')
        """)
        expired_ids = [row["id"] for row in cursor.fetchall()]

        if not expired_ids:
            return 0

        # Delete from main table
        cursor.execute("""
            DELETE FROM memory_entries
            WHERE expires_at IS NOT NULL AND expires_at < datetime('now')
        """)
        deleted = cursor.rowcount

        # Clean up vectors
        if self._has_vec and expired_ids:
            placeholders = ",".join("?" * len(expired_ids))
            try:
                cursor.execute(
                    f"DELETE FROM memory_vectors WHERE id IN ({placeholders})",
                    expired_ids,
                )
            except Exception:
                pass

        conn.commit()
        return deleted

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
