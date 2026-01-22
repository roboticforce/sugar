# Sugar - Library Installation and Usage Guide

## Overview

Sugar is an AI-powered autonomous development system packaged as a reusable Python library that can be installed in any project to provide 24/7 autonomous development capabilities. Each project gets its own isolated Sugar instance with project-specific configuration, database, and logs.

## Prerequisites

**Required:** Sugar requires Claude Code CLI to be installed and accessible on your system.

### Install Claude Code CLI

1. **Install Claude Code CLI** (if not already installed):
   ```bash
   # Install via npm (recommended)
   npm install -g @anthropic-ai/claude-code-cli
   
   # Or follow official installation guide
   # Visit: https://docs.anthropic.com/en/docs/claude-code
   ```

2. **Verify installation:**
   ```bash
   claude --version
   ```

3. **Ensure Claude CLI is accessible:**
   - Add to your PATH, or
   - Note the full path for Sugar configuration

**System Requirements:**
- Python 3.11 or higher
- Claude Code CLI installed and accessible
- Git (for project management)
- Internet connection (for Claude API access)

## Installation

### Option 1: PyPI Installation (Recommended)

```bash
pip install sugarai
```

### Option 2: Install from Source (For Development)

```bash
# Clone or download the sugar package
git clone https://github.com/roboticforce/sugar.git

# Install in development mode (allows for easy updates)
cd sugar
pip install -e .

# Or install directly without cloning
pip install -e git+https://github.com/roboticforce/sugar.git#egg=sugar
```

## Quick Start

### 1. Initialize Sugar in Your Project

Navigate to any project directory and initialize Sugar:

```bash
cd /path/to/your/project
sugar init
```

This creates:
- `.sugar/` directory with project-specific configuration and database
- `.sugar/config.yaml` - customizable configuration file
- `.sugar/sugar.db` - SQLite database for task storage
- `.sugar/logs/` - Sugar-specific log directory
- `logs/errors/` directory with sample error log for testing
- Auto-detects Claude CLI location

### 2. Enable MCP Features in Claude Code (Recommended)

To get the most out of Sugar, add the MCP server to Claude Code:

```bash
claude mcp add sugar -- sugar mcp memory
```

This gives Claude Code access to your project's memory system - decisions, preferences, error patterns, and more. See the [Memory System Guide](memory.md) for details.

**Claude CLI Detection:**
Sugar will automatically search for Claude CLI in common locations:
- `claude` (in PATH)
- `/usr/local/bin/claude`
- `/opt/homebrew/bin/claude`
- `~/.claude/local/claude`
- `~/.local/bin/claude`

If Claude CLI is not found automatically, you'll see a warning and can manually configure the path in `.sugar/config.yaml`.

### 2. Customize Configuration (Optional)

Edit `.sugar/config.yaml` to match your project:

```yaml
sugar:
  # Core Loop Settings
  loop_interval: 300  # 5 minutes between cycles
  max_concurrent_work: 3  # Execute multiple tasks per cycle
  dry_run: true       # Set to false when ready for real execution
  
  # Claude Code Integration
  claude:
    command: "/path/to/claude"  # Auto-detected, but can be customized
    timeout: 1800       # 30 minutes max per task
    context_file: ".sugar/context.json"
    
  # Work Discovery
  discovery:
    error_logs:
      enabled: true
      paths:
        - "logs/errors/"      # Your error log directories
        - "logs/feedback/"
        - ".sugar/logs/"
      patterns: ["*.json", "*.log"]
      max_age_hours: 24
    
    code_quality:
      enabled: true
      root_path: "."          # Project root to analyze
      file_extensions: [".py", ".js", ".ts", ".jsx", ".tsx"]
      source_dirs: ["src", "lib", "app"]  # Your source directories
      excluded_dirs: ["node_modules", ".git", "__pycache__", "venv", ".venv", ".sugar"]
      max_files_per_scan: 50
      
    test_coverage:
      enabled: true
      root_path: "."
      source_dirs: ["src", "lib", "app", "api", "server"]
      test_dirs: ["tests", "test", "__tests__", "spec"]
      
    github:
      enabled: false          # Enable for GitHub integration
      repo: "owner/repo"      # Your repository
      token: "ghp_..."        # GitHub token
      
  # Storage
  storage:
    database: ".sugar/sugar.db"  # Project-specific database
    backup_interval: 3600  # 1 hour
    
  # Safety
  safety:
    max_retries: 3
    excluded_paths: ["/System", "/usr/bin", "/etc", ".sugar"]
    
  # Logging
  logging:
    level: "INFO"
    file: ".sugar/sugar.log"  # Project-specific logs
```

### 3. Add Tasks

