#!/usr/bin/env python3
"""
Sugar Main Entry Point - Start the AI-powered autonomous development system
"""
import asyncio
import json
import logging
import signal
import sys
from pathlib import Path
import click
from datetime import datetime, timezone

from .core.loop import SugarLoop
from .__version__ import get_version_info, __version__


def validate_task_type(ctx, param, value):
    """Custom validation function for task types"""
    if not value:
        return value

    try:
        import yaml
        from .storage.task_type_manager import TaskTypeManager
        import asyncio

        # Get config file path from context
        config_file = (
            ctx.obj.get("config", ".sugar/config.yaml")
            if ctx.obj
            else ".sugar/config.yaml"
        )

        async def get_types():
            with open(config_file, "r") as f:
                config = yaml.safe_load(f)
            db_path = config["sugar"]["storage"]["database"]
            manager = TaskTypeManager(db_path)
            return await manager.get_task_type_ids()

        # Get available task types
        valid_choices = asyncio.run(get_types())

        if value in valid_choices:
            return value

        # If not found, raise error with available choices
        choices_str = ", ".join(valid_choices)
        raise click.BadParameter(
            f"Invalid choice: {value}. (choose from {choices_str})"
        )

    except Exception as e:
        # Fallback validation to default types
        fallback_choices = ["bug_fix", "feature", "test", "refactor", "documentation"]
        if value in fallback_choices:
            return value
        choices_str = ", ".join(fallback_choices)
        raise click.BadParameter(
            f"Invalid choice: {value}. (choose from {choices_str})"
        )


def validate_task_type_with_all(ctx, param, value):
    """Custom validation function for task types including 'all' option"""
    if not value or value == "all":
        return value
    return validate_task_type(ctx, param, value)


