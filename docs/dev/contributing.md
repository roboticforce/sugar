# Contributing to Sugar

Thank you for your interest in contributing to Sugar! This guide will help you get started with contributing to the project.

## 🌿 Branching Model

Sugar uses **Gitflow** for development:

| Branch | Purpose |
|--------|---------|
| `main` | Production releases only - never commit directly |
| `develop` | Integration branch for all new work |
| `feature/*` | New features (branch from `develop`) |
| `bugfix/*` | Bug fixes (branch from `develop`) |
| `hotfix/*` | Urgent production fixes (branch from `main`) |

**All contributions should target the `develop` branch**, not `main`.

## 🚀 Quick Start

1. **Fork and Clone**
   ```bash
   git clone https://github.com/yourusername/sugar.git
   cd sugar
   git checkout develop  # Always work from develop
   ```

2. **Set up Development Environment**
   ```bash
   # Create virtual environment
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   
   # Install in development mode
   pip install -e ".[dev]"
   
   # Install pre-commit hooks
   pre-commit install
   ```

3. **Run Tests**
   ```bash
   pytest
   ```

4. **Make Changes and Submit PR**

## 🛠️ Development Setup

### Prerequisites

- **Python 3.11+**
- **Claude Code CLI** ([installation guide](https://docs.anthropic.com/claude-code))
- **Git**
- **Node.js** (for Claude CLI)

### Installation

```bash
# Clone your fork
git clone https://github.com/yourusername/sugar.git
cd sugar

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install development dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install

# Verify installation
sugar --version
pytest --version
```

## 🧪 Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=sugar --cov-report=html

# Run specific test file
pytest tests/test_cli.py

# Run specific test
pytest tests/test_cli.py::TestSugarInit::test_init_creates_sugar_directory

# Run tests in parallel (faster)
pytest -n auto
```

### Test Structure

- `tests/conftest.py` - Shared fixtures and configuration
- `tests/test_cli.py` - CLI command tests
- `tests/test_core_loop.py` - Core Sugar loop tests
- `tests/test_storage.py` - Database and storage tests
- `tests/test_*.py` - Additional test modules

### Writing Tests

```python
import pytest
from click.testing import CliRunner
from sugar.main import cli

def test_my_feature(cli_runner):
    """Test description following Google style."""
    result = cli_runner.invoke(cli, ['command', 'args'])
    assert result.exit_code == 0
    assert "expected output" in result.output
```

## 🏗️ Code Quality

### Pre-commit Hooks

We use pre-commit hooks to ensure code quality:

- **black** - Code formatting
- **flake8** - Linting
- **isort** - Import sorting
- **mypy** - Type checking
- **bandit** - Security scanning

```bash
# Run all pre-commit hooks
pre-commit run --all-files

# Run specific hook
pre-commit run black --all-files
```

### Manual Quality Checks

```bash
# Format code
black sugar/ tests/

# Sort imports
isort sugar/ tests/

# Lint code
flake8 sugar/ tests/

# Type checking
mypy sugar/

# Security scan
bandit -r sugar/
```

## 🔄 Development Workflow

### 1. Choose an Issue

- Look at [open issues](https://github.com/roboticforce/sugar/issues)
- Good first issues are labeled `good-first-issue`
- Ask questions in the issue comments if unclear

### 2. Create a Branch

```bash
# Ensure you're on develop and up to date
git checkout develop
git pull origin develop

# Create your feature branch from develop
git checkout -b feature/your-feature-name
# or
git checkout -b bugfix/issue-description
```

### 3. Make Changes

- Write code following existing patterns
- Add tests for new functionality
- Update documentation if needed
- Follow the coding standards

### 4. Test Your Changes

```bash
# Run tests
pytest

# Run quality checks
pre-commit run --all-files

# Test CLI manually
sugar init
sugar add "test task"
sugar status
```

### 5. Commit Changes

```bash
git add .
git commit -m "feat: add new feature

Detailed description of what was changed and why.

Closes #123"
```

### 6. Push and Create PR

```bash
git push origin feature/your-feature-name
```

Then create a pull request on GitHub **targeting the `develop` branch** (not `main`).

## 📝 Coding Standards

### Python Style

- **PEP 8** compliance (enforced by black and flake8)
- **Type hints** for all functions and methods
- **Google-style docstrings**
- **Maximum line length**: 88 characters

### Example Function

```python
def add_task(
    title: str,
    task_type: str = "feature",
    priority: int = 3,
    description: Optional[str] = None,
) -> str:
    """Add a new task to the work queue.
    
    Args:
        title: Task title
        task_type: Type of task (feature, bug_fix, etc.)
        priority: Priority level 1-5
        description: Optional detailed description
        
    Returns:
        Task ID of the created task
        
    Raises:
        ValueError: If priority is not between 1-5
    """
    if not 1 <= priority <= 5:
        raise ValueError("Priority must be between 1-5")
    
    # Implementation here
    return task_id
```

### Commit Message Format

We use [Conventional Commits](https://conventionalcommits.org/):

```
type(scope): description

Longer description if needed

Closes #123
```

**Types:**
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `style:` - Code style changes (formatting)
- `refactor:` - Code refactoring
- `test:` - Adding or updating tests
- `chore:` - Maintenance tasks

## 🧩 Project Structure

```
sugar/
├── sugar/              # Main package
│   ├── core/          # Core loop and orchestration
│   ├── discovery/     # Work discovery modules
│   ├── executor/      # Claude CLI execution
│   ├── learning/      # Adaptive learning
│   ├── storage/       # Database and persistence
│   └── utils/         # Utility functions
├── tests/             # Test suite
├── docs/              # Documentation
│   ├── user/         # User documentation
│   └── dev/          # Developer documentation
├── config/           # Configuration files
└── .github/          # CI/CD workflows
```

## 🐛 Reporting Bugs

### Before Reporting

1. Search existing issues
2. Check if it's already fixed in the `develop` branch
3. Try to reproduce with minimal example

### Bug Report Template

```markdown
**Bug Description**
A clear description of what the bug is.

**To Reproduce**
Steps to reproduce the behavior:
1. Run `sugar init`
2. Run `sugar add "test"`
3. See error

**Expected Behavior**
What you expected to happen.

**Environment**
- OS: [e.g. macOS 14.0]
- Python: [e.g. 3.11.5]
- Sugar: [e.g. 0.1.0]
- Claude CLI: [e.g. 1.2.3]

**Additional Context**
Any other context about the problem.
```

## 💡 Feature Requests

### Before Requesting

1. Check if similar feature exists
2. Search existing feature requests
3. Consider if it fits Sugar's goals

### Feature Request Template

```markdown
**Is your feature request related to a problem?**
A clear description of what the problem is.

**Describe the solution you'd like**
A clear description of what you want to happen.

**Describe alternatives you've considered**
Other solutions you've considered.

**Additional context**
Any other context or screenshots.
```

## 📋 Pull Request Process

1. **Fork** the repository
2. **Checkout** the `develop` branch
3. **Create** a feature branch from `develop`
4. **Make** your changes
5. **Add** tests for new functionality
6. **Update** documentation if needed
7. **Run** tests and quality checks
8. **Submit** pull request **targeting `develop`**

### PR Checklist

- [ ] Tests pass (`pytest`)
- [ ] Code is formatted (`black`, `isort`)
- [ ] Code passes linting (`flake8`)
- [ ] Type checking passes (`mypy`)
- [ ] Security scan passes (`bandit`)
- [ ] Documentation updated if needed
- [ ] CHANGELOG.md updated for significant changes

## 🏆 Recognition

Contributors will be:

- Added to the contributors list
- Mentioned in release notes for significant contributions
- Given credit in documentation

## 📞 Getting Help

- **GitHub Issues** - For bugs and feature requests
- **GitHub Discussions** - For questions and general discussion
- **Email** - [contact@roboticforce.io](mailto:contact@roboticforce.io)

## 📄 License

By contributing, you agree that your contributions will be licensed under the same license as the project (MIT).

---

Thank you for contributing to Sugar! 🎉