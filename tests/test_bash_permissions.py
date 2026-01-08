"""
Tests for wildcard bash permissions feature.

Tests the integration of bash permissions from task types into
the quality gate hooks system.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from sugar.agent.hooks import QualityGateHooks
from sugar.storage.task_type_manager import TaskTypeManager


class TestBashPermissionsPatternMatching:
    """Test bash permission pattern matching logic"""

    def test_simple_wildcard_pattern(self):
        """Test simple wildcard patterns like 'pytest *'"""
        hooks = QualityGateHooks(bash_permissions=["pytest *"])

        assert hooks._is_bash_command_allowed("pytest tests/")
        assert hooks._is_bash_command_allowed("pytest -v")
        assert hooks._is_bash_command_allowed("pytest test_file.py")
        assert not hooks._is_bash_command_allowed("python test.py")
        assert not hooks._is_bash_command_allowed("git status")

    def test_multiple_patterns(self):
        """Test multiple permission patterns"""
        hooks = QualityGateHooks(bash_permissions=[
            "pytest *",
            "git status*",
            "git diff*",
        ])

        assert hooks._is_bash_command_allowed("pytest tests/")
        assert hooks._is_bash_command_allowed("git status")
        assert hooks._is_bash_command_allowed("git status --short")
        assert hooks._is_bash_command_allowed("git diff HEAD")
        assert not hooks._is_bash_command_allowed("git push")
        assert not hooks._is_bash_command_allowed("rm -rf test")

    def test_exact_match(self):
        """Test exact command matching"""
        hooks = QualityGateHooks(bash_permissions=["ls"])

        assert hooks._is_bash_command_allowed("ls")
        assert not hooks._is_bash_command_allowed("ls -la")

    def test_empty_permissions_allows_all(self):
        """Test that empty bash_permissions list allows all commands"""
        hooks = QualityGateHooks(bash_permissions=[])

        # Empty list should allow all commands (except dangerous ones)
        assert hooks._is_bash_command_allowed("pytest tests/")
        assert hooks._is_bash_command_allowed("git push")
        assert hooks._is_bash_command_allowed("npm install")

    def test_none_permissions_no_whitelist(self):
        """Test that None bash_permissions means no whitelist checking"""
        hooks = QualityGateHooks(bash_permissions=None)

        # None means no whitelist, only dangerous command check applies
        assert hooks._is_bash_command_allowed("pytest tests/")
        assert hooks._is_bash_command_allowed("git push")


class TestBashPermissionsHookIntegration:
    """Test bash permissions in the hook security check"""

    @pytest.mark.asyncio
    async def test_allowed_command_passes(self):
        """Test that allowed commands pass security check"""
        hooks = QualityGateHooks(bash_permissions=["pytest *", "git status*"])

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/test_file.py"},
            "hook_event_name": "PreToolUse",
        }

        result = await hooks.pre_tool_security_check(input_data, "test_id", None)

        # Should return empty dict (allowed)
        assert result == {}

    @pytest.mark.asyncio
    async def test_disallowed_command_blocked(self):
        """Test that disallowed commands are blocked"""
        hooks = QualityGateHooks(bash_permissions=["pytest *"])

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "hook_event_name": "PreToolUse",
        }

        result = await hooks.pre_tool_security_check(input_data, "test_id", None)

        # Should return block response
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "not in the allowed bash permissions" in result["hookSpecificOutput"]["permissionDecisionReason"]

    @pytest.mark.asyncio
    async def test_dangerous_commands_always_blocked(self):
        """Test that dangerous commands are blocked even with whitelist"""
        hooks = QualityGateHooks(bash_permissions=["rm *"])  # Even if we allow rm

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
            "hook_event_name": "PreToolUse",
        }

        result = await hooks.pre_tool_security_check(input_data, "test_id", None)

        # Should be blocked as dangerous
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "blocked for safety reasons" in result["hookSpecificOutput"]["permissionDecisionReason"]

    @pytest.mark.asyncio
    async def test_no_whitelist_allows_safe_commands(self):
        """Test that None bash_permissions allows safe commands"""
        hooks = QualityGateHooks(bash_permissions=None)

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "hook_event_name": "PreToolUse",
        }

        result = await hooks.pre_tool_security_check(input_data, "test_id", None)

        # Should pass (no whitelist restriction)
        assert result == {}


class TestTaskTypeBashPermissions:
    """Test bash permissions in task type storage"""

    @pytest.mark.asyncio
    async def test_get_bash_permissions_for_type(self, tmp_path):
        """Test retrieving bash permissions from task type"""
        db_path = str(tmp_path / "test.db")
        manager = TaskTypeManager(db_path)
        await manager.initialize()

        # Get bash permissions for a default task type
        permissions = await manager.get_bash_permissions_for_type("test")

        # Should have standard tier permissions
        assert isinstance(permissions, list)
        assert "pytest *" in permissions
        assert "git status*" in permissions

    @pytest.mark.asyncio
    async def test_set_bash_permissions_for_type(self, tmp_path):
        """Test setting bash permissions for a task type"""
        db_path = str(tmp_path / "test.db")
        manager = TaskTypeManager(db_path)
        await manager.initialize()

        # Add a custom task type
        await manager.add_task_type(
            "custom_test",
            "Custom Test",
            "Custom task type for testing"
        )

        # Set custom bash permissions
        custom_permissions = ["npm test", "npm run lint"]
        success = await manager.set_bash_permissions_for_type(
            "custom_test",
            custom_permissions
        )

        assert success is True

        # Verify permissions were saved
        retrieved = await manager.get_bash_permissions_for_type("custom_test")
        assert retrieved == custom_permissions

    @pytest.mark.asyncio
    async def test_bash_permissions_by_tier(self, tmp_path):
        """Test that bash permissions are set correctly by tier"""
        db_path = str(tmp_path / "test.db")
        manager = TaskTypeManager(db_path)
        await manager.initialize()

        # Check simple tier (docs)
        simple_perms = await manager.get_bash_permissions_for_type("docs")
        assert "cat *" in simple_perms
        assert "pytest *" not in simple_perms  # Should not have test commands

        # Check standard tier (test)
        standard_perms = await manager.get_bash_permissions_for_type("test")
        assert "pytest *" in standard_perms
        assert "git status*" in standard_perms

        # Check complex tier (feature)
        complex_perms = await manager.get_bash_permissions_for_type("feature")
        assert complex_perms == []  # Empty = all allowed


class TestBashPermissionsEndToEnd:
    """End-to-end integration tests"""

    @pytest.mark.asyncio
    async def test_task_type_permissions_flow(self, tmp_path):
        """Test full flow from task type to hook enforcement"""
        db_path = str(tmp_path / "test.db")
        manager = TaskTypeManager(db_path)
        await manager.initialize()

        # Get task type info (simulates what executor does)
        task_type_info = await manager.get_task_type("test")
        assert task_type_info is not None

        # Extract bash permissions
        bash_permissions = task_type_info.get("bash_permissions", [])
        assert isinstance(bash_permissions, list)
        assert len(bash_permissions) > 0

        # Create hooks with these permissions
        hooks = QualityGateHooks(bash_permissions=bash_permissions)

        # Test that allowed command passes
        pytest_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/"},
            "hook_event_name": "PreToolUse",
        }
        result = await hooks.pre_tool_security_check(pytest_input, "test_1", None)
        assert result == {}

        # Test that disallowed command is blocked
        docker_input = {
            "tool_name": "Bash",
            "tool_input": {"command": "docker build ."},
            "hook_event_name": "PreToolUse",
        }
        result = await hooks.pre_tool_security_check(docker_input, "test_2", None)
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
