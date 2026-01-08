"""
Comprehensive test suite for the configurable task type system.

Tests the full CLI workflow, database operations, migration, and integration.
"""

import asyncio
import json
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch
from click.testing import CliRunner

from sugar.main import cli, task_type
from sugar.storage.task_type_manager import TaskTypeManager
from sugar.storage.work_queue import WorkQueue


@pytest.fixture
def temp_sugar_env():
    """Create isolated temporary Sugar environment for testing"""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        sugar_dir = temp_path / ".sugar"
        sugar_dir.mkdir()

        # Create minimal config
        config_path = sugar_dir / "config.yaml"
        # Use forward slashes for cross-platform compatibility in YAML
        db_path_str = str(sugar_dir / "sugar.db").replace("\\", "/")
        config_content = f"""
sugar:
  storage:
    database: "{db_path_str}"
  claude:
    command: "echo"  # Mock Claude CLI
    timeout: 1800
    context_file: "context.json"
  dry_run: true
  loop_interval: 300
  max_concurrent_work: 1
"""
        config_path.write_text(config_content)

        # Change to temp directory
        old_cwd = os.getcwd()
        os.chdir(temp_path)

        try:
            yield {
                "temp_dir": temp_path,
                "sugar_dir": sugar_dir,
                "config_path": config_path,
                "db_path": sugar_dir / "sugar.db",
            }
        finally:
            os.chdir(old_cwd)


@pytest.fixture
def task_type_manager(temp_sugar_env):
    """Initialize TaskTypeManager with temporary database"""
    db_path = str(temp_sugar_env["db_path"])
    manager = TaskTypeManager(db_path)

    # Initialize the database with default types
    asyncio.run(_init_database(db_path))

    return manager


async def _init_database(db_path):
    """Helper to initialize database with default task types"""
    # Initialize TaskTypeManager first to get the proper defaults with tool restrictions
    task_type_mgr = TaskTypeManager(db_path)
    await task_type_mgr.initialize()

    # Then initialize work queue (which runs its own migrations)
    work_queue = WorkQueue(db_path)
    await work_queue.initialize()


