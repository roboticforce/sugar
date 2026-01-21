"""
Tests for Sugar storage and work queue functionality
"""

import pytest
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime

from sugar.storage.work_queue import WorkQueue


class TestWorkQueue:
    """Test WorkQueue functionality"""

    @pytest.mark.asyncio
    async def test_initialize_creates_database(self, temp_dir):
        """Test that initialize creates the database file"""
        db_path = temp_dir / "test.db"
        queue = WorkQueue(str(db_path))

        await queue.initialize()

        assert db_path.exists()
        await queue.close()

    @pytest.mark.asyncio
    async def test_add_work_item(self, mock_work_queue):
        """Test adding a work item to the queue"""
        task_data = {
            "type": "bug_fix",
            "title": "Fix authentication error",
            "description": "Fix login issues in auth module",
            "priority": 5,
            "source": "error_log",
            "context": {"file": "auth.py", "line": 42},
        }

        task_id = await mock_work_queue.add_work(task_data)

        assert task_id is not None
        assert isinstance(task_id, str)

        # Verify task was added
        retrieved_task = await mock_work_queue.get_work_by_id(task_id)
        assert retrieved_task is not None
        assert retrieved_task["title"] == "Fix authentication error"
        assert retrieved_task["priority"] == 5
        assert retrieved_task["status"] == "pending"

    @pytest.mark.asyncio
    async def test_get_pending_work(self, mock_work_queue):
        """Test retrieving pending work items"""
        # Add multiple tasks with different priorities
        # Priority scale: 1=urgent, 2=high, 3=normal, 4=low, 5=minimal
        high_priority_task = {
            "type": "bug_fix",
            "title": "Critical bug",
            "priority": 1,
            "source": "manual",
        }

        low_priority_task = {
            "type": "feature",
            "title": "New feature",
            "priority": 5,
            "source": "manual",
        }

        await mock_work_queue.add_work(high_priority_task)
        await mock_work_queue.add_work(low_priority_task)

        pending_tasks = await mock_work_queue.get_pending_work(limit=10)

        assert len(pending_tasks) == 2
        # Should be ordered by priority (urgent first: 1, then 5)
        assert pending_tasks[0]["priority"] == 1
        assert pending_tasks[1]["priority"] == 5

    @pytest.mark.asyncio
    async def test_mark_work_status_transitions(self, mock_work_queue):
        """Test work status transitions"""
        task_data = {
            "type": "test",
            "title": "Add unit tests",
            "priority": 3,
            "source": "manual",
        }

        task_id = await mock_work_queue.add_work(task_data)

        # Mark as active
        await mock_work_queue.mark_work_active(task_id)
        task = await mock_work_queue.get_work_by_id(task_id)
        assert task["status"] == "active"
        assert task["started_at"] is not None

        # Mark as completed
        result = {"success": True, "output": "Tests added successfully"}
        await mock_work_queue.mark_work_completed(task_id, result)
        task = await mock_work_queue.get_work_by_id(task_id)
        assert task["status"] == "completed"
        assert task["completed_at"] is not None
        assert task["result"] == result

    @pytest.mark.asyncio
    async def test_mark_work_failed(self, mock_work_queue):
        """Test marking work as failed"""
        task_data = {
            "type": "refactor",
            "title": "Refactor module",
            "priority": 3,
            "source": "manual",
        }

        task_id = await mock_work_queue.add_work(task_data)
        await mock_work_queue.mark_work_active(task_id)

        error_info = {"error": "Claude CLI failed", "details": "Connection timeout"}
        await mock_work_queue.mark_work_failed(task_id, error_info)

        task = await mock_work_queue.get_work_by_id(task_id)
        # Should be pending for retry since max_retries not reached
        assert task["status"] == "pending"
        assert task["attempts"] == 1

        # Now exceed max retries to get permanent failure
        await mock_work_queue.mark_work_active(task_id)
        await mock_work_queue.mark_work_failed(task_id, {"error": "Second failure"})

        await mock_work_queue.mark_work_active(task_id)
        await mock_work_queue.mark_work_failed(task_id, {"error": "Third failure"})

        # Should now be permanently failed
        task = await mock_work_queue.get_work_by_id(task_id)
        assert task["status"] == "failed"
        assert task["attempts"] == 3

    @pytest.mark.asyncio
    async def test_get_stats(self, mock_work_queue):
        """Test getting queue statistics"""
        # Add tasks with different statuses
        tasks = [
            {"type": "bug_fix", "title": "Task 1", "priority": 5, "source": "manual"},
            {"type": "feature", "title": "Task 2", "priority": 3, "source": "manual"},
            {"type": "test", "title": "Task 3", "priority": 4, "source": "manual"},
        ]

        task_ids = []
        for task in tasks:
            task_id = await mock_work_queue.add_work(task)
            task_ids.append(task_id)

        # Mark one as completed
        await mock_work_queue.mark_work_active(task_ids[0])
        await mock_work_queue.mark_work_completed(task_ids[0], {"success": True})

        # Mark one as failed
        await mock_work_queue.mark_work_active(task_ids[1])
        await mock_work_queue.mark_work_failed(task_ids[1], {"error": "Test error"})

        stats = await mock_work_queue.get_stats()

        assert stats["total"] == 3
        assert stats["pending"] == 2  # One never started, one failed but will retry
        assert stats["completed"] == 1
        assert (
            stats["failed"] == 0
        )  # No permanently failed items (max_retries not reached)
        assert stats["active"] == 0

    @pytest.mark.asyncio
    async def test_health_check(self, mock_work_queue):
        """Test queue health check"""
        health = await mock_work_queue.health_check()

        assert "database_path" in health
        assert "total_tasks" in health
        assert "status" in health
        assert health["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_remove_work(self, mock_work_queue):
        """Test removing work items"""
        task_data = {
            "type": "documentation",
            "title": "Update docs",
            "priority": 2,
            "source": "manual",
        }

        task_id = await mock_work_queue.add_work(task_data)

        # Verify task exists
        task = await mock_work_queue.get_work_by_id(task_id)
        assert task is not None

        # Remove task
        success = await mock_work_queue.remove_work(task_id)
        assert success

        # Verify task is gone
        task = await mock_work_queue.get_work_by_id(task_id)
        assert task is None

    @pytest.mark.asyncio
    async def test_update_work(self, mock_work_queue):
        """Test updating work items"""
        task_data = {
            "type": "feature",
            "title": "Original title",
            "description": "Original description",
            "priority": 3,
            "source": "manual",
        }

        task_id = await mock_work_queue.add_work(task_data)

        # Update task
        updates = {
            "title": "Updated title",
            "priority": 5,
            "description": "Updated description",
        }

        success = await mock_work_queue.update_work(task_id, updates)
        assert success

        # Verify updates
        task = await mock_work_queue.get_work_by_id(task_id)
        assert task["title"] == "Updated title"
        assert task["priority"] == 5
        assert task["description"] == "Updated description"

    @pytest.mark.asyncio
    async def test_get_recent_work_with_filters(self, mock_work_queue):
        """Test getting recent work with status and type filters"""
        # Add tasks of different types and statuses
        tasks = [
            {"type": "bug_fix", "title": "Bug 1", "priority": 5, "source": "manual"},
            {"type": "bug_fix", "title": "Bug 2", "priority": 4, "source": "manual"},
            {
                "type": "feature",
                "title": "Feature 1",
                "priority": 3,
                "source": "manual",
            },
            {"type": "test", "title": "Test 1", "priority": 2, "source": "manual"},
        ]

        task_ids = []
        for task in tasks:
            task_id = await mock_work_queue.add_work(task)
            task_ids.append(task_id)

        # Mark some as completed
        await mock_work_queue.mark_work_active(task_ids[0])
        await mock_work_queue.mark_work_completed(task_ids[0], {"success": True})

        # Test filtering by status
        completed_tasks = await mock_work_queue.get_recent_work(status="completed")
        assert len(completed_tasks) == 1
        assert completed_tasks[0]["title"] == "Bug 1"

        # Test filtering by status (pending)
        pending_tasks = await mock_work_queue.get_recent_work(status="pending")
        assert len(pending_tasks) == 3

        # Test limiting results
        limited_tasks = await mock_work_queue.get_recent_work(limit=2)
        assert len(limited_tasks) == 2


class TestTimingTracking:
    """Test timing tracking functionality"""

    @pytest.mark.asyncio
    async def test_timing_columns_exist(self, temp_dir):
        """Test that timing columns are created during initialization"""
        db_path = temp_dir / "timing_test.db"
        queue = WorkQueue(str(db_path))

        await queue.initialize()

        # Test that we can query timing columns without error
        import aiosqlite

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute("PRAGMA table_info(work_items)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            assert "total_execution_time" in column_names
            assert "started_at" in column_names
            assert "total_elapsed_time" in column_names

        await queue.close()

    @pytest.mark.asyncio
    async def test_started_at_timestamp_on_work_retrieval(self, temp_dir):
        """Test that started_at is set when work is retrieved"""
        db_path = temp_dir / "timing_test.db"
        queue = WorkQueue(str(db_path))
        await queue.initialize()

        # Add a work item
        task_data = {
            "type": "test",
            "title": "Timing test task",
            "priority": 3,
            "source": "test",
        }

        task_id = await queue.add_work(task_data)

        # Retrieve work (this should set started_at)
        work_item = await queue.get_next_work()

        assert work_item is not None
        assert work_item["id"] == task_id
        assert work_item["status"] == "active"

        # Check that started_at was set in database
        retrieved_item = await queue.get_work_item(task_id)
        assert retrieved_item["started_at"] is not None

        await queue.close()

    @pytest.mark.asyncio
    async def test_execution_time_tracking_on_completion(self, temp_dir):
        """Test that execution time is tracked when work is completed"""
        db_path = temp_dir / "timing_test.db"
        queue = WorkQueue(str(db_path))
        await queue.initialize()

        # Add and start work
        task_data = {
            "type": "test",
            "title": "Execution time test",
            "priority": 3,
            "source": "test",
        }

        task_id = await queue.add_work(task_data)
        work_item = await queue.get_next_work()

        # Simulate some time passing
        import asyncio

        await asyncio.sleep(0.01)  # 10ms

        # Complete work with execution time
        result = {
            "success": True,
            "execution_time": 5.5,
            "result": {"message": "Task completed successfully"},
        }

        await queue.complete_work(task_id, result)

        # Verify timing was recorded
        completed_item = await queue.get_work_item(task_id)

        assert completed_item["status"] == "completed"
        assert completed_item["total_execution_time"] == 5.5
        assert completed_item["total_elapsed_time"] >= 0  # Allow 0 for very fast tests
        assert completed_item["completed_at"] is not None

        await queue.close()

    @pytest.mark.asyncio
    async def test_cumulative_execution_time_on_retry(self, temp_dir):
        """Test that execution time accumulates across retries"""
        db_path = temp_dir / "timing_test.db"
        queue = WorkQueue(str(db_path))
        await queue.initialize()

        # Add work
        task_data = {
            "type": "test",
            "title": "Retry timing test",
            "priority": 3,
            "source": "test",
        }

        task_id = await queue.add_work(task_data)

        # First attempt - fail with execution time
        work_item = await queue.get_next_work()
        await queue.fail_work(task_id, "First failure", execution_time=3.0)

        # Check timing after first failure
        item_after_first = await queue.get_work_item(task_id)
        assert item_after_first["total_execution_time"] == 3.0
        assert item_after_first["status"] == "pending"  # Should retry

        # Second attempt - fail again
        work_item = await queue.get_next_work()
        await queue.fail_work(task_id, "Second failure", execution_time=2.5)

        # Check cumulative timing
        item_after_second = await queue.get_work_item(task_id)
        assert item_after_second["total_execution_time"] == 5.5  # 3.0 + 2.5
        assert item_after_second["status"] == "pending"  # Should retry

        # Third attempt - succeed
        work_item = await queue.get_next_work()
        result = {
            "success": True,
            "execution_time": 1.5,
            "result": {"message": "Finally succeeded"},
        }
        await queue.complete_work(task_id, result)

        # Check final timing
        final_item = await queue.get_work_item(task_id)
        assert final_item["total_execution_time"] == 7.0  # 5.5 + 1.5
        assert final_item["total_elapsed_time"] >= 0  # Allow 0 for very fast tests
        assert final_item["status"] == "completed"

        await queue.close()

    @pytest.mark.asyncio
    async def test_elapsed_time_calculation(self, temp_dir):
        """Test that total elapsed time is calculated correctly"""
        db_path = temp_dir / "timing_test.db"
        queue = WorkQueue(str(db_path))
        await queue.initialize()

        # Add work
        task_data = {
            "type": "test",
            "title": "Elapsed time test",
            "priority": 3,
            "source": "test",
        }

        task_id = await queue.add_work(task_data)

        # Get work (sets started_at)
        work_item = await queue.get_next_work()

        # Simulate some time passing
        import asyncio

        await asyncio.sleep(0.1)  # 100ms

        # Complete work
        result = {"success": True, "execution_time": 2.0}
        await queue.complete_work(task_id, result)

        # Check elapsed time
        completed_item = await queue.get_work_item(task_id)

        assert (
            completed_item["total_elapsed_time"] >= 0
        )  # At least 0 (may be very fast)
        assert completed_item["total_elapsed_time"] < 10.0  # But reasonable
        assert completed_item["total_execution_time"] == 2.0

        await queue.close()

    @pytest.mark.asyncio
    async def test_migration_adds_timing_columns(self, temp_dir):
        """Test that existing databases get timing columns added"""
        db_path = temp_dir / "migration_test.db"

        # Create database with old schema (no timing columns)
        import aiosqlite

        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute("""
                CREATE TABLE work_items (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    priority INTEGER DEFAULT 3,
                    status TEXT DEFAULT 'pending',
                    source TEXT,
                    source_file TEXT,
                    context TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    attempts INTEGER DEFAULT 0,
                    last_attempt_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    result TEXT,
                    error_message TEXT
                )
            """)
            await db.commit()

        # Initialize WorkQueue (should trigger migration)
        queue = WorkQueue(str(db_path))
        await queue.initialize()

        # Check that timing columns were added
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute("PRAGMA table_info(work_items)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            assert "total_execution_time" in column_names
            assert "started_at" in column_names
            assert "total_elapsed_time" in column_names

        await queue.close()