def format_json_pretty(data, max_width=80):
    """Format JSON data for readable terminal display"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return data

    if not isinstance(data, (dict, list)):
        return str(data)

    # Format with nice indentation - let json.dumps handle the structure
    return json.dumps(data, indent=2, ensure_ascii=False)


def setup_logging(log_file_path=".sugar/sugar.log", debug=False):
    """Setup logging with proper file path from configuration"""
    # Ensure log directory exists
    Path(log_file_path).parent.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if debug else logging.INFO

    # Clear any existing handlers
    logging.getLogger().handlers.clear()

    # Use simple handlers with UTF-8 encoding for file, errors='replace' for console
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),  # Console output
            logging.FileHandler(
                log_file_path, encoding="utf-8", errors="replace"
            ),  # File output
        ],
    )

    # Set encoding options for the console handler to handle emojis gracefully
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if (
            isinstance(handler, logging.StreamHandler)
            and handler.stream.name == "<stderr>"
        ):
            # For console output, set errors='replace' to handle emoji issues on Windows
            if hasattr(handler.stream, "reconfigure"):
                try:
                    handler.stream.reconfigure(errors="replace")
                except Exception:
                    pass


logger = logging.getLogger(__name__)


def _format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable format"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        remaining_seconds = seconds % 60
        return f"{minutes}m {remaining_seconds:.0f}s"
    else:
        hours = int(seconds / 3600)
        remaining_minutes = int((seconds % 3600) / 60)
        return f"{hours}h {remaining_minutes}m"


# Global variable to hold the loop instance
sugar_loop = None
shutdown_event = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"🛑 Shutdown signal received, stopping Sugar...")
    if shutdown_event:
        shutdown_event.set()
        logger.info("🔔 Shutdown event triggered")
    else:
        logger.warning("⚠️ Shutdown event not available")


@click.group(invoke_without_command=True)
@click.option("--config", default=".sugar/config.yaml", help="Configuration file path")
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.option("--version", is_flag=True, help="Show version information")
@click.pass_context
def cli(ctx, config, debug, version):
    """Sugar 🍰 - AI-powered autonomous development system

    A lightweight autonomous development system that works with Claude Code CLI
    """
    # Handle version request
    if version:
        click.echo(get_version_info())
        ctx.exit()

    # If no command was given, show help
    if ctx.invoked_subcommand is None and not version:
        click.echo(ctx.get_help())
        return

    # Setup logging with proper configuration
    log_file_path = ".sugar/sugar.log"  # Default
    if Path(config).exists():
        try:
            import yaml

            with open(config, "r") as f:
                config_data = yaml.safe_load(f)
            log_file_path = (
                config_data.get("sugar", {})
                .get("logging", {})
                .get("file", ".sugar/sugar.log")
            )
        except Exception:
            pass  # Use default if config can't be read

    setup_logging(log_file_path, debug)

    if debug:
        logger.debug("🐛 Debug logging enabled")

    ctx.ensure_object(dict)
    ctx.obj["config"] = config


@cli.command()
@click.option(
    "--project-dir", default=".", help="Project directory to initialize Sugar in"
)
def init(project_dir):
    """Initialize Sugar in a project directory"""
    import shutil
    import json

    project_path = Path(project_dir).resolve()
    sugar_dir = project_path / ".sugar"

    click.echo(f"🚀 Initializing {get_version_info()} in {project_path}")

    try:
        # Create .sugar directory
        sugar_dir.mkdir(exist_ok=True)

        # Find Claude CLI
        claude_cmd = _find_claude_cli()
        if not claude_cmd:
            click.echo("⚠️ Claude CLI not found in PATH or standard locations")
            claude_cmd = "claude"
        else:
            click.echo(f"✅ Found Claude CLI: {claude_cmd}")

        # Detect GitHub CLI and repository
        github_config = _detect_github_config(project_path)
        if github_config["cli_available"]:
            click.echo(f"✅ Found GitHub CLI: {github_config['gh_command']}")
            if github_config["repo"]:
                click.echo(f"✅ Detected GitHub repository: {github_config['repo']}")
            if not github_config["authenticated"]:
                click.echo(
                    "⚠️ GitHub CLI found but not authenticated. Run 'gh auth login' to enable GitHub integration."
                )
        else:
            click.echo(
                "ℹ️ GitHub CLI not found. You can install it later for GitHub integration."
            )

        # Create default config
        config_content = _generate_default_config(
            claude_cmd, str(project_path), github_config
        )
        config_path = sugar_dir / "config.yaml"

        with open(config_path, "w") as f:
            f.write(config_content)

        # Create directories
        (sugar_dir / "logs").mkdir(exist_ok=True)
        (sugar_dir / "backups").mkdir(exist_ok=True)

        # Create logs/errors directory structure (for user's actual error logs)
        logs_dir = project_path / "logs" / "errors"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Create .gitkeep to preserve directory structure but don't create sample files
        # that would be discovered as work items
        with open(logs_dir / ".gitkeep", "w") as f:
            f.write("# This directory is monitored by Sugar for error logs\n")

        click.echo(f"✅ {get_version_info()} initialized successfully! 🍰")
        click.echo(f"📁 Config: {config_path}")
        click.echo(f"📁 Database: {sugar_dir / 'sugar.db'}")
        click.echo(f"📁 Logs: {sugar_dir / 'logs'}")
        click.echo("\n🎯 Next steps:")
        click.echo("1. Review and customize the config: .sugar/config.yaml")
        click.echo("2. Add tasks: sugar add 'Your first task'")
        click.echo("3. Start autonomous mode: sugar run")
        click.echo(
            "\n⚖️  By using Sugar, you agree to the Terms of Service (see TERMS.md)"
        )
        click.echo(
            "   Software provided 'AS IS' - users responsible for reviewing AI-generated code."
        )

    except Exception as e:
        click.echo(f"❌ Failed to initialize Sugar: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("title")
@click.option(
    "--type",
    "task_type",
    default="feature",
    callback=validate_task_type,
    help="Type of task",
)
@click.option(
    "--priority",
    default=3,
    type=click.IntRange(1, 5),
    help="Priority (1=low, 5=urgent)",
)
@click.option("--description", help="Detailed description of the task")
@click.option("--urgent", is_flag=True, help="Mark as urgent (priority 5)")
@click.option(
    "--status",
    type=click.Choice(["pending", "hold"]),
    default="pending",
    help="Initial task status",
)
@click.option("--input-file", help="JSON file containing task data")
@click.option("--stdin", is_flag=True, help="Read task data from stdin (JSON format)")
@click.option("--json", "parse_json", is_flag=True, help="Parse description as JSON")
@click.option("--orchestrate", is_flag=True, help="Enable orchestration for this task")
@click.option(
    "--skip-stages", help="Comma-separated stages to skip (research,planning)"
)
@click.option("--ralph", is_flag=True, help="Enable Ralph Wiggum iterative execution")
@click.option(
    "--max-iterations",
    type=int,
    default=None,
    help="Max iterations for Ralph mode (default: 10)",
)
@click.pass_context
def add(
    ctx,
    title,
    task_type,
    priority,
    description,
    urgent,
    status,
    input_file,
    stdin,
    parse_json,
    orchestrate,
    skip_stages,
    ralph,
    max_iterations,
):
    """Add a new task to Sugar work queue

    Supports multiple input methods for complex data:
    - Standard CLI flags (--description, --type, etc.)
    - JSON file input (--input-file path/to/task.json)
    - Stdin input (--stdin with JSON data)
    - JSON description parsing (--json with --description containing JSON)
    """

    if urgent:
        priority = 5

    # Handle different input methods
    task_data_override = None

    try:
        # Method 1: JSON file input
        if input_file:
            with open(input_file, "r") as f:
                task_data_override = json.load(f)

        # Method 2: Stdin input
        elif stdin:
            import sys

            stdin_data = sys.stdin.read().strip()
            if stdin_data:
                task_data_override = json.loads(stdin_data)

        # Method 3: JSON description parsing
        elif parse_json and description:
            parsed_description = json.loads(description)
            # Keep the original description as text, but add parsed JSON to context
            task_data_override = {
                "context": {
                    "parsed_description": parsed_description,
                    "description_format": "json",
                }
            }

    except json.JSONDecodeError as e:
        click.echo(f"❌ Invalid JSON input: {e}", err=True)
        sys.exit(1)
    except FileNotFoundError:
        click.echo(f"❌ Input file not found: {input_file}", err=True)
        sys.exit(1)

    # Set default description if none provided
    if not description:
        description = f"Task: {title}"

    # Import here to avoid circular imports
    from .storage.work_queue import WorkQueue
    import uuid

    try:
        config_file = ctx.obj["config"]
        # Load config to get database path
        import yaml

        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        # Initialize work queue
        work_queue = WorkQueue(config["sugar"]["storage"]["database"])

        # Create base task data
        task_data = {
            "id": str(uuid.uuid4()),
            "type": task_type,
            "title": title,
            "description": description,
            "priority": priority,
            "status": status,
            "source": "cli",
            "orchestrate": orchestrate,
            "context": {
                "added_via": "sugar_cli",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

        # Add orchestration metadata if enabled
        if orchestrate:
            if skip_stages:
                task_data["context"]["skip_stages"] = [
                    s.strip() for s in skip_stages.split(",")
                ]

        # Add Ralph Wiggum settings if enabled
        if ralph:
            task_data["context"]["ralph_enabled"] = True
            task_data["context"]["max_iterations"] = max_iterations or 10
            # Validate completion criteria if strict mode
            from .ralph import CompletionCriteriaValidator

            validator = CompletionCriteriaValidator(strict=False)
            result = validator.validate(
                description, {"max_iterations": max_iterations or 10}
            )
            if not result.is_valid:
                click.echo(
                    f"⚠️  Ralph enabled but completion criteria may be weak: {', '.join(result.suggestions[:2])}",
                    err=True,
                )

        # Override/merge with complex input data
        if task_data_override:
            # Handle context merging specially to preserve base context
            if "context" in task_data_override:
                task_data["context"].update(task_data_override["context"])
                del task_data_override["context"]

            # Override other fields
            task_data.update(task_data_override)

            # Ensure required fields are still present
            if "id" not in task_data or not task_data["id"]:
                task_data["id"] = str(uuid.uuid4())

        # Add to queue
        asyncio.run(_add_task_async(work_queue, task_data))

        urgency = (
            "🚨 URGENT"
            if task_data.get("priority", priority) == 5
            else f"Priority {task_data.get('priority', priority)}"
        )
        input_method = ""
        if input_file:
            input_method = f" (from {input_file})"
        elif stdin:
            input_method = " (from stdin)"
        elif parse_json:
            input_method = " (JSON parsed)"

        ralph_mode = ""
        if ralph:
            max_iter = task_data["context"].get("max_iterations", 10)
            ralph_mode = f" [Ralph: max {max_iter} iterations]"

        click.echo(
            f"✅ Added {task_data.get('type', task_type)} task: '{task_data.get('title', title)}' ({urgency}){input_method}{ralph_mode}"
        )

    except Exception as e:
        click.echo(f"❌ Error adding task: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--status",
    type=click.Choice(["pending", "hold", "active", "completed", "failed", "all"]),
    default="all",
    help="Filter by status",
)
@click.option("--limit", default=20, help="Number of tasks to show")
@click.option(
    "--type",
    "task_type",
    default="all",
    callback=validate_task_type_with_all,
    help="Filter by type",
)
@click.option(
    "--priority",
    type=click.IntRange(1, 5),
    default=None,
    help="Filter by priority (1-5)",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["pretty", "text", "json"]),
    default="pretty",
    help="Output format",
)
@click.pass_context
def list(ctx, status, limit, task_type, priority, output_format):
    """List tasks in Sugar work queue"""

    from .storage.work_queue import WorkQueue
    import yaml

    try:
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        work_queue = WorkQueue(config["sugar"]["storage"]["database"])

        # Get tasks
        tasks = asyncio.run(
            _list_tasks_async(work_queue, status, limit, task_type, priority)
        )

        if not tasks:
            click.echo(f"No {status if status != 'all' else ''} tasks found")
            return

        # Count tasks by status for summary header
        status_counts = {}
        for task in tasks:
            task_status = task["status"]
            status_counts[task_status] = status_counts.get(task_status, 0) + 1

        # Handle different output formats
        if output_format == "json":
            import json

            click.echo(json.dumps(tasks, indent=2, default=str))
            return

        # Build summary parts
        summary_parts = []
        status_order = ["pending", "hold", "active", "completed", "failed"]
        emoji_map = {
            "pending": "⏳",
            "hold": "⏸️",
            "active": "⚡",
            "completed": "✅",
            "failed": "❌",
        }

        for status_type in status_order:
            count = status_counts.get(status_type, 0)
            if count > 0:
                emoji = emoji_map[status_type]
                if output_format == "text":
                    summary_parts.append(f"{count} {status_type}")
                else:  # pretty format
                    summary_parts.append(f"{count} {status_type} {emoji}")

        summary_text = ", ".join(summary_parts) if summary_parts else "no tasks"

        if output_format == "text":
            click.echo(f"\n{len(tasks)} Tasks ({summary_text}):")
            click.echo("-" * 40)
        else:
            click.echo(f"\n📋 {len(tasks)} Tasks ({summary_text}):")
            click.echo("=" * 60)

        for task in tasks:
            status = task["status"]

            # Handle hold reason display
            hold_reason = ""
            if status == "hold" and task.get("context", {}).get("hold_reason"):
                hold_reason = f" - {task['context']['hold_reason']}"

            if output_format == "text":
                priority_str = f"P{task['priority']}"
                click.echo(
                    f"[{status.upper()}] {priority_str} [{task['type']}] {task['title']}{hold_reason}"
                )
                if task.get("description") and len(task["description"]) < 100:
                    click.echo(f"  Description: {task['description']}")
                click.echo(
                    f"  ID: {task['id']} | Created: {task['created_at']} | Attempts: {task['attempts']}"
                )
            else:  # pretty format
                status_emoji = emoji_map.get(status, "📄")
                priority_str = "🚨" if task["priority"] == 5 else f"P{task['priority']}"

                click.echo(
                    f"{status_emoji} {priority_str} [{task['type']}] {task['title']}{hold_reason}"
                )
                if task.get("description") and len(task["description"]) < 100:
                    click.echo(f"   📝 {task['description']}")

                # Build info line with timing for completed/failed tasks
                info_parts = [
                    f"🆔 {task['id']}",
                    f"📅 {task['created_at']}",
                    f"🔄 {task['attempts']} attempts",
                ]

                # Add timing information for completed/failed tasks
                if task["status"] in ["completed", "failed"]:
                    if task.get("total_execution_time", 0) > 0:
                        info_parts.append(f"⏱️ {task['total_execution_time']:.1f}s")
                    if task.get("total_elapsed_time", 0) > 0:
                        info_parts.append(
                            f"🕐 {_format_duration(task['total_elapsed_time'])}"
                        )

                click.echo(f"   {' | '.join(info_parts)}")
            click.echo()

    except Exception as e:
        click.echo(f"❌ Error listing tasks: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("task_id")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["pretty", "compact"]),
    default="pretty",
    help="JSON output format (default: pretty)",
)
@click.pass_context
def view(ctx, task_id, output_format):
    """View detailed information about a specific task"""

    from .storage.work_queue import WorkQueue
    import yaml

    try:
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        work_queue = WorkQueue(config["sugar"]["storage"]["database"])

        # Get specific task
        task = asyncio.run(_get_task_by_id_async(work_queue, task_id))

        if not task:
            click.echo(f"❌ Task not found: {task_id}")
            return

        # Display detailed task information
        status_emoji_map = {
            "pending": "⏳",
            "hold": "⏸️",
            "active": "⚡",
            "completed": "✅",
            "failed": "❌",
        }
        status_emoji = status_emoji_map.get(task["status"], "📄")

        priority_str = "🚨" if task["priority"] == 5 else f"P{task['priority']}"

        click.echo(f"\n📋 Task Details")
        click.echo("=" * 50)
        click.echo(f"{status_emoji} {priority_str} [{task['type']}] {task['title']}")
        click.echo(f"📝 Description: {task.get('description', 'No description')}")
        click.echo(f"🆔 ID: {task['id']}")
        click.echo(f"📅 Created: {task['created_at']}")
        click.echo(f"🔄 Attempts: {task['attempts']}")
        click.echo(f"📊 Status: {task['status']}")

        # Show hold reason if task is on hold
        if task["status"] == "hold" and task.get("context", {}).get("hold_reason"):
            click.echo(f"⏸️  Hold Reason: {task['context']['hold_reason']}")
            if task.get("context", {}).get("held_at"):
                click.echo(f"⏸️  Held Since: {task['context']['held_at']}")

        click.echo(f"🎯 Priority: {task['priority']}/5")
        click.echo(f"🏷️  Source: {task.get('source', 'unknown')}")

        # Display timing information
        if task.get("total_execution_time", 0) > 0:
            click.echo(f"⏱️  Execution Time: {task['total_execution_time']:.1f}s")
        if task.get("total_elapsed_time", 0) > 0:
            click.echo(
                f"🕐 Total Elapsed: {_format_duration(task['total_elapsed_time'])}"
            )
        if task.get("started_at"):
            click.echo(f"🚀 Started: {task['started_at']}")

        # Display commit SHA if available
        if task.get("commit_sha"):
            click.echo(f"🔗 Commit: {task['commit_sha']}")

        if task.get("context"):
            click.echo(f"🔍 Context:")
            if output_format == "pretty":
                formatted_context = format_json_pretty(task["context"])
                click.echo(formatted_context)
            else:
                click.echo(
                    json.dumps(task["context"])
                    if isinstance(task["context"], dict)
                    else str(task["context"])
                )

        if task.get("result"):
            click.echo(f"📋 Result:")
            if output_format == "pretty":
                formatted_result = format_json_pretty(task["result"])
                click.echo(formatted_result)
            else:
                click.echo(
                    json.dumps(task["result"])
                    if isinstance(task["result"], dict)
                    else str(task["result"])
                )

        click.echo()

    except Exception as e:
        click.echo(f"❌ Error viewing task: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("task_id")
@click.pass_context
def remove(ctx, task_id):
    """Remove a task from the work queue"""

    from .storage.work_queue import WorkQueue
    import yaml

    try:
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        work_queue = WorkQueue(config["sugar"]["storage"]["database"])

        # Remove the task
        success = asyncio.run(_remove_task_async(work_queue, task_id))

        if success:
            click.echo(f"✅ Removed task: {task_id}")
        else:
            click.echo(f"❌ Task not found: {task_id}")
            sys.exit(1)

    except Exception as e:
        click.echo(f"❌ Error removing task: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("task_id")
@click.option("--reason", help="Reason for putting task on hold")
@click.pass_context
def hold(ctx, task_id, reason):
    """Put a task on hold"""
    from .storage.work_queue import WorkQueue
    import yaml

    try:
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        work_queue = WorkQueue(config["sugar"]["storage"]["database"])

        async def _hold_task_async():
            await work_queue.initialize()
            success = await work_queue.hold_work(task_id, reason)
            return success

        success = asyncio.run(_hold_task_async())

        if success:
            reason_text = f" - {reason}" if reason else ""
            click.echo(f"⏸️ Task put on hold: {task_id}{reason_text}")
        else:
            click.echo(f"❌ Task not found: {task_id}")
            sys.exit(1)

    except Exception as e:
        click.echo(f"❌ Error putting task on hold: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("task_id")
@click.pass_context
def release(ctx, task_id):
    """Release a task from hold"""
    from .storage.work_queue import WorkQueue
    import yaml

    try:
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        work_queue = WorkQueue(config["sugar"]["storage"]["database"])

        async def _release_task_async():
            await work_queue.initialize()
            success = await work_queue.release_work(task_id)
            return success

        success = asyncio.run(_release_task_async())

        if success:
            click.echo(f"▶️ Task released from hold: {task_id}")
        else:
            click.echo(f"❌ Task not found or not on hold: {task_id}")
            sys.exit(1)

    except Exception as e:
        click.echo(f"❌ Error releasing task: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("task_id")
@click.option("--title", help="Update task title")
@click.option("--description", help="Update task description")
@click.option("--priority", type=click.IntRange(1, 5), help="Update priority (1-5)")
@click.option(
    "--type",
    "task_type",
    callback=validate_task_type,
    help="Update task type",
)
@click.option(
    "--status",
    type=click.Choice(["pending", "hold", "active", "completed", "failed"]),
    help="Update task status",
)
@click.pass_context
def update(ctx, task_id, title, description, priority, task_type, status):
    """Update an existing task"""

    from .storage.work_queue import WorkQueue
    import yaml

    if not any([title, description, priority, task_type, status]):
        click.echo("❌ No updates specified. Use --help to see available options.")
        sys.exit(1)

    try:
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        work_queue = WorkQueue(config["sugar"]["storage"]["database"])

        # Build updates dictionary
        updates = {}
        if title:
            updates["title"] = title
        if description:
            updates["description"] = description
        if priority:
            updates["priority"] = priority
        if task_type:
            updates["type"] = task_type
        if status:
            updates["status"] = status

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Update the task
        success = asyncio.run(_update_task_async(work_queue, task_id, updates))

        if success:
            click.echo(f"✅ Updated task: {task_id}")
            # Show updated task
            task = asyncio.run(_get_task_by_id_async(work_queue, task_id))
            if task:
                status_emoji = {
                    "pending": "⏳",
                    "active": "⚡",
                    "completed": "✅",
                    "failed": "❌",
                }.get(task["status"], "📄")
                priority_str = "🚨" if task["priority"] == 5 else f"P{task['priority']}"
                click.echo(
                    f"{status_emoji} {priority_str} [{task['type']}] {task['title']}"
                )
        else:
            click.echo(f"❌ Task not found: {task_id}")
            sys.exit(1)

    except Exception as e:
        click.echo(f"❌ Error updating task: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("task_id")
@click.option(
    "--priority",
    "-p",
    type=click.IntRange(1, 5),
    help="Set priority (1=highest, 5=lowest)",
)
@click.option("--urgent", is_flag=True, help="Set priority to urgent (1)")
@click.option("--high", is_flag=True, help="Set priority to high (2)")
@click.option("--normal", is_flag=True, help="Set priority to normal (3)")
@click.option("--low", is_flag=True, help="Set priority to low (4)")
@click.option("--minimal", is_flag=True, help="Set priority to minimal (5)")
@click.pass_context
def priority(ctx, task_id, priority, urgent, high, normal, low, minimal):
    """Change the priority of a task"""

    from .storage.work_queue import WorkQueue
    import yaml

    # Count how many priority options were specified
    priority_flags = [urgent, high, normal, low, minimal]
    flag_count = sum(priority_flags)

    # Validate that only one priority method is specified
    if priority is not None and flag_count > 0:
        click.echo(
            "❌ Cannot specify both --priority and priority flags (--urgent, --high, etc.)"
        )
        sys.exit(1)

    if flag_count > 1:
        click.echo("❌ Can only specify one priority flag at a time")
        sys.exit(1)

    if priority is None and flag_count == 0:
        click.echo(
            "❌ Must specify either --priority <1-5> or a priority flag (--urgent, --high, etc.)"
        )
        sys.exit(1)

    # Map priority flags to numeric values
    if urgent:
        new_priority = 1
        priority_name = "urgent"
    elif high:
        new_priority = 2
        priority_name = "high"
    elif normal:
        new_priority = 3
        priority_name = "normal"
    elif low:
        new_priority = 4
        priority_name = "low"
    elif minimal:
        new_priority = 5
        priority_name = "minimal"
    else:
        new_priority = priority
        priority_names = {1: "urgent", 2: "high", 3: "normal", 4: "low", 5: "minimal"}
        priority_name = priority_names.get(new_priority, str(new_priority))

    try:
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        work_queue = WorkQueue(
            config.get("storage", {}).get("database", ".sugar/sugar.db")
        )

        async def change_priority():
            await work_queue.initialize()

            # Get current task to show before/after
            current_task = await work_queue.get_work_by_id(task_id)
            if not current_task:
                click.echo(f"❌ Task not found: {task_id}")
                return False

            old_priority = current_task.get("priority", 3)
            old_priority_names = {
                1: "urgent",
                2: "high",
                3: "normal",
                4: "low",
                5: "minimal",
            }
            old_priority_name = old_priority_names.get(old_priority, str(old_priority))

            # Update the priority
            success = await work_queue.update_work(task_id, {"priority": new_priority})

            if success:
                # Priority indicators for display
                priority_indicators = {
                    1: "🔥",  # urgent
                    2: "⚡",  # high
                    3: "📋",  # normal
                    4: "📝",  # low
                    5: "💤",  # minimal
                }

                old_indicator = priority_indicators.get(old_priority, "📋")
                new_indicator = priority_indicators.get(new_priority, "📋")

                click.echo(
                    f"✅ Priority changed: {old_indicator} {old_priority_name} → {new_indicator} {priority_name}"
                )
                click.echo(f"   Task: {current_task['title']}")
                return True
            else:
                click.echo(f"❌ Failed to update task priority")
                return False

        import asyncio

        success = asyncio.run(change_priority())
        if not success:
            sys.exit(1)

    except Exception as e:
        click.echo(f"❌ Error changing task priority: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("task_id", required=False)
@click.option("--stages", is_flag=True, help="Show orchestration stages")
@click.pass_context
def orchestrate(ctx, task_id, stages):
    """View or manage task orchestration.

    Without arguments: shows all orchestrating tasks
    With task_id: shows orchestration status for that task
    With --stages: shows detailed stage information
    """
    from .storage.work_queue import WorkQueue
    import yaml

    try:
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        work_queue = WorkQueue(config["sugar"]["storage"]["database"])

        async def show_orchestration():
            await work_queue.initialize()

            if task_id:
                # Show specific task orchestration
                task = await work_queue.get_work_by_id(task_id)
                if not task:
                    click.echo(f"❌ Task not found: {task_id}")
                    return

                click.echo(f"\n🎭 Orchestration Status for Task: {task['title']}")
                click.echo(f"ID: {task['id']}")
                click.echo(f"Type: {task['type']}")
                click.echo(f"Status: {task['status']}")

                if task.get("orchestrate"):
                    click.echo(f"Orchestration: ✅ Enabled")
                    if task.get("stage"):
                        click.echo(f"Current Stage: {task['stage']}")
                    if task.get("context_path"):
                        click.echo(f"Context Path: {task['context_path']}")
                    if task.get("assigned_agent"):
                        click.echo(f"Assigned Agent: {task['assigned_agent']}")

                    # Show subtasks
                    subtasks = await work_queue.get_subtasks(task_id)
                    if subtasks:
                        click.echo(f"\nSubtasks ({len(subtasks)}):")
                        for subtask in subtasks:
                            status_emoji = {
                                "pending": "⏳",
                                "active": "🔄",
                                "completed": "✅",
                                "failed": "❌",
                                "hold": "⏸️",
                            }.get(subtask["status"], "❓")

                            blocked_info = ""
                            if subtask.get("blocked_by"):
                                blocked_count = len(subtask["blocked_by"])
                                blocked_info = f" (blocked by {blocked_count} task{'s' if blocked_count > 1 else ''})"

                            agent_info = ""
                            if subtask.get("assigned_agent"):
                                agent_info = f" [{subtask['assigned_agent']}]"

                            click.echo(
                                f"  {status_emoji} {subtask['title']}{agent_info}{blocked_info}"
                            )

                        # Show ready subtasks
                        ready = await work_queue.get_ready_subtasks(task_id)
                        click.echo(
                            f"\nReady to execute: {len(ready)} of {len(subtasks)}"
                        )

                        # Show completion status
                        all_complete = await work_queue.check_subtasks_complete(task_id)
                        if all_complete:
                            click.echo("✅ All subtasks complete!")
                    else:
                        click.echo("\nNo subtasks yet.")

                    # Show skip_stages if present
                    if task.get("context", {}).get("skip_stages"):
                        skip_stages = task["context"]["skip_stages"]
                        click.echo(f"\nSkipped Stages: {', '.join(skip_stages)}")

                else:
                    click.echo(f"Orchestration: ❌ Not enabled")

                # Show stages if requested
                if stages:
                    click.echo("\n📋 Orchestration Stages:")
                    default_stages = [
                        "research",
                        "planning",
                        "implementation",
                        "review",
                    ]
                    skip_stages = task.get("context", {}).get("skip_stages", [])
                    current_stage = task.get("stage")

                    for stage in default_stages:
                        status = ""
                        if stage in skip_stages:
                            status = "⏭️  Skipped"
                        elif stage == current_stage:
                            status = "▶️  Current"
                        else:
                            status = "⏸️  Pending"
                        click.echo(f"  {status}: {stage}")
            else:
                # Show all orchestrating tasks
                tasks = await work_queue.get_recent_work(limit=100)
                orchestrating = [t for t in tasks if t.get("orchestrate")]

                if not orchestrating:
                    click.echo("No orchestrating tasks found.")
                    return

                click.echo(f"\n🎭 Orchestrating Tasks ({len(orchestrating)}):\n")
                for task in orchestrating:
                    status_emoji = {
                        "pending": "⏳",
                        "active": "🔄",
                        "completed": "✅",
                        "failed": "❌",
                        "hold": "⏸️",
                    }.get(task["status"], "❓")

                    stage_info = (
                        f" [Stage: {task['stage']}]" if task.get("stage") else ""
                    )
                    subtask_count = len(await work_queue.get_subtasks(task["id"]))
                    subtask_info = (
                        f" ({subtask_count} subtasks)" if subtask_count > 0 else ""
                    )

                    click.echo(
                        f"{status_emoji} {task['title']}{stage_info}{subtask_info}"
                    )
                    click.echo(f"   ID: {task['id']}")

        asyncio.run(show_orchestration())

    except Exception as e:
        click.echo(f"❌ Error showing orchestration: {e}", err=True)
        import traceback

        traceback.print_exc()
        sys.exit(1)


@cli.command()
@click.argument("task_id")
@click.pass_context
def context(ctx, task_id):
    """View accumulated context for an orchestrated task."""
    from .storage.work_queue import WorkQueue
    import yaml
    from pathlib import Path

    try:
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        work_queue = WorkQueue(config["sugar"]["storage"]["database"])

        async def show_context():
            await work_queue.initialize()

            task = await work_queue.get_work_by_id(task_id)
            if not task:
                click.echo(f"❌ Task not found: {task_id}")
                return

            if not task.get("orchestrate"):
                click.echo(f"❌ Task is not orchestrated: {task['title']}")
                return

            click.echo(f"\n📝 Orchestration Context for: {task['title']}\n")

            # Show context path if available
            if task.get("context_path"):
                context_path = Path(task["context_path"])
                click.echo(f"Context Path: {context_path}")

                if context_path.exists():
                    click.echo(f"\nContents of {context_path}:\n")
                    click.echo("=" * 80)
                    with open(context_path, "r") as f:
                        click.echo(f.read())
                    click.echo("=" * 80)
                else:
                    click.echo("⚠️  Context file not found yet.")
            else:
                click.echo("⚠️  No context path set for this task.")

            # Show task context metadata
            if task.get("context"):
                click.echo("\nTask Context Metadata:")
                for key, value in task["context"].items():
                    if key not in ["added_via", "timestamp"]:
                        click.echo(f"  {key}: {value}")

        asyncio.run(show_context())

    except Exception as e:
        click.echo(f"❌ Error showing context: {e}", err=True)
        import traceback

        traceback.print_exc()
        sys.exit(1)


@cli.command()
@click.option("--lines", "-n", "--tail", default=50, help="Number of log lines to show")
@click.option("--follow", "-f", is_flag=True, help="Follow log output (like tail -f)")
@click.option(
    "--level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
    help="Filter by log level",
)
@click.pass_context
def logs(ctx, lines, follow, level):
    """Show Sugar logs with debugging information"""
    import yaml

    try:
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        log_file = (
            config.get("sugar", {}).get("logging", {}).get("file", ".sugar/sugar.log")
        )
        log_path = Path(log_file)

        if not log_path.exists():
            click.echo(f"❌ Log file not found: {log_path}")
            return

        if follow:
            click.echo(f"📋 Following Sugar logs (Ctrl+C to stop): {log_path}")
            click.echo("=" * 60)

            # Use tail -f equivalent
            import subprocess
            import sys

            cmd = ["tail", "-f"]
            if lines != 50:
                cmd.extend(["-n", str(lines)])
            cmd.append(str(log_path))

            try:
                process = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)
                process.wait()
            except KeyboardInterrupt:
                process.terminate()
                click.echo("\n👋 Stopped following logs")
        else:
            click.echo(f"📋 Last {lines} lines from Sugar logs: {log_path}")
            click.echo("=" * 60)

            # Read last N lines
            with open(log_path, "r") as f:
                log_lines = f.readlines()

            # Filter by level if specified
            if level:
                log_lines = [line for line in log_lines if f" - {level} - " in line]

            # Show last N lines
            for line in log_lines[-lines:]:
                click.echo(line.rstrip())

    except Exception as e:
        click.echo(f"❌ Error reading logs: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.pass_context
def debug(ctx):
    """Show debugging information about last Claude execution"""
    import yaml
    import os

    try:
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        # Check if session state exists
        context_file = (
            config.get("sugar", {})
            .get("claude", {})
            .get("context_file", ".sugar/context.json")
        )
        session_file = context_file.replace(".json", "_session.json")

        click.echo("🔍 Sugar Debug Information")
        click.echo("=" * 50)

        # Show session state
        if Path(session_file).exists():
            with open(session_file, "r") as f:
                session_state = json.load(f)

            click.echo("📋 Last Session State:")
            click.echo(
                f"   Last execution: {session_state.get('last_execution_time', 'unknown')}"
            )
            click.echo(
                f"   Task type: {session_state.get('last_task_type', 'unknown')}"
            )
            click.echo(
                f"   Context strategy: {session_state.get('context_strategy', 'unknown')}"
            )
            click.echo(f"   Execution count: {session_state.get('execution_count', 0)}")
            click.echo(f"   Simulated: {session_state.get('simulated', False)}")
            click.echo()
        else:
            click.echo("📋 No session state found (fresh start)")
            click.echo()

        # Show current context file
        if Path(context_file).exists():
            with open(context_file, "r") as f:
                context = json.load(f)

            click.echo("📄 Current Context:")
            click.echo(f"   Continue session: {context.get('continue_session', False)}")
            click.echo(f"   Execution count: {context.get('execution_count', 0)}")
            click.echo(f"   Safety mode: {context.get('safety_mode', True)}")
            click.echo()
        else:
            click.echo("📄 No context file found")
            click.echo()

        # Show Claude CLI configuration
        claude_config = config.get("sugar", {}).get("claude", {})
        click.echo("🤖 Claude Configuration:")
        click.echo(f"   Command: {claude_config.get('command', 'unknown')}")
        click.echo(f"   Timeout: {claude_config.get('timeout', 'unknown')}s")
        click.echo(f"   Use continuous: {claude_config.get('use_continuous', True)}")
        click.echo(
            f"   Context strategy: {claude_config.get('context_strategy', 'project')}"
        )
        click.echo()

        # Show working directory and key files
        click.echo("📁 Environment:")
        click.echo(f"   Working directory: {os.getcwd()}")
        click.echo(f"   Config file: {config_file}")
        click.echo(f"   Context file: {context_file}")
        click.echo(f"   Session file: {session_file}")
        click.echo()

        # Test Claude CLI availability
        claude_cmd = claude_config.get("command", "claude")
        click.echo("🧪 Claude CLI Test:")
        try:
            import subprocess

            result = subprocess.run(
                [claude_cmd, "--version"], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                click.echo(f"   ✅ Claude CLI working: {result.stdout.strip()}")
            else:
                click.echo(f"   ❌ Claude CLI error: {result.stderr.strip()}")
        except Exception as e:
            click.echo(f"   ❌ Claude CLI not found: {e}")
        click.echo()

        # Suggest next steps
        click.echo("💡 Debugging Tips:")
        click.echo("   • Use 'sugar logs -f' to follow live logs")
        click.echo("   • Use 'sugar logs --level DEBUG' to see detailed execution")
        click.echo("   • Check if Claude CLI works: claude --version")
        click.echo("   • Try dry run mode first: set dry_run: true in config")
        click.echo("   • Use 'sugar run --once --dry-run' to test execution")

    except Exception as e:
        click.echo(f"❌ Error getting debug info: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.pass_context
def status(ctx):
    """Show Sugar system status and queue statistics"""

    from .storage.work_queue import WorkQueue
    import yaml

    try:
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        work_queue = WorkQueue(config["sugar"]["storage"]["database"])

        # Get statistics
        stats = asyncio.run(_get_status_async(work_queue))

        click.echo("\n🤖 Sugar System Status")
        click.echo("=" * 40)
        click.echo(f"📊 Total Tasks: {stats['total']}")
        click.echo(f"⏳ Pending: {stats['pending']}")
        click.echo(f"⏸️ On Hold: {stats['hold']}")
        click.echo(f"⚡ Active: {stats['active']}")
        click.echo(f"✅ Completed: {stats['completed']}")
        click.echo(f"❌ Failed: {stats['failed']}")
        click.echo(f"📈 Recent (24h): {stats['recent_24h']}")

        # Show next few pending tasks
        next_tasks = asyncio.run(_get_next_tasks_async(work_queue, 3))
        if next_tasks:
            click.echo("\n🔜 Next Tasks:")
            click.echo("-" * 20)
            for task in next_tasks:
                priority_str = "🚨" if task["priority"] == 5 else f"P{task['priority']}"
                click.echo(f"{priority_str} [{task['type']}] {task['title']}")

        click.echo()

    except Exception as e:
        click.echo(f"❌ Error getting status: {e}", err=True)
        sys.exit(1)


@cli.command()
def help():
    """Show comprehensive Sugar help and getting started guide"""

    click.echo(
        """