class TestTaskTypeManager:
    """Test the TaskTypeManager database operations"""

    @pytest.mark.asyncio
    async def test_get_default_task_types(self, task_type_manager):
        """Test that default task types are created during initialization"""
        task_types = await task_type_manager.get_all_task_types()

        # Should have 10 default types
        assert len(task_types) == 10

        # Check specific defaults exist
        type_ids = [t["id"] for t in task_types]
        expected_defaults = ["bug_fix", "feature", "test", "refactor", "docs",
                           "chore", "style", "perf", "ci", "security"]
        assert all(default in type_ids for default in expected_defaults)

        # Check they're marked as default (SQLite returns 1 for True)
        for task_type in task_types:
            assert task_type["is_default"] == 1

    @pytest.mark.asyncio
    async def test_add_custom_task_type(self, task_type_manager):
        """Test adding a custom task type"""
        success = await task_type_manager.add_task_type(
            "database_migration",
            "Database Migration",
            "Schema and data migrations",
            "tech-lead",
            "migrate: {title}",
            "🗃️",
            ["migrations/*.sql", "schemas/*.py"],
        )

        assert success is True

        # Verify it was added
        task_type = await task_type_manager.get_task_type("database_migration")
        assert task_type is not None
        assert task_type["id"] == "database_migration"
        assert task_type["name"] == "Database Migration"
        assert task_type["description"] == "Schema and data migrations"
        assert task_type["agent"] == "tech-lead"
        assert task_type["commit_template"] == "migrate: {title}"
        assert task_type["emoji"] == "🗃️"
        assert task_type["file_patterns"] == ["migrations/*.sql", "schemas/*.py"]
        assert task_type["is_default"] == 0

    @pytest.mark.asyncio
    async def test_duplicate_task_type_rejected(self, task_type_manager):
        """Test that duplicate task type IDs are rejected"""
        # Add first instance
        success1 = await task_type_manager.add_task_type("duplicate_test", "Test Type")
        assert success1 is True

        # Try to add duplicate
        success2 = await task_type_manager.add_task_type(
            "duplicate_test", "Test Type 2"
        )
        assert success2 is False

    @pytest.mark.asyncio
    async def test_update_task_type(self, task_type_manager):
        """Test updating an existing task type"""
        # Add a task type first
        await task_type_manager.add_task_type("update_test", "Original Name")

        # Update it
        success = await task_type_manager.update_task_type(
            "update_test",
            name="Updated Name",
            description="Updated description",
            emoji="🔄",
        )

        assert success is True

        # Verify updates
        task_type = await task_type_manager.get_task_type("update_test")
        assert task_type["name"] == "Updated Name"
        assert task_type["description"] == "Updated description"
        assert task_type["emoji"] == "🔄"

    @pytest.mark.asyncio
    async def test_update_nonexistent_task_type(self, task_type_manager):
        """Test updating a task type that doesn't exist"""
        success = await task_type_manager.update_task_type("nonexistent", name="Test")
        assert success is False

    @pytest.mark.asyncio
    async def test_remove_custom_task_type(self, task_type_manager):
        """Test removing a custom task type"""
        # Add a custom task type
        await task_type_manager.add_task_type("removable", "Removable Type")

        # Verify it exists
        task_type = await task_type_manager.get_task_type("removable")
        assert task_type is not None

        # Remove it
        success = await task_type_manager.remove_task_type("removable")
        assert success is True

        # Verify it's gone
        task_type = await task_type_manager.get_task_type("removable")
        assert task_type is None

    @pytest.mark.asyncio
    async def test_cannot_remove_default_task_type(self, task_type_manager):
        """Test that default task types cannot be removed"""
        success = await task_type_manager.remove_task_type("feature")
        assert success is False

        # Verify it still exists
        task_type = await task_type_manager.get_task_type("feature")
        assert task_type is not None
        assert task_type["is_default"] == 1

    @pytest.mark.asyncio
    async def test_export_import_task_types(self, task_type_manager):
        """Test export/import functionality"""
        # Add some custom task types
        await task_type_manager.add_task_type("custom1", "Custom Type 1", emoji="🔥")
        await task_type_manager.add_task_type("custom2", "Custom Type 2", emoji="⚡")

        # Export custom types
        exported = await task_type_manager.export_task_types()

        assert len(exported) == 2
        assert any(t["id"] == "custom1" for t in exported)
        assert any(t["id"] == "custom2" for t in exported)

        # Verify default types are not exported
        assert not any(t["id"] == "feature" for t in exported)

        # Remove the custom types
        await task_type_manager.remove_task_type("custom1")
        await task_type_manager.remove_task_type("custom2")

        # Import them back
        imported_count = await task_type_manager.import_task_types(exported)
        assert imported_count == 2

        # Verify they're back
        custom1 = await task_type_manager.get_task_type("custom1")
        custom2 = await task_type_manager.get_task_type("custom2")
        assert custom1 is not None
        assert custom2 is not None


