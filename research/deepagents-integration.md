# DeepAgents Integration Research

**Date:** 2025-12-21
**Status:** Planning
**Priority Features:** Sub-agent spawning, Context summarization

---

## Source Analysis

**Repository:** https://github.com/langchain-ai/deepagents

DeepAgents is a LangChain/LangGraph agent framework implementing three core principles:
1. Planning (TODO-based task management)
2. Computer access (shell + filesystem)
3. Sub-agent delegation (isolated parallel execution)

### Why Not Direct Integration

- Sugar already uses Claude Agent SDK natively (no LangChain layer needed)
- DeepAgents would add unnecessary abstraction
- Sugar already has planning (WorkQueue), filesystem access (Claude tools), quality gates

### Patterns Worth Adopting

| Pattern | Priority | Complexity |
|---------|----------|------------|
| Sub-agent spawning | HIGH | Medium |
| Context summarization | HIGH | Low-Medium |
| Filesystem offloading | LOW | Low |

---

## Feature 1: Sub-Agent Spawning

### Problem Statement

Sugar currently executes tasks sequentially with a single agent. Complex tasks that could benefit from parallel execution (e.g., "refactor module A while writing tests for module B") are handled serially, increasing total execution time.

### DeepAgents Approach

```python
# DeepAgents uses a `task` tool to spawn sub-agents
def task(description: str, agent_type: str = "default") -> str:
    """Spawn a sub-agent with isolated context to handle a subtask."""
    subagent = create_subagent(agent_type)
    result = subagent.invoke(description)
    return result.summary  # Only summary returns to parent
```

Key characteristics:
- Each sub-agent has its own context window (isolation)
- Parent only receives summary (context efficiency)
- Sub-agents can run in parallel
- Failed sub-agents don't crash parent

### Proposed Sugar Implementation

#### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Parent Agent                          │
│                  (AgentSDKExecutor)                      │
└─────────────────────┬───────────────────────────────────┘
                      │
         ┌────────────┼────────────┐
         │            │            │
         ▼            ▼            ▼
    ┌─────────┐  ┌─────────┐  ┌─────────┐
    │SubAgent │  │SubAgent │  │SubAgent │
    │ Task A  │  │ Task B  │  │ Task C  │
    └────┬────┘  └────┬────┘  └────┬────┘
         │            │            │
         └────────────┼────────────┘
                      │
                      ▼
              ┌──────────────┐
              │ Aggregated   │
              │ Results      │
              └──────────────┘
```

#### New Components

**1. SubAgentManager** (`sugar/agent/subagent_manager.py`)

```python
from dataclasses import dataclass
from typing import Optional, List
import asyncio
from claude_code_sdk import query, AgentConfig

@dataclass
class SubAgentResult:
    task_id: str
    success: bool
    summary: str
    files_modified: List[str]
    execution_time: float
    error: Optional[str] = None