🍰 Sugar - AI-Powered Autonomous Development System
================================================

Sugar 🍰 is an autonomous development system that works with Claude Code CLI to
provide 24/7 development assistance through task discovery and execution.

📋 QUICK START
--------------
1. Initialize Sugar in your project:
   sugar init

2. Add your first task:
   sugar add "Implement user authentication" --type feature --priority 4

3. Test Sugar in safe mode:
   sugar run --dry-run --once

4. Start autonomous development:
   sugar run

🎯 WHAT SUGAR DOES
------------------
Sugar operates in TWO modes:

🤖 AUTONOMOUS DISCOVERY:
   • Discovers work from error logs, GitHub issues, code quality analysis
   • Analyzes test coverage gaps and suggests improvements  
   • Continuously monitors and adapts to your project needs

👤 MANUAL TASK MANAGEMENT:
   • Add specific tasks via CLI: sugar add "task description"
   • Set priorities and task types (bug_fix, feature, test, refactor, documentation)
   • Full control over work queue with sugar list, sugar view, sugar update

📚 CORE COMMANDS
----------------
sugar init              Initialize Sugar in current project
sugar add TITLE         Add new task to work queue (--status hold available)
sugar list              List tasks (--status, --type, --limit, --format options)
sugar view TASK_ID      Show detailed task information
sugar update TASK_ID    Update existing task (--title, --priority, etc.)
sugar hold TASK_ID      Put task on hold (--reason for context)
sugar release TASK_ID   Release task from hold
sugar remove TASK_ID    Remove task from queue
sugar status            Show system status and queue statistics
sugar run               Start autonomous development system
                        (--dry-run, --once, --validate options)