class TestTaskTypeCLI:
    """Test the task-type CLI commands"""

    def test_task_type_list_command(self, temp_sugar_env):
        """Test listing task types via CLI"""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Create config with correct local path
            os.makedirs(".sugar", exist_ok=True)
            config_content = """
sugar:
  storage:
    database: ".sugar/sugar.db"
  claude:
    command: "echo"  # Mock Claude CLI
    timeout: 1800
    context_file: "context.json"
  dry_run: true
  loop_interval: 300
  max_concurrent_work: 1
"""
            with open(".sugar/config.yaml", "w") as f:
                f.write(config_content)

            # Initialize database using the same path as in the config
            asyncio.run(_init_database(".sugar/sugar.db"))

            # Test list command with proper context
            result = runner.invoke(
                cli, ["--config", ".sugar/config.yaml", "task-type", "list"]
            )
            if result.exit_code != 0:
                print(f"Command failed with exit code {result.exit_code}")
                print(f"Output: {result.output}")
                print(f"Exception: {result.exception}")
            assert result.exit_code == 0
            assert "bug_fix (default)" in result.output
            assert "feature (default)" in result.output
            assert "🐛" in result.output  # Check emoji display

    def test_task_type_add_command(self, temp_sugar_env):
        """Test adding task type via CLI"""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Setup
            os.makedirs(".sugar", exist_ok=True)
            config_content = """
sugar:
  storage:
    database: ".sugar/sugar.db"
  claude:
    command: "echo"  # Mock Claude CLI
    timeout: 1800
    context_file: "context.json"
  dry_run: true
  loop_interval: 300
  max_concurrent_work: 1
"""
            with open(".sugar/config.yaml", "w") as f:
                f.write(config_content)

            asyncio.run(_init_database(".sugar/sugar.db"))

            # Add custom task type
            result = runner.invoke(
                cli,
                [
                    "--config",
                    ".sugar/config.yaml",
                    "task-type",
                    "add",
                    "security_audit",
                    "--name",
                    "Security Audit",
                    "--description",
                    "Security vulnerability scanning",
                    "--agent",
                    "tech-lead",
                    "--emoji",
                    "🔒",
                ],
            )

            assert result.exit_code == 0
            assert "✅ Added task type: 🔒 security_audit" in result.output

            # Verify it appears in list
            result = runner.invoke(
                cli, ["--config", ".sugar/config.yaml", "task-type", "list"]
            )
            assert result.exit_code == 0
            assert "security_audit" in result.output
            assert "Security Audit" in result.output

    def test_task_type_show_command(self, temp_sugar_env):
        """Test showing task type details via CLI"""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Setup
            os.makedirs(".sugar", exist_ok=True)
            config_content = """
sugar:
  storage:
    database: ".sugar/sugar.db"
  claude:
    command: "echo"  # Mock Claude CLI
    timeout: 1800
    context_file: "context.json"
  dry_run: true
  loop_interval: 300
  max_concurrent_work: 1
"""
            with open(".sugar/config.yaml", "w") as f:
                f.write(config_content)

            asyncio.run(_init_database(".sugar/sugar.db"))

            # Show default task type
            result = runner.invoke(
                cli, ["--config", ".sugar/config.yaml", "task-type", "show", "feature"]
            )
            assert result.exit_code == 0
            assert "✨ Feature (default)" in result.output
            assert "ID: feature" in result.output
            assert "Agent: general-purpose" in result.output

    def test_task_type_edit_command(self, temp_sugar_env):
        """Test editing task type via CLI"""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Setup
            os.makedirs(".sugar", exist_ok=True)
            config_content = """
sugar:
  storage:
    database: ".sugar/sugar.db"
  claude:
    command: "echo"  # Mock Claude CLI
    timeout: 1800
    context_file: "context.json"
  dry_run: true
  loop_interval: 300
  max_concurrent_work: 1
"""
            with open(".sugar/config.yaml", "w") as f:
                f.write(config_content)

            asyncio.run(_init_database(".sugar/sugar.db"))

            # Add a custom task type first
            runner.invoke(
                cli,
                [
                    "--config",
                    ".sugar/config.yaml",
                    "task-type",
                    "add",
                    "editable",
                    "--name",
                    "Editable Type",
                ],
            )

            # Edit it
            result = runner.invoke(
                cli,
                [
                    "--config",
                    ".sugar/config.yaml",
                    "task-type",
                    "edit",
                    "editable",
                    "--name",
                    "Updated Name",
                    "--emoji",
                    "🔧",
                ],
            )

            assert result.exit_code == 0
            assert "✅ Updated task type: editable" in result.output

            # Verify changes
            result = runner.invoke(
                cli, ["--config", ".sugar/config.yaml", "task-type", "show", "editable"]
            )
            assert "🔧 Updated Name" in result.output

    def test_task_type_remove_command(self, temp_sugar_env):
        """Test removing task type via CLI"""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Setup
            os.makedirs(".sugar", exist_ok=True)
            config_content = """
sugar:
  storage:
    database: ".sugar/sugar.db"
  claude:
    command: "echo"  # Mock Claude CLI
    timeout: 1800
    context_file: "context.json"
  dry_run: true
  loop_interval: 300
  max_concurrent_work: 1
"""
            with open(".sugar/config.yaml", "w") as f:
                f.write(config_content)

            asyncio.run(_init_database(".sugar/sugar.db"))

            # Add a custom task type first
            runner.invoke(
                cli,
                [
                    "--config",
                    ".sugar/config.yaml",
                    "task-type",
                    "add",
                    "removable",
                    "--name",
                    "Removable Type",
                ],
            )

            # Remove it with force flag
            result = runner.invoke(
                cli,
                [
                    "--config",
                    ".sugar/config.yaml",
                    "task-type",
                    "remove",
                    "removable",
                    "--force",
                ],
            )

            assert result.exit_code == 0
            assert "✅ Removed task type: removable" in result.output

            # Verify it's gone
            result = runner.invoke(
                cli,
                ["--config", ".sugar/config.yaml", "task-type", "show", "removable"],
            )
            assert result.exit_code == 1
            assert "not found" in result.output

    def test_cannot_remove_default_via_cli(self, temp_sugar_env):
        """Test that default task types cannot be removed via CLI"""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Setup
            os.makedirs(".sugar", exist_ok=True)
            config_content = """
sugar:
  storage:
    database: ".sugar/sugar.db"
  claude:
    command: "echo"  # Mock Claude CLI
    timeout: 1800
    context_file: "context.json"
  dry_run: true
  loop_interval: 300
  max_concurrent_work: 1
"""
            with open(".sugar/config.yaml", "w") as f:
                f.write(config_content)

            asyncio.run(_init_database(".sugar/sugar.db"))

            # Try to remove default task type
            result = runner.invoke(
                cli,
                [
                    "--config",
                    ".sugar/config.yaml",
                    "task-type",
                    "remove",
                    "feature",
                    "--force",
                ],
            )

            assert result.exit_code == 1
            assert "Cannot remove default task type" in result.output


