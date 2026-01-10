# Sugar - AGENTS.md

Instructions for AI coding agents working with the Sugar project.

## Git Branching Rules (STRICT)

**NEVER push directly to `develop` or `main` branches.**

All changes MUST go through feature/bugfix branches and PRs:

| Branch | Purpose |
|--------|---------|
| `main` | Production releases only (PyPI) |
| `develop` | Integration branch (target for all PRs) |
| `feature/*` | New features |
| `bugfix/*` | Bug fixes |
| `hotfix/*` | Urgent production fixes (branch from main) |
| `docs/*` | Documentation changes |

```bash
# Always create branches from develop
git checkout develop
git pull origin develop
git checkout -b feature/your-feature

# After work is complete
git push -u origin feature/your-feature
# Then create PR targeting develop
```

## Changelog Requirements

**CHANGELOG.md must be updated for every release.**

When preparing a release (merging to develop or main):

1. Add new section at top of CHANGELOG.md:
```markdown
## [X.Y.Z] - YYYY-MM-DD

### Added
- New features

### Changed
- Changes to existing functionality

### Fixed
- Bug fixes

### Removed
- Removed features
```

2. Follow [Keep a Changelog](https://keepachangelog.com/) format
3. Reference PR numbers where applicable: `(#123)`
4. Group changes by category: Added, Changed, Fixed, Removed, Security

## Version Numbering

Sugar uses [PEP 440](https://peps.python.org/pep-0440/):

| Branch | Format | Example |
|--------|--------|---------|
| `develop` | `X.Y.Z.devN` | `3.4.4.dev3` |
| `main` | `X.Y.Z` | `3.4.4` |

Version is in `pyproject.toml` only. Bump dev number after merging PRs.

## Project Structure

```
sugar/
├── agent/           # Claude Agent SDK integration
│   ├── base.py      # SugarAgent class
│   ├── hooks.py     # Quality gate hooks
│   ├── subagent_manager.py  # Sub-agent spawning
│   └── tools.py     # Custom tools
├── billing/         # SaaS billing (usage, API keys, tiers)
├── config/          # Configuration management
├── core/            # Main loop and orchestration
│   └── loop.py      # Primary execution loop
├── discovery/       # Work discovery (GitHub, error logs, code quality)
├── executor/        # Task execution
│   ├── agent_sdk.py # Agent SDK executor
│   └── wrapper.py   # Legacy CLI wrapper
├── integrations/    # External integrations (GitHub)
├── learning/        # Adaptive learning system
├── mcp/             # MCP server implementation
├── orchestration/   # Task orchestration and decomposition
├── profiles/        # Workflow profiles (default, issue_responder)
├── quality_gates/   # Security and quality checks
├── ralph/           # Ralph Wiggum iterative execution
├── storage/         # Database and work queue (SQLite/SQLAlchemy)
├── triage/          # Task triage and prioritization
├── utils/           # Utility functions
├── workflow/        # Workflow management
└── main.py          # CLI entry point (Click-based)

tests/               # Test suite (pytest)
docs/                # Documentation
.claude-plugin/      # Claude Code plugin
packages/mcp-server/ # npm MCP server package
```

## Key Concepts

### Agent-Agnostic
Sugar works with multiple AI coding agents:
- Claude Code (default)
- OpenCode
- Aider
- Any CLI-based AI agent

### Core Components
- **SugarAgent**: Native Claude Agent SDK integration
- **Task Queue**: SQLite-backed priority queue
- **Discovery**: Finds work from GitHub issues, error logs, code analysis
- **Quality Gates**: Security hooks for file protection and command blocking
- **Ralph Wiggum**: Iterative execution until tests pass
- **Orchestration**: Decomposes complex tasks into subtasks

## Development Setup

```bash
# Clone and checkout develop
git clone https://github.com/roboticforce/sugar.git
cd sugar
git checkout develop

# Create venv and install
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev,test,github]"

# Verify
sugar --version
```

## Testing

```bash
# Run all tests
pytest

# With coverage
pytest --cov=sugar --cov-report=term-missing

# Specific categories
pytest -m unit
pytest -m integration

# Code quality
black sugar/
isort sugar/
flake8 sugar/
mypy sugar/
```

## PR Checklist

1. Branch from `develop`
2. Code formatted (`black`, `isort`)
3. Tests pass (`pytest`)
4. Update CHANGELOG.md if releasing
5. PR targets `develop` (not `main`)

## Common Patterns

- Use `click.echo()` for output, not `print()`
- Async functions for I/O (database, filesystem)
- `pathlib.Path` for path operations
- Configuration in `.sugar/config.yaml`
- Graceful error handling with user-friendly messages