🔧 CONFIGURATION
----------------
Sugar uses .sugar/config.yaml for project-specific settings:
• Discovery sources (error logs, GitHub, code analysis)
• Claude CLI integration settings
• Safety controls and execution limits
• Task prioritization and scheduling

📁 PROJECT STRUCTURE
--------------------
your-project/
├── .sugar/                    Sugar configuration and data
│   ├── config.yaml           Project settings
│   ├── sugar.db             Task database  
│   └── sugar.log            Activity logs
└── logs/errors/             Error logs monitored by Sugar

🛡️ SAFETY FEATURES
-------------------
• Dry-run mode by default (no changes until you set dry_run: false)
• Path exclusions prevent system file modifications
• Timeout protection prevents runaway processes
• Project isolation - each project gets its own Sugar instance

⚠️  EXECUTION CONTEXT
---------------------
• Run Sugar OUTSIDE of Claude Code sessions (in regular terminal)
• Sugar calls Claude Code CLI as needed for task execution
• Architecture: Terminal → Sugar → Claude Code CLI
• Avoid: Claude Code → Sugar (recursive execution)

📖 DOCUMENTATION
----------------
Complete documentation: docs/README.md
• User Guide: docs/user/quick-start.md
• CLI Reference: docs/user/cli-reference.md
• Examples: docs/user/examples.md
• Troubleshooting: docs/user/troubleshooting.md
• Contributing: docs/dev/contributing.md

🆘 NEED HELP?
--------------
• Check troubleshooting guide: docs/user/troubleshooting.md
• GitHub Issues: https://github.com/roboticforce/sugar/issues
• Email: contact@roboticforce.io

💡 TIPS
-------
• Start with 'sugar run --dry-run --once' to see what Sugar would do
• Monitor logs with 'tail -f .sugar/sugar.log'
• Use 'sugar status' to check queue health
• Each project needs its own 'sugar init'

⚖️  LEGAL DISCLAIMER
--------------------
• Sugar is provided "AS IS" without warranty of any kind
• Users are responsible for reviewing all AI-generated code
• Not affiliated with Anthropic, Inc. ("Claude" is their trademark)
• See TERMS.md for complete terms and liability disclaimers
• By using Sugar, you agree to these terms and conditions

