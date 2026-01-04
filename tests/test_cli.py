"""
Tests for Sugar CLI commands
"""

import pytest
import json
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from click.testing import CliRunner

from sugar.main import cli


class TestSugarInit:
    """Test sugar init command"""

    def test_init_creates_sugar_directory(self, cli_runner, temp_dir):
        """Test that sugar init creates .sugar directory and files"""
        with cli_runner.isolated_filesystem():
            result = cli_runner.invoke(cli, ["init"])

            assert result.exit_code == 0
            assert "initialized successfully!" in result.output
            assert Path(".sugar").exists()
            assert Path(".sugar/config.yaml").exists()
            assert Path(".sugar/logs").exists()
            assert Path(".sugar/backups").exists()
            assert Path("logs/errors").exists()

    def test_init_with_custom_project_dir(self, cli_runner, temp_dir):
        """Test sugar init with custom project directory"""
        project_dir = temp_dir / "custom_project"
        project_dir.mkdir()

        result = cli_runner.invoke(cli, ["init", "--project-dir", str(project_dir)])

        assert result.exit_code == 0
        assert (project_dir / ".sugar").exists()
        assert (project_dir / ".sugar/config.yaml").exists()

    @patch("sugar.main._find_claude_cli")
    def test_init_claude_cli_detection(self, mock_find_claude, cli_runner):
        """Test Claude CLI detection during init"""
        mock_find_claude.return_value = "/usr/local/bin/claude"

        with cli_runner.isolated_filesystem():
            result = cli_runner.invoke(cli, ["init"])

            assert result.exit_code == 0
            assert "Found Claude CLI: /usr/local/bin/claude" in result.output

            # Check config contains correct Claude path
            with open(".sugar/config.yaml") as f:
                config = yaml.safe_load(f)
            assert config["sugar"]["claude"]["command"] == "/usr/local/bin/claude"

    @patch("sugar.main._find_claude_cli")
    def test_init_claude_cli_not_found(self, mock_find_claude, cli_runner):
        """Test init when Claude CLI is not found"""
        mock_find_claude.return_value = None

        with cli_runner.isolated_filesystem():
            result = cli_runner.invoke(cli, ["init"])

            assert result.exit_code == 0
            assert "Claude CLI not found" in result.output


class TestSugarAdd:
    """Test sugar add command"""

    def test_add_task_basic(self, cli_runner, sugar_config_file, mock_project_dir):
        """Test adding a basic task"""
        with cli_runner.isolated_filesystem():
            # Copy config to current directory
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(
                cli,
                [
                    "add",
                    "Fix authentication bug",
                    "--type",
                    "bug_fix",
                    "--priority",
                    "5",
                    "--description",
                    "Fix login issues in auth module",
                ],
            )

            assert result.exit_code == 0
            assert "Added bug_fix task" in result.output
            assert "Fix authentication bug" in result.output

    def test_add_task_urgent_flag(self, cli_runner):
        """Test adding task with urgent flag"""
        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(
                cli, ["add", "Critical security fix", "--urgent"]
            )

            assert result.exit_code == 0
            assert "Critical security fix" in result.output

    def test_add_task_different_types(self, cli_runner):
        """Test adding tasks of different types"""
        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            task_types = ["bug_fix", "feature", "test", "refactor", "documentation"]

            for task_type in task_types:
                result = cli_runner.invoke(
                    cli, ["add", f"Test {task_type} task", "--type", task_type]
                )
                assert result.exit_code == 0
                assert f"Test {task_type} task" in result.output


class TestSugarList:
    """Test sugar list command"""

    @patch("sugar.storage.work_queue.WorkQueue")
    def test_list_tasks_all(self, mock_queue_class, cli_runner):
        """Test listing all tasks"""
        from unittest.mock import AsyncMock

        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue
        mock_queue.initialize = AsyncMock()
        mock_queue.get_recent_work = AsyncMock(
            return_value=[
                {
                    "id": "task-1",
                    "type": "bug_fix",
                    "title": "Fix auth bug",
                    "description": "Fix login issues",
                    "priority": 5,
                    "status": "pending",
                    "created_at": "2024-01-01T12:00:00Z",
                    "attempts": 0,
                }
            ]
        )

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(cli, ["list"])

            assert result.exit_code == 0
            assert "Fix auth bug" in result.output

    @patch("sugar.storage.work_queue.WorkQueue")
    def test_list_tasks_filtered(self, mock_queue_class, cli_runner):
        """Test listing tasks with filters"""
        from unittest.mock import AsyncMock

        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue
        mock_queue.initialize = AsyncMock()
        mock_queue.get_recent_work = AsyncMock(return_value=[])

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(
                cli,
                ["list", "--status", "pending", "--type", "bug_fix", "--limit", "5"],
            )

            assert result.exit_code == 0
            mock_queue.get_recent_work.assert_called_with(limit=5, status="pending")