```bash
# Add various types of tasks
sugar add "Implement user authentication" --type feature --priority 4
sugar add "Fix memory leak in auth module" --type bug_fix --urgent
sugar add "Add unit tests for payments" --type test --priority 3
sugar add "Update API documentation" --type documentation --priority 2
sugar add "Refactor user service" --type refactor --priority 3
```

### 4. Monitor Status

**Note:** All Sugar commands are project-specific. Make sure you're in the correct project directory.

```bash
# Check system status and queue statistics (for current project)
sugar status

# List pending tasks (for current project)
sugar list --status pending

# List completed work (for current project)
sugar list --status completed --limit 10

# List all tasks with type filter (for current project)
sugar list --status all --type feature --limit 20

# View detailed task information
sugar view TASK_ID

# Update or modify existing tasks
sugar update TASK_ID --priority 5 --status active

# Remove unwanted tasks
sugar remove TASK_ID
```

### 5. Start Autonomous Operation

```bash
# Test with dry run first
sugar run --dry-run --once

# Start 24/7 autonomous operation
sugar run

# Run in background
nohup sugar run > sugar-autonomous.log 2>&1 &
```

## Multi-Project Usage

Sugar can be used across multiple projects simultaneously. Each project maintains complete isolation:

### Project A
```bash
cd /path/to/project-a
sugar init
sugar add "Build user dashboard" --type feature
sugar run &  # Start background operation
```

### Project B  
```bash
cd /path/to/project-b  
sugar init
sugar add "Fix payment processing bug" --type bug_fix --urgent
sugar run &  # Independent operation
```

### Project C
```bash
cd /path/to/project-c
sugar init  
sugar add "Add integration tests" --type test
sugar run &  # Separate autonomous loop
```

Each project will have:
- **Isolated configuration**: `.sugar/config.yaml`
- **Separate database**: `.sugar/sugar.db`
- **Independent logs**: `.sugar/sugar.log`
- **Project-specific discovery**: Scans only that project's files

## Directory Structure (Per Project)

```
your-project/
├── src/                    # Your project source
├── tests/                  # Your project tests  
├── logs/                   # Error/feedback logs (optional)
│   └── errors/
├── .sugar/                  # Sugar-specific files (isolated)
│   ├── config.yaml         # Project-specific config
│   ├── sugar.db            # Project-specific SQLite database
│   ├── sugar.log           # Project-specific logs
│   ├── context.json       # Claude Code session context
│   ├── logs/              # Sugar internal logs
│   └── backups/           # Automated database backups
└── .gitignore             # Add .sugar/ to ignore Sugar files
```

## Configuration Best Practices

### Exclude Sugar from Version Control

Add to your `.gitignore`:
```gitignore
# Sugar autonomous development system
.sugar/
```

### Project-Specific Settings

Customize `.sugar/config.yaml` for each project:

**Python Project:**
```yaml
discovery:
  code_quality:
    file_extensions: [".py"]
    excluded_dirs: ["venv", ".venv", "__pycache__", ".pytest_cache"]
  test_coverage:
    source_dirs: ["src", "app"]
    test_dirs: ["tests", "test"]
```

**JavaScript/Node Project:**
```yaml
discovery:
  code_quality:
    file_extensions: [".js", ".ts", ".jsx", ".tsx"]
    excluded_dirs: ["node_modules", "dist", "build"]
  test_coverage:
    source_dirs: ["src", "lib"]
    test_dirs: ["tests", "__tests__", "spec"]
```

**Full-Stack Project:**
```yaml
discovery:
  code_quality:
    file_extensions: [".py", ".js", ".ts", ".jsx", ".tsx"]
    excluded_dirs: ["node_modules", "venv", "dist", "build"]
  test_coverage:
    source_dirs: ["backend", "frontend/src", "api"]
    test_dirs: ["tests", "frontend/tests", "e2e"]
```

## Advanced Usage

### Custom Error Log Integration

Sugar monitors your error logs. Configure paths where your application writes errors:

```yaml
discovery:
  error_logs:
    paths:
      - "logs/errors/"           # Application error logs
      - "logs/feedback/"         # User feedback logs  
      - "monitoring/alerts/"     # Monitoring system alerts
      - "var/log/app/"          # System logs
    patterns:
      - "*.json"                # JSON error files
      - "*.log"                 # Plain text logs
      - "error_*.txt"           # Custom error formats
```

### GitHub Integration

Enable GitHub integration to discover issues and PRs:

```yaml
discovery:
  github:
    enabled: true
    repo: "yourorg/yourproject"
    token: "ghp_your_github_token"  # Create at github.com/settings/tokens
```

Sugar will:
- Monitor open issues for new development tasks
- Check PR feedback for improvement opportunities  
- Prioritize work based on issue labels and urgency

### Background Operation Management