class TestTaskTypeIntegration:
    """Test integration with the main CLI commands"""

    def test_add_task_with_custom_type(self, temp_sugar_env):
        """Test that custom task types can be used in sugar add command"""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Setup
            os.makedirs(".sugar", exist_ok=True)
            config_content = """
sugar:
  storage:
    database: ".sugar/sugar.db"
  claude:
    command: "echo"  # Mock Claude CLI
    timeout: 1800
    context_file: "context.json"
  dry_run: true
  loop_interval: 300
  max_concurrent_work: 1
"""
            with open(".sugar/config.yaml", "w") as f:
                f.write(config_content)

            asyncio.run(_init_database(".sugar/sugar.db"))

            # Add custom task type
            type_result = runner.invoke(
                cli,
                [
                    "--config",
                    ".sugar/config.yaml",
                    "task-type",
                    "add",
                    "integration_test",
                    "--name",
                    "Integration Test",
                    "--agent",
                    "general-purpose",
                ],
            )
            assert (
                type_result.exit_code == 0
            ), f"Task type creation failed: {type_result.output}"

            # Use it in sugar add command
            result = runner.invoke(
                cli,
                [
                    "--config",
                    ".sugar/config.yaml",
                    "add",
                    "Test integration workflow",
                    "--type",
                    "integration_test",
                    "--priority",
                    "4",
                ],
            )

            assert result.exit_code == 0, f"Task creation failed: {result.output}"
            assert "✅ Added integration_test task" in result.output

            # Verify task was created with correct type
            result = runner.invoke(cli, ["--config", ".sugar/config.yaml", "list"])
            assert result.exit_code == 0
            assert "[integration_test]" in result.output
            assert "Test integration workflow" in result.output

    def test_invalid_task_type_rejected(self, temp_sugar_env):
        """Test that invalid task types are rejected with helpful error"""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Setup
            os.makedirs(".sugar", exist_ok=True)
            config_content = """
sugar:
  storage:
    database: ".sugar/sugar.db"
  claude:
    command: "echo"  # Mock Claude CLI
    timeout: 1800
    context_file: "context.json"
  dry_run: true
  loop_interval: 300
  max_concurrent_work: 1
"""
            with open(".sugar/config.yaml", "w") as f:
                f.write(config_content)

            asyncio.run(_init_database(".sugar/sugar.db"))

            # Try to use invalid task type
            result = runner.invoke(
                cli,
                [
                    "--config",
                    ".sugar/config.yaml",
                    "add",
                    "Test task",
                    "--type",
                    "nonexistent_type",
                ],
            )

            assert result.exit_code == 2
            assert "Invalid choice: nonexistent_type" in result.output
            assert "choose from" in result.output

    def test_list_with_custom_type_filter(self, temp_sugar_env):
        """Test filtering tasks by custom task type"""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Setup
            os.makedirs(".sugar", exist_ok=True)
            config_content = """
sugar:
  storage:
    database: ".sugar/sugar.db"
  claude:
    command: "echo"  # Mock Claude CLI
    timeout: 1800
    context_file: "context.json"
  dry_run: true
  loop_interval: 300
  max_concurrent_work: 1
"""
            with open(".sugar/config.yaml", "w") as f:
                f.write(config_content)

            asyncio.run(_init_database(".sugar/sugar.db"))

            # Add custom task type and task
            runner.invoke(
                cli,
                [
                    "--config",
                    ".sugar/config.yaml",
                    "task-type",
                    "add",
                    "filter_test",
                    "--name",
                    "Filter Test",
                ],
            )
            runner.invoke(
                cli,
                [
                    "--config",
                    ".sugar/config.yaml",
                    "add",
                    "Filterable task",
                    "--type",
                    "filter_test",
                ],
            )
            runner.invoke(
                cli,
                [
                    "--config",
                    ".sugar/config.yaml",
                    "add",
                    "Regular task",
                    "--type",
                    "feature",
                ],
            )

            # Filter by custom type
            result = runner.invoke(
                cli, ["--config", ".sugar/config.yaml", "list", "--type", "filter_test"]
            )

            assert result.exit_code == 0
            assert "[filter_test]" in result.output
            assert "Filterable task" in result.output
            assert "Regular task" not in result.output