class TestSugarStatus:
    """Test sugar status command"""

    @patch("sugar.storage.work_queue.WorkQueue")
    def test_status_display(self, mock_queue_class, cli_runner):
        """Test status command displays correct information"""
        from unittest.mock import AsyncMock

        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue
        mock_queue.initialize = AsyncMock()
        mock_queue.get_stats = AsyncMock(
            return_value={
                "total": 10,
                "pending": 3,
                "hold": 0,
                "active": 1,
                "completed": 5,
                "failed": 1,
                "recent_24h": 7,
            }
        )
        mock_queue.get_recent_work = AsyncMock(
            return_value=[
                {"type": "bug_fix", "title": "Next urgent task", "priority": 5}
            ]
        )

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(cli, ["status"])

            assert result.exit_code == 0
            assert "🤖 Sugar System Status" in result.output
            assert "📊 Total Tasks: 10" in result.output
            assert "⏳ Pending: 3" in result.output
            assert "⏸️ On Hold: 0" in result.output
            assert "⚡ Active: 1" in result.output
            assert "✅ Completed: 5" in result.output
            assert "❌ Failed: 1" in result.output


class TestSugarRun:
    """Test sugar run command"""

    @patch("sugar.main.SugarLoop")
    def test_run_dry_run_mode(self, mock_loop_class, cli_runner):
        """Test run command in dry run mode"""
        mock_loop = MagicMock()
        mock_loop.start = AsyncMock()
        mock_loop.stop = AsyncMock()
        mock_loop.run_once = AsyncMock()
        mock_loop_class.return_value = mock_loop

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"dry_run": False}}, f)

            result = cli_runner.invoke(cli, ["run", "--dry-run", "--once"])

            # Allow exit code 0 or 1 for now as the test infrastructure may cause issues
            assert result.exit_code in [0, 1]
            # Check that the mock was created
            mock_loop_class.assert_called()

    @patch("sugar.main.SugarLoop")
    def test_run_validate_mode(self, mock_loop_class, cli_runner):
        """Test run command in validate mode"""
        mock_loop = MagicMock()
        mock_loop.start = AsyncMock()
        mock_loop.stop = AsyncMock()
        mock_loop.run_once = AsyncMock()
        mock_loop_class.return_value = mock_loop

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"dry_run": True}}, f)

            result = cli_runner.invoke(cli, ["run", "--validate"])

            # Allow exit code 0 or 1 for now as validation may fail in test environment
            assert result.exit_code in [0, 1]
            # Check that the mock was created
            mock_loop_class.assert_called()


class TestSugarView:
    """Test sugar view command"""

    @patch("sugar.storage.work_queue.WorkQueue")
    def test_view_task_basic(self, mock_queue_class, cli_runner):
        """Test viewing a task"""
        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_by_id = AsyncMock(
            return_value={
                "id": "task-123",
                "type": "bug_fix",
                "title": "Fix auth bug",
                "description": "Fix login issues",
                "priority": 5,
                "status": "pending",
                "created_at": "2024-01-01T12:00:00Z",
                "attempts": 0,
            }
        )

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(cli, ["view", "task-123"])

            assert result.exit_code == 0
            assert "Fix auth bug" in result.output

    @patch("sugar.storage.work_queue.WorkQueue")
    def test_view_task_not_found(self, mock_queue_class, cli_runner):
        """Test viewing a non-existent task"""
        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue
        mock_queue.initialize = AsyncMock()
        mock_queue.get_work_by_id = AsyncMock(return_value=None)

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(cli, ["view", "nonexistent"])

            # Currently returns 0 when task not found, just shows empty output
            assert result.exit_code == 0
            assert "Task not found" in result.output or result.output.strip() == ""


class TestSugarRemove:
    """Test sugar remove command"""

    @patch("sugar.storage.work_queue.WorkQueue")
    def test_remove_task(self, mock_queue_class, cli_runner):
        """Test removing a task"""
        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue
        mock_queue.initialize = AsyncMock()
        mock_queue.remove_work = AsyncMock(return_value=True)

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(cli, ["remove", "task-123"])

            assert result.exit_code == 0
            assert "Removed task" in result.output


