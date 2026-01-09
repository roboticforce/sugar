# Contributing to Sugar

First off, thank you for considering contributing to Sugar! It's people like you that make Sugar such a great tool.

## Contributor License Agreement (CLA)

Before we can accept your contributions, you must sign our [Contributor License Agreement (CLA)](../CLA.md).

**Why a CLA?**
The CLA ensures that RoboticForce, Inc. has the rights to distribute your contributions under our license terms, including offering commercial licenses. This protects both you and the project.

**How to sign:**
When you open your first pull request, the CLA Assistant bot will comment with instructions. Simply reply with:

```
I have read the CLA Document and I hereby sign the CLA
```

This is a one-time requirement. Once signed, all your future contributions are automatically covered.

## Code of Conduct

This project and everyone participating in it is governed by our commitment to providing a welcoming and inspiring community for all.

## How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check the existing issues as you might find that you don't need to create one. When you are creating a bug report, please include as many details as possible using our bug report template.

### Suggesting Features

Feature requests are welcome! Please use our feature request template and provide as much detail as possible about your use case.

### Pull Requests

1. Fork the repo and create your branch from `main`
2. If you've added code that should be tested, add tests
3. If you've changed APIs, update the documentation
4. Ensure the test suite passes
5. Make sure your code follows the existing code style (we use Black for formatting)
6. Issue that pull request!

## Development Setup

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/sugar.git
cd sugar

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black sugar/ tests/
```

## Testing

We use pytest for testing. Please ensure all tests pass before submitting a PR:

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=sugar --cov-report=term-missing

# Run specific test file
pytest tests/test_cli.py
```

## Code Style

- We use [Black](https://github.com/psf/black) for Python code formatting
- Line length is 88 characters
- We use type hints where applicable
- Follow PEP 8 style guide

## Commit Messages

- Use clear and meaningful commit messages
- Start with a verb in present tense (e.g., "Add feature" not "Added feature")
- Reference issues and pull requests where relevant

Format:
```
type: brief description

Detailed explanation if needed

Fixes #123
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

## Plugin Development

If you're contributing to the Claude Code plugin:

1. Test your changes locally:
   ```bash
   # Link plugin for testing
   ln -s /path/to/sugar/.claude-plugin ~/.claude-plugins/sugar

   # Test in Claude Code
   /plugin reload sugar
   ```

2. Ensure all plugin components work:
   - Slash commands
   - Agents
   - Hooks
   - MCP server

3. Update plugin documentation in `.claude-plugin/README.md`

## Documentation

- Update the README.md if you change functionality
- Add docstrings to new functions and classes
- Update CHANGELOG.md for notable changes
- Keep examples up to date

## Release Process

Maintainers will handle releases. Version bumps follow [Semantic Versioning](https://semver.org/).

## Questions?

Feel free to ask questions in [GitHub Discussions](https://github.com/roboticforce/sugar/discussions) or open an issue.

## License

By contributing to Sugar, you agree to:

1. Sign the [Contributor License Agreement (CLA)](../CLA.md)
2. License your contributions under the project's license (see [LICENSE](../LICENSE))

The CLA grants RoboticForce, Inc. the rights to use, modify, and distribute your contributions, including under commercial licenses.

Thank you for contributing to Sugar! 🍰