class SubAgentManager:
    """Manages spawning and coordination of sub-agents."""

    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.active_agents: dict[str, asyncio.Task] = {}

    async def spawn(
        self,
        task_description: str,
        agent_type: str = "general-purpose",
        context: Optional[str] = None,
        timeout: int = 300
    ) -> SubAgentResult:
        """Spawn a sub-agent to handle a specific subtask."""

        task_id = self._generate_task_id()

        async with self.semaphore:
            try:
                result = await asyncio.wait_for(
                    self._execute_subagent(task_id, task_description, agent_type, context),
                    timeout=timeout
                )
                return result
            except asyncio.TimeoutError:
                return SubAgentResult(
                    task_id=task_id,
                    success=False,
                    summary="",
                    files_modified=[],
                    execution_time=timeout,
                    error="Timeout exceeded"
                )

    async def spawn_parallel(
        self,
        tasks: List[dict],  # [{description, agent_type, context}, ...]
        timeout: int = 600
    ) -> List[SubAgentResult]:
        """Spawn multiple sub-agents in parallel."""

        coroutines = [
            self.spawn(
                task["description"],
                task.get("agent_type", "general-purpose"),
                task.get("context"),
                timeout=timeout // len(tasks)
            )
            for task in tasks
        ]

        return await asyncio.gather(*coroutines, return_exceptions=True)

    async def _execute_subagent(
        self,
        task_id: str,
        description: str,
        agent_type: str,
        context: Optional[str]
    ) -> SubAgentResult:
        """Execute a single sub-agent."""

        import time
        start_time = time.time()

        # Build prompt with isolation instructions
        prompt = self._build_isolated_prompt(description, context)

        # Configure sub-agent (isolated session)
        config = AgentConfig(
            model="claude-sonnet-4-20250514",
            agent_type=agent_type,
            # Sub-agents don't inherit parent session
        )

        files_modified = []
        content_parts = []

        try:
            async for event in query(prompt=prompt, config=config):
                if hasattr(event, 'content'):
                    content_parts.append(event.content)
                if hasattr(event, 'tool_use') and event.tool_use.get('name') in ['write', 'edit']:
                    files_modified.append(event.tool_use.get('file_path', 'unknown'))

            full_content = "".join(content_parts)
            summary = self._extract_summary(full_content)

            return SubAgentResult(
                task_id=task_id,
                success=True,
                summary=summary,
                files_modified=files_modified,
                execution_time=time.time() - start_time
            )
        except Exception as e:
            return SubAgentResult(
                task_id=task_id,
                success=False,
                summary="",
                files_modified=[],
                execution_time=time.time() - start_time,
                error=str(e)
            )

    def _build_isolated_prompt(self, description: str, context: Optional[str]) -> str:
        """Build prompt with isolation instructions."""

        prompt = f"""You are a specialized sub-agent handling a specific subtask.

TASK: {description}

IMPORTANT:
- Focus ONLY on this specific task
- Do not explore beyond what's needed
- Provide a clear summary of what you accomplished at the end
- If you encounter blockers, document them clearly

"""
        if context:
            prompt += f"CONTEXT FROM PARENT:\n{context}\n\n"

        prompt += "Begin working on this task now."
        return prompt

    def _extract_summary(self, content: str, max_length: int = 500) -> str:
        """Extract a summary from the full content."""

        # Look for explicit summary sections
        if "## Summary" in content:
            summary_start = content.index("## Summary")
            return content[summary_start:summary_start + max_length]

        # Otherwise take the last paragraph
        paragraphs = content.strip().split("\n\n")
        if paragraphs:
            return paragraphs[-1][:max_length]

        return content[:max_length]

    def _generate_task_id(self) -> str:
        import uuid
        return f"subagent-{uuid.uuid4().hex[:8]}"
```

**2. SubAgent Tool** (`sugar/agent/tools.py` addition)

```python
from claude_code_sdk import tool

@tool
async def spawn_subagent(
    task_description: str,
    agent_type: str = "general-purpose",
    context: str = ""
) -> str:
    """
    Spawn a sub-agent to handle a specific subtask independently.

    Use this when:
    - A task can be broken into independent pieces
    - You want parallel execution
    - A subtask needs specialized focus

    Args:
        task_description: Clear description of what the sub-agent should do
        agent_type: Type of agent (general-purpose, code-reviewer, tech-lead, etc.)
        context: Optional context to pass to the sub-agent

    Returns:
        Summary of what the sub-agent accomplished
    """
    from sugar.agent.subagent_manager import SubAgentManager

    manager = SubAgentManager()
    result = await manager.spawn(task_description, agent_type, context)

    if result.success:
        response = f"Sub-agent completed successfully.\n\n"
        response += f"Summary: {result.summary}\n"
        if result.files_modified:
            response += f"Files modified: {', '.join(result.files_modified)}\n"
        response += f"Execution time: {result.execution_time:.1f}s"
        return response
    else:
        return f"Sub-agent failed: {result.error}"


