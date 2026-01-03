# Local Development Setup

How to set up Sugar for local development and testing before it's available on PyPI.

## 🚀 Quick Setup

### Prerequisites

- **Python 3.11+** installed
- **Claude Code CLI** installed and accessible (`claude --version` works)
- **Git** for cloning the repository
- **Regular terminal/shell** (not Claude Code session)

### Method 1: Development Mode Installation (Recommended)

This is the easiest way to test Sugar locally:

```bash
# 1. Clone Sugar repository
git clone https://github.com/roboticforce/sugar.git
cd sugar

# 2. Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install Sugar in development mode
pip install -e .

# 4. For GitHub integration, you have two options:
#    Option A: Use GitHub CLI (recommended)
gh auth login  # If you want to use GitHub CLI authentication

#    Option B: Use PyGithub with tokens (optional)
pip install --upgrade PyGithub>=1.59.0  # Only needed if using token auth

# 5. Verify installation
sugar --help
sugar help

# 5. Test in a project
cd /path/to/your/test/project
sugar init
sugar add "Test task" --type feature --priority 3
sugar run --dry-run --once
```

**Benefits:**
- ✅ `sugar` command available globally
- ✅ Source code changes reflected immediately
- ✅ Easy to uninstall: `pip uninstall sugar`
- ✅ Works exactly like PyPI package

## 🔧 Alternative Methods

### Method 2: Direct Python Execution

If you prefer not to install:

```bash
# 1. Clone and setup
git clone https://github.com/roboticforce/sugar.git
cd sugar
pip install -r requirements.txt

# 2. Run Sugar directly
python -m sugar.main help
python -m sugar.main init
python -m sugar.main add "Test task" --type feature
python -m sugar.main run --dry-run --once
```

### Method 3: Shell Script Wrapper

Create a convenient wrapper:

```bash
# 1. Clone Sugar
git clone https://github.com/roboticforce/sugar.git

# 2. Create wrapper script
cat > sugar-dev << 'EOF'
#!/bin/bash
cd /path/to/sugar
python -m sugar.main "$@"
EOF

# 3. Make executable and add to PATH
chmod +x sugar-dev
sudo mv sugar-dev /usr/local/bin/
# or add to your PATH: export PATH="/path/to/sugar-wrapper:$PATH"

# 4. Use it
sugar-dev init
sugar-dev help
```

## 🧪 Testing Steps

### 1. Verify Environment

```bash
# Ensure you're NOT in Claude Code
echo $CLAUDE_SESSION  # Should be empty or undefined

# Check Claude CLI
claude --version

# Check Sugar installation
sugar help  # Should show comprehensive help
which sugar  # Should show installation path
```

### 2. Create Test Project

```bash
# Create isolated test directory
mkdir ~/sugar-test-project
cd ~/sugar-test-project

# Initialize Git (optional, for more realistic testing)
git init
echo "# Sugar Test Project" > README.md
git add README.md
git commit -m "Initial commit"
```

### 3. Initialize Sugar

```bash
# Initialize Sugar in test project
sugar init

# Verify Sugar directory structure
ls -la .sugar/
cat .sugar/config.yaml  # Check configuration
```

### 4. Add Test Tasks

```bash
# Add various types of tasks
sugar add "Implement user authentication" --type feature --priority 4
sugar add "Fix memory leak in auth module" --type bug_fix --urgent
sugar add "Add unit tests for API" --type test --priority 3
sugar add "Refactor database queries" --type refactor --priority 2
sugar add "Update API documentation" --type documentation --priority 1

# Check task queue
sugar list
sugar status
```

### 5. Test Discovery

```bash
# Create sample error logs for discovery testing
mkdir -p logs/errors
echo '{"error": "Database connection failed", "timestamp": "2024-01-15T10:30:00Z", "severity": "CRITICAL"}' > logs/errors/db_error.json
echo '{"error": "API timeout", "timestamp": "2024-01-15T10:35:00Z", "severity": "ERROR"}' > logs/errors/api_error.json

# Test discovery (dry run)
sugar run --dry-run --once
```

### 6. Test Execution (Safe Mode)

```bash
# Always start with dry run
sugar run --dry-run --once

# Monitor what Sugar would do
tail -f .sugar/sugar.log  # In another terminal

# Check configuration validation
sugar run --validate
```