class TestTaskTypeToolRestrictions:
    """Test tool restrictions for task types"""

    @pytest.mark.asyncio
    async def test_default_tool_restrictions(self, task_type_manager):
        """Test that default task types have appropriate tool restrictions"""
        # Simple tier tasks should have restricted tools
        docs = await task_type_manager.get_task_type("docs")
        assert docs["allowed_tools"] == ["Read", "Write", "Edit", "Glob", "Grep"]
        assert docs["disallowed_tools"] is None

        style = await task_type_manager.get_task_type("style")
        assert style["allowed_tools"] == ["Read", "Write", "Edit", "Glob", "Grep"]

        # Standard tier tasks should disallow WebSearch
        bug_fix = await task_type_manager.get_task_type("bug_fix")
        assert bug_fix["allowed_tools"] is None  # All tools
        assert bug_fix["disallowed_tools"] == ["WebSearch"]

        # Complex tier tasks should have no restrictions
        feature = await task_type_manager.get_task_type("feature")
        assert feature["allowed_tools"] is None
        assert feature["disallowed_tools"] is None

    @pytest.mark.asyncio
    async def test_get_tool_restrictions(self, task_type_manager):
        """Test getting tool restrictions for a task type"""
        restrictions = await task_type_manager.get_tool_restrictions_for_type("docs")
        assert restrictions["allowed_tools"] == ["Read", "Write", "Edit", "Glob", "Grep"]
        assert restrictions["disallowed_tools"] is None

        restrictions = await task_type_manager.get_tool_restrictions_for_type("feature")
        assert restrictions["allowed_tools"] is None
        assert restrictions["disallowed_tools"] is None

    @pytest.mark.asyncio
    async def test_set_tool_restrictions(self, task_type_manager):
        """Test setting tool restrictions for a task type"""
        # Add a custom task type
        await task_type_manager.add_task_type("custom_restricted", "Custom Restricted")

        # Set tool restrictions
        success = await task_type_manager.set_tool_restrictions_for_type(
            "custom_restricted",
            allowed_tools=["Read", "Grep"],
            disallowed_tools=None,
        )
        assert success is True

        # Verify restrictions were set
        task_type = await task_type_manager.get_task_type("custom_restricted")
        assert task_type["allowed_tools"] == ["Read", "Grep"]
        assert task_type["disallowed_tools"] is None

    @pytest.mark.asyncio
    async def test_update_tool_restrictions(self, task_type_manager):
        """Test updating tool restrictions for existing task type"""
        # Get original restrictions for feature type
        original = await task_type_manager.get_task_type("feature")
        assert original["allowed_tools"] is None
        assert original["disallowed_tools"] is None

        # Update restrictions (this would normally not be done for defaults, but testing the functionality)
        success = await task_type_manager.update_task_type(
            "feature",
            allowed_tools=["Read", "Write", "Edit"],
            disallowed_tools=["Bash"],
        )
        assert success is True

        # Verify updated restrictions
        updated = await task_type_manager.get_task_type("feature")
        assert updated["allowed_tools"] == ["Read", "Write", "Edit"]
        assert updated["disallowed_tools"] == ["Bash"]

    @pytest.mark.asyncio
    async def test_tool_restrictions_persist_across_sessions(self, task_type_manager):
        """Test that tool restrictions are persisted in database"""
        db_path = task_type_manager.db_path

        # Set restrictions
        await task_type_manager.add_task_type("persist_test", "Persist Test")
        await task_type_manager.set_tool_restrictions_for_type(
            "persist_test",
            allowed_tools=["Read", "Write"],
            disallowed_tools=None,
        )

        # Create new manager instance (simulating new session)
        new_manager = TaskTypeManager(db_path)
        await new_manager.initialize()

        # Verify restrictions persisted
        task_type = await new_manager.get_task_type("persist_test")
        assert task_type["allowed_tools"] == ["Read", "Write"]
        assert task_type["disallowed_tools"] is None