Ready to supercharge your development workflow? 🚀
"""
    )


@cli.command()
@click.option(
    "--dry-run", is_flag=True, help="Run in simulation mode (override config)"
)
@click.option("--once", is_flag=True, help="Run one cycle and exit")
@click.option("--validate", is_flag=True, help="Validate configuration and exit")
@click.pass_context
def run(ctx, dry_run, once, validate):
    """
    Start Sugar - AI-powered autonomous development system

    A lightweight autonomous development system that:
    - Discovers work from error logs and feedback
    - Executes tasks using Claude Code CLI
    - Learns and adapts from results
    """
    global sugar_loop

    try:
        # Initialize Sugar
        config = ctx.obj["config"]
        sugar_loop = SugarLoop(config)

        # Override dry_run if specified
        if dry_run:
            sugar_loop.config["sugar"]["dry_run"] = True
            logger.info("🧪 Dry run mode enabled via command line")

        # Validation mode
        if validate:
            asyncio.run(validate_config(sugar_loop))
            return

        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Run Sugar
        if once:
            asyncio.run(run_once(sugar_loop))
        else:
            asyncio.run(run_continuous(sugar_loop))

    except KeyboardInterrupt:
        logger.info("🛑 Shutdown requested by user")
    except Exception as e:
        logger.error(f"💥 Sugar crashed: {e}", exc_info=True)
        sys.exit(1)


async def validate_config(sugar_loop):
    """Validate configuration and dependencies"""
    logger.info("🔍 Validating Sugar configuration...")

    # Check config structure
    config = sugar_loop.config
    required_sections = ["sugar"]

    for section in required_sections:
        if section not in config:
            logger.error(f"❌ Missing required config section: {section}")
            sys.exit(1)

    # Validate Claude CLI
    from .executor.claude_wrapper import ClaudeWrapper

    claude_wrapper = ClaudeWrapper(config["sugar"]["claude"])

    if await claude_wrapper.validate_claude_cli():
        logger.info("✅ Claude CLI validation passed")
    else:
        logger.warning("⚠️ Claude CLI validation failed - dry run mode recommended")

    # Check discovery paths
    from .discovery.error_monitor import ErrorLogMonitor

    if config["sugar"]["discovery"]["error_logs"]["enabled"]:
        error_monitor = ErrorLogMonitor(config["sugar"]["discovery"]["error_logs"])
        health = await error_monitor.health_check()
        logger.info(
            f"📁 Discovery paths: {health['paths_accessible']}/{health['paths_configured']} accessible"
        )

    # Initialize storage
    await sugar_loop.work_queue.initialize()
    queue_health = await sugar_loop.work_queue.health_check()
    logger.info(f"💾 Storage initialized: {queue_health['database_path']}")

    logger.info("✅ Configuration validation completed")


async def run_once(sugar_loop):
    """Run Sugar for one cycle and exit"""
    logger.info(f"🔄 Running {get_version_info()} for one cycle...")

    # Initialize
    await sugar_loop.work_queue.initialize()

    # Run discovery
    await sugar_loop._discover_work()

    # Execute work
    await sugar_loop._execute_work()

    # Process feedback
    await sugar_loop._process_feedback()

    # Show final stats
    stats = await sugar_loop.work_queue.get_stats()
    logger.info(f"📊 Final stats: {stats}")

    logger.info("✅ Single cycle completed")


async def run_continuous(sugar_loop):
    """Run Sugar continuously"""
    global shutdown_event
    shutdown_event = asyncio.Event()

    # Create PID file for stop command
    import pathlib
    import os

    config_dir = pathlib.Path(
        sugar_loop.config.get("sugar", {})
        .get("storage", {})
        .get("database", ".sugar/sugar.db")
    ).parent
    config_dir.mkdir(exist_ok=True)
    pidfile = config_dir / "sugar.pid"

    try:
        # Create a new process group so force kill can terminate all children
        os.setpgrp()

        with open(pidfile, "w") as f:
            f.write(str(os.getpid()))

        logger.info(f"🚀 Starting {get_version_info()} in continuous mode...")
        logger.info("💡 Press Ctrl+C to stop Sugar gracefully")
        logger.info("💡 Or run 'sugar stop' from another terminal")
        logger.info("💡 Or run 'sugar stop --force' to force immediate termination")

        await sugar_loop.start_with_shutdown(shutdown_event)
    except KeyboardInterrupt:
        logger.info("🛑 Shutdown signal received")
    finally:
        logger.info("⏳ Stopping Sugar gracefully...")
        await sugar_loop.stop()

        # Clean up PID file
        if pidfile.exists():
            pidfile.unlink()

        logger.info("🏁 Sugar stopped")


# Async helper functions for CLI commands
async def _add_task_async(work_queue, task_data):
    """Helper to add task asynchronously"""
    await work_queue.initialize()
    task_id = await work_queue.add_work(task_data)
    return task_id


async def _list_tasks_async(
    work_queue, status_filter, limit, task_type_filter, priority_filter=None
):
    """Helper to list tasks asynchronously"""
    await work_queue.initialize()

    if status_filter == "all":
        status_filter = None

    tasks = await work_queue.get_recent_work(limit=limit, status=status_filter)

    # Filter by task type if specified
    if task_type_filter != "all":
        tasks = [task for task in tasks if task["type"] == task_type_filter]

    # Filter by priority if specified
    if priority_filter is not None:
        tasks = [task for task in tasks if task.get("priority") == priority_filter]

    return tasks


async def _get_status_async(work_queue):
    """Helper to get status asynchronously"""
    await work_queue.initialize()
    return await work_queue.get_stats()


async def _get_next_tasks_async(work_queue, limit):
    """Helper to get next pending tasks"""
    await work_queue.initialize()
    return await work_queue.get_recent_work(limit=limit, status="pending")


async def _get_task_by_id_async(work_queue, task_id):
    """Helper to get specific task by ID"""
    await work_queue.initialize()
    return await work_queue.get_work_by_id(task_id)


async def _remove_task_async(work_queue, task_id):
    """Helper to remove task by ID"""
    await work_queue.initialize()
    return await work_queue.remove_work(task_id)


async def _update_task_async(work_queue, task_id, updates):
    """Helper to update task by ID"""
    await work_queue.initialize()
    return await work_queue.update_work(task_id, updates)


def _detect_github_config(project_path: Path) -> dict:
    """Detect GitHub CLI availability and current repository configuration"""
    import subprocess
    import os

    github_config = {
        "detected": True,  # Mark that detection was attempted
        "cli_available": False,
        "gh_available": False,  # Keep for backward compatibility
        "gh_command": "gh",
        "authenticated": False,
        "repo": "",
        "auth_method": "auto",
    }

    try:
        # Check if GitHub CLI is available
        result = subprocess.run(
            ["gh", "--version"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            github_config["cli_available"] = True
            github_config["gh_available"] = True  # Keep for backward compatibility

            # Check if authenticated
            auth_result = subprocess.run(
                ["gh", "auth", "status"], capture_output=True, text=True, timeout=10
            )
            github_config["authenticated"] = auth_result.returncode == 0

            # Try to detect current repository
            try:
                # Change to project directory for repo detection
                original_cwd = os.getcwd()
                os.chdir(project_path)

                repo_result = subprocess.run(
                    ["gh", "repo", "view", "--json", "nameWithOwner"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if repo_result.returncode == 0:
                    import json

                    repo_data = json.loads(repo_result.stdout)
                    github_config["repo"] = repo_data.get("nameWithOwner", "")

                # Restore original directory
                os.chdir(original_cwd)

            except Exception:
                # If repo detection fails, try git remote
                try:
                    os.chdir(project_path)
                    git_result = subprocess.run(
                        ["git", "remote", "get-url", "origin"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if git_result.returncode == 0:
                        remote_url = git_result.stdout.strip()
                        # Parse GitHub repository from remote URL
                        repo = _parse_github_repo_from_url(remote_url)
                        if repo:
                            github_config["repo"] = repo
                    os.chdir(original_cwd)
                except Exception:
                    os.chdir(original_cwd)
                    pass

            # Set auth method based on availability
            if github_config["authenticated"]:
                github_config["auth_method"] = "gh_cli"
            else:
                github_config["auth_method"] = "auto"

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass

    return github_config


def _parse_github_repo_from_url(url: str) -> str:
    """Parse GitHub repository name from remote URL"""
    import re

    # Handle both HTTPS and SSH URLs
    # HTTPS: https://github.com/owner/repo.git
    # SSH: git@github.com:owner/repo.git

    patterns = [
        r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$",
        r"github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    return ""


def _find_claude_cli():
    """Find Claude CLI in standard locations"""
    # Try common paths
    possible_paths = [
        "claude",  # In PATH
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
        Path.home() / ".claude" / "local" / "claude",
        Path.home() / ".local" / "bin" / "claude",
    ]

    for path in possible_paths:
        try:
            import subprocess

            result = subprocess.run(
                [str(path), "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return str(path)
        except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
            continue

    return None


def _get_workflow_config_section() -> str:
    """Generate GitHub workflow configuration section"""
    return """
      # Label filtering options:
      # issue_labels: ["bug", "enhancement"]  # Specific labels to watch
      # issue_labels: []                      # No filtering - work on ALL open issues  
      # issue_labels: ["*"]                   # Work on issues with any labels (exclude unlabeled)
      # issue_labels: ["unlabeled"]           # Work only on issues without labels
      
      # Workflow settings for completed work
      workflow:
        # Auto-close issues after successful completion
        auto_close_issues: true
        
        # Git workflow: "direct_commit" or "pull_request"
        git_workflow: "direct_commit"  # direct_commit|pull_request
        
        # Branch settings (used when git_workflow: "pull_request")
        branch:
          # Auto-create feature branches for each issue
          create_branches: true
          # Branch naming pattern (variables: {issue_number}, {issue_title_slug})
          name_pattern: "sugar/issue-{issue_number}"
          # Base branch for new branches and PRs
          base_branch: "main"
          
        # Pull request settings (used when git_workflow: "pull_request")
        pull_request:
          # Auto-create PRs after completing work
          auto_create: true
          # Auto-merge PRs (only if all checks pass)
          auto_merge: false
          # PR title pattern (variables: same as branch naming)
          title_pattern: "Fix #{issue_number}: {issue_title}"
          # Include work summary in PR description
          include_work_summary: true
          
        # Commit settings
        commit:
          # Include issue reference in commit messages
          include_issue_ref: true
          # Commit message pattern (variables: {issue_number}, {work_summary})
          message_pattern: "Fix #{issue_number}: {work_summary}"
          # Auto-commit changes after completing work
          auto_commit: true"""


def _get_github_config_section(github_config: dict = None) -> str:
    """Generate GitHub configuration section based on detection results"""
    if not github_config or not github_config.get("detected"):
        # Default GitHub section when no detection attempted
        return f"""
      enabled: false  # Set to true and configure to enable
      repo: ""  # e.g., "user/repository"
      
      # Authentication method: "token", "gh_cli", or "auto"
      auth_method: "auto"  # Try gh CLI first, fallback to token
      
      # GitHub Personal Access Token (if using "token" method)
      token: ""  # Or set GITHUB_TOKEN environment variable
      
      # GitHub CLI integration (if using "gh_cli" method)  
      gh_cli:
        command: "gh"  # Path to gh command
        use_default_auth: true  # Use gh CLI's existing authentication
        
      # Discovery settings  
      issue_labels: []  # No filtering - work on ALL open issues
      check_interval_minutes: 30{_get_workflow_config_section()}"""

    if github_config.get("authenticated") and github_config.get("repo"):
        # GitHub CLI detected, authenticated, and repo found
        return f"""
      enabled: true  # GitHub CLI detected and authenticated
      repo: "{github_config['repo']}"  # Auto-detected from git remote
      
      # Authentication method: using GitHub CLI
      auth_method: "gh_cli"  # GitHub CLI is authenticated
      
      # GitHub CLI integration  
      gh_cli:
        command: "gh"  # GitHub CLI detected
        use_default_auth: true  # Using existing gh authentication
        
      # Discovery settings  
      issue_labels: []  # No filtering - work on ALL open issues
      check_interval_minutes: 30{_get_workflow_config_section()}"""

    elif github_config.get("cli_available"):
        repo_comment = (
            f'# Auto-detected: "{github_config["repo"]}"'
            if github_config.get("repo")
            else '# Set to "owner/repository" format'
        )
        auth_status = (
            "# GitHub CLI detected but not authenticated - run 'gh auth login'"
        )

        return f"""
      enabled: false  {auth_status}
      repo: "{github_config.get('repo', '')}"  {repo_comment}
      
      # Authentication method: GitHub CLI available but not authenticated
      auth_method: "gh_cli"  # GitHub CLI detected
      
      # GitHub CLI integration (run 'gh auth login' to authenticate)
      gh_cli:
        command: "gh"  # GitHub CLI detected
        use_default_auth: true  # Authenticate with 'gh auth login'
        
      # Discovery settings  
      issue_labels: []  # No filtering - work on ALL open issues
      check_interval_minutes: 30{_get_workflow_config_section()}"""

    else:
        # GitHub CLI not detected
        return f"""
      enabled: false  # GitHub CLI not detected - install or use token auth
      repo: ""  # e.g., "user/repository"
      
      # Authentication method: GitHub CLI not found
      auth_method: "auto"  # Install gh CLI or use token
      
      # GitHub Personal Access Token (alternative if gh CLI not available)
      token: ""  # Get from: https://github.com/settings/tokens
      
      # GitHub CLI integration (install GitHub CLI for best experience)
      gh_cli:
        command: "gh"  # Install with: brew install gh (macOS) or see github.com/cli/cli
        use_default_auth: true
        
      # Discovery settings  
      issue_labels: []  # No filtering - work on ALL open issues
      check_interval_minutes: 30{_get_workflow_config_section()}"""


def _generate_default_config(
    claude_cmd: str, project_root: str, github_config: dict = None
) -> str:
    """Generate default Sugar configuration"""
    return f"""# Sugar Configuration for {Path(project_root).name}
# Documentation: https://github.com/roboticforce/sugar
sugar:
  # Core Loop Settings
  loop_interval: 300  # Seconds between discovery cycles
  max_concurrent_work: 3  # Max tasks to execute per cycle
  dry_run: true  # Safe mode - set to false to execute tasks

  # Claude Agent SDK Integration (V3)
  claude:
    command: "{claude_cmd}"  # Auto-detected Claude CLI path
    timeout: 1800  # Max seconds per task (30 min)

    # Model and permissions
    model: "claude-sonnet-4-20250514"
    permission_mode: "acceptEdits"  # default | acceptEdits | bypassPermissions

    # Quality gates (pre/post execution checks)
    quality_gates:
      enabled: true

    # MCP servers (optional - uncomment to configure)
    # mcp_servers:
    #   filesystem:
    #     command: "npx"
    #     args: ["-y", "@anthropic/mcp-fs"]

  # Work Discovery
  discovery:
    # Directories excluded from all discovery modules
    global_excluded_dirs: [
      "node_modules", ".git", "__pycache__",
      "venv", ".venv", "env", ".env",
      "build", "dist", ".tox", ".nox",
      "coverage", "htmlcov", ".pytest_cache",
      ".sugar", ".claude"
    ]

    error_logs:
      enabled: true
      paths: ["logs/errors/", "logs/feedback/", ".sugar/logs/"]
      patterns: ["*.json", "*.log"]
      max_age_hours: 24

    github:{_get_github_config_section(github_config)}

    code_quality:
      enabled: true
      root_path: "."
      file_extensions: [".py", ".js", ".ts", ".jsx", ".tsx"]
      max_files_per_scan: 50

    test_coverage:
      enabled: true
      root_path: "."
      source_dirs: ["src", "lib", "app", "api", "server"]
      test_dirs: ["tests", "test", "__tests__", "spec"]

  # Storage
  storage:
    database: ".sugar/sugar.db"
    backup_interval: 3600

  # Safety
  safety:
    max_retries: 3
    excluded_paths: ["/System", "/usr/bin", "/etc", ".sugar"]

  # Logging
  logging:
    level: "INFO"
    file: ".sugar/sugar.log"

  # Workflow System
  workflow:
    # Profiles: solo (direct commits), balanced (PRs), enterprise (full governance)
    profile: "solo"

  # Issue Responder (AI-powered issue responses)
  issue_responder:
    enabled: false              # Enable automatic issue responses

    # Response behavior
    auto_post_threshold: 0.8    # Min confidence to auto-post (0.0-1.0)
    max_response_length: 2000   # Max chars per response
    response_delay_seconds: 0   # Delay before posting (0 = immediate)

    # Rate limiting
    rate_limit_per_hour: 10     # Max responses per hour (0 = unlimited)

    # Issue filtering
    respond_to_labels: []       # Empty = all issues, or ["question", "bug"]
    skip_labels: ["wontfix", "duplicate", "stale"]
    skip_bot_issues: true       # Skip issues created by bots

    # Follow-up handling
    handle_follow_ups: "ignore" # "ignore" | "queue" | "auto"

    # Model selection
    model: "claude-sonnet-4-20250514"

  # Ralph Wiggum (iterative AI execution)
  ralph:
    enabled: true                   # Allow Ralph mode for tasks
    max_iterations: 10              # Default max iterations per task
    iteration_timeout: 300          # Max seconds per iteration (5 min)
    completion_promise: "DONE"      # Default completion signal
    require_completion_criteria: true  # Require <promise> tags or --max-iterations
    quality_gates_enabled: true     # Run quality gates between iterations
    stop_on_gate_failure: false     # Keep trying even if gates fail
