"""
Issue Response Manager - Track GitHub issue responses
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)


class IssueResponseManager:
    """Manage tracking of responses to GitHub issues"""

    def __init__(self, db_path: str = ".sugar/sugar.db"):
        self.db_path = db_path
        self._initialized = False

    async def initialize(self) -> None:
        """Create table if not exists"""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS issue_responses (
                    id TEXT PRIMARY KEY,
                    repo TEXT NOT NULL,
                    issue_number INTEGER NOT NULL,
                    response_type TEXT NOT NULL,
                    work_item_id TEXT,
                    confidence REAL,
                    posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    response_content TEXT,
                    labels_applied TEXT,
                    was_auto_posted BOOLEAN DEFAULT 0,
                    UNIQUE(repo, issue_number, response_type)
                )
            """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_issue_responses_repo_number
                ON issue_responses (repo, issue_number)
            """
            )

            await db.commit()

        self._initialized = True
        logger.debug(f"Issue response manager initialized: {self.db_path}")

    async def has_responded(
        self, repo: str, issue_number: int, response_type: str = "initial"
    ) -> bool:
        """Check if we've already responded to this issue"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM issue_responses
                WHERE repo = ? AND issue_number = ? AND response_type = ?
            """,
                (repo, issue_number, response_type),
            )
            count = (await cursor.fetchone())[0]
            return count > 0

    async def record_response(
        self,
        repo: str,
        issue_number: int,
        response_type: str,
        confidence: float,
        response_content: str,
        labels_applied: Optional[List[str]] = None,
        was_auto_posted: bool = False,
        work_item_id: Optional[str] = None,
    ) -> str:
        """Record a response, returns the response ID"""
        response_id = str(uuid.uuid4())

        # Convert labels to JSON string
        labels_json = None
        if labels_applied:
            labels_json = json.dumps(labels_applied)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO issue_responses
                (id, repo, issue_number, response_type, work_item_id, confidence,
                 response_content, labels_applied, was_auto_posted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    response_id,
                    repo,
                    issue_number,
                    response_type,
                    work_item_id,
                    confidence,
                    response_content,
                    labels_json,
                    was_auto_posted,
                ),
            )
            await db.commit()

        logger.debug(
            f"Recorded {response_type} response for {repo}#{issue_number} "
            f"(confidence: {confidence:.2f}, auto_posted: {was_auto_posted})"
        )
        return response_id

    async def get_response_history(
        self, repo: str, issue_number: Optional[int] = None
    ) -> List[Dict]:
        """Get response history for an issue or all issues in a repo"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            if issue_number is not None:
                # Get responses for a specific issue
                cursor = await db.execute(
                    """
                    SELECT * FROM issue_responses
                    WHERE repo = ? AND issue_number = ?
                    ORDER BY posted_at DESC
                """,
                    (repo, issue_number),
                )
            else:
                # Get all responses for a repo
                cursor = await db.execute(
                    """
                    SELECT * FROM issue_responses
                    WHERE repo = ?
                    ORDER BY posted_at DESC
                """,
                    (repo,),
                )

            rows = await cursor.fetchall()

            responses = []
            for row in rows:
                response = dict(row)

                # Parse labels_applied JSON
                if response["labels_applied"]:
                    try:
                        response["labels_applied"] = json.loads(
                            response["labels_applied"]
                        )
                    except json.JSONDecodeError:
                        response["labels_applied"] = []
                else:
                    response["labels_applied"] = []

                responses.append(response)

            return responses

    async def get_stats(self, repo: Optional[str] = None) -> Dict:
        """Get response statistics"""
        async with aiosqlite.connect(self.db_path) as db:
            stats = {}

            # Base query conditions
            where_clause = "WHERE repo = ?" if repo else ""
            where_and = "WHERE repo = ? AND" if repo else "WHERE"
            params = [repo] if repo else []

            # Total responses
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM issue_responses {where_clause}", params
            )
            stats["total_responses"] = (await cursor.fetchone())[0]

            # Count by response type
            cursor = await db.execute(
                f"""
                SELECT response_type, COUNT(*) as count
                FROM issue_responses
                {where_clause}
                GROUP BY response_type
            """,
                params,
            )
            rows = await cursor.fetchall()
            stats["by_type"] = {row[0]: row[1] for row in rows}

            # Auto-posted vs manual
            cursor = await db.execute(
                f"""
                SELECT was_auto_posted, COUNT(*) as count
                FROM issue_responses
                {where_clause}
                GROUP BY was_auto_posted
            """,
                params,
            )
            rows = await cursor.fetchall()
            stats["auto_posted"] = 0
            stats["manual_posted"] = 0
            for row in rows:
                if row[0]:
                    stats["auto_posted"] = row[1]
                else:
                    stats["manual_posted"] = row[1]

            # Unique issues responded to
            cursor = await db.execute(
                f"""
                SELECT COUNT(DISTINCT issue_number)
                FROM issue_responses
                {where_clause}
            """,
                params,
            )
            stats["unique_issues"] = (await cursor.fetchone())[0]

            # Average confidence
            cursor = await db.execute(
                f"""
                SELECT AVG(confidence)
                FROM issue_responses
                {where_and} confidence IS NOT NULL
            """,
                params,
            )
            avg_confidence = (await cursor.fetchone())[0]
            stats["avg_confidence"] = avg_confidence if avg_confidence else 0.0

            # Recent activity (last 24 hours)
            cursor = await db.execute(
                f"""
                SELECT COUNT(*)
                FROM issue_responses
                {where_and} posted_at > datetime('now', '-1 day')
            """,
                params,
            )
            stats["recent_24h"] = (await cursor.fetchone())[0]

            return stats

    async def close(self):
        """Close the manager (for testing)"""
        # SQLite connections are closed automatically, but this method
        # provides a consistent interface for tests
        pass
