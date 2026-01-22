# Quick Start Guide

Get Sugar up and running in your project in just a few minutes!

💡 **Need help anytime?** Run `sugar help` for a complete overview and quick reference guide.

⚠️ **Important:** Run Sugar in a regular terminal/shell, **not** within a Claude Code session. Sugar will call Claude Code CLI as needed.

## Prerequisites

Before installing Sugar, make sure you have:

- **Python 3.11 or higher**
- **Claude Code CLI installed** ([installation guide](https://docs.anthropic.com/en/docs/claude-code))
- **Git** (for project management)

### Install Claude Code CLI

```bash
# Install via npm (recommended)
npm install -g @anthropic-ai/claude-code-cli

# Verify installation
claude --version
```

## Installation

### Option 1: Install from PyPI (Recommended)

```bash
pip install sugarai
```

### Option 2: Install from Source (For Development)

```bash
# Clone the repository
git clone https://github.com/roboticforce/sugar.git
cd sugar

# Install Sugar
pip install -e .
```

## Basic Usage

### 1. Initialize Sugar in Your Project

Navigate to your project directory and initialize Sugar:

```bash
cd /path/to/your/project
sugar init
```

This creates:
- `.sugar/` directory with configuration and database
- `.sugar/config.yaml` - customizable settings
- `logs/errors/` - directory for error log monitoring

### 2. Enable MCP Features in Claude Code (Recommended)

To get the most out of Sugar, add the MCP server to Claude Code:

```bash
claude mcp add sugar -- sugar mcp memory
```

This gives Claude Code access to your project's memory - decisions, preferences, error patterns, and more.

### 3. Add Your First Task

Sugar accepts tasks in **two ways**:

**📝 Manual Task Addition (CLI)**
```bash
# Add a feature task
sugar add "Implement user authentication" --type feature --priority 4

# Add an urgent bug fix
sugar add "Fix memory leak in auth module" --type bug_fix --urgent

# Add a test task
sugar add "Add unit tests for payments" --type test --priority 3
```

**🤖 Autonomous Discovery (Automatic)**
Sugar will also automatically discover work from:
- Error logs in `logs/errors/`
- Code quality analysis
- Missing test coverage
- GitHub issues (when configured)

### 4. Check Status

```bash
# View system status
sugar status

# List all tasks
sugar list

# View specific task details
sugar view TASK_ID
```

### 5. Run Sugar

```bash
# Test run (safe mode - no actual changes)
sugar run --dry-run --once

# Start continuous autonomous development
sugar run
```

## Configuration

### Basic Configuration

Edit `.sugar/config.yaml` to customize Sugar for your project:

```yaml
sugar:
  # Core settings
  dry_run: true  # Set to false when ready for real execution
  loop_interval: 300  # 5 minutes between cycles
  max_concurrent_work: 3
  
  # Claude CLI path (auto-detected)
  claude:
    command: "/path/to/claude"
    timeout: 1800  # 30 minutes max per task
    
  # Work discovery
  discovery:
    error_logs:
      enabled: true
      paths: ["logs/errors/"]
    
    code_quality:
      enabled: true
      source_dirs: ["src", "lib", "app"]
```

### Safety First

Sugar starts in **dry-run mode** by default:
- ✅ Safe to test and configure
- ✅ Shows what would be done without making changes
- ✅ Set `dry_run: false` when ready for autonomous operation

## Next Steps

1. **Customize Configuration** → See [Configuration Best Practices](configuration-best-practices.md)
2. **Explore Commands** → See [CLI Reference](cli-reference.md)  
3. **Real Examples** → See [Examples](examples.md)
4. **Need Help?** → See [Troubleshooting](troubleshooting.md)

## Tips

💡 **Start Small**: Begin with `--dry-run --once` to see what Sugar would do  
💡 **Monitor Logs**: Check `.sugar/sugar.log` for detailed activity  
💡 **Project Isolation**: Each project gets its own Sugar instance  
💡 **Safety First**: Sugar excludes system paths and has multiple safety checks  

Ready to supercharge your development workflow? 🚀