"""


@cli.command()
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Force immediate termination of Sugar and all child processes",
)
@click.pass_context
def stop(ctx, force):
    """Stop running Sugar instance gracefully or forcefully"""
    import os
    import pathlib

    config_file = ctx.obj["config"]

    # Load config to get consistent path with PID file creation
    import yaml

    try:
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)
        # Use same path logic as PID file creation
        database_path = (
            config.get("sugar", {})
            .get("storage", {})
            .get("database", ".sugar/sugar.db")
        )
        config_dir = pathlib.Path(database_path).parent
    except:
        # Fallback to config file directory
        config_dir = pathlib.Path(config_file).parent

    pidfile = config_dir / "sugar.pid"

    if not pidfile.exists():
        click.echo("❌ No running Sugar instance found")
        return

    try:
        with open(pidfile, "r") as f:
            pid = int(f.read().strip())

        if force:
            # Force shutdown with SIGKILL - immediate termination
            try:
                # Kill the process group to terminate all child processes
                os.killpg(os.getpgid(pid), signal.SIGKILL)
                click.echo(
                    f"💥 Force terminated Sugar process and all children (PID: {pid})"
                )
            except ProcessLookupError:
                # Process group doesn't exist, try individual process
                os.kill(pid, signal.SIGKILL)
                click.echo(f"💥 Force terminated Sugar process (PID: {pid})")

            # Clean up PID file immediately since process was killed
            try:
                if pidfile.exists():
                    pidfile.unlink()
                click.echo("🏁 Sugar force stopped")
            except:
                click.echo("🏁 Sugar force stopped (PID file already cleaned up)")
        else:
            # Send SIGTERM for graceful shutdown
            os.kill(pid, signal.SIGTERM)
            click.echo(f"✅ Sent shutdown signal to Sugar process (PID: {pid})")
            click.echo("⏳ Sugar is shutting down...")

            # Note: PID file cleanup is handled by the main Sugar process

    except (ValueError, ProcessLookupError):
        # Clean up stale pid file if it still exists
        try:
            if pidfile.exists():
                pidfile.unlink()
            click.echo("❌ Stale PID file found and removed")
        except:
            click.echo("❌ Stale PID file found (already cleaned up)")
    except PermissionError:
        click.echo("❌ Permission denied - cannot stop Sugar process")
    except Exception as e:
        # Handle race condition where PID file was cleaned up by main process
        if "No such file or directory" in str(e) and "sugar.pid" in str(e):
            click.echo("🏁 Sugar process stopped (PID file already cleaned up)")
        else:
            click.echo(f"❌ Error stopping Sugar: {e}")


@cli.command()
@click.option(
    "--format",
    type=click.Choice(["json", "yaml", "text"]),
    default="json",
    help="Output format",
)
@click.option("--output", "-o", help="Output file path (default: stdout)")
@click.option(
    "--include-sensitive",
    is_flag=True,
    help="Include sensitive config (paths, tokens) - use with caution",
)
@click.pass_context
def debug(ctx, format, output, include_sensitive):
    """Generate comprehensive diagnostic information for troubleshooting

    This command outputs system state, configuration, and recent activity
    to help diagnose issues. Safe by default - excludes sensitive information.
    """
    import yaml
    import platform
    import subprocess
    import json
    from datetime import datetime, timedelta
    from pathlib import Path

    async def generate_diagnostic():
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        from .storage.work_queue import WorkQueue

        work_queue = WorkQueue(
            config.get("sugar", {})
            .get("storage", {})
            .get("database", ".sugar/sugar.db")
        )
        await work_queue.initialize()

        # Collect diagnostic information
        diagnostic = {
            "timestamp": datetime.now().isoformat(),
            "sugar_version": get_version_info(),
            "system_info": {
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "architecture": platform.architecture()[0],
                "hostname": platform.node() if include_sensitive else "***REDACTED***",
            },
            "environment": {
                "working_directory": (
                    str(Path.cwd()) if include_sensitive else "***REDACTED***"
                ),
                "config_file": (
                    config_file if include_sensitive else str(Path(config_file).name)
                ),
                "sugar_directory": (
                    str(Path(config_file).parent) if include_sensitive else ".sugar/"
                ),
            },
            "tool_status": {},
            "configuration": {},
            "work_queue_status": {},
            "recent_activity": {},
            "potential_issues": [],
        }

        # Check tool availability
        tools_to_check = [
            ("claude_cli", ["claude", "--version"]),
            ("github_cli", ["gh", "--version"]),
            ("git", ["git", "--version"]),
            ("python", ["python", "--version"]),
        ]

        for tool_name, cmd in tools_to_check:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                diagnostic["tool_status"][tool_name] = {
                    "available": result.returncode == 0,
                    "version": (
                        result.stdout.strip().split("\n")[0]
                        if result.returncode == 0
                        else None
                    ),
                    "error": result.stderr.strip() if result.returncode != 0 else None,
                }
            except Exception as e:
                diagnostic["tool_status"][tool_name] = {
                    "available": False,
                    "error": str(e),
                }

        # Git repository status
        try:
            git_status = subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True
            )
            git_branch = subprocess.run(
                ["git", "branch", "--show-current"], capture_output=True, text=True
            )
            git_remote = subprocess.run(
                ["git", "remote", "-v"], capture_output=True, text=True
            )

            diagnostic["git_status"] = {
                "is_git_repo": git_status.returncode == 0,
                "current_branch": (
                    git_branch.stdout.strip() if git_branch.returncode == 0 else None
                ),
                "has_changes": (
                    len(git_status.stdout.strip()) > 0
                    if git_status.returncode == 0
                    else None
                ),
                "remotes": (
                    git_remote.stdout.strip().split("\n")
                    if git_remote.returncode == 0 and not include_sensitive
                    else ["***REDACTED***"] if git_remote.returncode == 0 else []
                ),
            }
        except Exception as e:
            diagnostic["git_status"] = {"error": str(e)}

        # Configuration analysis (sanitized)
        config_analysis = config.copy()
        if not include_sensitive:
            # Sanitize sensitive paths and tokens
            if "sugar" in config_analysis and "claude" in config_analysis["sugar"]:
                if "cli_path" in config_analysis["sugar"]["claude"]:
                    config_analysis["sugar"]["claude"]["cli_path"] = "***REDACTED***"
            if "sugar" in config_analysis and "github" in config_analysis["sugar"]:
                if "token" in config_analysis["sugar"]["github"]:
                    config_analysis["sugar"]["github"]["token"] = "***REDACTED***"

        diagnostic["configuration"] = config_analysis

        # Work queue analysis
        try:
            # Get total counts by status
            all_work = await work_queue.get_pending_work(limit=1000)
            status_counts = {}
            for item in all_work:
                status = item.get("status", "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1

            # Get recent work (last 10 items)
            recent_work = [
                {
                    "id": item.get("id", ""),
                    "title": item.get("title", ""),
                    "type": item.get("type", ""),
                    "status": item.get("status", ""),
                    "priority": item.get("priority", 3),
                    "created_at": item.get("created_at", ""),
                    "attempts": item.get("attempts", 0),
                    "source": item.get("source", ""),
                    "error_message": (
                        item.get("error_message", "")
                        if item.get("error_message")
                        else None
                    ),
                }
                for item in all_work[:10]  # Last 10 items
            ]

            diagnostic["work_queue_status"] = {
                "total_items": len(all_work),
                "status_breakdown": status_counts,
                "recent_items": recent_work,
            }

        except Exception as e:
            diagnostic["work_queue_status"] = {"error": str(e)}

        # Analyze potential issues based on configuration and status
        issues = []

        # Check for common dry-run issues
        if config.get("sugar", {}).get("execution", {}).get("dry_run", True):
            issues.append(
                {
                    "severity": "warning",
                    "category": "configuration",
                    "issue": "Dry-run mode is enabled",
                    "description": "Sugar will simulate actions but not make actual changes (commits, GitHub updates, etc.)",
                    "fix": "Set 'dry_run: false' in .sugar/config.yaml under sugar.execution section",
                }
            )

        # Check for GitHub CLI auth
        if not diagnostic["tool_status"].get("github_cli", {}).get("available"):
            issues.append(
                {
                    "severity": "error",
                    "category": "tools",
                    "issue": "GitHub CLI not available",
                    "description": "Required for GitHub integration (reading/updating issues, creating branches)",
                    "fix": "Install GitHub CLI: https://cli.github.com/",
                }
            )

        # Check for Claude CLI
        if not diagnostic["tool_status"].get("claude_cli", {}).get("available"):
            issues.append(
                {
                    "severity": "error",
                    "category": "tools",
                    "issue": "Claude CLI not available",
                    "description": "Required for AI-powered code execution",
                    "fix": "Install Claude CLI: npm install -g @anthropic-ai/claude-code-cli",
                }
            )

        # Check for git repository
        if not diagnostic["git_status"].get("is_git_repo"):
            issues.append(
                {
                    "severity": "error",
                    "category": "environment",
                    "issue": "Not in a Git repository",
                    "description": "Sugar requires a Git repository to function",
                    "fix": "Run 'git init' or ensure you're in a Git repository",
                }
            )

        # Check for failed work items
        failed_count = (
            diagnostic["work_queue_status"].get("status_breakdown", {}).get("failed", 0)
        )
        if failed_count > 0:
            issues.append(
                {
                    "severity": "warning",
                    "category": "execution",
                    "issue": f"{failed_count} failed work items",
                    "description": "Some tasks have failed - check error messages in recent activity",
                    "fix": "Review failed items with 'sugar list --status failed' and check logs",
                }
            )

        diagnostic["potential_issues"] = issues

        return diagnostic

    # Generate diagnostic data
    import asyncio

    diagnostic_data = asyncio.run(generate_diagnostic())

    # Format output
    if format == "json":
        output_text = json.dumps(diagnostic_data, indent=2, default=str)
    elif format == "yaml":
        output_text = yaml.dump(
            diagnostic_data, default_flow_style=False, sort_keys=False
        )
    else:  # text
        output_text = f"""
Sugar Diagnostic Report
Generated: {diagnostic_data['timestamp']}

=== SYSTEM INFO ===
Sugar Version: {diagnostic_data['sugar_version']}
Platform: {diagnostic_data['system_info']['platform']}
Python: {diagnostic_data['system_info']['python_version']}

=== TOOL STATUS ===
"""
        for tool, status in diagnostic_data["tool_status"].items():
            output_text += f"{tool}: {'✓' if status['available'] else '✗'} {status.get('version', status.get('error', ''))}\n"

        output_text += f"""
=== WORK QUEUE ===
Total Items: {diagnostic_data['work_queue_status'].get('total_items', 'Error')}
Status Breakdown: {diagnostic_data['work_queue_status'].get('status_breakdown', {})}