@tool
async def spawn_parallel_subagents(
    tasks: list[dict]
) -> str:
    """
    Spawn multiple sub-agents in parallel for independent tasks.

    Args:
        tasks: List of task objects with 'description', optional 'agent_type', optional 'context'

    Example:
        tasks = [
            {"description": "Write unit tests for auth module", "agent_type": "qa-engineer"},
            {"description": "Refactor database queries", "agent_type": "backend-developer"}
        ]

    Returns:
        Combined summary of all sub-agent results
    """
    from sugar.agent.subagent_manager import SubAgentManager

    manager = SubAgentManager(max_concurrent=3)
    results = await manager.spawn_parallel(tasks)

    response_parts = []
    success_count = sum(1 for r in results if isinstance(r, SubAgentResult) and r.success)

    response_parts.append(f"Completed {success_count}/{len(tasks)} subtasks.\n")

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            response_parts.append(f"\n[Task {i+1}] ERROR: {result}")
        elif result.success:
            response_parts.append(f"\n[Task {i+1}] SUCCESS: {result.summary[:200]}")
        else:
            response_parts.append(f"\n[Task {i+1}] FAILED: {result.error}")

    return "\n".join(response_parts)
```

**3. Integration with AgentSDKExecutor**

```python
# In sugar/executor/agent_sdk_executor.py

class AgentSDKExecutor(BaseExecutor):
    def __init__(self, ...):
        ...
        self.subagent_manager = SubAgentManager(max_concurrent=3)

        # Register sub-agent tools
        self.tools.extend([
            spawn_subagent,
            spawn_parallel_subagents
        ])
```

### Configuration

```yaml
# sugar.yaml
executor:
  type: sdk
  subagents:
    enabled: true
    max_concurrent: 3
    default_timeout: 300
    allowed_agent_types:
      - general-purpose
      - code-reviewer
      - qa-engineer
      - backend-developer
```

### Usage Examples

**Example 1: Parallel Test Writing**
```
User: "Add comprehensive tests for the auth and payment modules"

Agent thinks: These are independent modules, I can parallelize this.

Agent calls spawn_parallel_subagents([
    {"description": "Write unit tests for auth module", "agent_type": "qa-engineer"},
    {"description": "Write unit tests for payment module", "agent_type": "qa-engineer"}
])
```

**Example 2: Code Review + Fix**
```
User: "Review the PR and fix any issues you find"

Agent calls spawn_subagent(
    "Review code quality and identify issues",
    agent_type="code-reviewer"
)

# Gets list of issues back

Agent calls spawn_parallel_subagents([
    {"description": "Fix issue 1: ...", "agent_type": "backend-developer"},
    {"description": "Fix issue 2: ...", "agent_type": "backend-developer"}
])
```

---

## Feature 2: Context Summarization

### Problem Statement

Long-running Sugar tasks accumulate context, eventually hitting token limits or degrading performance. Claude's context window is large but not infinite, and costs scale with tokens.

### DeepAgents Approach

- SummarizationMiddleware triggers at 170k tokens
- Older messages are summarized and replaced
- Recent messages preserved in full

### Proposed Sugar Implementation

#### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Agent Execution                        │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│              ContextManager                              │
│  ┌─────────────────────────────────────────────────┐   │
│  │ Token Counter                                    │   │
│  │ - Tracks cumulative tokens                       │   │
│  │ - Triggers summarization at threshold            │   │
│  └─────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────┐   │
│  │ Summarizer                                       │   │
│  │ - Uses smaller/faster model (Haiku)              │   │
│  │ - Preserves key decisions and file changes       │   │
│  │ - Maintains execution continuity                 │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

#### New Components

**1. ContextManager** (`sugar/agent/context_manager.py`)

```python
from dataclasses import dataclass, field
from typing import List, Optional
import tiktoken

@dataclass
class Message:
    role: str  # user, assistant, system
    content: str
    token_count: int = 0
    timestamp: float = 0
    summarized: bool = False

@dataclass
class ContextSummary:
    content: str
    original_token_count: int
    summarized_token_count: int
    messages_summarized: int
    key_decisions: List[str]
    files_modified: List[str]

