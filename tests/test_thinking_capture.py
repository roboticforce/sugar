"""
Tests for thinking capture functionality
"""

import os
import pytest
import tempfile
from datetime import datetime
from sugar.executor.thinking_display import (
    ThinkingCapture,
    ThinkingBlock,
    read_thinking_log,
    list_thinking_logs,
)


class TestThinkingBlock:
    """Test ThinkingBlock dataclass"""

    def test_basic_thinking_block(self):
        """Test creating a basic thinking block"""
        block = ThinkingBlock(
            timestamp=datetime.now(),
            content="Test thinking content",
        )

        assert block.content == "Test thinking content"
        assert block.tool_use is None
        assert block.signature is None

    def test_thinking_block_with_tool(self):
        """Test thinking block with tool use"""
        block = ThinkingBlock(
            timestamp=datetime.now(),
            content="Considering reading a file",
            tool_use="Read",
            signature="sig123",
        )

        assert block.content == "Considering reading a file"
        assert block.tool_use == "Read"
        assert block.signature == "sig123"


class TestThinkingCapture:
    """Test ThinkingCapture class"""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Change to temp dir for tests
            original_dir = os.getcwd()
            os.chdir(tmpdir)
            yield tmpdir
            os.chdir(original_dir)

    def test_thinking_capture_initialization(self, temp_dir):
        """Test initializing thinking capture"""
        capture = ThinkingCapture(
            task_id="test-123",
            task_title="Test Task",
        )

        assert capture.task_id == "test-123"
        assert capture.task_title == "Test Task"
        assert len(capture.thinking_blocks) == 0
        assert capture.log_to_file is True

    def test_capture_thinking_block(self, temp_dir):
        """Test capturing a thinking block"""
        capture = ThinkingCapture(
            task_id="test-123",
            task_title="Test Task",
        )

        capture.capture(
            thinking_content="This is my reasoning about the problem",
            tool_use="Read",
        )

        assert len(capture.thinking_blocks) == 1
        assert capture.thinking_blocks[0].content == "This is my reasoning about the problem"
        assert capture.thinking_blocks[0].tool_use == "Read"

    def test_capture_multiple_blocks(self, temp_dir):
        """Test capturing multiple thinking blocks"""
        capture = ThinkingCapture(
            task_id="test-123",
            task_title="Test Task",
        )

        capture.capture("First thought")
        capture.capture("Second thought", tool_use="Write")
        capture.capture("Third thought", tool_use="Bash")

        assert len(capture.thinking_blocks) == 3

    def test_get_summary(self, temp_dir):
        """Test getting thinking summary"""
        capture = ThinkingCapture(
            task_id="test-123",
            task_title="Test Task",
        )

        # No thinking captured
        summary = capture.get_summary()
        assert "No thinking captured" in summary

        # With thinking
        capture.capture("Some thinking" * 100)  # Long thinking
        summary = capture.get_summary()
        assert "1 thinking blocks" in summary
        assert ".sugar/thinking/test-123.md" in summary

    def test_get_stats(self, temp_dir):
        """Test getting thinking statistics"""
        capture = ThinkingCapture(
            task_id="test-123",
            task_title="Test Task",
        )

        # No thinking
        stats = capture.get_stats()
        assert stats["count"] == 0
        assert stats["total_characters"] == 0

        # With thinking
        capture.capture("Thinking 1" * 10, tool_use="Read")
        capture.capture("Thinking 2" * 20, tool_use="Write")
        capture.capture("Thinking 3" * 5)

        stats = capture.get_stats()
        assert stats["count"] == 3
        assert stats["total_characters"] > 0
        assert stats["average_length"] > 0
        assert "Read" in stats["tool_uses_considered"]
        assert "Write" in stats["tool_uses_considered"]
        assert "first_thinking" in stats
        assert "last_thinking" in stats

    def test_thinking_log_file_creation(self, temp_dir):
        """Test that thinking log file is created"""
        capture = ThinkingCapture(
            task_id="test-123",
            task_title="Test Task",
        )

        capture.capture("Test thinking content")
        capture.finalize()

        # Check file exists
        log_path = ".sugar/thinking/test-123.md"
        assert os.path.exists(log_path)

        # Check file content
        with open(log_path, "r") as f:
            content = f.read()
            assert "Test Task" in content
            assert "test-123" in content
            assert "Test thinking content" in content

    def test_thinking_log_without_file(self, temp_dir):
        """Test thinking capture without file logging"""
        capture = ThinkingCapture(
            task_id="test-123",
            task_title="Test Task",
            log_to_file=False,
        )

        capture.capture("Test thinking")
        capture.finalize()

        # File should not be created
        log_path = ".sugar/thinking/test-123.md"
        assert not os.path.exists(log_path)

        # But thinking should still be captured in memory
        assert len(capture.thinking_blocks) == 1

    def test_skip_empty_thinking(self, temp_dir):
        """Test that empty thinking is skipped"""
        capture = ThinkingCapture(
            task_id="test-123",
            task_title="Test Task",
        )

        capture.capture("")
        capture.capture("   ")
        capture.capture("Valid thinking")

        assert len(capture.thinking_blocks) == 1
        assert capture.thinking_blocks[0].content == "Valid thinking"