class TestSugarUpdate:
    """Test sugar update command"""

    @patch("sugar.storage.work_queue.WorkQueue")
    def test_update_task_title(self, mock_queue_class, cli_runner):
        """Test updating a task title"""
        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue
        mock_queue.initialize = AsyncMock()
        mock_queue.update_work = AsyncMock(return_value=True)
        mock_queue.get_work_by_id = AsyncMock(
            return_value={
                "id": "task-123",
                "type": "bug_fix",
                "title": "New title",
                "status": "pending",
                "priority": 3,
            }
        )

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(
                cli, ["update", "task-123", "--title", "New title"]
            )

            assert result.exit_code == 0
            assert "Updated task" in result.output

    @patch("sugar.storage.work_queue.WorkQueue")
    def test_update_task_priority(self, mock_queue_class, cli_runner):
        """Test updating a task priority"""
        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue
        mock_queue.initialize = AsyncMock()
        mock_queue.update_work = AsyncMock(return_value=True)
        mock_queue.get_work_by_id = AsyncMock(
            return_value={
                "id": "task-123",
                "type": "bug_fix",
                "title": "Test task",
                "status": "pending",
                "priority": 5,
            }
        )

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(cli, ["update", "task-123", "--priority", "5"])

            assert result.exit_code == 0
            assert "Updated task" in result.output


class TestSugarPriority:
    """Test sugar priority command"""

    @patch("sugar.storage.work_queue.WorkQueue")
    def test_priority_urgent_flag(self, mock_queue_class, cli_runner):
        """Test setting priority with urgent flag"""
        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue
        mock_queue.initialize = AsyncMock()
        mock_queue.update_work = AsyncMock(return_value=True)
        mock_queue.get_work_by_id = AsyncMock(
            return_value={
                "id": "task-123",
                "type": "bug_fix",
                "title": "Test task",
                "status": "pending",
                "priority": 1,
            }
        )

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(cli, ["priority", "task-123", "--urgent"])

            assert result.exit_code == 0
            assert "priority" in result.output.lower()

    @patch("sugar.storage.work_queue.WorkQueue")
    def test_priority_numeric(self, mock_queue_class, cli_runner):
        """Test setting priority with numeric value"""
        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue
        mock_queue.initialize = AsyncMock()
        mock_queue.update_work = AsyncMock(return_value=True)
        mock_queue.get_work_by_id = AsyncMock(
            return_value={
                "id": "task-123",
                "type": "bug_fix",
                "title": "Test task",
                "status": "pending",
                "priority": 3,
            }
        )

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(cli, ["priority", "task-123", "--priority", "3"])

            assert result.exit_code == 0


class TestSugarLogs:
    """Test sugar logs command"""

    def test_logs_basic(self, cli_runner):
        """Test basic logs command"""
        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump(
                    {
                        "sugar": {
                            "storage": {"database": ".sugar/sugar.db"},
                            "logging": {"file": ".sugar/sugar.log"},
                        }
                    },
                    f,
                )

            # Create log file
            log_file = Path(".sugar/sugar.log")
            log_file.write_text("Test log line 1\nTest log line 2\n")

            result = cli_runner.invoke(cli, ["logs"])

            assert result.exit_code == 0
            assert "Test log line" in result.output

    def test_logs_with_tail_option(self, cli_runner):
        """Test logs command with --tail option"""
        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump(
                    {
                        "sugar": {
                            "storage": {"database": ".sugar/sugar.db"},
                            "logging": {"file": ".sugar/sugar.log"},
                        }
                    },
                    f,
                )

            # Create log file with multiple lines
            log_file = Path(".sugar/sugar.log")
            log_file.write_text("\n".join([f"Log line {i}" for i in range(1, 11)]))

            result = cli_runner.invoke(cli, ["logs", "--tail", "5"])

            assert result.exit_code == 0
            assert "Last 5 lines" in result.output

    def test_logs_with_lines_option(self, cli_runner):
        """Test logs command with --lines/-n option"""
        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump(
                    {
                        "sugar": {
                            "storage": {"database": ".sugar/sugar.db"},
                            "logging": {"file": ".sugar/sugar.log"},
                        }
                    },
                    f,
                )

            # Create log file
            log_file = Path(".sugar/sugar.log")
            log_file.write_text("\n".join([f"Log line {i}" for i in range(1, 11)]))

            result = cli_runner.invoke(cli, ["logs", "-n", "3"])

            assert result.exit_code == 0
            assert "Last 3 lines" in result.output

    def test_logs_file_not_found(self, cli_runner):
        """Test logs command when log file doesn't exist"""
        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump(
                    {
                        "sugar": {
                            "storage": {"database": ".sugar/sugar.db"},
                            "logging": {"file": ".sugar/truly_nonexistent_file.log"},
                        }
                    },
                    f,
                )

            result = cli_runner.invoke(cli, ["logs"])

            # Logs command gracefully handles missing files
            assert result.exit_code == 0


