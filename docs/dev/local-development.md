# Local Development Setup

How to set up Sugar for local development, testing, and contributing.

## Branching Strategy (Gitflow)

Sugar uses **Gitflow** branching:

| Branch | Purpose |
|--------|---------|
| `main` | Production releases only (PyPI) |
| `develop` | Integration branch - **base for all work** |
| `feature/*` | New features, branched from `develop` |
| `bugfix/*` | Bug fixes, branched from `develop` |
| `hotfix/*` | Urgent fixes, branched from `main` |

**All PRs target `develop`**, not `main`. Releases are created by merging `develop` → `main`.

## Quick Setup

### Prerequisites

- **Python 3.11+** installed
- **An AI coding agent CLI** (Claude Code, OpenCode, Aider, etc.)
- **Git** for cloning the repository
- **Regular terminal/shell** (not inside an AI agent session)

### Method 1: Development Mode (Recommended)

```bash
# 1. Clone and checkout develop
git clone https://github.com/roboticforce/sugar.git
cd sugar
git checkout develop

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install Sugar in development mode with dev dependencies
pip install -e ".[dev,test,github]"

# 4. Verify installation
sugar --version
sugar help

# 5. Test in a project
cd /path/to/your/test/project
sugar init
sugar add "Test task" --type feature --priority 3
sugar run --dry-run --once
```

**Benefits:**
- `sugar` command available in venv
- Source code changes reflected immediately
- Easy to uninstall: `pip uninstall sugarai`

### Method 2: Test Latest Develop (Without Cloning)

```bash
# Install directly from develop branch
pip install --force-reinstall git+https://github.com/roboticforce/sugar.git@develop
```

## Development Workflow

### Creating a Feature Branch

```bash
# Start from develop
git checkout develop
git pull origin develop

# Create feature branch
git checkout -b feature/my-new-feature

# Make changes, commit
git add .
git commit -m "Add my new feature"

# Push and create PR targeting develop
git push -u origin feature/my-new-feature
```

### Creating a Bug Fix Branch

```bash
git checkout develop
git pull origin develop
git checkout -b bugfix/fix-something
# ... fix, commit, push, PR to develop
```

### Running Tests

```bash
# In Sugar repository directory
cd /path/to/sugar

# Run all tests
pytest

# With coverage
pytest --cov=sugar --cov-report=term-missing

# Run specific tests
pytest tests/test_cli.py -v
pytest tests/test_core_loop.py -v

# Run only unit tests
pytest -m unit

# Run integration tests
pytest -m integration
```

### Code Quality Checks

```bash
# Format code
black sugar/

# Sort imports
isort sugar/

# Lint
flake8 sugar/

# Type checking
mypy sugar/

# Security scan
bandit -r sugar/
```

## Testing Steps

### 1. Verify Environment

```bash
# Check Sugar installation
sugar --version
which sugar

# Check AI agent CLI (if using Claude Code)
claude --version
```

### 2. Create Test Project

```bash
# Create isolated test directory
mkdir ~/sugar-test-project
cd ~/sugar-test-project

# Initialize Git
git init
echo "# Sugar Test Project" > README.md
git add README.md
git commit -m "Initial commit"
```

### 3. Initialize and Test Sugar

```bash
# Initialize Sugar
sugar init

# Verify structure
ls -la .sugar/
cat .sugar/config.yaml

# Add test tasks
sugar add "Test feature" --type feature --priority 3
sugar add "Test bug fix" --type bug_fix --urgent

# Check queue
sugar list
sugar status

# Test execution (safe mode)
sugar run --dry-run --once

# Validate configuration
sugar run --validate
```

### 4. Test Discovery

```bash
# Create sample error logs
mkdir -p logs/errors
echo '{"error": "Test error", "timestamp": "2024-01-15T10:30:00Z"}' > logs/errors/test_error.json

# Run discovery
sugar run --dry-run --once
```

## Version Numbering

Sugar uses [PEP 440](https://peps.python.org/pep-0440/) versioning:

| Branch | Version Format | Example |
|--------|---------------|---------|
| `develop` | `X.Y.Z.devN` | `3.4.4.dev3` |
| `main` | `X.Y.Z` | `3.4.4` |

After merging PRs to develop, bump the dev number:
```bash
# In pyproject.toml
version = "3.4.4.dev3"  # → "3.4.4.dev4"
```

## Common Issues

### "sugar: command not found"

```bash
# Check if venv is activated
which python  # Should show venv path

# Reinstall
pip uninstall sugarai
pip install -e .
```

### "AI agent CLI not found"

```bash
# Check installation
which claude  # or opencode, aider

# Or specify path in .sugar/config.yaml:
# claude:
#   command: "/full/path/to/claude"
```

### Virtual Environment Issues

```bash
# Recreate venv
deactivate
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev,test]"
```

## Updating Your Fork

```bash
# Add upstream if not already added
git remote add upstream https://github.com/roboticforce/sugar.git

# Fetch and merge latest develop
git fetch upstream
git checkout develop
git merge upstream/develop

# Update your feature branch
git checkout feature/my-feature
git rebase develop
```

## Cleanup

```bash
# Uninstall Sugar
pip uninstall sugarai

# Deactivate venv
deactivate

# Remove test projects
rm -rf ~/sugar-test-project
```

---

See [CONTRIBUTING.md](contributing.md) for contribution guidelines.