=== POTENTIAL ISSUES ({len(diagnostic_data['potential_issues'])}) ===
"""
        for issue in diagnostic_data["potential_issues"]:
            output_text += f"[{issue['severity'].upper()}] {issue['issue']}\n"
            output_text += f"  Description: {issue['description']}\n"
            output_text += f"  Fix: {issue['fix']}\n\n"

    # Output to file or stdout
    if output:
        with open(output, "w") as f:
            f.write(output_text)
        click.echo(f"✅ Diagnostic information written to: {output}")
    else:
        click.echo(output_text)


@cli.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be removed without actually removing",
)
@click.pass_context
def dedupe(ctx, dry_run):
    """Remove duplicate work items based on source_file"""
    import aiosqlite
    from .storage.work_queue import WorkQueue
    import yaml

    async def _dedupe_work():
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        work_queue = WorkQueue(config["sugar"]["storage"]["database"])
        await work_queue.initialize()

        async with aiosqlite.connect(work_queue.db_path) as db:
            # Find duplicates - keep the earliest created one for each source_file
            cursor = await db.execute(
                """
                WITH ranked_items AS (
                    SELECT id, source_file, title, created_at,
                           ROW_NUMBER() OVER (PARTITION BY source_file ORDER BY created_at ASC) as rn
                    FROM work_items 
                    WHERE source_file != '' AND source_file IS NOT NULL
                )
                SELECT id, source_file, title, created_at
                FROM ranked_items 
                WHERE rn > 1
                ORDER BY source_file, created_at
            """
            )

            duplicates = await cursor.fetchall()

            if not duplicates:
                click.echo("✅ No duplicate work items found")
                return

            click.echo(f"Found {len(duplicates)} duplicate work items:")
            click.echo("=" * 60)

            for work_id, source_file, title, created_at in duplicates:
                click.echo(f"🗑️  {work_id[:8]}... - {title}")
                click.echo(f"    Source: {source_file}")
                click.echo(f"    Created: {created_at}")
                click.echo()

            if dry_run:
                click.echo("🔍 Dry run mode - no items were removed")
                return

            # Remove duplicates
            if click.confirm(f"Remove {len(duplicates)} duplicate work items?"):
                duplicate_ids = [row[0] for row in duplicates]

                for work_id in duplicate_ids:
                    await db.execute("DELETE FROM work_items WHERE id = ?", (work_id,))

                await db.commit()
                click.echo(f"✅ Removed {len(duplicates)} duplicate work items")
            else:
                click.echo("❌ Operation cancelled")

    try:
        asyncio.run(_dedupe_work())
    except Exception as e:
        click.echo(f"❌ Error deduplicating work items: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be removed without actually removing",
)
@click.pass_context
def cleanup(ctx, dry_run):
    """Remove bogus work items (Sugar initialization tests, venv files, etc.)"""
    import aiosqlite
    from .storage.work_queue import WorkQueue
    import yaml

    async def _cleanup_bogus_work():
        # Load configuration
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        # Connect to database
        db_path = config["sugar"]["storage"]["database"]
        async with aiosqlite.connect(db_path) as db:
            # Find bogus work items
            bogus_patterns = [
                "Sugar initialization test",
                "Sugar has been successfully initialized",
                "sugar_init_success.json",
                "init_test.json",
                "/venv/lib/",
                "/venv/site-packages/",
                "/.venv/lib/",
                "/node_modules/",
                "/__pycache__/",
            ]

            bogus_items = []
            for pattern in bogus_patterns:
                # Check title, description, and source_file
                query = """
                    SELECT id, title, source_file, created_at, status 
                    FROM work_items 
                    WHERE title LIKE ? 
                       OR description LIKE ?
                       OR source_file LIKE ?
                    ORDER BY created_at DESC
                """
                like_pattern = f"%{pattern}%"
                async with db.execute(
                    query, (like_pattern, like_pattern, like_pattern)
                ) as cursor:
                    rows = await cursor.fetchall()
                    bogus_items.extend(rows)

            # Remove duplicates (same ID)
            unique_bogus = {}
            for item in bogus_items:
                unique_bogus[item[0]] = item
            bogus_items = list(unique_bogus.values())

            if not bogus_items:
                click.echo("✅ No bogus work items found")
                return

            click.echo(f"Found {len(bogus_items)} potentially bogus work items:")
            click.echo("=" * 80)

            for work_id, title, source_file, created_at, status in bogus_items:
                status_icon = (
                    "⚡"
                    if status == "active"
                    else "✅" if status == "completed" else "⏳"
                )
                click.echo(f"{status_icon} {work_id[:8]}... - {title}")
                if source_file:
                    click.echo(f"    Source: {source_file}")
                click.echo(f"    Created: {created_at} | Status: {status}")
                click.echo()

            if dry_run:
                click.echo("🔍 Dry run mode - no items were removed")
                return

            # Remove bogus items
            if click.confirm(
                f"Remove {len(bogus_items)} potentially bogus work items?"
            ):
                bogus_ids = [row[0] for row in bogus_items]

                for work_id in bogus_ids:
                    await db.execute("DELETE FROM work_items WHERE id = ?", (work_id,))

                await db.commit()
                click.echo(f"✅ Removed {len(bogus_items)} bogus work items")

                # Also clean up the old init_test.json if it exists
                import pathlib

                project_path = pathlib.Path.cwd()
                old_test_file = project_path / "logs" / "errors" / "init_test.json"
                if old_test_file.exists():
                    old_test_file.unlink()
                    click.echo("🗑️  Removed old init_test.json file")

            else:
                click.echo("❌ Operation cancelled")

    try:
        asyncio.run(_cleanup_bogus_work())
    except Exception as e:
        click.echo(f"❌ Error cleaning up bogus work items: {e}", err=True)
        sys.exit(1)


# Task Type Management Commands
@cli.group()
@click.pass_context
def task_type(ctx):
    """Manage task types - add, edit, remove, and list custom task types"""
    pass


@task_type.command("list")
@click.option(
    "--format",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format",
)
@click.pass_context
def list_task_types(ctx, format):
    """List all task types"""
    import yaml
    from .storage.task_type_manager import TaskTypeManager

    async def _list_task_types():
        # Load configuration
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        # Initialize TaskTypeManager
        db_path = config["sugar"]["storage"]["database"]
        manager = TaskTypeManager(db_path)

        # Get all task types
        task_types = await manager.get_all_task_types()

        if format == "json":
            click.echo(json.dumps(task_types, indent=2))
        else:
            if not task_types:
                click.echo("No task types found.")
                return

            # Table format
            click.echo("Task Types:")
            click.echo("-" * 80)
            for task_type in task_types:
                default_marker = " (default)" if task_type["is_default"] else ""
                emoji = task_type.get("emoji", "")
                click.echo(f"{emoji} {task_type['id']}{default_marker}")
                click.echo(f"   Name: {task_type['name']}")
                if task_type.get("description"):
                    click.echo(f"   Description: {task_type['description']}")
                click.echo(f"   Agent: {task_type.get('agent', 'general-purpose')}")
                if task_type.get("commit_template"):
                    click.echo(f"   Commit: {task_type['commit_template']}")
                click.echo()

    try:
        asyncio.run(_list_task_types())
    except Exception as e:
        click.echo(f"❌ Error listing task types: {e}", err=True)
        sys.exit(1)


@task_type.command("add")
@click.argument("type_id")
@click.option("--name", help="Display name for the task type")
@click.option("--description", help="Description of the task type")
@click.option(
    "--agent", default="general-purpose", help="Claude agent to use for this type"
)
@click.option("--commit-template", help="Git commit template (e.g., 'feat: {title}')")
@click.option("--emoji", help="Emoji for the task type")
@click.pass_context
def add_task_type(ctx, type_id, name, description, agent, commit_template, emoji):
    """Add a new task type"""
    import yaml
    from .storage.task_type_manager import TaskTypeManager

    async def _add_task_type():
        # Load configuration
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        # Initialize TaskTypeManager
        db_path = config["sugar"]["storage"]["database"]
        manager = TaskTypeManager(db_path)

        # Use type_id as display_name if name not provided
        display_name = name if name else type_id.replace("_", " ").title()

        success = await manager.add_task_type(
            type_id, display_name, description, agent, commit_template, emoji
        )

        if success:
            emoji_display = f"{emoji} " if emoji else ""
            click.echo(f"✅ Added task type: {emoji_display}{type_id}")
        else:
            click.echo(
                f"❌ Failed to add task type '{type_id}' (may already exist)", err=True
            )
            sys.exit(1)

    try:
        asyncio.run(_add_task_type())
    except Exception as e:
        click.echo(f"❌ Error adding task type: {e}", err=True)
        sys.exit(1)


@task_type.command("edit")
@click.argument("type_id")
@click.option("--name", help="Display name for the task type")
@click.option("--description", help="Description of the task type")
@click.option("--agent", help="Claude agent to use for this type")
@click.option("--commit-template", help="Git commit template (e.g., 'feat: {title}')")
@click.option("--emoji", help="Emoji for the task type")
@click.pass_context
def edit_task_type(ctx, type_id, name, description, agent, commit_template, emoji):
    """Edit an existing task type"""
    import yaml
    from .storage.task_type_manager import TaskTypeManager

    async def _edit_task_type():
        # Load configuration
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        # Initialize TaskTypeManager
        db_path = config["sugar"]["storage"]["database"]
        manager = TaskTypeManager(db_path)

        success = await manager.update_task_type(
            type_id, name, description, agent, commit_template, emoji
        )

        if success:
            click.echo(f"✅ Updated task type: {type_id}")
        else:
            click.echo(
                f"❌ Failed to update task type '{type_id}' (not found?)", err=True
            )
            sys.exit(1)

    try:
        asyncio.run(_edit_task_type())
    except Exception as e:
        click.echo(f"❌ Error editing task type: {e}", err=True)
        sys.exit(1)


@task_type.command("remove")
@click.argument("type_id")
@click.option("--force", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def remove_task_type(ctx, type_id, force):
    """Remove a custom task type (cannot remove defaults)"""
    import yaml
    from .storage.task_type_manager import TaskTypeManager

    async def _remove_task_type():
        # Load configuration
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        # Initialize TaskTypeManager
        db_path = config["sugar"]["storage"]["database"]
        manager = TaskTypeManager(db_path)

        # Check if task type exists
        task_type = await manager.get_task_type(type_id)
        if not task_type:
            click.echo(f"❌ Task type '{type_id}' not found", err=True)
            sys.exit(1)

        if task_type["is_default"]:
            click.echo(f"❌ Cannot remove default task type '{type_id}'", err=True)
            sys.exit(1)

        # Confirmation prompt unless --force
        if not force:
            if not click.confirm(f"Remove task type '{type_id}'?"):
                click.echo("Operation cancelled")
                return

        success = await manager.remove_task_type(type_id)

        if success:
            click.echo(f"✅ Removed task type: {type_id}")
        else:
            click.echo(
                f"❌ Failed to remove task type '{type_id}' (active tasks?)", err=True
            )
            sys.exit(1)

    try:
        asyncio.run(_remove_task_type())
    except Exception as e:
        click.echo(f"❌ Error removing task type: {e}", err=True)
        sys.exit(1)


@task_type.command("show")
@click.argument("type_id")
@click.pass_context
def show_task_type(ctx, type_id):
    """Show details of a specific task type"""
    import yaml
    from .storage.task_type_manager import TaskTypeManager

    async def _show_task_type():
        # Load configuration
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        # Initialize TaskTypeManager
        db_path = config["sugar"]["storage"]["database"]
        manager = TaskTypeManager(db_path)

        task_type = await manager.get_task_type(type_id)

        if not task_type:
            click.echo(f"❌ Task type '{type_id}' not found", err=True)
            sys.exit(1)

        # Display details
        default_marker = " (default)" if task_type["is_default"] else ""
        emoji = task_type.get("emoji", "")

        click.echo(f"{emoji} {task_type['name']}{default_marker}")
        click.echo(f"ID: {task_type['id']}")
        if task_type.get("description"):
            click.echo(f"Description: {task_type['description']}")
        click.echo(f"Agent: {task_type.get('agent', 'general-purpose')}")
        if task_type.get("commit_template"):
            click.echo(f"Commit Template: {task_type['commit_template']}")
        if task_type.get("file_patterns"):
            click.echo(f"File Patterns: {', '.join(task_type['file_patterns'])}")
        click.echo(f"Created: {task_type['created_at']}")
        if task_type["updated_at"] != task_type["created_at"]:
            click.echo(f"Updated: {task_type['updated_at']}")

    try:
        asyncio.run(_show_task_type())
    except Exception as e:
        click.echo(f"❌ Error showing task type: {e}", err=True)
        sys.exit(1)


@task_type.command("export")
@click.option("--file", help="Export to file (default: stdout)")
@click.pass_context
def export_task_types(ctx, file):
    """Export custom task types to JSON for version control"""
    import yaml
    from .storage.task_type_manager import TaskTypeManager

    async def _export_task_types():
        # Load configuration
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        # Initialize TaskTypeManager
        db_path = config["sugar"]["storage"]["database"]
        manager = TaskTypeManager(db_path)

        task_types = await manager.export_task_types()

        export_data = {
            "task_types": task_types,
            "exported_at": datetime.now().isoformat(),
            "sugar_version": __version__,
        }

        output = json.dumps(export_data, indent=2)

        if file:
            with open(file, "w") as f:
                f.write(output)
            click.echo(f"✅ Exported {len(task_types)} custom task types to {file}")
        else:
            click.echo(output)

    try:
        asyncio.run(_export_task_types())
    except Exception as e:
        click.echo(f"❌ Error exporting task types: {e}", err=True)
        sys.exit(1)


@task_type.command("import")
@click.argument("file", type=click.File("r"))
@click.option("--overwrite", is_flag=True, help="Overwrite existing task types")
@click.pass_context
def import_task_types(ctx, file, overwrite):
    """Import task types from JSON file"""
    import yaml
    from .storage.task_type_manager import TaskTypeManager

    async def _import_task_types():
        # Load configuration
        config_file = ctx.obj["config"]
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        # Initialize TaskTypeManager
        db_path = config["sugar"]["storage"]["database"]
        manager = TaskTypeManager(db_path)

        # Parse import file
        try:
            import_data = json.load(file)
            task_types = import_data.get("task_types", [])
        except json.JSONDecodeError as e:
            click.echo(f"❌ Invalid JSON file: {e}", err=True)
            sys.exit(1)

        imported_count = await manager.import_task_types(task_types, overwrite)

        click.echo(f"✅ Imported {imported_count}/{len(task_types)} task types")

    try:
        asyncio.run(_import_task_types())
    except Exception as e:
        click.echo(f"❌ Error importing task types: {e}", err=True)
        sys.exit(1)


# =============================================================================
# Issue Responder Commands
# =============================================================================


@cli.group()
@click.pass_context
def issue(ctx):
    """Analyze and respond to GitHub issues with AI assistance"""
    pass


@issue.command("list")
@click.option("--state", type=click.Choice(["open", "closed", "all"]), default="open")
@click.option("--limit", default=10, help="Maximum number of issues to list")
@click.option("--repo", help="Repository (owner/repo). Defaults to current repo.")
@click.pass_context
def issue_list(ctx, state, limit, repo):
    """List GitHub issues"""
    from .integrations.github import GitHubClient

    try:
        client = GitHubClient(repo=repo)
        issues = client.list_issues(state=state, limit=limit)

        if not issues:
            click.echo(f"No {state} issues found.")
            return

        click.echo(f"\n{'#':<6} {'State':<8} {'Title':<60} {'Author':<15}")
        click.echo("-" * 95)

        for issue in issues:
            title = issue.title[:57] + "..." if len(issue.title) > 60 else issue.title
            click.echo(
                f"#{issue.number:<5} {issue.state:<8} {title:<60} @{issue.user.login:<15}"
            )

        click.echo(f"\n{len(issues)} issues listed")

    except Exception as e:
        click.echo(f"❌ Error listing issues: {e}", err=True)
        sys.exit(1)


@issue.command("view")
@click.argument("issue_number", type=int)
@click.option("--repo", help="Repository (owner/repo). Defaults to current repo.")
@click.pass_context
def issue_view(ctx, issue_number, repo):
    """View a specific GitHub issue"""
    from .integrations.github import GitHubClient

    try:
        client = GitHubClient(repo=repo)
        issue = client.get_issue(issue_number)

        click.echo(f"\n{'='*80}")
        click.echo(f"Issue #{issue.number}: {issue.title}")
        click.echo(f"{'='*80}")
        click.echo(f"State: {issue.state}")
        click.echo(f"Author: @{issue.user.login}")
        click.echo(f"Labels: {', '.join(l.name for l in issue.labels) or 'None'}")
        click.echo(f"Created: {issue.created_at}")
        click.echo(f"Comments: {issue.comments_count}")
        click.echo(f"URL: {issue.html_url}")
        click.echo(f"\n{'-'*80}")
        click.echo(f"\n{issue.body or '(No description provided)'}\n")

        if issue.comments:
            click.echo(f"{'-'*80}")
            click.echo(f"Comments ({len(issue.comments)}):\n")
            for comment in issue.comments:
                click.echo(f"  @{comment.user.login} ({comment.created_at}):")
                click.echo(
                    f"  {comment.body[:200]}{'...' if len(comment.body) > 200 else ''}\n"
                )

    except Exception as e:
        click.echo(f"❌ Error viewing issue: {e}", err=True)
        sys.exit(1)


@issue.command("analyze")
@click.argument("issue_number", type=int)
@click.option("--repo", help="Repository (owner/repo). Defaults to current repo.")
@click.option(
    "--format", "output_format", type=click.Choice(["text", "json"]), default="text"
)
@click.pass_context
def issue_analyze(ctx, issue_number, repo, output_format):
    """Analyze a GitHub issue and show insights (no AI call)"""
    from .integrations.github import GitHubClient
    from .profiles import IssueResponderProfile

    async def _analyze():
        client = GitHubClient(repo=repo)
        issue = client.get_issue(issue_number)

        profile = IssueResponderProfile()
        input_data = {"issue": issue.to_dict(), "repo": repo or ""}
        processed = await profile.process_input(input_data)

        analysis = processed.get("pre_analysis", {})

        if output_format == "json":
            click.echo(
                json.dumps(
                    {
                        "issue_number": issue_number,
                        "title": issue.title,
                        "analysis": analysis,
                        "has_maintainer_response": client.has_maintainer_response(
                            issue
                        ),
                    },
                    indent=2,
                )
            )
        else:
            click.echo(f"\n{'='*80}")
            click.echo(f"Analysis: Issue #{issue_number}")
            click.echo(f"{'='*80}")
            click.echo(f"\nTitle: {issue.title}")
            click.echo(f"\nDetected Type: {analysis.get('issue_type', 'unknown')}")
            click.echo(
                f"Key Topics: {', '.join(analysis.get('key_topics', [])) or 'None detected'}"
            )
            click.echo(
                f"Mentioned Files: {', '.join(analysis.get('mentioned_files', [])) or 'None detected'}"
            )
            click.echo(
                f"Error Patterns: {', '.join(analysis.get('mentioned_errors', [])) or 'None detected'}"
            )
            click.echo(
                f"Maintainer Response: {'Yes' if client.has_maintainer_response(issue) else 'No'}"
            )

            # Find similar issues
            similar = client.find_similar_issues(issue, limit=3)
            if similar:
                click.echo(f"\nSimilar Issues:")
                for s in similar:
                    click.echo(f"  #{s.number}: {s.title[:50]}... ({s.state})")

    try:
        asyncio.run(_analyze())
    except Exception as e:
        click.echo(f"❌ Error analyzing issue: {e}", err=True)
        sys.exit(1)


@issue.command("respond")
@click.argument("issue_number", type=int)
@click.option("--repo", help="Repository (owner/repo). Defaults to current repo.")
@click.option(
    "--post",
    is_flag=True,
    help="Actually post the response (requires confidence >= threshold)",
)
@click.option("--force-post", is_flag=True, help="Post regardless of confidence score")
@click.option(
    "--confidence-threshold",
    default=0.8,
    help="Minimum confidence to auto-post (0.0-1.0)",
)
@click.option(
    "--dry-run", is_flag=True, help="Show what would be posted without posting"
)
@click.option(
    "--max-length",
    default=2000,
    help="Maximum response length in characters (default: 2000)",
)
@click.pass_context
def issue_respond(
    ctx, issue_number, repo, post, force_post, confidence_threshold, dry_run, max_length
):
    """Generate an AI response to a GitHub issue

    By default, shows the generated response without posting.
    Use --post to actually post if confidence is high enough.
    Use --force-post to post regardless of confidence.
    """
    from .integrations.github import GitHubClient
    from .profiles import IssueResponderProfile
    from .agent import SugarAgent, SugarAgentConfig

    async def _respond():
        client = GitHubClient(repo=repo)
        issue = client.get_issue(issue_number)

        # Check if already responded
        if client.has_maintainer_response(issue) and not force_post:
            click.echo(
                f"⚠️  Issue #{issue_number} already has responses. Use --force-post to respond anyway."
            )
            if not post and not force_post:
                click.echo("Continuing with analysis...")

        click.echo(f"\n🔍 Analyzing issue #{issue_number}: {issue.title}")
        click.echo("-" * 60)

        # Process with IssueResponderProfile
        profile = IssueResponderProfile()
        profile.config.settings["max_response_length"] = max_length
        input_data = {"issue": issue.to_dict(), "repo": repo or ""}
        processed = await profile.process_input(input_data)

        click.echo(f"📋 Issue type: {processed['pre_analysis']['issue_type']}")
        click.echo(
            f"🏷️  Topics: {', '.join(processed['pre_analysis']['key_topics']) or 'None'}"
        )

        click.echo(f"\n🤖 Generating response with Claude...")

        # Run the agent
        agent_config = SugarAgentConfig(
            model="claude-sonnet-4-20250514",
            permission_mode="default",
            allowed_tools=["Read", "Glob", "Grep"],  # Read-only tools
            system_prompt_additions=profile.get_system_prompt({"repo": repo}),
        )

        agent = SugarAgent(agent_config)

        try:
            await agent.start_session()
            response = await agent.execute(processed["prompt"])

            # Process the output
            output_data = {"content": response.content}
            result = await profile.process_output(output_data)

            response_data = result.get("response", {})
            confidence = response_data.get("confidence", 0.0)
            content = response_data.get("content", "")
            suggested_labels = response_data.get("suggested_labels", [])
            should_auto_post = response_data.get("should_auto_post", False)

            click.echo(f"\n{'='*60}")
            click.echo(f"📊 Confidence Score: {confidence:.2f}")
            click.echo(f"🏷️  Suggested Labels: {', '.join(suggested_labels) or 'None'}")
            click.echo(f"✅ Auto-post eligible: {'Yes' if should_auto_post else 'No'}")
            click.echo(f"{'='*60}")
            click.echo(f"\n📝 Generated Response:\n")
            click.echo(content)
            click.echo(f"\n{'='*60}")

            # Decide whether to post
            will_post = False
            if force_post:
                will_post = True
                click.echo("⚠️  Force posting regardless of confidence...")
            elif post and confidence >= confidence_threshold:
                will_post = True
                click.echo(
                    f"✅ Confidence {confidence:.2f} >= {confidence_threshold}, posting..."
                )
            elif post and confidence < confidence_threshold:
                click.echo(
                    f"❌ Confidence {confidence:.2f} < {confidence_threshold}, not posting."
                )
                click.echo("   Use --force-post to override.")

            if will_post and not dry_run:
                client.post_comment(issue_number, content)
                click.echo(f"\n✅ Response posted to issue #{issue_number}")

                if suggested_labels:
                    try:
                        client.add_labels(issue_number, suggested_labels)
                        click.echo(f"✅ Labels added: {', '.join(suggested_labels)}")
                    except Exception as e:
                        click.echo(f"⚠️  Could not add labels: {e}")
            elif will_post and dry_run:
                click.echo(
                    f"\n🔍 DRY RUN: Would post response to issue #{issue_number}"
                )
                if suggested_labels:
                    click.echo(
                        f"🔍 DRY RUN: Would add labels: {', '.join(suggested_labels)}"
                    )
            elif not post and not force_post:
                click.echo(f"\n💡 To post this response, run:")
                click.echo(f"   sugar issue respond {issue_number} --post")

        finally:
            await agent.end_session()

    try:
        asyncio.run(_respond())
    except Exception as e:
        click.echo(f"❌ Error generating response: {e}", err=True)
        sys.exit(1)


@issue.command("search")
@click.argument("query")
@click.option("--repo", help="Repository (owner/repo). Defaults to current repo.")
@click.option("--limit", default=10, help="Maximum results")
@click.pass_context
def issue_search(ctx, query, repo, limit):
    """Search for issues matching a query"""
    from .integrations.github import GitHubClient

    try:
        client = GitHubClient(repo=repo)
        issues = client.search_issues(query, limit=limit)

        if not issues:
            click.echo(f"No issues found matching: {query}")
            return

        click.echo(f"\nSearch results for: {query}\n")
        click.echo(f"{'#':<6} {'State':<8} {'Title':<60}")
        click.echo("-" * 80)

        for issue in issues:
            title = issue.title[:57] + "..." if len(issue.title) > 60 else issue.title
            click.echo(f"#{issue.number:<5} {issue.state:<8} {title:<60}")

        click.echo(f"\n{len(issues)} issues found")

    except Exception as e:
        click.echo(f"❌ Error searching issues: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
