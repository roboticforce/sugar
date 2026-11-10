# Agent SDK Integration

Sugar 3.0 introduces native integration with the Claude Agent SDK, replacing the subprocess-based CLI wrapper for improved performance, visibility, and control.

## Overview

The Agent SDK executor provides:

- **Direct API calls** - No subprocess overhead
- **Quality gates** - Pre/post hook security enforcement
- **Observable execution** - Track every tool use and file modification
- **Retry logic** - Automatic retry for transient errors
- **Streaming responses** - Real-time progress updates

## Configuration

### Selecting the Executor

Configure the executor in `.sugar/config.yaml`:

```yaml
sugar:
  claude:
    # V3 - Native SDK (default in 3.0)
    executor: sdk

    # V2 - Legacy subprocess wrapper
    # executor: legacy
```

### Agent Configuration

```yaml
sugar:
  agent:
    # Model selection
    model: claude-sonnet-4-20250514
    max_tokens: 8192

    # Permission mode
    # - default: Ask for confirmation
    # - acceptEdits: Auto-accept file edits
    # - bypassPermissions: Full autonomy (use with caution)
    permission_mode: acceptEdits

    # Timeout per task (seconds)
    timeout: 300

    # Quality gates
    quality_gates:
      enabled: true
      protected_files:
        - ".env"
        - "*.pem"
        - "credentials.json"
      blocked_commands:
        - "rm -rf /"
        - "sudo"
```

## Quality Gates

Quality gates provide security enforcement through hooks that run before and after tool execution.

### Pre-Tool Hooks

Validate operations before they execute:

```python
from sugar.agent.hooks import QualityGateHooks

hooks = QualityGateHooks({
    "protected_files": [".env", "*.key"],
    "blocked_commands": ["sudo", "rm -rf"],
})

# Hook blocks access to protected files
# Returns: {"allowed": False, "reason": "Protected file access blocked"}
```

### Post-Tool Hooks

Audit operations after execution:

```python
# Post-tool hooks track:
# - Files modified
# - Commands executed
# - Security violations
summary = hooks.get_execution_summary()
# {"total_tool_executions": 5, "blocked_operations": 1, "security_violations": 0}
```

## Using the Agent Directly

For advanced use cases, you can use `SugarAgent` directly:

```python
import asyncio
from sugar.agent.base import SugarAgent, SugarAgentConfig

async def run_task():
    config = SugarAgentConfig(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        permission_mode="acceptEdits",
        quality_gates_enabled=True,
    )

    agent = SugarAgent(config)

    async with agent:
        response = await agent.execute(
            "Create a Python function that calculates factorial",
            task_context="Working on math utilities"
        )

        print(f"Success: {response.success}")
        print(f"Files modified: {response.files_modified}")
        print(f"Execution time: {response.execution_time:.2f}s")

asyncio.run(run_task())
```

## Response Structure

Agent execution returns an `AgentResponse`:

```python
@dataclass
class AgentResponse:
    success: bool              # Task completed successfully
    content: str               # Response text
    tool_uses: List[Dict]      # List of tool invocations
    files_modified: List[str]  # Files created or edited
    execution_time: float      # Total time in seconds
    error: Optional[str]       # Error message if failed
    quality_gate_results: Dict # Security audit summary
```

## Retry Logic

The agent automatically retries transient errors:

- Rate limit errors (429)
- Timeout errors
- Connection errors
- Service unavailable (503)

Configure retry behavior:

```yaml
sugar:
  agent:
    max_retries: 3
    retry_base_delay: 1.0  # Seconds
    retry_max_delay: 30.0  # Max backoff
```

## MCP Server Integration

The agent supports MCP (Model Context Protocol) servers:

```yaml
sugar:
  agent:
    mcp_servers:
      filesystem:
        command: npx
        args: ["-y", "@anthropic/mcp-server-filesystem"]
      github:
        command: npx
        args: ["-y", "@anthropic/mcp-server-github"]
```

## Migrating from V2

Sugar 3.0 maintains backwards compatibility. Existing configurations work without changes, but you can opt into new features:

| V2 Feature | V3 Equivalent |
|------------|---------------|
| ClaudeWrapper | AgentSDKExecutor |
| Subprocess calls | Native SDK |
| Limited visibility | Full tool tracking |
| No quality gates | Hook-based security |

To migrate:

1. Update Sugar: `pip install --upgrade sugarai`
2. Optionally set `executor: sdk` (default in 3.0)
3. Configure quality gates for enhanced security
4. Test with `sugar run --dry-run --once`

## Troubleshooting

### Agent Not Starting

```bash
# Verify SDK installation
python -c "from claude_agent_sdk import query; print('OK')"

# Check agent import
python -c "from sugar.agent.base import SugarAgent; print('OK')"
```

### Rate Limiting

If you encounter rate limits:

1. Increase `retry_max_delay` in config
2. Reduce concurrent tasks: `max_concurrent_work: 1`
3. Check your API tier limits

### Quality Gate Blocking

If operations are unexpectedly blocked:

```bash
# Check quality gate config
sugar status --verbose

# Review blocked files pattern
cat .sugar/config.yaml | grep -A5 protected_files
```

## Next Steps

- [Configuration Best Practices](configuration-best-practices.md) - Configuration patterns and exclusions
- [CLI Reference](cli-reference.md) - Command line usage
- [Troubleshooting](troubleshooting.md) - Common issues
