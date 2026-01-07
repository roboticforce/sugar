"""
Criteria Templates - Default acceptance criteria templates per task type

Provides structured acceptance criteria that can be applied to tasks
based on their type. These templates integrate with SuccessCriteriaVerifier
for automatic verification of task completion.

Supported criterion types:
- test_suite: Run test command and verify results
- file_exists: Verify file exists at specified path
- string_in_file: Verify string exists in file
- http_status: Verify HTTP endpoint returns expected status
- http_no_redirect: Verify URL doesn't redirect
- code_change: Verify code changes were made (requires git diff)
- no_regressions: Verify no test regressions
- documentation: Verify documentation exists/updated
"""

from typing import Any, Dict, List


class CriteriaTemplates:
    """Default acceptance criteria templates per task type"""

    # Feature task: New feature implementation
    FEATURE: List[Dict[str, Any]] = [
        {
            "type": "test_suite",
            "description": "All tests pass",
            "command": "pytest",
            "expected_failures": 0,
            "expected_errors": 0,
            "required": True,
        },
        {
            "type": "code_change",
            "description": "Code changes were made",
            "min_files_changed": 1,
            "required": True,
        },
        {
            "type": "no_regressions",
            "description": "No test regressions introduced",
            "required": True,
        },
    ]

    # Bug fix task: Fix existing issues
    BUG_FIX: List[Dict[str, Any]] = [
        {
            "type": "test_suite",
            "description": "All tests pass",
            "command": "pytest",
            "expected_failures": 0,
            "expected_errors": 0,
            "required": True,
        },
        {
            "type": "code_change",
            "description": "Code changes were made to fix the bug",
            "min_files_changed": 1,
            "required": True,
        },
        {
            "type": "no_regressions",
            "description": "No test regressions introduced",
            "required": True,
        },
    ]

    # Refactor task: Code restructuring without behavior changes
    REFACTOR: List[Dict[str, Any]] = [
        {
            "type": "test_suite",
            "description": "All tests pass after refactoring",
            "command": "pytest",
            "expected_failures": 0,
            "expected_errors": 0,
            "required": True,
        },
        {
            "type": "no_regressions",
            "description": "No test regressions introduced",
            "required": True,
        },
        {
            "type": "code_change",
            "description": "Code was refactored",
            "min_files_changed": 1,
            "required": True,
        },
    ]

    # Documentation task: Documentation updates
    DOCUMENTATION: List[Dict[str, Any]] = [
        {
            "type": "file_exists",
            "description": "Documentation file exists",
            "file_pattern": "*.md",
            "required": False,  # Will use task-specific path
        },
        {
            "type": "code_change",
            "description": "Documentation was updated",
            "file_patterns": ["*.md", "docs/**"],
            "min_files_changed": 1,
            "required": True,
        },
    ]

    # Test task: Test creation or updates
    TEST: List[Dict[str, Any]] = [
        {
            "type": "test_suite",
            "description": "All tests pass including new tests",
            "command": "pytest",
            "expected_failures": 0,
            "expected_errors": 0,
            "required": True,
        },
        {
            "type": "code_change",
            "description": "Test files were created/modified",
            "file_patterns": ["test_*.py", "*_test.py", "tests/**/*.py"],
            "min_files_changed": 1,
            "required": True,
        },
    ]

    # Chore task: Maintenance tasks
    CHORE: List[Dict[str, Any]] = [
        {
            "type": "test_suite",
            "description": "All tests still pass",
            "command": "pytest",
            "expected_failures": 0,
            "expected_errors": 0,
            "required": True,
        },
        {
            "type": "no_regressions",
            "description": "No regressions introduced",
            "required": True,
        },
    ]

    # Style task: Code formatting and style
    STYLE: List[Dict[str, Any]] = [
        {
            "type": "test_suite",
            "description": "All tests pass after style changes",
            "command": "pytest",
            "expected_failures": 0,
            "expected_errors": 0,
            "required": True,
        },
        {
            "type": "no_regressions",
            "description": "No test regressions",
            "required": True,
        },
    ]

    # Performance task: Performance improvements
    PERFORMANCE: List[Dict[str, Any]] = [
        {
            "type": "test_suite",
            "description": "All tests pass",
            "command": "pytest",
            "expected_failures": 0,
            "expected_errors": 0,
            "required": True,
        },
        {
            "type": "no_regressions",
            "description": "No test regressions",
            "required": True,
        },
        {
            "type": "code_change",
            "description": "Performance optimization code changes",
            "min_files_changed": 1,
            "required": True,
        },
    ]

    # CI/CD task: Pipeline configuration
    CI_CD: List[Dict[str, Any]] = [
        {
            "type": "code_change",
            "description": "CI/CD configuration updated",
            "file_patterns": [
                ".github/**",
                "Dockerfile",
                "docker-compose*.yml",
                ".gitlab-ci.yml",
            ],
            "min_files_changed": 1,
            "required": True,
        },
    ]

    # Security task: Security fixes
    SECURITY: List[Dict[str, Any]] = [
        {
            "type": "test_suite",
            "description": "All tests pass",
            "command": "pytest",
            "expected_failures": 0,
            "expected_errors": 0,
            "required": True,
        },
        {
            "type": "code_change",
            "description": "Security-related changes made",
            "min_files_changed": 1,
            "required": True,
        },
        {
            "type": "no_regressions",
            "description": "No test regressions",
            "required": True,
        },
    ]

    @classmethod
    def get_template(cls, task_type: str) -> List[Dict[str, Any]]:
        """Get the default acceptance criteria template for a task type

        Args:
            task_type: The type of task (feature, bug_fix, refactor, etc.)

        Returns:
            List of criterion definitions for the task type
        """
        templates = {
            "feature": cls.FEATURE,
            "bug_fix": cls.BUG_FIX,
            "refactor": cls.REFACTOR,
            "docs": cls.DOCUMENTATION,
            "documentation": cls.DOCUMENTATION,
            "test": cls.TEST,
            "chore": cls.CHORE,
            "style": cls.STYLE,
            "perf": cls.PERFORMANCE,
            "performance": cls.PERFORMANCE,
            "ci": cls.CI_CD,
            "ci_cd": cls.CI_CD,
            "security": cls.SECURITY,
        }
        return templates.get(task_type, cls.FEATURE)

    @classmethod
    def merge_criteria(
        cls,
        default_criteria: List[Dict[str, Any]],
        custom_criteria: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Merge custom criteria with default criteria

        Custom criteria can override defaults by matching on 'type' and 'description'.

        Args:
            default_criteria: Default criteria from template
            custom_criteria: Custom criteria provided by user

        Returns:
            Merged list of criteria
        """
        if not custom_criteria:
            return default_criteria.copy()

        if not default_criteria:
            return custom_criteria.copy()

        # Build index of custom criteria for faster lookup
        custom_index = {}
        for c in custom_criteria:
            key = (c.get("type"), c.get("description", ""))
            custom_index[key] = c

        merged = []

        # Process default criteria, replacing with custom if available
        for default in default_criteria:
            key = (default.get("type"), default.get("description", ""))
            if key in custom_index:
                merged.append(custom_index.pop(key))
            else:
                merged.append(default.copy())

        # Add remaining custom criteria
        merged.extend(custom_index.values())

        return merged

    @classmethod
    def validate_criterion(cls, criterion: Dict[str, Any]) -> tuple[bool, str]:
        """Validate a single criterion definition

        Args:
            criterion: Criterion definition to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not isinstance(criterion, dict):
            return False, "Criterion must be a dictionary"

        criterion_type = criterion.get("type")
        if not criterion_type:
            return False, "Criterion must have a 'type' field"

        valid_types = {
            "test_suite",
            "file_exists",
            "string_in_file",
            "http_status",
            "http_no_redirect",
            "code_change",
            "no_regressions",
            "browser_element_exists",
        }

        if criterion_type not in valid_types:
            return (
                False,
                f"Unknown criterion type: {criterion_type}. Valid types: {valid_types}",
            )

        # Type-specific validation
        if criterion_type == "test_suite":
            if "command" not in criterion:
                return False, "test_suite criterion requires 'command' field"

        elif criterion_type == "file_exists":
            if "file_path" not in criterion and "file_pattern" not in criterion:
                return (
                    False,
                    "file_exists criterion requires 'file_path' or 'file_pattern' field",
                )

        elif criterion_type == "string_in_file":
            if "file_path" not in criterion:
                return False, "string_in_file criterion requires 'file_path' field"
            if "search_string" not in criterion:
                return False, "string_in_file criterion requires 'search_string' field"

        elif criterion_type == "http_status":
            if "url" not in criterion:
                return False, "http_status criterion requires 'url' field"
            if "expected" not in criterion:
                return False, "http_status criterion requires 'expected' field"

        elif criterion_type == "http_no_redirect":
            if "url" not in criterion:
                return False, "http_no_redirect criterion requires 'url' field"

        return True, ""

    @classmethod
    def validate_criteria_list(
        cls, criteria: List[Dict[str, Any]]
    ) -> tuple[bool, List[str]]:
        """Validate a list of criteria definitions

        Args:
            criteria: List of criterion definitions to validate

        Returns:
            Tuple of (all_valid, list of error messages)
        """
        if not isinstance(criteria, list):
            return False, ["Criteria must be a list"]

        errors = []
        for i, criterion in enumerate(criteria):
            is_valid, error = cls.validate_criterion(criterion)
            if not is_valid:
                errors.append(f"Criterion {i}: {error}")

        return len(errors) == 0, errors