```bash
# Start Sugar as a service
nohup sugar run > sugar.log 2>&1 &
echo $! > sugar.pid

# Stop Sugar
kill $(cat sugar.pid)

# Check if running
ps aux | grep "sugar run"

# Monitor real-time
tail -f .sugar/sugar.log
```

### Multiple Project Monitoring

**Important:** Sugar commands are project-specific, not global. Each `sugar status` command only shows information for the current project directory.

```bash
#!/bin/bash
# monitor_all_sugar.sh - Monitor all Sugar instances

echo "=== Sugar Status Across All Projects ==="

for project in ~/projects/*; do
  if [ -d "$project/.sugar" ]; then
    echo "📂 Project: $(basename $project)"
    cd "$project"
    sugar status | grep -E "(Total Tasks|Pending|Active|Completed)"
    echo
  fi
done
```

**Key Point:** You must be in the project directory (or specify `--config` path) for Sugar commands to work with that project's data.

## Troubleshooting

### Common Issues

**Sugar not finding Claude CLI:**
```bash
# Check if Claude CLI is accessible
claude --version

# If not found, install Claude CLI first:
npm install -g @anthropic-ai/claude-code-cli

# Or update config with full path:
# Edit .sugar/config.yaml to set correct path:
claude:
  command: "/full/path/to/claude"
```

**No work being discovered:**
```bash
# Check discovery paths exist
ls -la logs/errors/

# Verify configuration
sugar run --validate

# Test with sample error
echo '{"error": "test", "message": "sample error"}' > logs/errors/test.json
sugar run --once --dry-run
```

**Tasks not executing:**
```bash
# Verify dry_run setting in .sugar/config.yaml
sugar run --validate

# Check logs for errors
tail -f .sugar/sugar.log

# Test with single cycle
sugar run --once
```

### Performance Optimization

**For large projects:**
```yaml
discovery:
  code_quality:
    max_files_per_scan: 25  # Reduce for faster scans
    excluded_dirs: [        # Exclude more directories
      "node_modules", "venv", "dist", "build", 
      "coverage", "docs", "examples"
    ]
```

**For projects with many logs:**
```yaml
discovery:
  error_logs:
    max_age_hours: 12       # Only process recent errors
    patterns: ["*.json"]    # Limit to structured logs only
```

## Security Considerations

### Safe Defaults

Sugar starts in **dry-run mode** by default:
- Simulates execution without making real changes
- Safe to test and configure
- Set `dry_run: false` when ready for autonomous operation

### Path Safety

Sugar excludes system paths by default:
```yaml
safety:
  excluded_paths:
    - "/System"      # macOS system files
    - "/usr/bin"     # System binaries  
    - "/etc"         # System configuration
    - ".sugar"        # Sugar's own files
```

### Access Control

- Sugar database and logs are project-local
- No network access except GitHub API (if enabled)
- Uses your existing Claude CLI authentication
- Add `.sugar/` to `.gitignore` to avoid committing sensitive data

## Best Practices

### Development Workflow

1. **Start with dry-run**: Always test Sugar behavior before enabling real execution
2. **Monitor initially**: Watch the first few cycles to ensure proper task discovery
3. **Gradual enablement**: Enable discovery sources one at a time
4. **Regular status checks**: Monitor queue depth and task success rates
5. **Customize priorities**: Adjust task types and priorities for your workflow

### Team Usage

- **Individual instances**: Each team member can run Sugar on their local environment
- **Shared configuration**: Version control the template `.sugar/config.yaml` (without tokens)
- **Different priorities**: Each developer can prioritize different types of work
- **Coordination**: Use GitHub integration to avoid duplicate work

### Production Considerations

- **Staging first**: Test Sugar on staging/dev environments before production
- **Resource limits**: Configure appropriate timeouts and concurrency limits
- **Monitoring**: Set up log monitoring for Sugar operations  
- **Rollback plans**: Ensure you can quickly disable autonomous operation if needed

---

## Summary

Claude Sugar as a library provides:

✅ **Project Isolation** - Each project gets independent Sugar instance with isolated database and logs  
✅ **Easy Installation** - Simple `sugar init` setup in any project  
✅ **Auto-Configuration** - Detects Claude CLI and sets sensible defaults  
✅ **Cross-Platform** - Works on any system with Python 3.11+ and Claude CLI  
✅ **Safe Defaults** - Starts in dry-run mode for testing  
✅ **Comprehensive CLI** - Full task management with add, list, view, update, remove commands  
✅ **Flexible Discovery** - Configurable error logs, code analysis, test coverage, GitHub integration  
✅ **No Conflicts** - Uses `.sugar/` directory to avoid naming conflicts  
✅ **Multi-Project** - Run autonomous development across multiple projects simultaneously  
✅ **Persistent Storage** - SQLite database with automated backups and task history  

This enables truly autonomous development at scale - install once, configure per project, and let Claude Code work 24/7 across your entire development portfolio.