### 7. Full Testing (Optional)

```bash
# Only after dry-run testing looks good
# Edit .sugar/config.yaml and set: dry_run: false

# Then test real execution
sugar run --once  # Single cycle
# If that works: sugar run  # Continuous mode
```

## 🛠️ Development Workflow

### Making Changes to Sugar

```bash
# 1. Make changes to Sugar source code
cd /path/to/sugar
# Edit files in sugar/ directory

# 2. Changes are automatically available (if using pip install -e .)
sugar help  # Shows your changes immediately

# 3. Test changes
cd /path/to/test/project
sugar run --dry-run --once
```

### Running Sugar Tests

```bash
# In Sugar repository directory
cd /path/to/sugar

# Install test dependencies
pip install -e ".[dev]"

# Run tests
pytest
pytest --cov=sugar  # With coverage
pytest -v  # Verbose output

# Run specific tests
pytest tests/test_cli.py
pytest tests/test_core_loop.py
```

## 📋 Quick Setup Script

Save this as `setup-sugar-dev.sh`:

```bash
#!/bin/bash
set -e

echo "🚀 Setting up Sugar for local development..."

# Check prerequisites
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.11+"; exit 1
fi

if ! command -v claude &> /dev/null; then
    echo "⚠️  Claude CLI not found. Install from: https://docs.anthropic.com/claude-code"
    echo "   Continuing anyway - you can configure the path later..."
fi

# Clone Sugar if not already present
if [ ! -d "sugar" ]; then
    echo "📥 Cloning Sugar repository..."
    git clone https://github.com/roboticforce/sugar.git
fi

cd sugar

# Create virtual environment
echo "🐍 Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Sugar in development mode
echo "📦 Installing Sugar in development mode..."
pip install -e .

# Verify installation
echo "✅ Verifying installation..."
if sugar help &> /dev/null; then
    echo "✅ Sugar installed successfully!"
    echo ""
    echo "🎯 Next steps:"
    echo "1. Navigate to your project: cd /path/to/your/project"
    echo "2. Initialize Sugar: sugar init"
    echo "3. Add a task: sugar add 'Your first task' --type feature"
    echo "4. Test safely: sugar run --dry-run --once"
    echo "5. Get help anytime: sugar help"
    echo ""
    echo "💡 Sugar is now ready for local development!"
else
    echo "❌ Installation verification failed"
    exit 1
fi
```

Usage:
```bash
chmod +x setup-sugar-dev.sh
./setup-sugar-dev.sh
```

## 🚨 Common Issues

### "sugar: command not found"

```bash
# Check if pip installed correctly
pip show sugar

# Check if virtual environment is activated
which python  # Should show venv path

# Try reinstalling
pip uninstall sugar
pip install -e .
```

### "Claude CLI not found"

```bash
# Check Claude CLI installation
which claude
claude --version

# If not found, install Claude CLI first:
npm install -g @anthropic-ai/claude-code-cli

# Or specify path in .sugar/config.yaml:
# claude:
#   command: "/full/path/to/claude"
```

### Permission Issues

```bash
# Fix permissions on Sugar directory
chmod 755 .sugar/
chmod 644 .sugar/config.yaml

# Check file ownership
ls -la .sugar/
```

### Virtual Environment Issues

```bash
# Recreate virtual environment
deactivate  # If currently activated
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

## 📝 Development Tips

1. **Always use virtual environment** to avoid conflicts
2. **Start with `--dry-run`** for all testing
3. **Create disposable test projects** for experimentation
4. **Monitor logs** with `tail -f .sugar/sugar.log`
5. **Use `sugar help`** for quick reference
6. **Test incrementally** - one command at a time
7. **Check configuration** with `sugar run --validate`

## 🔄 Updating Sugar

```bash
# Pull latest changes from develop
cd /path/to/sugar
git checkout develop
git pull origin develop

# Reinstall (if using development mode)
pip install -e .

# Test updated version
sugar help
```

## 🧹 Cleanup

When you're done testing:

```bash
# Uninstall Sugar
pip uninstall sugar

# Deactivate virtual environment
deactivate

# Remove test projects
rm -rf ~/sugar-test-project

# Remove Sugar repository (if desired)
rm -rf /path/to/sugar
```

---

This setup allows you to test Sugar exactly as end users will experience it, while having the flexibility to modify and iterate on the code during development.