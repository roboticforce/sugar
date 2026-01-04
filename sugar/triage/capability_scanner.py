"""
Codebase Capability Scanner

Detects available tools, frameworks, and capabilities in a codebase.
Used by the triage system to determine appropriate quality gates and commands.
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Directories to exclude from scanning
DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    ".tox",
    ".eggs",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
    ".coverage",
    "htmlcov",
}


@dataclass
class ToolCapability:
    """Represents a detected tool/framework capability"""

    category: str  # 'testing', 'linting', 'formatting', 'ci', 'type_checking', 'build'
    name: str
    enabled: bool = True
    config_file: Optional[str] = None
    default_command: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "name": self.name,
            "enabled": self.enabled,
            "config_file": self.config_file,
            "default_command": self.default_command,
            "metadata": self.metadata,
        }


@dataclass
class CodebaseCapabilities:
    """Complete capability profile of a codebase"""

    project_root: str
    language: str  # 'python', 'javascript', 'typescript', 'go', 'rust', 'mixed'

    # Framework detection results
    test_frameworks: List[ToolCapability] = field(default_factory=list)
    linters: List[ToolCapability] = field(default_factory=list)
    formatters: List[ToolCapability] = field(default_factory=list)
    type_checkers: List[ToolCapability] = field(default_factory=list)
    ci_systems: List[ToolCapability] = field(default_factory=list)
    build_tools: List[ToolCapability] = field(default_factory=list)

    # Aggregated flags for quick decision-making
    has_tests: bool = False
    has_linting: bool = False
    has_formatting: bool = False
    has_type_checking: bool = False
    has_ci: bool = False

    # Recommended commands for task execution
    test_command: Optional[str] = None
    lint_command: Optional[str] = None
    format_command: Optional[str] = None
    type_check_command: Optional[str] = None
    build_command: Optional[str] = None

    # Detection metadata
    scan_timestamp: Optional[datetime] = None
    scan_duration_ms: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_root": self.project_root,
            "language": self.language,
            "test_frameworks": [t.to_dict() for t in self.test_frameworks],
            "linters": [l.to_dict() for l in self.linters],
            "formatters": [f.to_dict() for f in self.formatters],
            "type_checkers": [t.to_dict() for t in self.type_checkers],
            "ci_systems": [c.to_dict() for c in self.ci_systems],
            "build_tools": [b.to_dict() for b in self.build_tools],
            "has_tests": self.has_tests,
            "has_linting": self.has_linting,
            "has_formatting": self.has_formatting,
            "has_type_checking": self.has_type_checking,
            "has_ci": self.has_ci,
            "test_command": self.test_command,
            "lint_command": self.lint_command,
            "format_command": self.format_command,
            "type_check_command": self.type_check_command,
            "build_command": self.build_command,
            "scan_timestamp": (
                self.scan_timestamp.isoformat() if self.scan_timestamp else None
            ),
            "scan_duration_ms": self.scan_duration_ms,
            "warnings": self.warnings,
        }


class CodebaseCapabilityScanner:
    """
    Scans a codebase to detect available tools, frameworks, and capabilities.

    Detects:
    - Test frameworks (pytest, jest, go test, cargo test, etc.)
    - Linters (flake8, ruff, eslint, golangci-lint, etc.)
    - Formatters (black, prettier, gofmt, etc.)
    - Type checkers (mypy, pyright, typescript, etc.)
    - CI systems (GitHub Actions, GitLab CI, etc.)
    - Build tools (poetry, npm, cargo, etc.)
    """

    # Python ecosystem detectors
    PYTHON_DETECTORS = {
        "pytest": {
            "category": "testing",
            "files": ["pytest.ini", "pyproject.toml", "setup.cfg", "conftest.py"],
            "patterns": [r"\[tool\.pytest", r"\[pytest\]", r"\[tool:pytest\]"],
            "command": "pytest",
        },
        "unittest": {
            "category": "testing",
            "files": [],  # stdlib, detected via test file patterns
            "command": "python -m unittest discover",
        },
        "flake8": {
            "category": "linting",
            "files": [".flake8", "setup.cfg", "tox.ini"],
            "patterns": [r"\[flake8\]"],
            "command": "flake8",
        },
        "ruff": {
            "category": "linting",
            "files": ["ruff.toml", "pyproject.toml"],
            "patterns": [r"\[tool\.ruff\]"],
            "command": "ruff check",
        },
        "pylint": {
            "category": "linting",
            "files": [".pylintrc", "pylintrc", "pyproject.toml"],
            "patterns": [r"\[tool\.pylint\]"],
            "command": "pylint",
        },
        "black": {
            "category": "formatting",
            "files": ["pyproject.toml"],
            "patterns": [r"\[tool\.black\]"],
            "command": "black",
        },
        "isort": {
            "category": "formatting",
            "files": [".isort.cfg", "pyproject.toml", "setup.cfg"],
            "patterns": [r"\[tool\.isort\]", r"\[isort\]"],
            "command": "isort",
        },
        "mypy": {
            "category": "type_checking",
            "files": ["mypy.ini", ".mypy.ini", "pyproject.toml", "setup.cfg"],
            "patterns": [r"\[tool\.mypy\]", r"\[mypy\]"],
            "command": "mypy",
        },
        "pyright": {
            "category": "type_checking",
            "files": ["pyrightconfig.json", "pyproject.toml"],
            "patterns": [r"\[tool\.pyright\]"],
            "command": "pyright",
        },
        "poetry": {
            "category": "build",
            "files": ["poetry.lock"],
            "patterns": [r"\[tool\.poetry\]"],
            "command": "poetry install",
        },
        "uv": {
            "category": "build",
            "files": ["uv.lock"],
            "command": "uv pip install",
        },
        "pre-commit": {
            "category": "ci",
            "files": [".pre-commit-config.yaml"],
            "command": "pre-commit run --all-files",
        },
    }

    # JavaScript/TypeScript ecosystem detectors
    JAVASCRIPT_DETECTORS = {
        "jest": {
            "category": "testing",
            "files": ["jest.config.js", "jest.config.ts", "jest.config.json"],
            "package_check": ["jest"],
            "command": "npm test",
        },
        "vitest": {
            "category": "testing",
            "files": ["vitest.config.ts", "vitest.config.js"],
            "package_check": ["vitest"],
            "command": "npm test",
        },
        "mocha": {
            "category": "testing",
            "files": [".mocharc.js", ".mocharc.json", ".mocharc.yaml"],
            "package_check": ["mocha"],
            "command": "npm test",
        },
        "eslint": {
            "category": "linting",
            "files": [
                ".eslintrc.js",
                ".eslintrc.cjs",
                ".eslintrc.json",
                ".eslintrc",
                "eslint.config.js",
            ],
            "package_check": ["eslint"],
            "command": "npx eslint .",
        },
        "prettier": {
            "category": "formatting",
            "files": [
                ".prettierrc",
                ".prettierrc.js",
                ".prettierrc.json",
                "prettier.config.js",
            ],
            "package_check": ["prettier"],
            "command": "npx prettier --write .",
        },
        "typescript": {
            "category": "type_checking",
            "files": ["tsconfig.json"],
            "package_check": ["typescript"],
            "command": "npx tsc --noEmit",
        },
        "npm": {
            "category": "build",
            "files": ["package-lock.json"],
            "command": "npm install",
        },
        "yarn": {
            "category": "build",
            "files": ["yarn.lock"],
            "command": "yarn install",
        },
        "pnpm": {
            "category": "build",
            "files": ["pnpm-lock.yaml"],
            "command": "pnpm install",
        },
    }

    # Go ecosystem detectors
    GO_DETECTORS = {
        "go-test": {
            "category": "testing",
            "files": ["go.mod"],  # Go has built-in testing
            "command": "go test ./...",
        },
        "golangci-lint": {
            "category": "linting",
            "files": [".golangci.yml", ".golangci.yaml"],
            "command": "golangci-lint run",
        },
        "gofmt": {
            "category": "formatting",
            "files": ["go.mod"],  # Built-in
            "command": "gofmt -w .",
        },
    }

    # Rust ecosystem detectors
    RUST_DETECTORS = {
        "cargo-test": {
            "category": "testing",
            "files": ["Cargo.toml"],
            "command": "cargo test",
        },
        "clippy": {
            "category": "linting",
            "files": ["Cargo.toml"],
            "command": "cargo clippy",
        },
        "rustfmt": {
            "category": "formatting",
            "files": ["rustfmt.toml", ".rustfmt.toml", "Cargo.toml"],
            "command": "cargo fmt",
        },
    }

    # CI system detectors
    CI_DETECTORS = {
        "github-actions": {
            "category": "ci",
            "dirs": [".github/workflows"],
        },
        "gitlab-ci": {
            "category": "ci",
            "files": [".gitlab-ci.yml"],
        },
        "circleci": {
            "category": "ci",
            "dirs": [".circleci"],
        },
        "jenkins": {
            "category": "ci",
            "files": ["Jenkinsfile"],
        },
    }

    def __init__(
        self, root_path: Optional[str] = None, excluded_dirs: Optional[set] = None
    ):
        """
        Initialize the scanner.

        Args:
            root_path: Project root directory (defaults to current directory)
            excluded_dirs: Directories to exclude from scanning
        """
        self.root_path = os.path.abspath(root_path or ".")
        self.excluded_dirs = excluded_dirs or DEFAULT_EXCLUDED_DIRS
        self._file_cache: Dict[str, bool] = {}
        self._content_cache: Dict[str, str] = {}

    async def scan(self) -> CodebaseCapabilities:
        """
        Perform comprehensive capability scan of the codebase.

        Returns:
            CodebaseCapabilities with all detected tools and recommended commands
        """
        start_time = datetime.now(timezone.utc)
        warnings = []

        try:
            # Step 1: Detect primary language
            language = await self._detect_primary_language()
            logger.debug(f"Detected primary language: {language}")

            # Step 2: Collect all existing files for detection
            existing_files = await self._scan_for_config_files()

            # Step 3: Detect capabilities by language
            test_frameworks = []
            linters = []
            formatters = []
            type_checkers = []
            build_tools = []
            ci_systems = []

            # Python
            if language in ["python", "mixed"]:
                for name, config in self.PYTHON_DETECTORS.items():
                    cap = await self._detect_tool(name, config, existing_files)
                    if cap:
                        self._categorize_capability(
                            cap,
                            test_frameworks,
                            linters,
                            formatters,
                            type_checkers,
                            build_tools,
                            ci_systems,
                        )

            # JavaScript/TypeScript
            if language in ["javascript", "typescript", "mixed"]:
                for name, config in self.JAVASCRIPT_DETECTORS.items():
                    cap = await self._detect_tool(name, config, existing_files)
                    if cap:
                        self._categorize_capability(
                            cap,
                            test_frameworks,
                            linters,
                            formatters,
                            type_checkers,
                            build_tools,
                            ci_systems,
                        )

            # Go
            if language in ["go", "mixed"]:
                for name, config in self.GO_DETECTORS.items():
                    cap = await self._detect_tool(name, config, existing_files)
                    if cap:
                        self._categorize_capability(
                            cap,
                            test_frameworks,
                            linters,
                            formatters,
                            type_checkers,
                            build_tools,
                            ci_systems,
                        )

            # Rust
            if language in ["rust", "mixed"]:
                for name, config in self.RUST_DETECTORS.items():
                    cap = await self._detect_tool(name, config, existing_files)
                    if cap:
                        self._categorize_capability(
                            cap,
                            test_frameworks,
                            linters,
                            formatters,
                            type_checkers,
                            build_tools,
                            ci_systems,
                        )

            # CI systems (language-independent)
            for name, config in self.CI_DETECTORS.items():
                cap = await self._detect_ci_system(name, config, existing_files)
                if cap:
                    ci_systems.append(cap)

            # Step 4: Determine recommended commands
            test_command = self._determine_command(test_frameworks, build_tools, "test")
            lint_command = self._determine_command(linters, build_tools, "lint")
            format_command = self._determine_command(formatters, build_tools, "format")
            type_check_command = self._determine_command(
                type_checkers, build_tools, "type_check"
            )
            build_command = self._determine_build_command(build_tools)

            scan_duration = (
                datetime.now(timezone.utc) - start_time
            ).total_seconds() * 1000

            return CodebaseCapabilities(
                project_root=self.root_path,
                language=language,
                test_frameworks=test_frameworks,
                linters=linters,
                formatters=formatters,
                type_checkers=type_checkers,
                ci_systems=ci_systems,
                build_tools=build_tools,
                has_tests=len(test_frameworks) > 0,
                has_linting=len(linters) > 0,
                has_formatting=len(formatters) > 0,
                has_type_checking=len(type_checkers) > 0,
                has_ci=len(ci_systems) > 0,
                test_command=test_command,
                lint_command=lint_command,
                format_command=format_command,
                type_check_command=type_check_command,
                build_command=build_command,
                scan_timestamp=start_time,
                scan_duration_ms=int(scan_duration),
                warnings=warnings,
            )

        except Exception as e:
            logger.error(f"Error scanning codebase capabilities: {e}")
            return CodebaseCapabilities(
                project_root=self.root_path,
                language="unknown",
                warnings=[f"Scan failed: {str(e)}"],
            )

    async def _detect_primary_language(self) -> str:
        """Detect the primary programming language of the project."""
        # High confidence indicators (lockfiles/config)
        indicators = {
            "Cargo.toml": "rust",
            "go.mod": "go",
            "tsconfig.json": "typescript",
            "package.json": "javascript",
            "pyproject.toml": "python",
            "setup.py": "python",
            "requirements.txt": "python",
        }

        detected = []
        for file, lang in indicators.items():
            if await self._file_exists(file):
                detected.append(lang)

        if not detected:
            return "unknown"
        if len(set(detected)) > 1:
            return "mixed"
        return detected[0]

    async def _scan_for_config_files(self) -> set:
        """Scan for configuration files and directories."""
        existing = set()

        # Check common config files at root
        for detector_set in [
            self.PYTHON_DETECTORS,
            self.JAVASCRIPT_DETECTORS,
            self.GO_DETECTORS,
            self.RUST_DETECTORS,
            self.CI_DETECTORS,
        ]:
            for config in detector_set.values():
                for file in config.get("files", []):
                    if await self._file_exists(file):
                        existing.add(file)
                for dir_path in config.get("dirs", []):
                    if await self._dir_exists(dir_path):
                        existing.add(dir_path)

        # Check for package.json
        if await self._file_exists("package.json"):
            existing.add("package.json")

        return existing

    async def _detect_tool(
        self, name: str, config: Dict[str, Any], existing_files: set
    ) -> Optional[ToolCapability]:
        """Detect a tool based on its configuration."""
        config_file = None
        enabled = False

        # Check for config files
        for file in config.get("files", []):
            if file in existing_files:
                config_file = file
                enabled = True
                break

        # Check patterns in files (e.g., [tool.pytest] in pyproject.toml)
        if not enabled and "patterns" in config:
            for pattern in config["patterns"]:
                for file in ["pyproject.toml", "setup.cfg"]:
                    if await self._file_exists(file):
                        content = await self._read_file(file)
                        if content and re.search(pattern, content):
                            config_file = file
                            enabled = True
                            break
                if enabled:
                    break

        # Check package.json for JS tools
        if not enabled and "package_check" in config:
            if await self._file_exists("package.json"):
                content = await self._read_file("package.json")
                if content:
                    try:
                        pkg = json.loads(content)
                        deps = {
                            **pkg.get("dependencies", {}),
                            **pkg.get("devDependencies", {}),
                        }
                        for dep in config["package_check"]:
                            if dep in deps:
                                config_file = "package.json"
                                enabled = True
                                break
                    except json.JSONDecodeError:
                        pass

        if not enabled:
            return None

        return ToolCapability(
            category=config["category"],
            name=name,
            enabled=enabled,
            config_file=config_file,
            default_command=config.get("command"),
        )

    async def _detect_ci_system(
        self, name: str, config: Dict[str, Any], existing_files: set
    ) -> Optional[ToolCapability]:
        """Detect a CI system."""
        config_file = None

        # Check files
        for file in config.get("files", []):
            if file in existing_files:
                config_file = file
                break

        # Check directories
        for dir_path in config.get("dirs", []):
            if dir_path in existing_files:
                config_file = dir_path
                break

        if not config_file:
            return None

        return ToolCapability(
            category="ci",
            name=name,
            enabled=True,
            config_file=config_file,
        )

    def _categorize_capability(
        self,
        cap: ToolCapability,
        test_frameworks: List,
        linters: List,
        formatters: List,
        type_checkers: List,
        build_tools: List,
        ci_systems: List,
    ):
        """Categorize a capability into the appropriate list."""
        if cap.category == "testing":
            test_frameworks.append(cap)
        elif cap.category == "linting":
            linters.append(cap)
        elif cap.category == "formatting":
            formatters.append(cap)
        elif cap.category == "type_checking":
            type_checkers.append(cap)
        elif cap.category == "build":
            build_tools.append(cap)
        elif cap.category == "ci":
            ci_systems.append(cap)

    def _determine_command(
        self,
        tools: List[ToolCapability],
        build_tools: List[ToolCapability],
        command_type: str,
    ) -> Optional[str]:
        """Determine the best command for a category."""
        if not tools:
            return None

        # Priority order for each category
        priority = {
            "test": ["pytest", "jest", "vitest", "go-test", "cargo-test"],
            "lint": ["ruff", "eslint", "golangci-lint", "clippy", "flake8", "pylint"],
            "format": ["black", "prettier", "gofmt", "rustfmt"],
            "type_check": ["mypy", "pyright", "typescript"],
        }

        order = priority.get(command_type, [])

        for tool_name in order:
            for tool in tools:
                if tool.name == tool_name and tool.enabled and tool.default_command:
                    return tool.default_command

        # Fallback to first enabled tool
        for tool in tools:
            if tool.enabled and tool.default_command:
                return tool.default_command

        return None

    def _determine_build_command(
        self, build_tools: List[ToolCapability]
    ) -> Optional[str]:
        """Determine the build/install command."""
        priority = ["poetry", "uv", "npm", "yarn", "pnpm"]

        for tool_name in priority:
            for tool in build_tools:
                if tool.name == tool_name and tool.enabled:
                    return tool.default_command

        return None

    async def _file_exists(self, filename: str) -> bool:
        """Check if a file exists (with caching)."""
        if filename in self._file_cache:
            return self._file_cache[filename]

        full_path = os.path.join(self.root_path, filename)
        exists = os.path.isfile(full_path)
        self._file_cache[filename] = exists
        return exists

    async def _dir_exists(self, dirname: str) -> bool:
        """Check if a directory exists."""
        full_path = os.path.join(self.root_path, dirname)
        return os.path.isdir(full_path)

    async def _read_file(self, filename: str) -> Optional[str]:
        """Read file content (with caching)."""
        if filename in self._content_cache:
            return self._content_cache[filename]

        full_path = os.path.join(self.root_path, filename)
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            self._content_cache[filename] = content
            return content
        except Exception:
            return None

    def get_summary(self, capabilities: CodebaseCapabilities) -> str:
        """Get a human-readable summary of detected capabilities."""
        lines = [
            f"Project: {capabilities.language}",
            "",
        ]

        if capabilities.test_frameworks:
            lines.append(
                f"Testing: {', '.join(t.name for t in capabilities.test_frameworks)}"
            )
        if capabilities.linters:
            lines.append(f"Linting: {', '.join(l.name for l in capabilities.linters)}")
        if capabilities.formatters:
            lines.append(
                f"Formatting: {', '.join(f.name for f in capabilities.formatters)}"
            )
        if capabilities.type_checkers:
            lines.append(
                f"Type Checking: {', '.join(t.name for t in capabilities.type_checkers)}"
            )
        if capabilities.ci_systems:
            lines.append(f"CI: {', '.join(c.name for c in capabilities.ci_systems)}")

        if capabilities.test_command:
            lines.append(f"\nTest command: {capabilities.test_command}")
        if capabilities.lint_command:
            lines.append(f"Lint command: {capabilities.lint_command}")

        return "\n".join(lines)
