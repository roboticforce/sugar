"""
Comprehensive tests for the Issue Responder integration

Tests IssueResponseManager and IssueResponderConfig
"""

import json
import pytest
import tempfile
import yaml
from pathlib import Path
from datetime import datetime

from sugar.storage.issue_response_manager import IssueResponseManager
from sugar.config.issue_responder_config import IssueResponderConfig

# ============================================================================
# IssueResponseManager Tests
# ============================================================================


class TestIssueResponseManager:
    """Test IssueResponseManager functionality"""

    @pytest.mark.asyncio
    async def test_initialize_creates_table(self, temp_dir):
        """Test that initialize creates the issue_responses table"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))

        await manager.initialize()

        # Verify database file was created
        assert db_path.exists()

        # Verify table structure by attempting to query it
        import aiosqlite

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute("PRAGMA table_info(issue_responses)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            # Verify all expected columns exist
            assert "id" in column_names
            assert "repo" in column_names
            assert "issue_number" in column_names
            assert "response_type" in column_names
            assert "work_item_id" in column_names
            assert "confidence" in column_names
            assert "posted_at" in column_names
            assert "response_content" in column_names
            assert "labels_applied" in column_names
            assert "was_auto_posted" in column_names

        await manager.close()

    @pytest.mark.asyncio
    async def test_initialize_is_idempotent(self, temp_dir):
        """Test that calling initialize multiple times is safe"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))

        # Initialize multiple times
        await manager.initialize()
        await manager.initialize()
        await manager.initialize()

        # Should still work fine
        assert db_path.exists()
        await manager.close()

    @pytest.mark.asyncio
    async def test_has_responded_returns_false_for_new_issue(self, temp_dir):
        """Test has_responded returns False for issues we haven't responded to"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Check for a response that doesn't exist
        has_responded = await manager.has_responded(
            repo="owner/repo", issue_number=123, response_type="initial"
        )

        assert has_responded is False
        await manager.close()

    @pytest.mark.asyncio
    async def test_has_responded_returns_true_after_recording(self, temp_dir):
        """Test has_responded returns True after we record a response"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Record a response
        await manager.record_response(
            repo="owner/repo",
            issue_number=123,
            response_type="initial",
            confidence=0.85,
            response_content="Thanks for reporting this issue!",
            labels_applied=["bug", "needs-triage"],
            was_auto_posted=True,
        )

        # Now check if we've responded
        has_responded = await manager.has_responded(
            repo="owner/repo", issue_number=123, response_type="initial"
        )

        assert has_responded is True
        await manager.close()

    @pytest.mark.asyncio
    async def test_has_responded_differentiates_response_types(self, temp_dir):
        """Test that has_responded differentiates between response types"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Record an initial response
        await manager.record_response(
            repo="owner/repo",
            issue_number=123,
            response_type="initial",
            confidence=0.85,
            response_content="Initial response",
        )

        # Check for initial response - should be True
        has_initial = await manager.has_responded(
            repo="owner/repo", issue_number=123, response_type="initial"
        )
        assert has_initial is True

        # Check for follow-up response - should be False
        has_followup = await manager.has_responded(
            repo="owner/repo", issue_number=123, response_type="follow_up"
        )
        assert has_followup is False

        await manager.close()

    @pytest.mark.asyncio
    async def test_record_response_stores_data(self, temp_dir):
        """Test that record_response properly stores all fields"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Record a response with all fields
        response_id = await manager.record_response(
            repo="owner/test-repo",
            issue_number=456,
            response_type="initial",
            confidence=0.92,
            response_content="This is a comprehensive response about the issue.",
            labels_applied=["bug", "priority-high", "needs-review"],
            was_auto_posted=True,
            work_item_id="work-123",
        )

        # Verify response ID was returned
        assert response_id is not None
        assert isinstance(response_id, str)

        # Retrieve and verify the data
        history = await manager.get_response_history(
            repo="owner/test-repo", issue_number=456
        )

        assert len(history) == 1
        response = history[0]

        assert response["id"] == response_id
        assert response["repo"] == "owner/test-repo"
        assert response["issue_number"] == 456
        assert response["response_type"] == "initial"
        assert response["confidence"] == 0.92
        assert (
            response["response_content"]
            == "This is a comprehensive response about the issue."
        )
        assert response["labels_applied"] == ["bug", "priority-high", "needs-review"]
        assert response["was_auto_posted"] == 1  # SQLite stores boolean as 0/1
        assert response["work_item_id"] == "work-123"
        assert response["posted_at"] is not None

        await manager.close()

    @pytest.mark.asyncio
    async def test_record_response_with_minimal_data(self, temp_dir):
        """Test recording response with only required fields"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Record with only required fields
        response_id = await manager.record_response(
            repo="owner/repo",
            issue_number=789,
            response_type="initial",
            confidence=0.75,
            response_content="Minimal response",
        )

        assert response_id is not None

        # Verify optional fields are handled correctly
        history = await manager.get_response_history(
            repo="owner/repo", issue_number=789
        )
        response = history[0]

        assert response["labels_applied"] == []  # Empty list for None
        assert response["was_auto_posted"] == 0  # False by default
        assert response["work_item_id"] is None

        await manager.close()

    @pytest.mark.asyncio
    async def test_record_response_prevents_duplicates(self, temp_dir):
        """Test that recording duplicate response type replaces existing"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Record first response
        first_id = await manager.record_response(
            repo="owner/repo",
            issue_number=100,
            response_type="initial",
            confidence=0.80,
            response_content="First response",
        )

        # Record duplicate (same repo, issue_number, response_type)
        second_id = await manager.record_response(
            repo="owner/repo",
            issue_number=100,
            response_type="initial",
            confidence=0.90,
            response_content="Updated response",
        )

        # IDs should be different (INSERT OR REPLACE generates new ID)
        assert first_id != second_id

        # Should only have one record (the replacement)
        history = await manager.get_response_history(
            repo="owner/repo", issue_number=100
        )
        assert len(history) == 1
        assert history[0]["response_content"] == "Updated response"
        assert history[0]["confidence"] == 0.90

        await manager.close()

    @pytest.mark.asyncio
    async def test_get_response_history_for_issue(self, temp_dir):
        """Test getting history for a specific issue"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Add multiple responses for the same issue
        await manager.record_response(
            repo="owner/repo",
            issue_number=200,
            response_type="initial",
            confidence=0.85,
            response_content="Initial response",
        )

        await manager.record_response(
            repo="owner/repo",
            issue_number=200,
            response_type="follow_up",
            confidence=0.78,
            response_content="Follow-up response",
        )

        # Add response for a different issue (should not appear)
        await manager.record_response(
            repo="owner/repo",
            issue_number=201,
            response_type="initial",
            confidence=0.90,
            response_content="Different issue",
        )

        # Get history for issue 200
        history = await manager.get_response_history(
            repo="owner/repo", issue_number=200
        )

        assert len(history) == 2
        assert all(r["issue_number"] == 200 for r in history)
        assert any(r["response_type"] == "initial" for r in history)
        assert any(r["response_type"] == "follow_up" for r in history)

        await manager.close()

    @pytest.mark.asyncio
    async def test_get_response_history_for_repo(self, temp_dir):
        """Test getting all responses for a repo"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Add responses for multiple issues in same repo
        await manager.record_response(
            repo="owner/repo1",
            issue_number=100,
            response_type="initial",
            confidence=0.85,
            response_content="Response 1",
        )

        await manager.record_response(
            repo="owner/repo1",
            issue_number=101,
            response_type="initial",
            confidence=0.90,
            response_content="Response 2",
        )

        await manager.record_response(
            repo="owner/repo1",
            issue_number=102,
            response_type="initial",
            confidence=0.75,
            response_content="Response 3",
        )

        # Add response for different repo (should not appear)
        await manager.record_response(
            repo="owner/repo2",
            issue_number=100,
            response_type="initial",
            confidence=0.88,
            response_content="Different repo",
        )

        # Get all responses for repo1
        history = await manager.get_response_history(repo="owner/repo1")

        assert len(history) == 3
        assert all(r["repo"] == "owner/repo1" for r in history)
        issue_numbers = [r["issue_number"] for r in history]
        assert 100 in issue_numbers
        assert 101 in issue_numbers
        assert 102 in issue_numbers

        await manager.close()

    @pytest.mark.asyncio
    async def test_get_response_history_ordered_by_posted_at(self, temp_dir):
        """Test that history is ordered by posted_at DESC"""
        import asyncio
        import time

        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Add multiple responses with delays to ensure different timestamps
        # SQLite CURRENT_TIMESTAMP has second precision, so we need at least 1 second delay
        await manager.record_response(
            repo="owner/repo",
            issue_number=100,
            response_type="initial",
            confidence=0.85,
            response_content="First",
        )

        time.sleep(1.1)  # Ensure different second in timestamp

        await manager.record_response(
            repo="owner/repo",
            issue_number=101,
            response_type="initial",
            confidence=0.90,
            response_content="Second",
        )

        time.sleep(1.1)  # Ensure different second in timestamp

        await manager.record_response(
            repo="owner/repo",
            issue_number=102,
            response_type="initial",
            confidence=0.75,
            response_content="Third",
        )

        history = await manager.get_response_history(repo="owner/repo")

        # Should be in DESC order (most recent first)
        assert len(history) == 3
        # Most recent should be "Third"
        assert history[0]["response_content"] == "Third"
        assert history[2]["response_content"] == "First"

        await manager.close()

    @pytest.mark.asyncio
    async def test_get_response_history_empty(self, temp_dir):
        """Test getting history when no responses exist"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Get history for non-existent repo
        history = await manager.get_response_history(repo="owner/nonexistent")

        assert history == []
        await manager.close()

    @pytest.mark.asyncio
    async def test_get_stats(self, temp_dir):
        """Test statistics calculation"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Add various responses
        await manager.record_response(
            repo="owner/repo1",
            issue_number=100,
            response_type="initial",
            confidence=0.85,
            response_content="Response 1",
            was_auto_posted=True,
        )

        await manager.record_response(
            repo="owner/repo1",
            issue_number=100,
            response_type="follow_up",
            confidence=0.75,
            response_content="Response 2",
            was_auto_posted=False,
        )

        await manager.record_response(
            repo="owner/repo1",
            issue_number=101,
            response_type="initial",
            confidence=0.90,
            response_content="Response 3",
            was_auto_posted=True,
        )

        await manager.record_response(
            repo="owner/repo2",
            issue_number=200,
            response_type="initial",
            confidence=0.95,
            response_content="Response 4",
            was_auto_posted=True,
        )

        # Get stats for all repos
        stats = await manager.get_stats()

        assert stats["total_responses"] == 4
        assert stats["by_type"]["initial"] == 3
        assert stats["by_type"]["follow_up"] == 1
        assert stats["auto_posted"] == 3
        assert stats["manual_posted"] == 1
        assert stats["unique_issues"] == 3  # Issues 100, 101, 200
        assert 0.86 <= stats["avg_confidence"] <= 0.87  # (0.85+0.75+0.90+0.95)/4

        await manager.close()

    @pytest.mark.asyncio
    async def test_get_stats_for_specific_repo(self, temp_dir):
        """Test statistics for a specific repository"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Add responses for repo1
        await manager.record_response(
            repo="owner/repo1",
            issue_number=100,
            response_type="initial",
            confidence=0.80,
            response_content="Response 1",
            was_auto_posted=True,
        )

        await manager.record_response(
            repo="owner/repo1",
            issue_number=101,
            response_type="initial",
            confidence=0.90,
            response_content="Response 2",
            was_auto_posted=False,
        )

        # Add responses for repo2 (should not be included)
        await manager.record_response(
            repo="owner/repo2",
            issue_number=200,
            response_type="initial",
            confidence=0.95,
            response_content="Response 3",
            was_auto_posted=True,
        )

        # Get stats for repo1 only
        stats = await manager.get_stats(repo="owner/repo1")

        assert stats["total_responses"] == 2
        assert stats["by_type"]["initial"] == 2
        assert stats["auto_posted"] == 1
        assert stats["manual_posted"] == 1
        assert stats["unique_issues"] == 2
        # Use approximate comparison for floating point
        assert abs(stats["avg_confidence"] - 0.85) < 0.0001  # (0.80+0.90)/2

        await manager.close()

    @pytest.mark.asyncio
    async def test_get_stats_empty(self, temp_dir):
        """Test statistics when no responses exist"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        stats = await manager.get_stats()

        assert stats["total_responses"] == 0
        assert stats["by_type"] == {}
        assert stats["auto_posted"] == 0
        assert stats["manual_posted"] == 0
        assert stats["unique_issues"] == 0
        assert stats["avg_confidence"] == 0.0

        await manager.close()

    @pytest.mark.asyncio
    async def test_labels_applied_json_parsing(self, temp_dir):
        """Test that labels_applied is correctly stored and parsed as JSON"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Test with labels
        await manager.record_response(
            repo="owner/repo",
            issue_number=100,
            response_type="initial",
            confidence=0.85,
            response_content="Response",
            labels_applied=["bug", "high-priority", "needs-triage"],
        )

        # Test with empty labels
        await manager.record_response(
            repo="owner/repo",
            issue_number=101,
            response_type="initial",
            confidence=0.85,
            response_content="Response",
            labels_applied=[],
        )

        # Test with None labels
        await manager.record_response(
            repo="owner/repo",
            issue_number=102,
            response_type="initial",
            confidence=0.85,
            response_content="Response",
            labels_applied=None,
        )

        # Verify parsing
        history_100 = await manager.get_response_history(
            repo="owner/repo", issue_number=100
        )
        assert history_100[0]["labels_applied"] == [
            "bug",
            "high-priority",
            "needs-triage",
        ]

        history_101 = await manager.get_response_history(
            repo="owner/repo", issue_number=101
        )
        assert history_101[0]["labels_applied"] == []

        history_102 = await manager.get_response_history(
            repo="owner/repo", issue_number=102
        )
        assert history_102[0]["labels_applied"] == []

        await manager.close()

    @pytest.mark.asyncio
    async def test_stats_recent_24h(self, temp_dir):
        """Test recent_24h statistics calculation"""
        db_path = temp_dir / "issue_responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Add a recent response (within last 24 hours)
        await manager.record_response(
            repo="owner/repo",
            issue_number=100,
            response_type="initial",
            confidence=0.85,
            response_content="Recent response",
        )

        stats = await manager.get_stats()

        # Should have at least 1 recent response
        assert stats["recent_24h"] >= 1

        await manager.close()


# ============================================================================
# IssueResponderConfig Tests
# ============================================================================


class TestIssueResponderConfig:
    """Test IssueResponderConfig functionality"""

    def test_default_values(self):
        """Test that defaults are set correctly"""
        config = IssueResponderConfig()

        assert config.enabled is False
        assert config.auto_post_threshold == 0.8
        assert config.max_response_length == 2000
        assert config.response_delay_seconds == 0
        assert config.rate_limit_per_hour == 10
        assert config.respond_to_labels == []
        assert config.skip_labels == ["wontfix", "duplicate", "stale"]
        assert config.skip_bot_issues is True
        assert config.handle_follow_ups == "ignore"
        assert config.model == "claude-sonnet-4-20250514"

    def test_from_dict(self):
        """Test loading from dictionary"""
        config_data = {
            "enabled": True,
            "auto_post_threshold": 0.9,
            "max_response_length": 1500,
            "response_delay_seconds": 60,
            "rate_limit_per_hour": 5,
            "respond_to_labels": ["question", "help-wanted"],
            "skip_labels": ["wontfix", "invalid"],
            "skip_bot_issues": False,
            "handle_follow_ups": "queue",
            "model": "claude-opus-4-5-20251101",
        }

        config = IssueResponderConfig.from_dict(config_data)

        assert config.enabled is True
        assert config.auto_post_threshold == 0.9
        assert config.max_response_length == 1500
        assert config.response_delay_seconds == 60
        assert config.rate_limit_per_hour == 5
        assert config.respond_to_labels == ["question", "help-wanted"]
        assert config.skip_labels == ["wontfix", "invalid"]
        assert config.skip_bot_issues is False
        assert config.handle_follow_ups == "queue"
        assert config.model == "claude-opus-4-5-20251101"

    def test_from_dict_with_partial_data(self):
        """Test loading with only some fields specified"""
        config_data = {
            "enabled": True,
            "auto_post_threshold": 0.95,
            "respond_to_labels": ["bug", "question"],
        }

        config = IssueResponderConfig.from_dict(config_data)

        # Specified fields should be set
        assert config.enabled is True
        assert config.auto_post_threshold == 0.95
        assert config.respond_to_labels == ["bug", "question"]

        # Unspecified fields should have defaults
        assert config.max_response_length == 2000
        assert config.response_delay_seconds == 0
        assert config.skip_bot_issues is True
        assert config.handle_follow_ups == "ignore"

    def test_from_dict_ignores_unknown_fields(self):
        """Test that from_dict ignores fields not in the dataclass"""
        config_data = {
            "enabled": True,
            "auto_post_threshold": 0.85,
            "unknown_field": "should be ignored",
            "another_unknown": 123,
        }

        # Should not raise an error
        config = IssueResponderConfig.from_dict(config_data)

        assert config.enabled is True
        assert config.auto_post_threshold == 0.85
        assert not hasattr(config, "unknown_field")

    def test_validate_valid_config(self):
        """Test validation passes for valid config"""
        config = IssueResponderConfig(
            enabled=True,
            auto_post_threshold=0.85,
            max_response_length=2000,
            response_delay_seconds=30,
            rate_limit_per_hour=10,
            handle_follow_ups="queue",
        )

        errors = config.validate()

        assert errors == []

    def test_validate_invalid_threshold_too_high(self):
        """Test validation fails for threshold > 1.0"""
        config = IssueResponderConfig(auto_post_threshold=1.5)

        errors = config.validate()

        assert len(errors) == 1
        assert "auto_post_threshold" in errors[0]
        assert "between 0.0 and 1.0" in errors[0]

    def test_validate_invalid_threshold_too_low(self):
        """Test validation fails for threshold < 0.0"""
        config = IssueResponderConfig(auto_post_threshold=-0.1)

        errors = config.validate()

        assert len(errors) == 1
        assert "auto_post_threshold" in errors[0]
        assert "between 0.0 and 1.0" in errors[0]

    def test_validate_invalid_max_response_length(self):
        """Test validation fails for non-positive max_response_length"""
        config = IssueResponderConfig(max_response_length=0)

        errors = config.validate()

        assert len(errors) == 1
        assert "max_response_length" in errors[0]
        assert "must be positive" in errors[0]

        config2 = IssueResponderConfig(max_response_length=-100)
        errors2 = config2.validate()
        assert len(errors2) == 1

    def test_validate_invalid_response_delay(self):
        """Test validation fails for negative response_delay_seconds"""
        config = IssueResponderConfig(response_delay_seconds=-10)

        errors = config.validate()

        assert len(errors) == 1
        assert "response_delay_seconds" in errors[0]
        assert "non-negative" in errors[0]

    def test_validate_invalid_rate_limit(self):
        """Test validation fails for negative rate_limit_per_hour"""
        config = IssueResponderConfig(rate_limit_per_hour=-5)

        errors = config.validate()

        assert len(errors) == 1
        assert "rate_limit_per_hour" in errors[0]
        assert "non-negative" in errors[0]

    def test_validate_invalid_handle_follow_ups(self):
        """Test validation fails for invalid handle_follow_ups value"""
        config = IssueResponderConfig(handle_follow_ups="invalid_value")

        errors = config.validate()

        assert len(errors) == 1
        assert "handle_follow_ups" in errors[0]
        assert "invalid_value" in errors[0]
        assert "ignore" in errors[0]
        assert "queue" in errors[0]
        assert "auto" in errors[0]

    def test_validate_multiple_errors(self):
        """Test validation returns all errors when multiple issues exist"""
        config = IssueResponderConfig(
            auto_post_threshold=1.5,
            max_response_length=-100,
            response_delay_seconds=-10,
            rate_limit_per_hour=-5,
            handle_follow_ups="bad_value",
        )

        errors = config.validate()

        # Should have 5 errors
        assert len(errors) == 5
        assert any("auto_post_threshold" in e for e in errors)
        assert any("max_response_length" in e for e in errors)
        assert any("response_delay_seconds" in e for e in errors)
        assert any("rate_limit_per_hour" in e for e in errors)
        assert any("handle_follow_ups" in e for e in errors)

    def test_validate_edge_case_boundaries(self):
        """Test validation edge cases at boundaries"""
        # Valid boundary values
        config_min = IssueResponderConfig(
            auto_post_threshold=0.0,
            max_response_length=1,
            response_delay_seconds=0,
            rate_limit_per_hour=0,
        )
        assert config_min.validate() == []

        config_max = IssueResponderConfig(
            auto_post_threshold=1.0,
            max_response_length=100000,
            response_delay_seconds=999999,
            rate_limit_per_hour=999999,
        )
        assert config_max.validate() == []

    def test_load_from_file(self, temp_dir):
        """Test loading from YAML file"""
        config_file = temp_dir / "config.yaml"

        config_data = {
            "sugar": {
                "issue_responder": {
                    "enabled": True,
                    "auto_post_threshold": 0.9,
                    "max_response_length": 1800,
                    "response_delay_seconds": 120,
                    "rate_limit_per_hour": 15,
                    "respond_to_labels": ["question", "help-wanted", "bug"],
                    "skip_labels": ["wontfix", "duplicate"],
                    "skip_bot_issues": False,
                    "handle_follow_ups": "auto",
                    "model": "claude-opus-4-5-20251101",
                }
            }
        }

        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = IssueResponderConfig.load_from_file(str(config_file))

        assert config.enabled is True
        assert config.auto_post_threshold == 0.9
        assert config.max_response_length == 1800
        assert config.response_delay_seconds == 120
        assert config.rate_limit_per_hour == 15
        assert config.respond_to_labels == ["question", "help-wanted", "bug"]
        assert config.skip_labels == ["wontfix", "duplicate"]
        assert config.skip_bot_issues is False
        assert config.handle_follow_ups == "auto"
        assert config.model == "claude-opus-4-5-20251101"

    def test_load_from_file_with_partial_config(self, temp_dir):
        """Test loading from YAML file with only some fields specified"""
        config_file = temp_dir / "config.yaml"

        config_data = {
            "sugar": {
                "issue_responder": {
                    "enabled": True,
                    "auto_post_threshold": 0.95,
                }
            }
        }

        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = IssueResponderConfig.load_from_file(str(config_file))

        # Specified fields
        assert config.enabled is True
        assert config.auto_post_threshold == 0.95

        # Defaults for unspecified fields
        assert config.max_response_length == 2000
        assert config.skip_bot_issues is True
        assert config.handle_follow_ups == "ignore"

    def test_load_from_file_missing_file(self, temp_dir):
        """Test that loading from non-existent file raises FileNotFoundError"""
        config_file = temp_dir / "nonexistent.yaml"

        with pytest.raises(FileNotFoundError):
            IssueResponderConfig.load_from_file(str(config_file))

    def test_load_from_file_missing_sugar_section(self, temp_dir):
        """Test that loading without 'sugar' section raises KeyError"""
        config_file = temp_dir / "config.yaml"

        config_data = {"other_section": {"key": "value"}}

        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        with pytest.raises(KeyError, match="sugar"):
            IssueResponderConfig.load_from_file(str(config_file))

    def test_load_from_file_missing_issue_responder_section(self, temp_dir):
        """Test that loading without 'issue_responder' section raises KeyError"""
        config_file = temp_dir / "config.yaml"

        config_data = {"sugar": {"other_key": "value"}}

        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        with pytest.raises(KeyError, match="issue_responder"):
            IssueResponderConfig.load_from_file(str(config_file))

    def test_load_from_file_malformed_yaml(self, temp_dir):
        """Test that loading malformed YAML raises yaml.YAMLError"""
        config_file = temp_dir / "config.yaml"

        # Write invalid YAML
        with open(config_file, "w") as f:
            f.write("invalid: yaml: content:\n  - broken")
            f.write("\n  this is not: [valid yaml")

        with pytest.raises(yaml.YAMLError):
            IssueResponderConfig.load_from_file(str(config_file))

    def test_handle_follow_ups_valid_values(self):
        """Test all valid handle_follow_ups values"""
        for value in ["ignore", "queue", "auto"]:
            config = IssueResponderConfig(handle_follow_ups=value)
            errors = config.validate()
            assert errors == [], f"Valid value '{value}' should not produce errors"

    def test_config_immutability_not_enforced(self):
        """Test that config values can be modified (dataclass is mutable by default)"""
        config = IssueResponderConfig()

        # Dataclasses are mutable by default
        config.enabled = True
        config.auto_post_threshold = 0.95

        assert config.enabled is True
        assert config.auto_post_threshold == 0.95


# ============================================================================
# Integration Tests - Using both components together
# ============================================================================


class TestIssueResponderIntegration:
    """Test IssueResponseManager and IssueResponderConfig working together"""

    @pytest.mark.asyncio
    async def test_workflow_with_config_and_manager(self, temp_dir):
        """Test a typical workflow using both config and manager"""
        # Load config
        config_file = temp_dir / "config.yaml"
        config_data = {
            "sugar": {
                "issue_responder": {
                    "enabled": True,
                    "auto_post_threshold": 0.85,
                    "respond_to_labels": ["question"],
                }
            }
        }

        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = IssueResponderConfig.load_from_file(str(config_file))

        # Validate config
        errors = config.validate()
        assert errors == []
        assert config.enabled is True
        assert config.auto_post_threshold == 0.85

        # Use manager to record response
        db_path = temp_dir / "responses.db"
        manager = IssueResponseManager(str(db_path))
        await manager.initialize()

        # Simulate recording a response based on config settings
        confidence = 0.90  # Above threshold

        if confidence >= config.auto_post_threshold:
            await manager.record_response(
                repo="owner/repo",
                issue_number=123,
                response_type="initial",
                confidence=confidence,
                response_content="Automated response",
                was_auto_posted=True,
            )

        # Verify response was recorded
        has_responded = await manager.has_responded(
            repo="owner/repo", issue_number=123, response_type="initial"
        )
        assert has_responded is True

        # Check stats
        stats = await manager.get_stats()
        assert stats["total_responses"] == 1
        assert stats["auto_posted"] == 1

        await manager.close()

    @pytest.mark.asyncio
    async def test_config_validation_prevents_bad_data(self, temp_dir):
        """Test that config validation catches errors before using manager"""
        config = IssueResponderConfig(
            enabled=True,
            auto_post_threshold=1.5,  # Invalid
            handle_follow_ups="bad",  # Invalid
        )

        errors = config.validate()
        assert len(errors) >= 2

        # In a real application, we wouldn't proceed to use the manager
        # if validation failed
        assert errors != [], "Should catch validation errors before proceeding"
