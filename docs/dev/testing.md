# Testing Guide

This guide covers running and writing tests for Sugar.

## Quick Start

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=sugar --cov-report=term-missing
```

## Test Structure

```
tests/
├── test_v3_benchmarks.py    # V2 vs V3 executor comparison
├── test_goose_integration.py # MCP CLI compatibility
├── test_task_types.py       # Task type management
├── test_profiles.py         # Workflow profiles
├── plugin/                  # Plugin system tests
│   └── test_structure.py
└── conftest.py              # Shared fixtures
```

## Running Tests

### All Tests

```bash
pytest tests/ -v
```

### Specific Test File

```bash
pytest tests/test_v3_benchmarks.py -v
```

### Specific Test Class

```bash
pytest tests/test_v3_benchmarks.py::TestV3QualityGates -v
```

### Specific Test Method

```bash
pytest tests/test_v3_benchmarks.py::TestV3QualityGates::test_security_violations_tracked -v
```

### With Output

```bash
pytest tests/ -v -s  # Show print statements
pytest tests/ -v --tb=short  # Short traceback
```

## Test Categories

### Unit Tests

```bash
pytest tests/ -v -m unit
```

### Integration Tests

```bash
pytest tests/ -v -m integration
```

### Slow Tests

```bash
pytest tests/ -v -m slow
```

## Coverage

### Generate Coverage Report

```bash
pytest tests/ -v --cov=sugar --cov-report=term-missing
```

### HTML Coverage Report

```bash
pytest tests/ --cov=sugar --cov-report=html
open htmlcov/index.html
```

### Coverage Thresholds

The CI pipeline enforces minimum coverage. Check current coverage:

```bash
pytest tests/ --cov=sugar --cov-report=term --cov-fail-under=70
```

## Writing Tests

### Test File Naming

- Test files: `test_*.py` or `*_test.py`
- Test functions: `test_*`
- Test classes: `Test*`

### Basic Test

```python
def test_task_creation():
    """Test creating a new task."""
    task = create_task("Test task", type="feature", priority=3)

    assert task.title == "Test task"
    assert task.type == "feature"
    assert task.priority == 3
```

### Async Tests

```python
import pytest

@pytest.mark.asyncio
async def test_agent_execution():
    """Test async agent execution."""
    from sugar.agent.base import SugarAgent, SugarAgentConfig

    config = SugarAgentConfig(model="claude-sonnet-4-20250514")
    agent = SugarAgent(config)

    await agent.start_session()
    assert agent._session_active is True

    await agent.end_session()
    assert agent._session_active is False
```

### Fixtures

```python
import pytest

@pytest.fixture
def sample_work_item():
    """Sample work item for testing."""
    return {
        "id": "test-123",
        "type": "feature",
        "title": "Test task",
        "description": "Test description",
        "priority": 3,
        "status": "pending",
    }

def test_work_item_processing(sample_work_item):
    """Test processing a work item."""
    result = process_work_item(sample_work_item)
    assert result["work_item_id"] == "test-123"
```

### Mocking

```python
from unittest.mock import AsyncMock, MagicMock, patch

def test_with_mock():
    """Test with mocked dependencies."""
    with patch("sugar.executor.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Success",
        )

        result = execute_command("test")

        assert result.success is True
        mock_run.assert_called_once()

@pytest.mark.asyncio
async def test_async_mock():
    """Test with async mock."""
    mock_query = AsyncMock(return_value={"content": "test"})

    with patch("sugar.agent.base.query", mock_query):
        result = await agent.execute("test prompt")

    mock_query.assert_called_once()
```

### Parametrized Tests

```python
import pytest

@pytest.mark.parametrize("priority,expected", [
    (1, "low"),
    (3, "medium"),
    (5, "urgent"),
])
def test_priority_labels(priority, expected):
    """Test priority label mapping."""
    assert get_priority_label(priority) == expected
```

## Linting and Formatting

### Black (Formatting)

```bash
# Check formatting
black --check sugar tests

# Fix formatting
black sugar tests
```

### Flake8 (Linting)

```bash
# Run linter
flake8 sugar --count --select=E9,F63,F7,F82 --show-source --statistics

# Full check (warnings)
flake8 sugar --count --exit-zero --max-complexity=10 --max-line-length=88 --statistics
```

### isort (Import Sorting)

```bash
# Check imports
isort --check-only sugar tests

# Fix imports
isort sugar tests
```

### mypy (Type Checking)

```bash
# Run type checker
mypy sugar

# Strict mode
mypy sugar --strict
```

## CI Integration

Tests run automatically on:

- Push to `main` or `develop`
- Pull requests to `main` or `develop`
- Release publishing

### CI Matrix

Tests run across:
- OS: Ubuntu, Windows, macOS
- Python: 3.11, 3.12, 3.13

### Pre-commit Hooks

Install pre-commit hooks:

```bash
pip install pre-commit
pre-commit install
```

This runs linting and formatting before each commit.

## Debugging Tests

### Verbose Output

```bash
pytest tests/ -v -s --tb=long
```

### Debug with pdb

```python
def test_debugging():
    import pdb; pdb.set_trace()  # Breakpoint
    result = some_function()
    assert result is not None
```

Run with:

```bash
pytest tests/test_file.py::test_debugging -v -s
```

### Last Failed Tests

```bash
# Run only tests that failed last time
pytest tests/ --lf

# Run last failed first
pytest tests/ --ff
```

## Test Data

### Fixtures Directory

Place test data in `tests/fixtures/`:

```
tests/fixtures/
├── sample_config.yaml
├── sample_tasks.json
└── mock_responses/
    └── claude_response.json
```

### Loading Test Data

```python
import json
from pathlib import Path

@pytest.fixture
def sample_response():
    fixture_path = Path(__file__).parent / "fixtures" / "mock_responses" / "claude_response.json"
    return json.loads(fixture_path.read_text())
```

## Next Steps

- [Contributing Guide](contributing.md) - Contribution guidelines
- [Local Development](local-development.md) - Development setup
- [Release Process](release-process.md) - How releases work
