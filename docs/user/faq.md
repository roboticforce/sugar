# Frequently Asked Questions

## General

### What is Sugar?

Sugar is an AI-powered autonomous development system that works with Claude to provide 24/7 development capabilities. It manages a queue of tasks and autonomously implements features, fixes bugs, writes tests, and more.

### How is Sugar different from using Claude directly?

Sugar adds:
- **Task queue management** - Prioritize and track multiple tasks
- **Autonomous operation** - Works continuously without human intervention
- **Quality gates** - Security enforcement and audit trails
- **Work discovery** - Automatically finds issues from logs, code quality, GitHub
- **Integration** - Works with your existing workflow (Git, CI/CD, GitHub)

### Is Sugar free?

Self-hosted Sugar is completely free. You only pay for Claude API usage directly to Anthropic. SaaS pricing starts with a free tier. See [Billing](billing.md) for details.

## Installation

### What are the requirements?

- Python 3.11 or higher
- Claude Code CLI installed
- Git (for project management)

### How do I install Sugar?

```bash
pip install sugarai
cd your-project
sugar init
```

See the [Installation Guide](installation-guide.md) for detailed instructions.

### Sugar command not found after installation

Make sure the pip bin directory is in your PATH:

```bash
# Add to ~/.bashrc or ~/.zshrc
export PATH="$HOME/.local/bin:$PATH"
```

## Configuration

### Where is the configuration stored?

Sugar stores configuration in `.sugar/config.yaml` in your project directory.

### How do I reset configuration?

```bash
rm -rf .sugar/
sugar init
```

### Can I use Sugar with multiple projects?

Yes. Each project has its own `.sugar/` directory. Sugar is project-local by design.

## Usage

### How do I add a task?

```bash
# Basic task
sugar add "Implement user login"

# With options
sugar add "Fix auth bug" --type bug_fix --priority 4 --urgent
```

### How do I run Sugar in the background?

```bash
# Run continuously
sugar run &

# Or use nohup
nohup sugar run > sugar.log 2>&1 &

# Or use screen/tmux
screen -S sugar -d -m sugar run
```

### What's the difference between dry-run and normal mode?

- **Dry-run** (`--dry-run`): Shows what would be done without making changes
- **Normal mode**: Actually implements tasks and modifies files

Always test with dry-run first:
```bash
sugar run --dry-run --once
```

### How do I stop Sugar?

```bash
# If running in foreground
Ctrl+C

# If running in background
pkill -f "sugar run"
```

## Safety

### Is Sugar safe to use?

Sugar includes multiple safety features:
- **Dry-run mode** (default) - Test without changes
- **Quality gates** - Block access to sensitive files
- **Protected paths** - System directories excluded
- **Audit logging** - Track all operations

### What files are protected?

By default:
- `.env` files (credentials)
- `*.pem`, `*.key` files (certificates)
- `credentials.json`, `secrets.*`
- System paths (`/etc`, `/usr`, etc.)

Add more in config:
```yaml
sugar:
  agent:
    quality_gates:
      protected_files:
        - "*.pem"
        - "production.config"
```

### Can Sugar delete files?

Sugar can modify files but is restricted from dangerous operations. Configure blocked commands:

```yaml
sugar:
  agent:
    quality_gates:
      blocked_commands:
        - "rm -rf"
        - "sudo"
```

## Troubleshooting

### Sugar is stuck on a task

1. Check the logs: `cat .sugar/sugar.log`
2. Increase timeout in config
3. Kill and restart: `pkill -f sugar && sugar run`

### No tasks being processed

Check:
1. Tasks exist: `sugar list`
2. Tasks have correct status: `sugar list --status pending`
3. Sugar is initialized: `ls .sugar/`

### Claude API errors

- **Rate limited**: Sugar retries automatically. Check your API tier.
- **Invalid key**: Verify `ANTHROPIC_API_KEY` is set correctly.
- **Timeout**: Increase timeout in config.

### "Claude CLI not found"

Install Claude Code CLI:
```bash
npm install -g @anthropic-ai/claude-code-cli
claude --version
```

## Integration

### Can Sugar create pull requests?

Yes, configure GitHub integration:
```yaml
sugar:
  github:
    enabled: true
    create_prs: true
    auto_merge: false
```

### Does Sugar work with Goose?

Yes. Sugar provides MCP (Model Context Protocol) server support for Goose and other MCP clients.

**Option 1: Built-in Python MCP Server**
```bash
# Install with MCP dependencies
pip install sugarai[mcp]

# Start the server
sugar mcp serve --port 8080
```

**Option 2: Standalone TypeScript MCP Server**

For npm-based installations, see `packages/mcp-server/` in the Sugar repository.

### Can I use Sugar with my CI/CD?

Yes. Add Sugar to your pipeline:
```yaml
# GitHub Actions example
- name: Run Sugar analysis
  run: |
    pip install sugarai
    sugar init
    sugar run --dry-run --once
```

## Agent SDK (v3.0)

### What changed in Sugar 3.0?

Sugar 3.0 introduces:
- Native Claude Agent SDK integration
- Quality gate hooks for security
- Observable execution (track every tool use)
- Automatic retry logic for transient errors

See [Agent SDK](agent-sdk.md) for details.

### How do I use the new SDK executor?

It's the default in 3.0. To explicitly set:
```yaml
sugar:
  claude:
    executor: sdk  # or "legacy" for v2
```

### Are v2 configurations compatible?

Yes. Sugar 3.0 is backwards compatible with v2 configurations.

## Getting Help

### Where can I report bugs?

Open an issue on [GitHub](https://github.com/roboticforce/sugar/issues).

### How do I request a feature?

Open a discussion on [GitHub Discussions](https://github.com/roboticforce/sugar/discussions).

### Is there community support?

- GitHub Discussions
- Email: contact@roboticforce.io

For Enterprise support, see [Billing](billing.md#enterprise-contact).
