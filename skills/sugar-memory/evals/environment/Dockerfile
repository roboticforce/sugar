FROM python:3.12-slim

# NOTE: sugarai[memory] alone pulls mcp>=2.0 which breaks sugar/mcp/memory_server.py
# (mcp.server.fastmcp removed). The [mcp] extra pins mcp==1.26.0 which still has it.
RUN pip install --no-cache-dir 'sugarai[memory,mcp]'

# Seed the Sugar memory store so the memory MCP server finds project context.
# .sugar is at the task context root; inject_mcp.py is staged by SkillEvaluator
# under the skill tree, so pre_agent_setup runs it from there.
COPY .sugar /workspace/.sugar
WORKDIR /workspace