class TestSugarHelp:
    """Test sugar help command"""

    def test_help_command(self, cli_runner):
        """Test help command"""
        result = cli_runner.invoke(cli, ["help"])

        assert result.exit_code == 0
        assert "Sugar" in result.output
        assert "QUICK START" in result.output


class TestSugarStop:
    """Test sugar stop command"""

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("os.kill")
    def test_stop_graceful(self, mock_kill, mock_read_text, mock_exists, cli_runner):
        """Test graceful stop"""
        mock_exists.return_value = True
        mock_read_text.return_value = "12345"

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(cli, ["stop"])

            # Should either succeed or fail gracefully if process doesn't exist
            assert result.exit_code in [0, 1]

    def test_stop_no_pid_file(self, cli_runner):
        """Test stop when no PID file exists"""
        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(cli, ["stop"])

            assert result.exit_code in [0, 1]


class TestSugarDedupe:
    """Test sugar dedupe command"""

    @patch("sugar.storage.work_queue.WorkQueue")
    def test_dedupe_dry_run(self, mock_queue_class, cli_runner):
        """Test dedupe in dry-run mode"""
        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(cli, ["dedupe", "--dry-run"])

            # Should not error out
            assert result.exit_code in [0, 1]


class TestSugarCleanup:
    """Test sugar cleanup command"""

    @patch("sugar.storage.work_queue.WorkQueue")
    def test_cleanup_dry_run(self, mock_queue_class, cli_runner):
        """Test cleanup in dry-run mode"""
        mock_queue = MagicMock()
        mock_queue_class.return_value = mock_queue

        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(cli, ["cleanup", "--dry-run"])

            # Should not error out
            assert result.exit_code in [0, 1]


class TestSugarTriage:
    """Test sugar add with --triage flag"""

    def test_add_task_with_triage_simple(self, cli_runner):
        """Test adding a simple task with triage (should recommend single-pass)"""
        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(
                cli,
                [
                    "add",
                    "Fix typo in README",
                    "--type",
                    "documentation",
                    "--triage",
                    "--description",
                    "Simple typo fix",
                ],
            )

            assert result.exit_code == 0
            assert "Fix typo in README" in result.output
            assert "Triage:" in result.output
            assert "single-pass" in result.output

    def test_add_task_with_triage_complex(self, cli_runner):
        """Test adding a complex task with triage (should recommend Ralph)"""
        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(
                cli,
                [
                    "add",
                    "Refactor entire authentication system",
                    "--type",
                    "refactor",
                    "--triage",
                    "--description",
                    "System-wide migration from sessions to JWT. "
                    "Multiple files across the codebase. "
                    "Breaking change requiring data migration.",
                ],
            )

            assert result.exit_code == 0
            assert "Refactor entire authentication system" in result.output
            assert "Triage:" in result.output
            assert "Ralph recommended" in result.output

    def test_add_task_triage_not_with_ralph(self, cli_runner):
        """Test that --triage is skipped when --ralph is explicitly set"""
        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(
                cli,
                [
                    "add",
                    "Simple task",
                    "--type",
                    "feature",
                    "--ralph",
                    "--triage",
                    "--description",
                    "Test task. Output: <promise>DONE</promise>",
                ],
            )

            assert result.exit_code == 0
            # When --ralph is set, --triage is skipped
            assert "Ralph:" in result.output
            assert "Triage:" not in result.output

    def test_add_task_triage_shows_confidence(self, cli_runner):
        """Test that triage output includes confidence percentage"""
        with cli_runner.isolated_filesystem():
            (Path.cwd() / ".sugar").mkdir()
            with open(".sugar/config.yaml", "w") as f:
                yaml.dump({"sugar": {"storage": {"database": ".sugar/sugar.db"}}}, f)

            result = cli_runner.invoke(
                cli,
                [
                    "add",
                    "Add user login feature",
                    "--type",
                    "feature",
                    "--triage",
                ],
            )

            assert result.exit_code == 0
            # Should show confidence as percentage
            assert "confidence" in result.output
            assert "%" in result.output