class ContextManager:
    """Manages conversation context with automatic summarization."""

    def __init__(
        self,
        token_threshold: int = 150_000,  # Trigger summarization
        preserve_recent: int = 10,        # Keep N recent messages
        summarization_model: str = "claude-3-haiku-20240307"
    ):
        self.token_threshold = token_threshold
        self.preserve_recent = preserve_recent
        self.summarization_model = summarization_model

        self.messages: List[Message] = []
        self.summaries: List[ContextSummary] = []
        self.total_tokens = 0

        # Try to use tiktoken, fallback to approximation
        try:
            self.encoder = tiktoken.encoding_for_model("gpt-4")
        except:
            self.encoder = None

    def add_message(self, role: str, content: str) -> None:
        """Add a message and check if summarization needed."""

        import time

        token_count = self._count_tokens(content)
        message = Message(
            role=role,
            content=content,
            token_count=token_count,
            timestamp=time.time()
        )

        self.messages.append(message)
        self.total_tokens += token_count

        if self.total_tokens > self.token_threshold:
            self._trigger_summarization()

    def get_context(self) -> List[dict]:
        """Get the current context for the agent."""

        context = []

        # Add summaries first
        for summary in self.summaries:
            context.append({
                "role": "system",
                "content": f"[CONTEXT SUMMARY]\n{summary.content}"
            })

        # Add non-summarized messages
        for msg in self.messages:
            if not msg.summarized:
                context.append({
                    "role": msg.role,
                    "content": msg.content
                })

        return context

    async def _trigger_summarization(self) -> None:
        """Summarize older messages to reduce context size."""

        # Keep recent messages, summarize the rest
        messages_to_summarize = self.messages[:-self.preserve_recent]

        if not messages_to_summarize:
            return

        # Extract key information
        key_decisions = self._extract_key_decisions(messages_to_summarize)
        files_modified = self._extract_files_modified(messages_to_summarize)

        # Build summarization prompt
        prompt = self._build_summarization_prompt(
            messages_to_summarize,
            key_decisions,
            files_modified
        )

        # Call summarization model
        summary_content = await self._call_summarization_model(prompt)

        # Calculate token savings
        original_tokens = sum(m.token_count for m in messages_to_summarize)
        summary_tokens = self._count_tokens(summary_content)

        # Create summary object
        summary = ContextSummary(
            content=summary_content,
            original_token_count=original_tokens,
            summarized_token_count=summary_tokens,
            messages_summarized=len(messages_to_summarize),
            key_decisions=key_decisions,
            files_modified=files_modified
        )

        self.summaries.append(summary)

        # Mark messages as summarized
        for msg in messages_to_summarize:
            msg.summarized = True

        # Update token count
        self.total_tokens = self.total_tokens - original_tokens + summary_tokens

    def _build_summarization_prompt(
        self,
        messages: List[Message],
        key_decisions: List[str],
        files_modified: List[str]
    ) -> str:
        """Build prompt for summarization model."""

        conversation = "\n\n".join([
            f"[{m.role.upper()}]: {m.content[:1000]}..."
            if len(m.content) > 1000 else f"[{m.role.upper()}]: {m.content}"
            for m in messages
        ])

        return f"""Summarize the following conversation context for an AI coding assistant.

IMPORTANT - Preserve:
1. Key decisions made
2. Files created or modified
3. Current task status
4. Any blockers or issues encountered
5. User preferences expressed

Known key decisions: {key_decisions}
Known files modified: {files_modified}

CONVERSATION:
{conversation}

Provide a concise summary (under 500 words) that captures the essential context needed to continue this work session."""

    async def _call_summarization_model(self, prompt: str) -> str:
        """Call the summarization model."""

        import anthropic

        client = anthropic.Anthropic()

        response = client.messages.create(
            model=self.summarization_model,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )

        return response.content[0].text

    def _extract_key_decisions(self, messages: List[Message]) -> List[str]:
        """Extract key decisions from messages."""

        decisions = []
        decision_keywords = ["decided", "will use", "going with", "chosen", "selected"]

        for msg in messages:
            if msg.role == "assistant":
                for keyword in decision_keywords:
                    if keyword in msg.content.lower():
                        # Extract sentence containing keyword
                        sentences = msg.content.split(". ")
                        for sentence in sentences:
                            if keyword in sentence.lower():
                                decisions.append(sentence.strip())
                                break

        return decisions[:10]  # Limit to 10 decisions

    def _extract_files_modified(self, messages: List[Message]) -> List[str]:
        """Extract files modified from messages."""

        import re

        files = set()
        # Pattern for file paths
        file_pattern = r'[`"\']?([a-zA-Z0-9_/\-\.]+\.(py|js|ts|yaml|json|md|txt))[`"\']?'

        for msg in messages:
            matches = re.findall(file_pattern, msg.content)
            for match in matches:
                if isinstance(match, tuple):
                    files.add(match[0])
                else:
                    files.add(match)

        return list(files)[:20]  # Limit to 20 files

    def _count_tokens(self, text: str) -> int:
        """Count tokens in text."""

        if self.encoder:
            return len(self.encoder.encode(text))
        else:
            # Rough approximation: ~4 chars per token
            return len(text) // 4

    def get_stats(self) -> dict:
        """Get context statistics."""

        return {
            "total_tokens": self.total_tokens,
            "message_count": len(self.messages),
            "summary_count": len(self.summaries),
            "tokens_saved": sum(
                s.original_token_count - s.summarized_token_count
                for s in self.summaries
            ),
            "threshold": self.token_threshold
        }
