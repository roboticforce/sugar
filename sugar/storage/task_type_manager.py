"""Task Type Management System

Provides database operations for managing configurable task types.
Integrates with the existing WorkQueue storage system.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)


class TaskTypeManager:
    """Manages task types in the database"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._initialized = False

    async def initialize(self):
        """Initialize the task_types table if it doesn't exist"""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            # Check if task_types table exists
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='task_types'"
            )
            table_exists = await cursor.fetchone()

            if not table_exists:
                # Create task_types table
                await db.execute(
                    """
                    CREATE TABLE task_types (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        agent TEXT DEFAULT 'general-purpose',
                        commit_template TEXT,
                        emoji TEXT,
                        file_patterns TEXT DEFAULT '[]',
                        default_acceptance_criteria TEXT DEFAULT '[]',
                        model_tier TEXT DEFAULT 'standard',
                        complexity_level INTEGER DEFAULT 3,
                        allowed_tools TEXT DEFAULT NULL,
                        disallowed_tools TEXT DEFAULT NULL,
                        bash_permissions TEXT DEFAULT '[]',
                        pre_hooks TEXT DEFAULT '[]',
                        post_hooks TEXT DEFAULT '[]',
                        is_default INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )

                # Populate with default types
                default_types = self._get_default_task_types()
                for task_type in default_types:
                    allowed_tools = task_type.get("allowed_tools")
                    disallowed_tools = task_type.get("disallowed_tools")
                    pre_hooks = task_type.get("pre_hooks", [])
                    post_hooks = task_type.get("post_hooks", [])

                    await db.execute(
                        """
                        INSERT INTO task_types
                        (id, name, description, agent, commit_template, emoji, file_patterns,
                         model_tier, complexity_level, allowed_tools, disallowed_tools,
                         bash_permissions, pre_hooks, post_hooks, is_default)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_type["id"],
                            task_type["name"],
                            task_type["description"],
                            task_type["agent"],
                            task_type["commit_template"],
                            task_type["emoji"],
                            json.dumps(task_type.get("file_patterns", [])),
                            task_type.get("model_tier", "standard"),
                            task_type.get("complexity_level", 3),
                            json.dumps(allowed_tools) if allowed_tools else None,
                            json.dumps(disallowed_tools) if disallowed_tools else None,
                            json.dumps(task_type.get("bash_permissions", [])),
                            json.dumps(pre_hooks),
                            json.dumps(post_hooks),
                            1,
                        ),
                    )

                await db.commit()
                logger.info("Created task_types table and populated with default types")
            else:
                # Migrate existing table to add default_acceptance_criteria column
                await self._migrate_acceptance_criteria_column(db)
                # Migrate to add model_tier and complexity_level columns (AUTO-001)
                await self._migrate_model_routing_columns(db)
                # Migrate to add tool restriction columns
                await self._migrate_tool_restriction_columns(db)
                # Migrate to add bash_permissions column
                await self._migrate_bash_permissions_column(db)
                # Migrate to add hooks columns
                await self._migrate_hooks_columns(db)

        self._initialized = True

    async def _migrate_acceptance_criteria_column(self, db):
        """Add default_acceptance_criteria column to existing task_types table"""
        try:
            cursor = await db.execute("PRAGMA table_info(task_types)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            if "default_acceptance_criteria" not in column_names:
                await db.execute(
                    "ALTER TABLE task_types ADD COLUMN default_acceptance_criteria TEXT DEFAULT '[]'"
                )
                await db.commit()
                logger.info(
                    "Added default_acceptance_criteria column to task_types table"
                )
        except Exception as e:
            logger.warning(f"Migration warning for default_acceptance_criteria: {e}")

    async def _migrate_model_routing_columns(self, db):
        """Add model_tier and complexity_level columns for AUTO-001 model routing"""
        try:
            cursor = await db.execute("PRAGMA table_info(task_types)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            # Add model_tier column (simple, standard, complex)
            if "model_tier" not in column_names:
                await db.execute(
                    "ALTER TABLE task_types ADD COLUMN model_tier TEXT DEFAULT 'standard'"
                )
                await db.commit()
                logger.info("Added model_tier column to task_types table")

            # Add complexity_level column (1-5)
            if "complexity_level" not in column_names:
                await db.execute(
                    "ALTER TABLE task_types ADD COLUMN complexity_level INTEGER DEFAULT 3"
                )
                await db.commit()
                logger.info("Added complexity_level column to task_types table")

            # Update default task types with appropriate tiers
            await self._set_default_model_tiers(db)

        except Exception as e:
            logger.warning(f"Migration warning for model_routing columns: {e}")

    async def _set_default_model_tiers(self, db):
        """Set default model tiers for built-in task types"""
        default_tiers = {
            # Simple tier - low complexity tasks
            "docs": ("simple", 1),
            "style": ("simple", 1),
            "chore": ("simple", 2),
            # Standard tier - moderate complexity tasks
            "test": ("standard", 2),
            "bug_fix": ("standard", 3),
            "ci": ("standard", 2),
            # Complex tier - high complexity tasks
            "feature": ("complex", 3),
            "refactor": ("complex", 4),
            "perf": ("complex", 4),
            "security": ("complex", 4),
        }

        for type_id, (tier, level) in default_tiers.items():
            try:
                await db.execute(
                    "UPDATE task_types SET model_tier = ?, complexity_level = ? WHERE id = ?",
                    (tier, level, type_id),
                )
            except Exception as e:
                logger.debug(f"Could not update tier for {type_id}: {e}")

        await db.commit()

    async def _migrate_tool_restriction_columns(self, db):
        """Add allowed_tools and disallowed_tools columns for tool restrictions"""
        try:
            cursor = await db.execute("PRAGMA table_info(task_types)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            # Add allowed_tools column (JSON array)
            if "allowed_tools" not in column_names:
                await db.execute(
                    "ALTER TABLE task_types ADD COLUMN allowed_tools TEXT DEFAULT NULL"
                )
                await db.commit()
                logger.info("Added allowed_tools column to task_types table")

            # Add disallowed_tools column (JSON array)
            if "disallowed_tools" not in column_names:
                await db.execute(
                    "ALTER TABLE task_types ADD COLUMN disallowed_tools TEXT DEFAULT NULL"
                )
                await db.commit()
                logger.info("Added disallowed_tools column to task_types table")

            # Set default tool restrictions for existing task types
            await self._set_default_tool_restrictions(db)

        except Exception as e:
            logger.warning(f"Migration warning for tool_restriction columns: {e}")

    async def _set_default_tool_restrictions(self, db):
        """Set default tool restrictions for built-in task types"""
        # Simple tasks (docs, style, chore) - restricted toolset
        simple_restrictions = {
            "allowed_tools": ["Read", "Write", "Edit", "Glob", "Grep"],
            "disallowed_tools": None,
        }

        # Standard tasks - limited web access
        standard_restrictions = {
            "allowed_tools": None,  # All tools
            "disallowed_tools": ["WebSearch"],
        }

        # Complex tasks - full access
        complex_restrictions = {
            "allowed_tools": None,
            "disallowed_tools": None,
        }

        restriction_map = {
            # Simple tier - basic file operations only
            "docs": simple_restrictions,
            "style": simple_restrictions,
            "chore": simple_restrictions,
            # Standard tier - most tools except web search
            "test": standard_restrictions,
            "bug_fix": standard_restrictions,
            "ci": standard_restrictions,
            # Complex tier - full tool access
            "feature": complex_restrictions,
            "refactor": complex_restrictions,
            "perf": complex_restrictions,
            "security": complex_restrictions,
        }

        for type_id, restrictions in restriction_map.items():
            try:
                allowed_json = (
                    json.dumps(restrictions["allowed_tools"])
                    if restrictions["allowed_tools"]
                    else None
                )
                disallowed_json = (
                    json.dumps(restrictions["disallowed_tools"])
                    if restrictions["disallowed_tools"]
                    else None
                )

                await db.execute(
                    "UPDATE task_types SET allowed_tools = ?, disallowed_tools = ? WHERE id = ?",
                    (allowed_json, disallowed_json, type_id),
                )
            except Exception as e:
                logger.debug(f"Could not update tool restrictions for {type_id}: {e}")

        await db.commit()

    async def _migrate_bash_permissions_column(self, db):
        """Add bash_permissions column for wildcard Bash command permissions"""
        try:
            cursor = await db.execute("PRAGMA table_info(task_types)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            if "bash_permissions" not in column_names:
                await db.execute(
                    "ALTER TABLE task_types ADD COLUMN bash_permissions TEXT DEFAULT '[]'"
                )
                await db.commit()
                logger.info("Added bash_permissions column to task_types table")

            # Set default bash permissions based on tier
            await self._set_default_bash_permissions(db)

        except Exception as e:
            logger.warning(f"Migration warning for bash_permissions column: {e}")

    async def _set_default_bash_permissions(self, db):
        """Set default bash permissions for built-in task types based on tier"""
        # Default bash permissions by tier
        bash_permissions_by_tier = {
            "simple": [
                "cat *",
                "ls *",
                "head *",
                "tail *",
                "grep *",
                "find *",
            ],
            "standard": [
                "pytest *",
                "python *",
                "python3 *",
                "pip *",
                "npm *",
                "git status*",
                "git diff*",
                "git log*",
                "cat *",
                "ls *",
                "head *",
                "tail *",
                "grep *",
                "find *",
            ],
            "complex": [
                # No restrictions for complex tier - empty list means all allowed
            ],
        }

        # Get all task types
        cursor = await db.execute("SELECT id, model_tier FROM task_types")
        task_types = await cursor.fetchall()

        for type_id, tier in task_types:
            permissions = bash_permissions_by_tier.get(tier, [])
            try:
                await db.execute(
                    "UPDATE task_types SET bash_permissions = ? WHERE id = ?",
                    (json.dumps(permissions), type_id),
                )
            except Exception as e:
                logger.debug(f"Could not update bash permissions for {type_id}: {e}")

        await db.commit()

    async def _migrate_hooks_columns(self, db):
        """Add pre_hooks and post_hooks columns for task execution hooks"""
        try:
            cursor = await db.execute("PRAGMA table_info(task_types)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            # Add pre_hooks column (JSON array of shell commands)
            if "pre_hooks" not in column_names:
                await db.execute(
                    "ALTER TABLE task_types ADD COLUMN pre_hooks TEXT DEFAULT '[]'"
                )
                await db.commit()
                logger.info("Added pre_hooks column to task_types table")

            # Add post_hooks column (JSON array of shell commands)
            if "post_hooks" not in column_names:
                await db.execute(
                    "ALTER TABLE task_types ADD COLUMN post_hooks TEXT DEFAULT '[]'"
                )
                await db.commit()
                logger.info("Added post_hooks column to task_types table")

            # Set default hooks for built-in task types
            await self._set_default_hooks(db)

        except Exception as e:
            logger.warning(f"Migration warning for hooks columns: {e}")

    async def _set_default_hooks(self, db):
        """Set default hooks for built-in task types"""
        default_hooks = {
            # Bug fixes should run tests after completion
            "bug_fix": {"pre_hooks": [], "post_hooks": ["pytest tests/ -x --tb=short"]},
            # Features should run tests and check formatting
            "feature": {
                "pre_hooks": [],
                "post_hooks": ["pytest tests/ -x --tb=short", "black --check ."],
            },
            # Tests should run the test suite
            "test": {"pre_hooks": [], "post_hooks": ["pytest tests/ -v"]},
            # Style tasks should check formatting
            "style": {"pre_hooks": [], "post_hooks": ["black --check ."]},
            # Refactoring should run tests
            "refactor": {
                "pre_hooks": [],
                "post_hooks": ["pytest tests/ -x --tb=short"],
            },
        }

        for type_id, hooks in default_hooks.items():
            try:
                await db.execute(
                    "UPDATE task_types SET pre_hooks = ?, post_hooks = ? WHERE id = ?",
                    (
                        json.dumps(hooks.get("pre_hooks", [])),
                        json.dumps(hooks.get("post_hooks", [])),
                        type_id,
                    ),
                )
            except Exception as e:
                logger.debug(f"Could not update hooks for {type_id}: {e}")

        await db.commit()

    def _get_default_task_types(self) -> List[Dict]:
        """Get the default task types"""
        # Import default criteria templates
        from ..quality_gates.criteria_templates import CriteriaTemplates

        # Default bash permissions by tier
        bash_permissions_by_tier = {
            "simple": [
                "cat *",
                "ls *",
                "head *",
                "tail *",
                "grep *",
                "find *",
            ],
            "standard": [
                "pytest *",
                "python *",
                "python3 *",
                "pip *",
                "npm *",
                "git status*",
                "git diff*",
                "git log*",
                "cat *",
                "ls *",
                "head *",
                "tail *",
                "grep *",
                "find *",
            ],
            "complex": [],  # Empty = all allowed
        }

        return [
            {
                "id": "feature",
                "name": "Feature",
                "description": "New feature implementation",
                "agent": "general-purpose",
                "commit_template": "feat: {title}",
                "emoji": "✨",
                "file_patterns": [],
                "default_acceptance_criteria": CriteriaTemplates.FEATURE,
                "model_tier": "complex",
                "complexity_level": 3,
                "allowed_tools": None,  # All tools
                "disallowed_tools": None,
                "bash_permissions": bash_permissions_by_tier["complex"],
                "pre_hooks": [],
                "post_hooks": ["pytest tests/ -x --tb=short", "black --check ."],
            },
            {
                "id": "bug_fix",
                "name": "Bug Fix",
                "description": "Bug fix or error correction",
                "agent": "general-purpose",
                "commit_template": "fix: {title}",
                "emoji": "🐛",
                "file_patterns": [],
                "default_acceptance_criteria": CriteriaTemplates.BUG_FIX,
                "model_tier": "standard",
                "complexity_level": 3,
                "allowed_tools": None,
                "disallowed_tools": ["WebSearch"],
                "bash_permissions": bash_permissions_by_tier["standard"],
                "pre_hooks": [],
                "post_hooks": ["pytest tests/ -x --tb=short"],
            },
            {
                "id": "refactor",
                "name": "Refactor",
                "description": "Code refactoring",
                "agent": "general-purpose",
                "commit_template": "refactor: {title}",
                "emoji": "♻️",
                "file_patterns": [],
                "default_acceptance_criteria": CriteriaTemplates.REFACTOR,
                "model_tier": "complex",
                "complexity_level": 4,
                "allowed_tools": None,
                "disallowed_tools": None,
                "bash_permissions": bash_permissions_by_tier["complex"],
                "pre_hooks": [],
                "post_hooks": ["pytest tests/ -x --tb=short"],
            },
            {
                "id": "docs",
                "name": "Documentation",
                "description": "Documentation updates",
                "agent": "general-purpose",
                "commit_template": "docs: {title}",
                "emoji": "📝",
                "file_patterns": ["*.md", "docs/**"],
                "default_acceptance_criteria": CriteriaTemplates.DOCUMENTATION,
                "model_tier": "simple",
                "complexity_level": 1,
                "allowed_tools": ["Read", "Write", "Edit", "Glob", "Grep"],
                "disallowed_tools": None,
                "bash_permissions": bash_permissions_by_tier["simple"],
            },
            {
                "id": "test",
                "name": "Test",
                "description": "Test creation or updates",
                "agent": "general-purpose",
                "commit_template": "test: {title}",
                "emoji": "🧪",
                "file_patterns": ["test_*.py", "*_test.py", "tests/**"],
                "default_acceptance_criteria": CriteriaTemplates.TEST,
                "model_tier": "standard",
                "complexity_level": 2,
                "allowed_tools": None,
                "disallowed_tools": ["WebSearch"],
                "bash_permissions": bash_permissions_by_tier["standard"],
                "pre_hooks": [],
                "post_hooks": ["pytest tests/ -v"],
            },
            {
                "id": "chore",
                "name": "Chore",
                "description": "Maintenance and chores",
                "agent": "general-purpose",
                "commit_template": "chore: {title}",
                "emoji": "🔧",
                "file_patterns": [],
                "default_acceptance_criteria": CriteriaTemplates.CHORE,
                "model_tier": "simple",
                "complexity_level": 2,
                "allowed_tools": ["Read", "Write", "Edit", "Glob", "Grep"],
                "disallowed_tools": None,
                "bash_permissions": bash_permissions_by_tier["simple"],
            },
            {
                "id": "style",
                "name": "Style",
                "description": "Code style and formatting",
                "agent": "general-purpose",
                "commit_template": "style: {title}",
                "emoji": "💄",
                "file_patterns": [],
                "default_acceptance_criteria": CriteriaTemplates.STYLE,
                "model_tier": "simple",
                "complexity_level": 1,
                "allowed_tools": ["Read", "Write", "Edit", "Glob", "Grep"],
                "disallowed_tools": None,
                "bash_permissions": bash_permissions_by_tier["simple"],
                "pre_hooks": [],
                "post_hooks": ["black --check ."],
            },
            {
                "id": "perf",
                "name": "Performance",
                "description": "Performance improvements",
                "agent": "general-purpose",
                "commit_template": "perf: {title}",
                "emoji": "⚡",
                "file_patterns": [],
                "default_acceptance_criteria": CriteriaTemplates.PERFORMANCE,
                "model_tier": "complex",
                "complexity_level": 4,
                "allowed_tools": None,
                "disallowed_tools": None,
                "bash_permissions": bash_permissions_by_tier["complex"],
            },
            {
                "id": "ci",
                "name": "CI/CD",
                "description": "CI/CD configuration",
                "agent": "general-purpose",
                "commit_template": "ci: {title}",
                "emoji": "👷",
                "file_patterns": [".github/**", "Dockerfile", "docker-compose*.yml"],
                "default_acceptance_criteria": CriteriaTemplates.CI_CD,
                "model_tier": "standard",
                "complexity_level": 2,
                "allowed_tools": None,
                "disallowed_tools": ["WebSearch"],
                "bash_permissions": bash_permissions_by_tier["standard"],
            },
            {
                "id": "security",
                "name": "Security",
                "description": "Security fixes and improvements",
                "agent": "general-purpose",
                "commit_template": "security: {title}",
                "emoji": "🔒",
                "file_patterns": [],
                "default_acceptance_criteria": CriteriaTemplates.SECURITY,
                "model_tier": "complex",
                "complexity_level": 4,
                "allowed_tools": None,
                "disallowed_tools": None,
                "bash_permissions": bash_permissions_by_tier["complex"],
            },
        ]

    async def get_all_task_types(self) -> List[Dict]:
        """Get all task types from the database"""
        try:
            return await self._get_all_task_types_internal()
        except aiosqlite.OperationalError as e:
            if "no such table: task_types" in str(e):
                await self.initialize()
                return await self._get_all_task_types_internal()
            raise

    async def _get_all_task_types_internal(self) -> List[Dict]:
        """Internal method to get all task types"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM task_types ORDER BY is_default DESC, name ASC"
            )
            rows = await cursor.fetchall()

            result = []
            for row in rows:
                task_type = dict(row)
                # Parse JSON file_patterns
                if task_type.get("file_patterns"):
                    try:
                        task_type["file_patterns"] = json.loads(
                            task_type["file_patterns"]
                        )
                    except json.JSONDecodeError:
                        task_type["file_patterns"] = []
                else:
                    task_type["file_patterns"] = []
                # Parse JSON default_acceptance_criteria
                if task_type.get("default_acceptance_criteria"):
                    try:
                        task_type["default_acceptance_criteria"] = json.loads(
                            task_type["default_acceptance_criteria"]
                        )
                    except json.JSONDecodeError:
                        task_type["default_acceptance_criteria"] = []
                else:
                    task_type["default_acceptance_criteria"] = []
                # Parse JSON bash_permissions
                if task_type.get("bash_permissions"):
                    try:
                        task_type["bash_permissions"] = json.loads(
                            task_type["bash_permissions"]
                        )
                    except json.JSONDecodeError:
                        task_type["bash_permissions"] = []
                else:
                    task_type["bash_permissions"] = []
                # Parse JSON allowed_tools
                if task_type.get("allowed_tools"):
                    try:
                        task_type["allowed_tools"] = json.loads(
                            task_type["allowed_tools"]
                        )
                    except json.JSONDecodeError:
                        task_type["allowed_tools"] = None
                # Parse JSON disallowed_tools
                if task_type.get("disallowed_tools"):
                    try:
                        task_type["disallowed_tools"] = json.loads(
                            task_type["disallowed_tools"]
                        )
                    except json.JSONDecodeError:
                        task_type["disallowed_tools"] = None
                # Parse JSON pre_hooks
                if task_type.get("pre_hooks"):
                    try:
                        task_type["pre_hooks"] = json.loads(task_type["pre_hooks"])
                    except json.JSONDecodeError:
                        task_type["pre_hooks"] = []
                else:
                    task_type["pre_hooks"] = []
                # Parse JSON post_hooks
                if task_type.get("post_hooks"):
                    try:
                        task_type["post_hooks"] = json.loads(task_type["post_hooks"])
                    except json.JSONDecodeError:
                        task_type["post_hooks"] = []
                else:
                    task_type["post_hooks"] = []
                result.append(task_type)

            return result

    async def get_task_type(self, type_id: str) -> Optional[Dict]:
        """Get a specific task type by ID"""
        try:
            return await self._get_task_type_internal(type_id)
        except aiosqlite.OperationalError as e:
            if "no such table: task_types" in str(e):
                await self.initialize()
                return await self._get_task_type_internal(type_id)
            raise

    async def _get_task_type_internal(self, type_id: str) -> Optional[Dict]:
        """Internal method to get a specific task type"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM task_types WHERE id = ?", (type_id,)
            )
            row = await cursor.fetchone()

            if row:
                task_type = dict(row)
                # Parse JSON file_patterns
                if task_type.get("file_patterns"):
                    try:
                        task_type["file_patterns"] = json.loads(
                            task_type["file_patterns"]
                        )
                    except json.JSONDecodeError:
                        task_type["file_patterns"] = []
                else:
                    task_type["file_patterns"] = []
                # Parse JSON default_acceptance_criteria
                if task_type.get("default_acceptance_criteria"):
                    try:
                        task_type["default_acceptance_criteria"] = json.loads(
                            task_type["default_acceptance_criteria"]
                        )
                    except json.JSONDecodeError:
                        task_type["default_acceptance_criteria"] = []
                else:
                    task_type["default_acceptance_criteria"] = []
                # Parse JSON bash_permissions
                if task_type.get("bash_permissions"):
                    try:
                        task_type["bash_permissions"] = json.loads(
                            task_type["bash_permissions"]
                        )
                    except json.JSONDecodeError:
                        task_type["bash_permissions"] = []
                else:
                    task_type["bash_permissions"] = []
                # Parse JSON allowed_tools
                if task_type.get("allowed_tools"):
                    try:
                        task_type["allowed_tools"] = json.loads(
                            task_type["allowed_tools"]
                        )
                    except json.JSONDecodeError:
                        task_type["allowed_tools"] = None
                # Parse JSON disallowed_tools
                if task_type.get("disallowed_tools"):
                    try:
                        task_type["disallowed_tools"] = json.loads(
                            task_type["disallowed_tools"]
                        )
                    except json.JSONDecodeError:
                        task_type["disallowed_tools"] = None
                        # Parse JSON pre_hooks
                if task_type.get("pre_hooks"):
                    try:
                        task_type["pre_hooks"] = json.loads(task_type["pre_hooks"])
                    except json.JSONDecodeError:
                        task_type["pre_hooks"] = []
                else:
                    task_type["pre_hooks"] = []
                # Parse JSON post_hooks
                if task_type.get("post_hooks"):
                    try:
                        task_type["post_hooks"] = json.loads(task_type["post_hooks"])
                    except json.JSONDecodeError:
                        task_type["post_hooks"] = []
                else:
                    task_type["post_hooks"] = []
                return task_type

            return None

    async def get_task_type_ids(self) -> List[str]:
        """Get all task type IDs for CLI validation"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("SELECT id FROM task_types ORDER BY name ASC")
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
        except aiosqlite.OperationalError as e:
            if "no such table: task_types" in str(e):
                await self.initialize()
                async with aiosqlite.connect(self.db_path) as db:
                    cursor = await db.execute(
                        "SELECT id FROM task_types ORDER BY name ASC"
                    )
                    rows = await cursor.fetchall()
                    return [row[0] for row in rows]
            raise

    async def add_task_type(
        self,
        type_id: str,
        name: str,
        description: str = None,
        agent: str = "general-purpose",
        commit_template: str = None,
        emoji: str = None,
        file_patterns: List[str] = None,
        default_acceptance_criteria: List[Dict] = None,
    ) -> bool:
        """Add a new task type"""
        await self.initialize()
        if not commit_template:
            commit_template = f"{type_id}: {{title}}"

        if file_patterns is None:
            file_patterns = []

        if default_acceptance_criteria is None:
            default_acceptance_criteria = []

        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT INTO task_types
                    (id, name, description, agent, commit_template, emoji, file_patterns,
                     default_acceptance_criteria, is_default)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                    (
                        type_id,
                        name,
                        description,
                        agent,
                        commit_template,
                        emoji,
                        json.dumps(file_patterns),
                        json.dumps(default_acceptance_criteria),
                    ),
                )
                await db.commit()
                logger.info(f"Added new task type: {type_id}")
                return True
        except aiosqlite.IntegrityError:
            logger.error(f"Task type '{type_id}' already exists")
            return False
        except Exception as e:
            logger.error(f"Error adding task type '{type_id}': {e}")
            return False

    async def update_task_type(
        self,
        type_id: str,
        name: str = None,
        description: str = None,
        agent: str = None,
        commit_template: str = None,
        emoji: str = None,
        file_patterns: List[str] = None,
        default_acceptance_criteria: List[Dict] = None,
        model_tier: str = None,
        complexity_level: int = None,
        allowed_tools: List[str] = None,
        disallowed_tools: List[str] = None,
        bash_permissions: List[str] = None,
    ) -> bool:
        """Update an existing task type"""
        await self.initialize()
        # First check if task type exists
        existing = await self.get_task_type(type_id)
        if not existing:
            logger.error(f"Task type '{type_id}' not found")
            return False

        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if agent is not None:
            updates.append("agent = ?")
            params.append(agent)
        if commit_template is not None:
            updates.append("commit_template = ?")
            params.append(commit_template)
        if emoji is not None:
            updates.append("emoji = ?")
            params.append(emoji)
        if file_patterns is not None:
            updates.append("file_patterns = ?")
            params.append(json.dumps(file_patterns))
        if default_acceptance_criteria is not None:
            updates.append("default_acceptance_criteria = ?")
            params.append(json.dumps(default_acceptance_criteria))
        if model_tier is not None:
            updates.append("model_tier = ?")
            params.append(model_tier)
        if complexity_level is not None:
            updates.append("complexity_level = ?")
            params.append(complexity_level)
        if allowed_tools is not None:
            updates.append("allowed_tools = ?")
            params.append(json.dumps(allowed_tools) if allowed_tools else None)
        if disallowed_tools is not None:
            updates.append("disallowed_tools = ?")
            params.append(json.dumps(disallowed_tools) if disallowed_tools else None)
        if bash_permissions is not None:
            updates.append("bash_permissions = ?")
            params.append(json.dumps(bash_permissions))

        if not updates:
            logger.warning(f"No updates provided for task type '{type_id}'")
            return False

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(type_id)

        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    f"UPDATE task_types SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
                await db.commit()
                logger.info(f"Updated task type: {type_id}")
                return True
        except Exception as e:
            logger.error(f"Error updating task type '{type_id}': {e}")
            return False

    async def remove_task_type(self, type_id: str) -> bool:
        """Remove a task type (if not default and no active tasks)"""
        await self.initialize()
        # Check if task type exists and is not default
        existing = await self.get_task_type(type_id)
        if not existing:
            logger.error(f"Task type '{type_id}' not found")
            return False

        if existing["is_default"]:
            logger.error(f"Cannot delete default task type '{type_id}'")
            return False

        # Check if there are active tasks with this type
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM work_items WHERE type = ? AND status NOT IN ('completed', 'failed')",
                (type_id,),
            )
            active_count = (await cursor.fetchone())[0]

            if active_count > 0:
                logger.error(
                    f"Cannot delete task type '{type_id}': {active_count} active tasks exist"
                )
                return False

            try:
                await db.execute("DELETE FROM task_types WHERE id = ?", (type_id,))
                await db.commit()
                logger.info(f"Removed task type: {type_id}")
                return True
            except Exception as e:
                logger.error(f"Error removing task type '{type_id}': {e}")
                return False

    async def export_task_types(self) -> List[Dict]:
        """Export all non-default task types for version control"""
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM task_types WHERE is_default = 0 ORDER BY name ASC"
            )
            rows = await cursor.fetchall()

            result = []
            for row in rows:
                task_type = dict(row)
                # Remove database-specific fields
                task_type.pop("created_at", None)
                task_type.pop("updated_at", None)
                task_type.pop("is_default", None)

                # Parse JSON file_patterns
                if task_type.get("file_patterns"):
                    try:
                        task_type["file_patterns"] = json.loads(
                            task_type["file_patterns"]
                        )
                    except json.JSONDecodeError:
                        task_type["file_patterns"] = []
                else:
                    task_type["file_patterns"] = []

                result.append(task_type)

            return result

    async def import_task_types(
        self, task_types: List[Dict], overwrite: bool = False
    ) -> int:
        """Import task types from external source"""
        await self.initialize()
        imported_count = 0

        for task_type in task_types:
            type_id = task_type.get("id")
            if not type_id:
                logger.warning("Skipping task type without ID")
                continue

            # Check if already exists
            existing = await self.get_task_type(type_id)
            if existing and not overwrite:
                logger.warning(f"Task type '{type_id}' already exists, skipping")
                continue

            if existing and overwrite:
                # Update existing
                success = await self.update_task_type(
                    type_id,
                    name=task_type.get("name"),
                    description=task_type.get("description"),
                    agent=task_type.get("agent"),
                    commit_template=task_type.get("commit_template"),
                    emoji=task_type.get("emoji"),
                    file_patterns=task_type.get("file_patterns", []),
                )
            else:
                # Add new
                success = await self.add_task_type(
                    type_id,
                    name=task_type.get("name", type_id.title()),
                    description=task_type.get("description"),
                    agent=task_type.get("agent", "general-purpose"),
                    commit_template=task_type.get("commit_template"),
                    emoji=task_type.get("emoji"),
                    file_patterns=task_type.get("file_patterns", []),
                )

            if success:
                imported_count += 1

        return imported_count

    async def validate_task_type_id(self, type_id: str) -> bool:
        """Validate that a task type ID exists"""
        await self.initialize()
        existing = await self.get_task_type(type_id)
        return existing is not None

    async def get_agent_for_type(self, type_id: str) -> str:
        """Get the agent configured for a task type"""
        await self.initialize()
        task_type = await self.get_task_type(type_id)
        return (
            task_type.get("agent", "general-purpose")
            if task_type
            else "general-purpose"
        )

    async def get_commit_template_for_type(self, type_id: str) -> str:
        """Get the commit template for a task type"""
        await self.initialize()
        task_type = await self.get_task_type(type_id)
        return (
            task_type.get("commit_template", f"{type_id}: {{title}}")
            if task_type
            else f"{type_id}: {{title}}"
        )

    async def get_file_patterns_for_type(self, type_id: str) -> List[str]:
        """Get the file patterns for a task type"""
        await self.initialize()
        task_type = await self.get_task_type(type_id)
        return task_type.get("file_patterns", []) if task_type else []

    async def get_default_acceptance_criteria_for_type(
        self, type_id: str
    ) -> List[Dict]:
        """Get the default acceptance criteria for a task type"""
        await self.initialize()
        task_type = await self.get_task_type(type_id)
        return task_type.get("default_acceptance_criteria", []) if task_type else []

    async def set_default_acceptance_criteria_for_type(
        self, type_id: str, criteria: List[Dict]
    ) -> bool:
        """Set the default acceptance criteria for a task type"""
        return await self.update_task_type(
            type_id, default_acceptance_criteria=criteria
        )

    async def get_model_tier_for_type(self, type_id: str) -> str:
        """Get the model tier for a task type (simple, standard, complex)"""
        await self.initialize()
        task_type = await self.get_task_type(type_id)
        return task_type.get("model_tier", "standard") if task_type else "standard"

    async def get_complexity_level_for_type(self, type_id: str) -> int:
        """Get the complexity level for a task type (1-5)"""
        await self.initialize()
        task_type = await self.get_task_type(type_id)
        return task_type.get("complexity_level", 3) if task_type else 3

    async def set_model_tier_for_type(self, type_id: str, model_tier: str) -> bool:
        """Set the model tier for a task type"""
        if model_tier not in ("simple", "standard", "complex"):
            logger.error(
                f"Invalid model tier '{model_tier}'. Must be simple, standard, or complex."
            )
            return False
        return await self.update_task_type(type_id, model_tier=model_tier)

    async def set_complexity_level_for_type(
        self, type_id: str, complexity_level: int
    ) -> bool:
        """Set the complexity level for a task type"""
        if complexity_level not in range(1, 6):
            logger.error(f"Invalid complexity level '{complexity_level}'. Must be 1-5.")
            return False
        return await self.update_task_type(type_id, complexity_level=complexity_level)

    async def get_tool_restrictions_for_type(
        self, type_id: str
    ) -> Dict[str, Optional[List[str]]]:
        """Get the tool restrictions for a task type

        Returns:
            Dictionary with 'allowed_tools' and 'disallowed_tools' keys.
            None values mean no restriction for that category.
        """
        await self.initialize()
        task_type = await self.get_task_type(type_id)
        if not task_type:
            return {"allowed_tools": None, "disallowed_tools": None}

        return {
            "allowed_tools": task_type.get("allowed_tools"),
            "disallowed_tools": task_type.get("disallowed_tools"),
        }

    async def set_tool_restrictions_for_type(
        self,
        type_id: str,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> bool:
        """Set the tool restrictions for a task type

        Args:
            type_id: Task type ID
            allowed_tools: List of allowed tool names (None = all tools)
            disallowed_tools: List of disallowed tool names (None = no restrictions)

        Returns:
            True if successful, False otherwise
        """
        updates = {}
        if allowed_tools is not None:
            updates["allowed_tools"] = allowed_tools
        if disallowed_tools is not None:
            updates["disallowed_tools"] = disallowed_tools

        if not updates:
            logger.warning(f"No tool restrictions provided for task type '{type_id}'")
            return False

        return await self.update_task_type(type_id, **updates)

    async def get_bash_permissions_for_type(self, type_id: str) -> List[str]:
        """Get the bash permissions for a task type"""
        await self.initialize()
        task_type = await self.get_task_type(type_id)
        return task_type.get("bash_permissions", []) if task_type else []

    async def set_bash_permissions_for_type(
        self, type_id: str, bash_permissions: List[str]
    ) -> bool:
        """Set the bash permissions for a task type"""
        return await self.update_task_type(type_id, bash_permissions=bash_permissions)

    async def get_pre_hooks_for_type(self, type_id: str) -> List[str]:
        """Get the pre-execution hooks for a task type"""
        await self.initialize()
        task_type = await self.get_task_type(type_id)
        if not task_type:
            return []

        hooks = task_type.get("pre_hooks", "[]")
        if isinstance(hooks, str):
            try:
                return json.loads(hooks)
            except json.JSONDecodeError:
                return []
        return hooks if isinstance(hooks, list) else []

    async def get_post_hooks_for_type(self, type_id: str) -> List[str]:
        """Get the post-execution hooks for a task type"""
        await self.initialize()
        task_type = await self.get_task_type(type_id)
        if not task_type:
            return []

        hooks = task_type.get("post_hooks", "[]")
        if isinstance(hooks, str):
            try:
                return json.loads(hooks)
            except json.JSONDecodeError:
                return []
        return hooks if isinstance(hooks, list) else []

    async def set_hooks_for_type(
        self,
        type_id: str,
        pre_hooks: Optional[List[str]] = None,
        post_hooks: Optional[List[str]] = None,
    ) -> bool:
        """Set pre and/or post hooks for a task type"""
        updates = {}

        if pre_hooks is not None:
            updates["pre_hooks"] = pre_hooks
        if post_hooks is not None:
            updates["post_hooks"] = post_hooks

        if not updates:
            logger.warning(f"No hooks provided for task type '{type_id}'")
            return False

        # Convert to JSON for storage
        if "pre_hooks" in updates:
            updates["pre_hooks"] = json.dumps(updates["pre_hooks"])
        if "post_hooks" in updates:
            updates["post_hooks"] = json.dumps(updates["post_hooks"])

        await self.initialize()
        existing = await self.get_task_type(type_id)
        if not existing:
            logger.error(f"Task type '{type_id}' not found")
            return False

        try:
            async with aiosqlite.connect(self.db_path) as db:
                update_parts = []
                params = []

                if "pre_hooks" in updates:
                    update_parts.append("pre_hooks = ?")
                    params.append(updates["pre_hooks"])
                if "post_hooks" in updates:
                    update_parts.append("post_hooks = ?")
                    params.append(updates["post_hooks"])

                update_parts.append("updated_at = CURRENT_TIMESTAMP")
                params.append(type_id)

                await db.execute(
                    f"UPDATE task_types SET {', '.join(update_parts)} WHERE id = ?",
                    params,
                )
                await db.commit()
                logger.info(f"Updated hooks for task type: {type_id}")
                return True
        except Exception as e:
            logger.error(f"Error updating hooks for task type '{type_id}': {e}")
            return False