class TestTaskTypeMigration:
    """Test database migration and backwards compatibility"""

    def test_migration_creates_task_types_table(self, temp_sugar_env):
        """Test that database migration creates task_types table with defaults"""
        db_path = str(temp_sugar_env["db_path"])

        # Initialize TaskTypeManager first (gets proper defaults)
        manager = TaskTypeManager(db_path)
        asyncio.run(manager.initialize())

        # Verify task_types table was created
        task_types = asyncio.run(manager.get_all_task_types())

        assert len(task_types) >= 10  # Should have at least 10 defaults

        # Check expected defaults are present
        type_ids = {t["id"] for t in task_types}
        expected_defaults = {"bug_fix", "feature", "test", "refactor", "docs",
                           "chore", "style", "perf", "ci", "security"}
        assert expected_defaults.issubset(type_ids)

    def test_migration_is_idempotent(self, temp_sugar_env):
        """Test that running migration multiple times is safe"""
        db_path = str(temp_sugar_env["db_path"])

        # Initialize twice
        manager1 = TaskTypeManager(db_path)
        asyncio.run(manager1.initialize())

        manager2 = TaskTypeManager(db_path)
        asyncio.run(manager2.initialize())

        # Should still have the default types (no duplicates)
        task_types = asyncio.run(manager2.get_all_task_types())
        assert len(task_types) == 10  # Exactly 10, not duplicated

    def test_tool_restriction_columns_added(self, temp_sugar_env):
        """Test that migration adds tool restriction columns"""
        db_path = str(temp_sugar_env["db_path"])

        # Initialize database
        manager = TaskTypeManager(db_path)
        asyncio.run(manager.initialize())

        # Verify tool restriction columns exist
        task_types = asyncio.run(manager.get_all_task_types())

        # Check that at least one task type has tool restrictions
        docs_type = next((t for t in task_types if t["id"] == "docs"), None)
        assert docs_type is not None
        assert "allowed_tools" in docs_type
        assert "disallowed_tools" in docs_type

        # Verify the docs type has the expected restrictions
        assert docs_type["allowed_tools"] == ["Read", "Write", "Edit", "Glob", "Grep"]
        assert docs_type["disallowed_tools"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