```

**2. Integration with SugarAgent**

```python
# In sugar/agent/base.py

class SugarAgent:
    def __init__(self, config: SugarAgentConfig):
        ...
        self.context_manager = ContextManager(
            token_threshold=config.context_threshold or 150_000,
            preserve_recent=config.preserve_recent_messages or 10
        )

    async def query(self, prompt: str) -> AsyncGenerator[AgentEvent, None]:
        # Add user message to context
        self.context_manager.add_message("user", prompt)

        # Get managed context
        context = self.context_manager.get_context()

        # Execute with managed context
        response_content = []
        async for event in self._execute_with_context(context):
            if hasattr(event, 'content'):
                response_content.append(event.content)
            yield event

        # Add assistant response to context
        self.context_manager.add_message("assistant", "".join(response_content))
```

### Configuration

```yaml
# sugar.yaml
agent:
  context:
    enabled: true
    token_threshold: 150000
    preserve_recent_messages: 10
    summarization_model: claude-3-haiku-20240307
```

---

## Implementation Phases

### Phase 1: Sub-Agent Spawning (Priority: HIGH)

1. Create `SubAgentManager` class
2. Add `spawn_subagent` and `spawn_parallel_subagents` tools
3. Integrate with `AgentSDKExecutor`
4. Add configuration options
5. Write tests
6. Update documentation

**Estimated complexity:** Medium
**Dependencies:** Claude Agent SDK

### Phase 2: Context Summarization (Priority: HIGH)

1. Create `ContextManager` class
2. Implement token counting
3. Implement summarization logic
4. Integrate with `SugarAgent`
5. Add configuration options
6. Write tests

**Estimated complexity:** Low-Medium
**Dependencies:** anthropic SDK (for Haiku calls)

### Phase 3: Filesystem Offloading (Priority: LOW - Future)

1. Design offloading strategy
2. Implement `.sugar/context/` storage
3. Add retrieval mechanisms
4. Integrate with context manager

---

## Open Questions

1. **Sub-agent isolation:** Should sub-agents share any state with parent?
2. **Billing:** How to track/limit sub-agent costs?
3. **Error handling:** How should parent handle partial sub-agent failures?
4. **Summarization trigger:** Token count vs message count vs time-based?
5. **Summary quality:** How to validate summaries don't lose critical info?

---

---

## Feature 2b: TOON Format for Structured Data

### What is TOON?

**TOON** (Token-Oriented Object Notation) is a compact data format designed specifically for LLMs.
- **Repository:** https://github.com/toon-format/toon
- **Token savings:** 30-60% vs JSON for tabular data

### How it works

TOON declares field names once (like CSV headers) instead of repeating them:

**JSON (verbose):**
```json
{"tasks": [
  {"id": 1, "title": "Fix bug", "status": "done"},
  {"id": 2, "title": "Add tests", "status": "pending"}
]}
```

**TOON (compact):**
```
tasks[2]{id,title,status}:
  1,Fix bug,done
  2,Add tests,pending