class TestThinkingReadFunctions:
    """Test helper functions for reading thinking logs"""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing"""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_dir = os.getcwd()
            os.chdir(tmpdir)
            yield tmpdir
            os.chdir(original_dir)

    def test_read_thinking_log(self, temp_dir):
        """Test reading a thinking log"""
        # Create a thinking log
        capture = ThinkingCapture(
            task_id="read-test",
            task_title="Read Test",
        )
        capture.capture("Test thinking content")
        capture.finalize()

        # Read it back
        content = read_thinking_log("read-test")
        assert content is not None
        assert "Test thinking content" in content

    def test_read_nonexistent_log(self, temp_dir):
        """Test reading a non-existent log"""
        content = read_thinking_log("nonexistent")
        assert content is None

    def test_list_thinking_logs(self, temp_dir):
        """Test listing thinking logs"""
        # Create multiple logs
        for i in range(3):
            capture = ThinkingCapture(
                task_id=f"task-{i}",
                task_title=f"Task {i}",
            )
            capture.capture(f"Thinking {i}")
            capture.finalize()

        # List them
        logs = list_thinking_logs()
        assert len(logs) == 3

        # Check structure
        task_id, log_path, modified_time = logs[0]
        assert task_id.startswith("task-")
        assert log_path.endswith(".md")
        assert isinstance(modified_time, datetime)

    def test_list_no_logs(self, temp_dir):
        """Test listing when no logs exist"""
        logs = list_thinking_logs()
        assert len(logs) == 0


class TestThinkingIntegration:
    """Integration tests for thinking capture"""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing"""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_dir = os.getcwd()
            os.chdir(tmpdir)
            yield tmpdir
            os.chdir(original_dir)

    def test_full_thinking_capture_workflow(self, temp_dir):
        """Test complete workflow of capturing thinking"""
        # Setup
        task_id = "workflow-test"
        capture = ThinkingCapture(
            task_id=task_id,
            task_title="Workflow Test Task",
        )

        # Simulate thinking during execution
        capture.capture("First, I need to understand the requirements")
        capture.capture("Now I'll read the relevant files", tool_use="Read")
        capture.capture("I should write the implementation", tool_use="Write")
        capture.capture("Finally, I'll test the changes", tool_use="Bash")

        # Finalize
        capture.finalize()

        # Verify results
        assert len(capture.thinking_blocks) == 4

        # Check stats
        stats = capture.get_stats()
        assert stats["count"] == 4
        assert len(stats["tool_uses_considered"]) == 3

        # Check file
        content = read_thinking_log(task_id)
        assert content is not None
        assert "understand the requirements" in content
        assert "Read" in content
        assert "Write" in content
        assert "Bash" in content
        assert "Summary" in content

        # Check it appears in list
        logs = list_thinking_logs()
        task_ids = [log[0] for log in logs]
        assert task_id in task_ids