```

### Sugar Integration Points

| Component | Current Format | TOON Opportunity |
|-----------|---------------|------------------|
| Execution history | JSON | High - repetitive structure |
| Work queue context | JSON | High - tabular task list |
| File change lists | Plain text | Medium - structured list |
| Quality gate results | JSON | Medium - validation data |
| Context summaries | Plain text | Low - mostly prose |

### Implementation

**1. Add toon dependency:**
```bash
pip install toon-format  # or use the Python implementation
```

**2. Create TOON encoder utility** (`sugar/utils/toon_encoder.py`):

```python
from typing import List, Dict, Any

def to_toon(data: List[Dict[str, Any]], name: str = "items") -> str:
    """Convert list of dicts to TOON format."""
    if not data:
        return f"{name}[0]{{}}:"

    # Get fields from first item
    fields = list(data[0].keys())
    field_str = ",".join(fields)

    # Build rows
    rows = []
    for item in data:
        values = [str(item.get(f, "")) for f in fields]
        rows.append("  " + ",".join(values))

    return f"{name}[{len(data)}]{{{field_str}}}:\n" + "\n".join(rows)


def execution_history_to_toon(history: List[Dict]) -> str:
    """Convert execution history to TOON for context injection."""
    simplified = [
        {
            "task": h.get("title", "")[:50],
            "status": "ok" if h.get("success") else "fail",
            "files": len(h.get("files_modified", [])),
            "time": f"{h.get('execution_time', 0):.0f}s"
        }
        for h in history
    ]
    return to_toon(simplified, "history")


def work_queue_to_toon(tasks: List[Dict]) -> str:
    """Convert work queue to TOON for context."""
    simplified = [
        {
            "id": t.get("id", "")[:8],
            "type": t.get("type", ""),
            "title": t.get("title", "")[:40],
            "priority": t.get("priority", 3)
        }
        for t in tasks
    ]
    return to_toon(simplified, "queue")
```

**3. Use in ContextManager:**

```python
# In context_manager.py

def _build_summarization_prompt(self, ...):
    # Convert structured data to TOON before summarizing
    if self.files_modified:
        files_toon = to_toon(
            [{"path": f, "action": "modified"} for f in self.files_modified],
            "files"
        )
    ...
```

### When to use TOON vs JSON

| Scenario | Use TOON | Use JSON |
|----------|----------|----------|
| Tabular data (tasks, files, results) | Yes | No |
| Deeply nested structures | No | Yes |
| Single objects | No | Yes |
| Human-readable config | No | Yes |
| LLM context injection | Yes | No |

### Token Savings Estimate

For Sugar's typical context:
- 10 tasks in queue: ~45% savings
- 20 file changes: ~50% savings
- 5 execution history items: ~40% savings

**Combined impact:** Could reduce structured context tokens by 40-50%

---

## Combined Strategy: Summarization + TOON

The optimal approach combines both:

1. **TOON** for structured data (tasks, files, results)
2. **Summarization** for conversational context (decisions, reasoning)

```
┌─────────────────────────────────────────────────────────┐
│                 Context Optimization                     │
└─────────────────────────────────────────────────────────┘
                         │
         ┌───────────────┴───────────────┐
         │                               │
         ▼                               ▼
┌─────────────────┐            ┌─────────────────┐
│ Structured Data │            │ Conversational  │
│ (tasks, files)  │            │ (decisions,     │
│                 │            │  reasoning)     │
└────────┬────────┘            └────────┬────────┘
         │                               │
         ▼                               ▼
┌─────────────────┐            ┌─────────────────┐
│  TOON Encoding  │            │  Summarization  │
│  (30-60% less)  │            │  (Haiku model)  │
└────────┬────────┘            └────────┬────────┘
         │                               │
         └───────────────┬───────────────┘
                         │
                         ▼
              ┌─────────────────┐
              │ Optimized       │
              │ Context         │
              └─────────────────┘
```

---

## References

- DeepAgents: https://github.com/langchain-ai/deepagents
- TOON Format: https://github.com/toon-format/toon
- Claude Agent SDK docs
- Sugar architecture: `sugar/executor/`, `sugar/agent/`
