# Phase 4: Memory Context Injection

**Technical Specification Document**

Version: 1.0
Date: 2026-01-20
Status: Draft

---

## Executive Summary

This document specifies the architecture and implementation for automatically injecting relevant Sugar memories into OpenCode sessions. The system provides intelligent, low-latency context enhancement that improves LLM responses by surfacing relevant decisions, preferences, error patterns, and research from previous sessions.

---

## 1. Memory Injection Architecture

### 1.1 Overview

The memory injection system operates at three levels:

```
+------------------+     +-------------------+     +------------------+
|   Session Start  | --> | Before Prompt     | --> | After Completion |
|   (Full Context) |     | (Query-Specific)  |     | (Learning)       |
+------------------+     +-------------------+     +------------------+
        |                         |                        |
        v                         v                        v
   Project prefs            Semantic search           Store outcomes
   Recent decisions         Recency boost             Deduplicate
   Error patterns           Type priority             Assign importance
```

### 1.2 Injection Points

| Injection Point | Trigger | Content Type | Token Budget |
|-----------------|---------|--------------|--------------|
| `SessionStart` | New session begins | Full project context | 1500-2000 |
| `BeforePrompt` | Each user prompt | Query-specific memories | 500-1000 |
| `AfterToolUse` | Tool success/failure | Error patterns (if error) | 200-300 |
| `SessionEnd` | Session completes | Capture learnings | N/A (storage) |

### 1.3 Component Architecture

```
sugar/
  memory/
    __init__.py
    types.py              # Existing: MemoryEntry, MemoryType, etc.
    store.py              # Existing: SQLite + sqlite-vec storage
    embedder.py           # Existing: Sentence transformers
    retriever.py          # Existing: Basic retrieval
    injector.py           # NEW: Context injection engine
    scorer.py             # NEW: Relevance scoring
    learning_capture.py   # NEW: Automatic learning extraction
    budget_manager.py     # NEW: Token budget management
    config.py             # NEW: Injection configuration
  mcp/
    memory_server.py      # Existing: MCP tools (enhanced)
  integrations/
    opencode/
      __init__.py         # NEW: OpenCode plugin module
      hooks.py            # NEW: Session hooks
      context_provider.py # NEW: Context formatting
```

---

## 2. Relevance Algorithm

### 2.1 Multi-Factor Scoring

Each memory receives a composite relevance score from 0.0 to 1.0:

```python
@dataclass
class RelevanceScore:
    semantic_similarity: float    # 0.0-1.0, weight: 0.40
    recency_score: float          # 0.0-1.0, weight: 0.20
    importance_score: float       # 0.0-1.0, weight: 0.15
    access_frequency: float       # 0.0-1.0, weight: 0.10
    type_priority: float          # 0.0-1.0, weight: 0.15

    def composite(self) -> float:
        return (
            self.semantic_similarity * 0.40 +
            self.recency_score * 0.20 +
            self.importance_score * 0.15 +
            self.access_frequency * 0.10 +
            self.type_priority * 0.15
        )
```

### 2.2 Query Construction

Transform user prompts into optimized memory queries:

```python
class QueryBuilder:
    """Build optimized queries from user prompts."""

    def build_query(self, user_prompt: str, context: SessionContext) -> MemoryQuery:
        """
        Extract search terms from user prompt.

        Steps:
        1. Extract key entities (file names, function names, concepts)
        2. Identify task type keywords (fix, add, refactor, test)
        3. Extract technology/framework mentions
        4. Build combined query with context
        """
        # Extract entities using simple NLP patterns
        entities = self._extract_entities(user_prompt)

        # Determine relevant memory types based on task
        task_type = self._classify_task(user_prompt)
        memory_types = self._types_for_task(task_type)

        # Build query with entity weighting
        query_text = self._build_weighted_query(
            user_prompt=user_prompt,
            entities=entities,
            recent_files=context.recent_files,
        )

        return MemoryQuery(
            query=query_text,
            memory_types=memory_types,
            limit=10,  # Retrieve more, filter down
            min_importance=0.3,
        )

    def _classify_task(self, prompt: str) -> TaskType:
        """Classify prompt to determine relevant memory types."""
        prompt_lower = prompt.lower()

        if any(kw in prompt_lower for kw in ["fix", "bug", "error", "broken"]):
            return TaskType.BUG_FIX
        elif any(kw in prompt_lower for kw in ["add", "create", "new", "implement"]):
            return TaskType.FEATURE
        elif any(kw in prompt_lower for kw in ["refactor", "clean", "improve"]):
            return TaskType.REFACTOR
        elif any(kw in prompt_lower for kw in ["test", "testing", "coverage"]):
            return TaskType.TEST
        else:
            return TaskType.GENERAL

    def _types_for_task(self, task_type: TaskType) -> List[MemoryType]:
        """Map task type to relevant memory types."""
        mapping = {
            TaskType.BUG_FIX: [
                MemoryType.ERROR_PATTERN,      # Highest priority
                MemoryType.FILE_CONTEXT,
                MemoryType.DECISION,
                MemoryType.PREFERENCE,
            ],
            TaskType.FEATURE: [
                MemoryType.DECISION,           # Architecture decisions
                MemoryType.PREFERENCE,         # Coding style
                MemoryType.FILE_CONTEXT,
                MemoryType.RESEARCH,
            ],
            TaskType.REFACTOR: [
                MemoryType.PREFERENCE,         # Style preferences
                MemoryType.DECISION,
                MemoryType.FILE_CONTEXT,
            ],
            TaskType.TEST: [
                MemoryType.PREFERENCE,         # Testing preferences
                MemoryType.FILE_CONTEXT,
                MemoryType.ERROR_PATTERN,
            ],
            TaskType.GENERAL: [
                MemoryType.PREFERENCE,
                MemoryType.DECISION,
                MemoryType.FILE_CONTEXT,
                MemoryType.ERROR_PATTERN,
            ],
        }
        return mapping.get(task_type, mapping[TaskType.GENERAL])
```

### 2.3 Semantic Similarity Thresholds

```python
class SimilarityConfig:
    # Minimum threshold for inclusion
    MIN_THRESHOLD: float = 0.35

    # High confidence threshold (always include)
    HIGH_CONFIDENCE: float = 0.70

    # Thresholds by memory type (some types need higher relevance)
    TYPE_THRESHOLDS: Dict[MemoryType, float] = {
        MemoryType.PREFERENCE: 0.30,      # Cast wider net
        MemoryType.DECISION: 0.40,
        MemoryType.FILE_CONTEXT: 0.45,
        MemoryType.ERROR_PATTERN: 0.50,   # Need higher relevance
        MemoryType.RESEARCH: 0.45,
        MemoryType.OUTCOME: 0.55,         # Highest threshold
    }
```

### 2.4 Recency Weighting

```python
def calculate_recency_score(created_at: datetime, memory_type: MemoryType) -> float:
    """
    Calculate recency score with type-specific decay.

    Preferences: No decay (always relevant)
    Decisions: Slow decay over months
    Error patterns: Medium decay over weeks
    Research: Fast decay over weeks
    Outcomes: Fast decay over days
    """
    age = datetime.now(timezone.utc) - created_at
    age_days = age.total_seconds() / 86400

    # Half-life in days by type
    half_life = {
        MemoryType.PREFERENCE: float('inf'),  # No decay
        MemoryType.DECISION: 180,             # 6 months
        MemoryType.FILE_CONTEXT: 90,          # 3 months
        MemoryType.ERROR_PATTERN: 30,         # 1 month
        MemoryType.RESEARCH: 45,              # 1.5 months
        MemoryType.OUTCOME: 14,               # 2 weeks
    }

    hl = half_life.get(memory_type, 60)
    if hl == float('inf'):
        return 1.0

    # Exponential decay
    return 2 ** (-age_days / hl)
```

### 2.5 Memory Type Priority

```python
class TypePriority:
    """Priority ordering for memory types in different contexts."""

    # Default priority (higher = more important)
    DEFAULT: Dict[MemoryType, float] = {
        MemoryType.PREFERENCE: 1.0,       # Always highest
        MemoryType.DECISION: 0.9,
        MemoryType.ERROR_PATTERN: 0.8,
        MemoryType.FILE_CONTEXT: 0.7,
        MemoryType.RESEARCH: 0.6,
        MemoryType.OUTCOME: 0.5,
    }

    # Bug fix context
    BUG_FIX: Dict[MemoryType, float] = {
        MemoryType.ERROR_PATTERN: 1.0,    # Highest for bugs
        MemoryType.FILE_CONTEXT: 0.9,
        MemoryType.PREFERENCE: 0.8,
        MemoryType.DECISION: 0.7,
        MemoryType.RESEARCH: 0.5,
        MemoryType.OUTCOME: 0.6,
    }
```

---

## 3. Injection Mechanisms

### 3.1 OpenCode Plugin Hooks

OpenCode provides session lifecycle hooks. Sugar registers callbacks at each point:

```python
# sugar/integrations/opencode/hooks.py

from typing import Any, Dict, Optional
from sugar.memory import MemoryStore, MemoryRetriever
from sugar.memory.injector import ContextInjector

class SugarOpenCodePlugin:
    """OpenCode plugin for Sugar memory integration."""

    def __init__(self, config: InjectionConfig):
        self.config = config
        self.store = MemoryStore(config.db_path)
        self.retriever = MemoryRetriever(self.store)
        self.injector = ContextInjector(self.retriever, config)
        self._session_context: Optional[SessionContext] = None

    async def on_session_start(self, session: OpenCodeSession) -> Dict[str, Any]:
        """
        Called when OpenCode session begins.

        Inject full project context including:
        - All user preferences
        - Recent architectural decisions
        - Known error patterns
        - File context for commonly accessed files
        """
        self._session_context = SessionContext(
            session_id=session.id,
            project_path=session.working_directory,
            start_time=datetime.now(timezone.utc),
        )

        if not self.config.auto_inject_enabled:
            return {}

        context = self.injector.get_session_start_context(
            max_tokens=self.config.session_start_budget,
        )

        return {
            "system_prompt_addition": context,
            "injected_memories": self.injector.last_injection_stats(),
        }

    async def on_before_prompt(
        self,
        session: OpenCodeSession,
        user_prompt: str
    ) -> Dict[str, Any]:
        """
        Called before each user prompt is sent to Claude.

        Inject query-specific memories based on semantic relevance.
        """
        if not self.config.per_prompt_injection_enabled:
            return {}

        # Update session context with recent activity
        self._session_context.last_prompt = user_prompt

        context = self.injector.get_prompt_context(
            user_prompt=user_prompt,
            session_context=self._session_context,
            max_tokens=self.config.per_prompt_budget,
        )

        if not context:
            return {}

        return {
            "context_injection": context,
            "injected_memories": self.injector.last_injection_stats(),
        }

    async def on_tool_result(
        self,
        session: OpenCodeSession,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Called after tool execution completes.

        For errors, inject relevant error patterns.
        For file operations, update session context.
        """
        # Track file access
        if tool_name in ("Read", "Write", "Edit"):
            file_path = tool_input.get("file_path", "")
            self._session_context.accessed_files.add(file_path)

        # Inject error patterns on failure
        if self._is_error_result(tool_result):
            error_context = self.injector.get_error_context(
                error=tool_result.get("error", ""),
                tool_name=tool_name,
                max_tokens=self.config.error_context_budget,
            )
            if error_context:
                return {"error_context_injection": error_context}

        return {}

    async def on_session_end(
        self,
        session: OpenCodeSession,
        conversation: List[Dict[str, Any]],
    ) -> None:
        """
        Called when session ends.

        Capture learnings from the conversation.
        """
        if not self.config.learning_capture_enabled:
            return

        await self.injector.capture_session_learnings(
            session_context=self._session_context,
            conversation=conversation,
        )
```

### 3.2 MCP Tool Enhancement

Enhance existing MCP tools for on-demand memory access:

```python
# sugar/mcp/memory_server.py (additions)

@mcp.tool()
async def inject_context(
    topic: Optional[str] = None,
    memory_types: Optional[str] = None,
    max_tokens: int = 1000,
) -> str:
    """
    Inject relevant memory context into the current conversation.

    Call this when you need context about a specific topic or
    when working on something that might have relevant history.

    Args:
        topic: Optional topic to focus on (uses recent context if None)
        memory_types: Comma-separated types to include (or all)
        max_tokens: Maximum tokens for injected context

    Returns:
        Formatted markdown context from relevant memories
    """
    from sugar.memory.injector import ContextInjector

    store = get_memory_store()
    retriever = MemoryRetriever(store)
    injector = ContextInjector(retriever, InjectionConfig())

    types = None
    if memory_types:
        types = [MemoryType(t.strip()) for t in memory_types.split(",")]

    context = injector.get_targeted_context(
        topic=topic,
        memory_types=types,
        max_tokens=max_tokens,
    )

    store.close()
    return context


@mcp.tool()
async def get_context_for_task(
    task_description: str,
    include_file_context: bool = True,
    include_error_patterns: bool = True,
) -> str:
    """
    Get comprehensive context for a specific development task.

    Automatically determines relevant memories based on task type
    and returns formatted context optimized for the task.

    Args:
        task_description: Description of the task you're working on
        include_file_context: Include file/module context
        include_error_patterns: Include relevant error patterns

    Returns:
        Task-optimized context from Sugar memory
    """
    from sugar.memory.injector import ContextInjector
    from sugar.memory.scorer import QueryBuilder

    store = get_memory_store()
    retriever = MemoryRetriever(store)
    injector = ContextInjector(retriever, InjectionConfig())

    context = injector.get_task_context(
        task_description=task_description,
        include_file_context=include_file_context,
        include_error_patterns=include_error_patterns,
    )

    store.close()
    return context
```

### 3.3 System Prompt Augmentation

For session-level context, augment the system prompt:

```python
class ContextFormatter:
    """Format memories for LLM consumption."""

    def format_system_prompt_addition(
        self,
        memories: List[MemorySearchResult],
        max_tokens: int = 1500,
    ) -> str:
        """
        Format memories as a system prompt addition.

        Structure:
        1. Brief header explaining the context
        2. Grouped by type with clear headers
        3. Most important/relevant first within each group
        4. Truncate to fit budget
        """
        if not memories:
            return ""

        lines = [
            "## Project Context from Previous Sessions",
            "",
            "The following context was automatically retrieved from your project's memory:",
            "",
        ]

        # Group by type
        by_type: Dict[MemoryType, List[MemorySearchResult]] = defaultdict(list)
        for result in memories:
            by_type[result.entry.memory_type].append(result)

        # Type display order
        type_order = [
            MemoryType.PREFERENCE,
            MemoryType.DECISION,
            MemoryType.FILE_CONTEXT,
            MemoryType.ERROR_PATTERN,
            MemoryType.RESEARCH,
        ]

        char_budget = max_tokens * 4  # Approximate chars
        current_chars = sum(len(line) for line in lines)

        for mem_type in type_order:
            if mem_type not in by_type:
                continue

            type_results = by_type[mem_type]
            type_label = mem_type.value.replace("_", " ").title()

            section = [f"### {type_label}", ""]

            for result in sorted(type_results, key=lambda r: r.score, reverse=True):
                entry = result.entry
                content = entry.summary or entry.content[:200]

                # Format based on type
                if mem_type == MemoryType.PREFERENCE:
                    line = f"- {content}"
                elif mem_type == MemoryType.ERROR_PATTERN:
                    line = f"- **Pattern**: {content}"
                else:
                    line = f"- {content}"

                section.append(line)

                if current_chars + len("\n".join(section)) > char_budget:
                    break

            section.append("")

            if current_chars + len("\n".join(section)) > char_budget:
                break

            lines.extend(section)
            current_chars = sum(len(line) for line in lines)

        lines.append("---")
        return "\n".join(lines)

    def format_inline_context(
        self,
        memories: List[MemorySearchResult],
        max_tokens: int = 500,
    ) -> str:
        """
        Format memories for inline injection before a prompt.

        More compact format suitable for per-prompt injection.
        """
        if not memories:
            return ""

        lines = [
            "<relevant_context>",
            "Based on your project history:",
            "",
        ]

        char_budget = max_tokens * 4
        current_chars = sum(len(line) for line in lines)

        for result in sorted(memories, key=lambda r: r.score, reverse=True):
            entry = result.entry
            type_prefix = self._type_prefix(entry.memory_type)
            content = entry.summary or entry.content[:150]

            line = f"- [{type_prefix}] {content}"

            if current_chars + len(line) + 20 > char_budget:
                break

            lines.append(line)
            current_chars += len(line)

        lines.extend(["", "</relevant_context>"])
        return "\n".join(lines)

    def _type_prefix(self, mem_type: MemoryType) -> str:
        """Short prefix for memory type."""
        prefixes = {
            MemoryType.PREFERENCE: "PREF",
            MemoryType.DECISION: "DEC",
            MemoryType.FILE_CONTEXT: "FILE",
            MemoryType.ERROR_PATTERN: "ERR",
            MemoryType.RESEARCH: "RES",
            MemoryType.OUTCOME: "OUT",
        }
        return prefixes.get(mem_type, "MEM")
```

---

## 4. Learning Capture

### 4.1 What to Capture

The system automatically captures learnings during and after sessions:

| Event | What to Capture | Memory Type | Importance |
|-------|-----------------|-------------|------------|
| Explicit decision | Architecture/implementation choice | DECISION | 1.2-1.5 |
| Error fixed | Error pattern and solution | ERROR_PATTERN | 1.0-1.3 |
| Preference stated | Coding style preference | PREFERENCE | 1.5-2.0 |
| File purpose explained | File/module responsibility | FILE_CONTEXT | 0.8-1.0 |
| Task completed | Summary of what was done | OUTCOME | 0.6-0.8 |
| Research done | API/library findings | RESEARCH | 0.8-1.0 |

### 4.2 Capture Triggers

```python
class LearningCapture:
    """Capture learnings from conversations."""

    # Patterns indicating a decision
    DECISION_PATTERNS = [
        r"(?:decided|choosing|going with|we'll use|opting for)\s+(.+?)(?:\.|$)",
        r"(?:because|reason being)\s+(.+?)(?:\.|$)",
        r"(?:the approach is|strategy is)\s+(.+?)(?:\.|$)",
    ]

    # Patterns indicating a preference
    PREFERENCE_PATTERNS = [
        r"(?:always|prefer|should always|never)\s+(.+?)(?:\.|$)",
        r"(?:convention is|style is|standard is)\s+(.+?)(?:\.|$)",
        r"(?:make sure to|remember to always)\s+(.+?)(?:\.|$)",
    ]

    # Patterns indicating an error pattern
    ERROR_PATTERNS = [
        r"(?:the issue was|bug was|problem was|caused by)\s+(.+?)(?:\.|$)",
        r"(?:fixed by|solved by|resolved by)\s+(.+?)(?:\.|$)",
        r"(?:root cause|the reason)\s+(.+?)(?:\.|$)",
    ]

    async def capture_from_conversation(
        self,
        conversation: List[Dict[str, Any]],
        session_context: SessionContext,
    ) -> List[CapturedLearning]:
        """
        Extract learnings from conversation history.

        Analyzes both user and assistant messages for:
        1. Explicit statements of decisions/preferences
        2. Error patterns and solutions
        3. Task outcomes
        """
        learnings = []

        for message in conversation:
            role = message.get("role", "")
            content = message.get("content", "")

            # Extract from assistant responses (decisions, explanations)
            if role == "assistant":
                learnings.extend(
                    self._extract_decisions(content, session_context)
                )
                learnings.extend(
                    self._extract_error_patterns(content, session_context)
                )

            # Extract from user messages (preferences, corrections)
            if role == "user":
                learnings.extend(
                    self._extract_preferences(content, session_context)
                )

        # Deduplicate before storing
        return self._deduplicate(learnings)

    async def capture_on_tool_success(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_result: Dict[str, Any],
        context: SessionContext,
    ) -> Optional[CapturedLearning]:
        """
        Capture learnings from successful tool execution.

        For file operations, capture file context.
        For bash operations, capture command patterns.
        """
        if tool_name in ("Write", "Edit"):
            file_path = tool_input.get("file_path", "")
            if file_path:
                return CapturedLearning(
                    memory_type=MemoryType.FILE_CONTEXT,
                    content=f"Modified: {file_path}",
                    metadata={"file_path": file_path},
                    importance=0.5,  # Lower, will be enriched
                    needs_enrichment=True,
                )

        return None

    async def capture_on_error(
        self,
        error: str,
        tool_name: str,
        context: SessionContext,
    ) -> Optional[CapturedLearning]:
        """
        Capture error patterns when they occur.

        Note: The fix is captured separately when resolved.
        """
        # Don't capture generic errors
        if len(error) < 20 or "timeout" in error.lower():
            return None

        return CapturedLearning(
            memory_type=MemoryType.ERROR_PATTERN,
            content=f"Error in {tool_name}: {error[:200]}",
            metadata={
                "tool": tool_name,
                "files": list(context.accessed_files),
            },
            importance=0.8,
            pending_resolution=True,  # Will be updated when fixed
        )
```

### 4.3 Deduplication Strategy

```python
class DeduplicationEngine:
    """Prevent duplicate memories."""

    def __init__(self, store: MemoryStore, similarity_threshold: float = 0.85):
        self.store = store
        self.threshold = similarity_threshold

    async def is_duplicate(self, learning: CapturedLearning) -> bool:
        """
        Check if a learning already exists in memory.

        Uses semantic similarity to find near-duplicates.
        """
        # Search for similar memories of same type
        query = MemoryQuery(
            query=learning.content,
            memory_types=[learning.memory_type],
            limit=5,
        )

        results = self.store.search(query)

        for result in results:
            if result.score >= self.threshold:
                return True

        return False

    async def merge_or_store(
        self,
        learning: CapturedLearning,
    ) -> str:
        """
        Either merge with existing memory or create new.

        If near-duplicate exists, update importance and access.
        Otherwise, store as new memory.
        """
        query = MemoryQuery(
            query=learning.content,
            memory_types=[learning.memory_type],
            limit=3,
        )

        results = self.store.search(query)

        for result in results:
            if result.score >= self.threshold:
                # Boost existing memory instead of duplicating
                existing = result.entry
                new_importance = min(2.0, existing.importance + 0.1)

                # Update existing entry
                existing.importance = new_importance
                existing.access_count += 1
                existing.last_accessed_at = datetime.now(timezone.utc)

                # Merge metadata
                if learning.metadata:
                    existing.metadata.update(learning.metadata)

                self.store.store(existing)
                return existing.id

        # No duplicate, store as new
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            memory_type=learning.memory_type,
            content=learning.content,
            summary=learning.content[:100] if len(learning.content) > 100 else None,
            metadata=learning.metadata,
            importance=learning.importance,
        )

        return self.store.store(entry)
```

### 4.4 Importance Assignment

```python
class ImportanceScorer:
    """Assign importance scores to captured learnings."""

    # Base importance by type
    BASE_IMPORTANCE: Dict[MemoryType, float] = {
        MemoryType.PREFERENCE: 1.5,     # High - permanent value
        MemoryType.DECISION: 1.2,       # High - architectural impact
        MemoryType.ERROR_PATTERN: 1.0,  # Medium - prevents future issues
        MemoryType.FILE_CONTEXT: 0.8,   # Medium-low - reference value
        MemoryType.RESEARCH: 0.9,       # Medium - time-limited value
        MemoryType.OUTCOME: 0.6,        # Low - historical record
    }

    def score(
        self,
        learning: CapturedLearning,
        context: SessionContext,
    ) -> float:
        """
        Calculate importance score for a learning.

        Factors:
        1. Base score by type
        2. Explicitness (user stated vs inferred)
        3. Repetition (mentioned multiple times)
        4. Scope (affects many files vs one)
        5. User emphasis (exclamation, "important", "always")
        """
        base = self.BASE_IMPORTANCE.get(learning.memory_type, 1.0)

        # Explicit boost
        if learning.explicitly_stated:
            base += 0.3

        # Repetition boost
        if learning.mention_count > 1:
            base += min(0.3, learning.mention_count * 0.1)

        # Scope boost
        if learning.affects_multiple_files:
            base += 0.2

        # Emphasis detection
        if self._has_emphasis(learning.content):
            base += 0.2

        return min(2.0, base)  # Cap at 2.0

    def _has_emphasis(self, content: str) -> bool:
        """Check for emphasis markers."""
        emphasis_markers = [
            "important",
            "critical",
            "always",
            "never",
            "must",
            "!",
            "essential",
            "crucial",
        ]
        content_lower = content.lower()
        return any(marker in content_lower for marker in emphasis_markers)
```

---

## 5. Token Budget Management

### 5.1 Budget Configuration

```python
@dataclass
class TokenBudget:
    """Token budget configuration for memory injection."""

    # Session start injection
    session_start_total: int = 2000
    session_start_preferences: int = 500
    session_start_decisions: int = 600
    session_start_file_context: int = 400
    session_start_error_patterns: int = 300
    session_start_reserve: int = 200  # For headers/formatting

    # Per-prompt injection
    per_prompt_total: int = 800
    per_prompt_high_relevance: int = 500  # Score > 0.7
    per_prompt_medium_relevance: int = 300  # Score 0.5-0.7

    # Error context injection
    error_context: int = 300

    # Dynamic adjustment
    min_budget: int = 200
    max_budget: int = 3000
```

### 5.2 Dynamic Adjustment

```python
class BudgetManager:
    """Manage token budget for memory injection."""

    def __init__(self, config: TokenBudget):
        self.config = config
        self._usage_history: List[BudgetUsage] = []

    def calculate_available_budget(
        self,
        injection_point: InjectionPoint,
        prompt_size: int,
        model_context_window: int = 200000,
    ) -> int:
        """
        Calculate available budget based on context.

        Factors:
        - Injection point default budget
        - Prompt size (larger prompts = less memory budget)
        - Model context window
        - Recent usage patterns
        """
        base_budget = self._get_base_budget(injection_point)

        # Reduce budget for large prompts
        if prompt_size > 5000:
            reduction = min(0.5, (prompt_size - 5000) / 20000)
            base_budget = int(base_budget * (1 - reduction))

        # Never go below minimum
        return max(self.config.min_budget, base_budget)

    def allocate_by_type(
        self,
        total_budget: int,
        injection_point: InjectionPoint,
    ) -> Dict[MemoryType, int]:
        """
        Allocate budget across memory types.

        Different allocation strategies for different injection points.
        """
        if injection_point == InjectionPoint.SESSION_START:
            # Balanced allocation for session start
            return {
                MemoryType.PREFERENCE: int(total_budget * 0.25),
                MemoryType.DECISION: int(total_budget * 0.30),
                MemoryType.FILE_CONTEXT: int(total_budget * 0.20),
                MemoryType.ERROR_PATTERN: int(total_budget * 0.15),
                MemoryType.RESEARCH: int(total_budget * 0.10),
            }

        elif injection_point == InjectionPoint.BEFORE_PROMPT:
            # More focused for per-prompt
            return {
                MemoryType.PREFERENCE: int(total_budget * 0.20),
                MemoryType.DECISION: int(total_budget * 0.25),
                MemoryType.FILE_CONTEXT: int(total_budget * 0.30),
                MemoryType.ERROR_PATTERN: int(total_budget * 0.25),
            }

        else:
            # Default even split
            per_type = total_budget // 5
            return {t: per_type for t in MemoryType}
```

### 5.3 Truncation Strategies

```python
class TruncationStrategy:
    """Strategies for fitting content within budget."""

    @staticmethod
    def truncate_by_importance(
        memories: List[MemorySearchResult],
        budget_tokens: int,
    ) -> List[MemorySearchResult]:
        """
        Truncate list by keeping most important memories.

        1. Sort by composite score
        2. Keep adding until budget exhausted
        3. Ensure at least one from each type if possible
        """
        # Sort by score descending
        sorted_memories = sorted(memories, key=lambda m: m.score, reverse=True)

        selected = []
        used_tokens = 0
        seen_types = set()

        # First pass: ensure type diversity
        for mem in sorted_memories:
            if mem.entry.memory_type not in seen_types:
                tokens = estimate_tokens(mem.entry.content)
                if used_tokens + tokens <= budget_tokens:
                    selected.append(mem)
                    used_tokens += tokens
                    seen_types.add(mem.entry.memory_type)

        # Second pass: fill remaining budget
        for mem in sorted_memories:
            if mem in selected:
                continue
            tokens = estimate_tokens(mem.entry.content)
            if used_tokens + tokens <= budget_tokens:
                selected.append(mem)
                used_tokens += tokens

        return selected

    @staticmethod
    def truncate_content(
        content: str,
        max_tokens: int,
    ) -> str:
        """
        Truncate individual memory content while preserving meaning.

        1. Preserve first sentence (usually most important)
        2. Preserve key phrases (file names, decisions)
        3. Add ellipsis if truncated
        """
        estimated = estimate_tokens(content)
        if estimated <= max_tokens:
            return content

        # Split into sentences
        sentences = content.split(". ")

        # Always keep first sentence
        result = sentences[0]

        # Add more sentences if budget allows
        for sentence in sentences[1:]:
            candidate = result + ". " + sentence
            if estimate_tokens(candidate) <= max_tokens - 5:  # Reserve for ellipsis
                result = candidate
            else:
                break

        if len(result) < len(content):
            result += "..."

        return result


def estimate_tokens(text: str) -> int:
    """Estimate token count from text length."""
    # Rough estimate: ~4 characters per token for English
    return len(text) // 4
```

---

## 6. Configuration

### 6.1 Configuration Schema

```yaml
# .sugar/config.yaml

memory:
  injection:
    # Master enable/disable
    enabled: true

    # Injection points
    session_start:
      enabled: true
      budget_tokens: 2000

    per_prompt:
      enabled: true
      budget_tokens: 800
      min_relevance_score: 0.40

    error_context:
      enabled: true
      budget_tokens: 300

    # Learning capture
    learning_capture:
      enabled: true
      capture_decisions: true
      capture_preferences: true
      capture_errors: true
      capture_outcomes: true
      deduplication_threshold: 0.85

    # Relevance scoring weights
    scoring:
      semantic_similarity_weight: 0.40
      recency_weight: 0.20
      importance_weight: 0.15
      access_frequency_weight: 0.10
      type_priority_weight: 0.15

    # Type-specific thresholds
    type_thresholds:
      preference: 0.30
      decision: 0.40
      file_context: 0.45
      error_pattern: 0.50
      research: 0.45
      outcome: 0.55

    # Memory types to inject
    types:
      - preference
      - decision
      - file_context
      - error_pattern
      # - research     # Uncomment to include
      # - outcome      # Uncomment to include

    # Cross-project isolation
    isolation:
      enabled: true
      share_preferences: false  # Set true to share prefs across projects
```

### 6.2 Runtime Configuration

```python
@dataclass
class InjectionConfig:
    """Runtime configuration for memory injection."""

    # Database path
    db_path: str = ".sugar/memory.db"

    # Enable/disable flags
    auto_inject_enabled: bool = True
    per_prompt_injection_enabled: bool = True
    learning_capture_enabled: bool = True

    # Token budgets
    session_start_budget: int = 2000
    per_prompt_budget: int = 800
    error_context_budget: int = 300

    # Relevance thresholds
    min_relevance_score: float = 0.40
    high_relevance_threshold: float = 0.70

    # Scoring weights
    semantic_weight: float = 0.40
    recency_weight: float = 0.20
    importance_weight: float = 0.15
    access_weight: float = 0.10
    type_weight: float = 0.15

    # Type configuration
    enabled_types: List[MemoryType] = field(default_factory=lambda: [
        MemoryType.PREFERENCE,
        MemoryType.DECISION,
        MemoryType.FILE_CONTEXT,
        MemoryType.ERROR_PATTERN,
    ])

    # Deduplication
    dedup_threshold: float = 0.85

    # Isolation
    project_isolation_enabled: bool = True
    share_preferences_across_projects: bool = False

    @classmethod
    def from_yaml(cls, config_path: str) -> "InjectionConfig":
        """Load configuration from YAML file."""
        import yaml

        with open(config_path) as f:
            data = yaml.safe_load(f)

        memory_config = data.get("memory", {}).get("injection", {})

        return cls(
            auto_inject_enabled=memory_config.get("enabled", True),
            session_start_budget=memory_config.get("session_start", {}).get(
                "budget_tokens", 2000
            ),
            per_prompt_budget=memory_config.get("per_prompt", {}).get(
                "budget_tokens", 800
            ),
            min_relevance_score=memory_config.get("per_prompt", {}).get(
                "min_relevance_score", 0.40
            ),
            # ... additional fields
        )
```

---

## 7. Privacy & Security

### 7.1 Memory Classification

```python
class MemorySensitivity(str, Enum):
    """Sensitivity levels for memories."""

    PUBLIC = "public"           # Can be shared across sessions/projects
    PROJECT = "project"         # Limited to this project
    SESSION = "session"         # Limited to current session only
    RESTRICTED = "restricted"   # Never inject automatically
```

### 7.2 Sensitive Data Handling

```python
class SensitivityFilter:
    """Filter sensitive data from memory injection."""

    # Patterns that indicate sensitive content
    SENSITIVE_PATTERNS = [
        r"api[_-]?key\s*[=:]\s*['\"]?\w+",
        r"password\s*[=:]\s*['\"]?\w+",
        r"secret\s*[=:]\s*['\"]?\w+",
        r"token\s*[=:]\s*['\"]?\w+",
        r"bearer\s+\w+",
        r"-----BEGIN\s+\w+\s+KEY-----",
        r"sk-[a-zA-Z0-9]{48}",  # OpenAI keys
        r"sk-ant-[a-zA-Z0-9-]+",  # Anthropic keys
    ]

    def is_sensitive(self, content: str) -> bool:
        """Check if content contains sensitive information."""
        content_lower = content.lower()

        for pattern in self.SENSITIVE_PATTERNS:
            if re.search(pattern, content_lower):
                return True

        return False

    def filter_for_injection(
        self,
        memories: List[MemorySearchResult],
    ) -> List[MemorySearchResult]:
        """Filter out memories that shouldn't be injected."""
        filtered = []

        for mem in memories:
            # Skip restricted memories
            if mem.entry.metadata.get("sensitivity") == "restricted":
                continue

            # Skip if content looks sensitive
            if self.is_sensitive(mem.entry.content):
                continue

            filtered.append(mem)

        return filtered
```

### 7.3 Cross-Project Isolation

```python
class ProjectIsolation:
    """Manage memory isolation between projects."""

    def __init__(self, config: InjectionConfig):
        self.config = config
        self._project_id = self._compute_project_id()

    def _compute_project_id(self) -> str:
        """Compute unique project identifier."""
        # Use git remote URL or directory path
        import subprocess
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return hashlib.sha256(result.stdout.strip().encode()).hexdigest()[:16]
        except Exception:
            pass

        # Fallback to directory path
        return hashlib.sha256(os.getcwd().encode()).hexdigest()[:16]

    def filter_by_project(
        self,
        memories: List[MemorySearchResult],
    ) -> List[MemorySearchResult]:
        """Filter memories to current project only."""
        if not self.config.project_isolation_enabled:
            return memories

        filtered = []

        for mem in memories:
            project_id = mem.entry.metadata.get("project_id")

            # Allow if same project
            if project_id == self._project_id:
                filtered.append(mem)
                continue

            # Allow shared preferences if enabled
            if (
                self.config.share_preferences_across_projects and
                mem.entry.memory_type == MemoryType.PREFERENCE
            ):
                filtered.append(mem)
                continue

        return filtered
```

---

## 8. Implementation Details

### 8.1 New Modules

#### `sugar/memory/injector.py`

```python
"""
Context injection engine for Sugar memory system.

Handles automatic injection of relevant memories into LLM sessions.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .retriever import MemoryRetriever
from .scorer import RelevanceScorer, QueryBuilder
from .budget_manager import BudgetManager, TokenBudget
from .config import InjectionConfig
from .types import MemoryEntry, MemoryQuery, MemorySearchResult, MemoryType


@dataclass
class InjectionStats:
    """Statistics from last injection operation."""

    memories_considered: int = 0
    memories_injected: int = 0
    tokens_used: int = 0
    tokens_budget: int = 0
    by_type: Dict[str, int] = field(default_factory=dict)
    avg_relevance: float = 0.0


class ContextInjector:
    """
    Core engine for memory context injection.

    Handles:
    - Session start context generation
    - Per-prompt context injection
    - Error context injection
    - Learning capture
    """

    def __init__(
        self,
        retriever: MemoryRetriever,
        config: InjectionConfig,
    ):
        self.retriever = retriever
        self.config = config
        self.scorer = RelevanceScorer(config)
        self.query_builder = QueryBuilder()
        self.budget_manager = BudgetManager(TokenBudget())
        self.formatter = ContextFormatter()
        self._last_stats: Optional[InjectionStats] = None

    def get_session_start_context(
        self,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Generate context for session start.

        Retrieves and formats:
        - All user preferences
        - Recent decisions (last 30 days)
        - Important file context
        - Recent error patterns
        """
        budget = max_tokens or self.config.session_start_budget

        memories = []

        # Get preferences (all of them, high value)
        preferences = self.retriever.store.get_by_type(
            MemoryType.PREFERENCE,
            limit=20,
        )
        for pref in preferences:
            memories.append(MemorySearchResult(
                entry=pref,
                score=1.0,  # Maximum score for preferences
                match_type="direct",
            ))

        # Get recent decisions
        decisions = self.retriever.store.list_memories(
            memory_type=MemoryType.DECISION,
            limit=10,
            since_days=30,
        )
        for dec in decisions:
            score = self.scorer.score_for_session_start(dec)
            memories.append(MemorySearchResult(
                entry=dec,
                score=score,
                match_type="recency",
            ))

        # Get file context (most accessed)
        file_context = self.retriever.store.list_memories(
            memory_type=MemoryType.FILE_CONTEXT,
            limit=10,
        )
        for fc in file_context:
            score = self.scorer.score_for_session_start(fc)
            memories.append(MemorySearchResult(
                entry=fc,
                score=score,
                match_type="importance",
            ))

        # Get error patterns
        errors = self.retriever.store.list_memories(
            memory_type=MemoryType.ERROR_PATTERN,
            limit=5,
            since_days=60,
        )
        for err in errors:
            score = self.scorer.score_for_session_start(err)
            memories.append(MemorySearchResult(
                entry=err,
                score=score,
                match_type="recency",
            ))

        # Truncate to budget
        selected = self.budget_manager.truncate_by_importance(
            memories,
            budget,
        )

        # Track stats
        self._last_stats = self._compute_stats(selected, budget)

        # Format for injection
        return self.formatter.format_system_prompt_addition(
            selected,
            max_tokens=budget,
        )

    def get_prompt_context(
        self,
        user_prompt: str,
        session_context: "SessionContext",
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Generate context specific to the user's prompt.

        Uses semantic search to find relevant memories.
        """
        budget = max_tokens or self.config.per_prompt_budget

        # Build optimized query
        query = self.query_builder.build_query(user_prompt, session_context)

        # Search
        results = self.retriever.get_relevant(
            query=query.query,
            memory_types=query.memory_types,
            limit=query.limit,
        )

        # Filter by threshold
        filtered = [
            r for r in results
            if r.score >= self.config.min_relevance_score
        ]

        if not filtered:
            return ""

        # Re-score with full algorithm
        scored = []
        for result in filtered:
            full_score = self.scorer.compute_full_score(
                result,
                user_prompt,
                session_context,
            )
            scored.append((result, full_score))

        # Sort by full score
        scored.sort(key=lambda x: x[1], reverse=True)

        # Update results with full scores
        for result, score in scored:
            result.score = score

        # Truncate to budget
        selected = self.budget_manager.truncate_by_importance(
            [r for r, _ in scored],
            budget,
        )

        # Track stats
        self._last_stats = self._compute_stats(selected, budget)

        # Format for injection
        return self.formatter.format_inline_context(
            selected,
            max_tokens=budget,
        )

    def get_error_context(
        self,
        error: str,
        tool_name: str,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Get relevant error patterns for a tool failure.
        """
        budget = max_tokens or self.config.error_context_budget

        # Search for similar errors
        results = self.retriever.get_relevant(
            query=error,
            memory_types=[MemoryType.ERROR_PATTERN],
            limit=5,
        )

        # Filter by high relevance only for errors
        filtered = [
            r for r in results
            if r.score >= 0.50  # Higher threshold for errors
        ]

        if not filtered:
            return ""

        return self.formatter.format_error_context(
            filtered,
            max_tokens=budget,
        )

    def last_injection_stats(self) -> Optional[InjectionStats]:
        """Get statistics from last injection operation."""
        return self._last_stats

    def _compute_stats(
        self,
        memories: List[MemorySearchResult],
        budget: int,
    ) -> InjectionStats:
        """Compute injection statistics."""
        by_type = {}
        for mem in memories:
            type_key = mem.entry.memory_type.value
            by_type[type_key] = by_type.get(type_key, 0) + 1

        tokens_used = sum(
            len(m.entry.content) // 4
            for m in memories
        )

        avg_relevance = (
            sum(m.score for m in memories) / len(memories)
            if memories else 0.0
        )

        return InjectionStats(
            memories_considered=len(memories),
            memories_injected=len(memories),
            tokens_used=tokens_used,
            tokens_budget=budget,
            by_type=by_type,
            avg_relevance=avg_relevance,
        )
```

### 8.2 Caching Strategy

```python
class EmbeddingCache:
    """
    Cache embeddings to avoid recomputation.

    Uses LRU cache with TTL for query embeddings.
    Memory embeddings are stored in SQLite with the memory.
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 3600):
        self._cache: Dict[str, CacheEntry] = {}
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds

    def get(self, text: str) -> Optional[List[float]]:
        """Get cached embedding if available and not expired."""
        key = self._hash(text)
        entry = self._cache.get(key)

        if entry is None:
            return None

        if entry.is_expired(self.ttl_seconds):
            del self._cache[key]
            return None

        entry.access()
        return entry.embedding

    def put(self, text: str, embedding: List[float]) -> None:
        """Cache an embedding."""
        if len(self._cache) >= self.max_size:
            self._evict_oldest()

        key = self._hash(text)
        self._cache[key] = CacheEntry(embedding=embedding)

    def _hash(self, text: str) -> str:
        """Hash text for cache key."""
        return hashlib.sha256(text.encode()).hexdigest()[:32]

    def _evict_oldest(self) -> None:
        """Evict least recently accessed entries."""
        if not self._cache:
            return

        # Sort by last access time
        sorted_entries = sorted(
            self._cache.items(),
            key=lambda x: x[1].last_access,
        )

        # Remove oldest 10%
        evict_count = max(1, len(self._cache) // 10)
        for key, _ in sorted_entries[:evict_count]:
            del self._cache[key]


@dataclass
class CacheEntry:
    """Cache entry with access tracking."""

    embedding: List[float]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_access: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    access_count: int = 0

    def access(self) -> None:
        """Record access."""
        self.last_access = datetime.now(timezone.utc)
        self.access_count += 1

    def is_expired(self, ttl_seconds: int) -> bool:
        """Check if entry is expired."""
        age = (datetime.now(timezone.utc) - self.created_at).total_seconds()
        return age > ttl_seconds
```

### 8.3 Performance Optimization

```python
class PerformanceOptimizer:
    """
    Optimize memory injection performance.

    Goals:
    - < 50ms for per-prompt injection
    - < 200ms for session start injection
    - Minimal memory overhead
    """

    def __init__(self, config: InjectionConfig):
        self.config = config
        self.embedding_cache = EmbeddingCache()
        self._precomputed_session_context: Optional[str] = None
        self._precompute_time: Optional[datetime] = None

    async def precompute_session_context(self) -> None:
        """
        Precompute session start context in background.

        Called when Sugar initializes or when significant
        memory changes occur.
        """
        # This runs in background, doesn't block session start
        pass

    def get_session_context_fast(self) -> Optional[str]:
        """
        Get precomputed session context if available and fresh.

        Returns None if context needs recomputation.
        """
        if self._precomputed_session_context is None:
            return None

        if self._precompute_time is None:
            return None

        age = (datetime.now(timezone.utc) - self._precompute_time).total_seconds()

        # Stale after 5 minutes
        if age > 300:
            return None

        return self._precomputed_session_context

    def should_inject(
        self,
        prompt_length: int,
        session_memory_count: int,
    ) -> bool:
        """
        Quick check if injection is worthwhile.

        Skip injection for:
        - Very short prompts (< 20 chars)
        - Already memory-heavy sessions
        - Disabled in config
        """
        if not self.config.auto_inject_enabled:
            return False

        if prompt_length < 20:
            return False

        # Skip if session already has lots of injected context
        if session_memory_count > 20:
            return False

        return True
```

---

## 9. Testing Strategy

### 9.1 Unit Tests

```python
# tests/memory/test_injector.py

class TestContextInjector:
    """Unit tests for context injection."""

    def test_session_start_includes_preferences(self, injector, store):
        """Preferences should always be included in session start."""
        # Setup: Add preferences
        store.store(MemoryEntry(
            id="pref-1",
            memory_type=MemoryType.PREFERENCE,
            content="Always use type hints",
        ))

        # Act
        context = injector.get_session_start_context()

        # Assert
        assert "type hints" in context.lower()

    def test_per_prompt_respects_threshold(self, injector, store):
        """Only memories above threshold should be injected."""
        # Setup: Add memory
        store.store(MemoryEntry(
            id="dec-1",
            memory_type=MemoryType.DECISION,
            content="Use Redis for caching",
        ))

        # Act: Query for unrelated topic
        context = injector.get_prompt_context(
            user_prompt="How do I parse JSON?",
            session_context=SessionContext(),
        )

        # Assert: Should not include irrelevant memory
        assert "redis" not in context.lower()

    def test_token_budget_respected(self, injector, store):
        """Injected context should respect token budget."""
        # Setup: Add many memories
        for i in range(50):
            store.store(MemoryEntry(
                id=f"dec-{i}",
                memory_type=MemoryType.DECISION,
                content=f"Decision {i}: " + "x" * 100,
            ))

        # Act
        context = injector.get_session_start_context(max_tokens=500)

        # Assert
        estimated_tokens = len(context) // 4
        assert estimated_tokens <= 550  # Allow small buffer
```

### 9.2 Relevance Accuracy Tests

```python
# tests/memory/test_relevance.py

class TestRelevanceScoring:
    """Test relevance scoring accuracy."""

    @pytest.mark.parametrize("query,expected_memory,should_match", [
        ("fix the login bug", "Login timeout caused by missing await", True),
        ("fix the login bug", "Use PostgreSQL for the database", False),
        ("add authentication", "JWT vs session tokens decision", True),
        ("add authentication", "CSS styling preferences", False),
    ])
    def test_semantic_relevance(
        self,
        retriever,
        query,
        expected_memory,
        should_match,
    ):
        """Test that semantic search finds relevant memories."""
        # Setup
        retriever.store.store(MemoryEntry(
            id="test-1",
            memory_type=MemoryType.DECISION,
            content=expected_memory,
        ))

        # Act
        results = retriever.get_relevant(query, limit=5)

        # Assert
        found = any(
            expected_memory in r.entry.content
            for r in results
        )
        assert found == should_match
```

### 9.3 Performance Benchmarks

```python
# tests/memory/test_performance.py

class TestInjectionPerformance:
    """Performance benchmarks for memory injection."""

    @pytest.mark.benchmark
    def test_per_prompt_injection_latency(self, injector, benchmark):
        """Per-prompt injection should complete in < 50ms."""
        def inject():
            return injector.get_prompt_context(
                user_prompt="Fix the authentication bug",
                session_context=SessionContext(),
            )

        result = benchmark(inject)
        assert benchmark.stats.mean < 0.050  # 50ms

    @pytest.mark.benchmark
    def test_session_start_latency(self, injector, benchmark):
        """Session start injection should complete in < 200ms."""
        def inject():
            return injector.get_session_start_context()

        result = benchmark(inject)
        assert benchmark.stats.mean < 0.200  # 200ms

    @pytest.mark.benchmark
    def test_large_memory_store(self, injector, store, benchmark):
        """Should handle 10k+ memories efficiently."""
        # Setup: Add many memories
        for i in range(10000):
            store.store(MemoryEntry(
                id=f"mem-{i}",
                memory_type=MemoryType.DECISION,
                content=f"Decision about feature {i}",
            ))

        def inject():
            return injector.get_prompt_context(
                user_prompt="Feature implementation",
                session_context=SessionContext(),
            )

        result = benchmark(inject)
        assert benchmark.stats.mean < 0.100  # 100ms even with 10k memories
```

### 9.4 Integration Tests

```python
# tests/integration/test_opencode_integration.py

class TestOpenCodeIntegration:
    """Integration tests with OpenCode hooks."""

    @pytest.mark.integration
    async def test_session_lifecycle(self, plugin, mock_session):
        """Test full session lifecycle with memory injection."""
        # Session start
        start_result = await plugin.on_session_start(mock_session)
        assert "system_prompt_addition" in start_result

        # First prompt
        prompt_result = await plugin.on_before_prompt(
            mock_session,
            "Fix the authentication bug",
        )
        # May or may not inject depending on relevance

        # Tool result
        tool_result = await plugin.on_tool_result(
            mock_session,
            tool_name="Edit",
            tool_input={"file_path": "auth.py"},
            tool_result={"success": True},
        )

        # Session end
        await plugin.on_session_end(
            mock_session,
            conversation=[{"role": "user", "content": "Fix auth"}],
        )

    @pytest.mark.integration
    async def test_learning_capture_on_decision(self, plugin, mock_session):
        """Test that decisions are captured from conversation."""
        conversation = [
            {"role": "user", "content": "Should we use JWT or sessions?"},
            {"role": "assistant", "content": "I decided to use JWT with RS256..."},
        ]

        await plugin.on_session_end(mock_session, conversation)

        # Verify decision was captured
        results = plugin.store.search(MemoryQuery(query="JWT RS256"))
        assert len(results) > 0
        assert results[0].entry.memory_type == MemoryType.DECISION
```

### 9.5 User Experience Validation

```python
# tests/ux/test_user_experience.py

class TestUserExperience:
    """Validate user experience aspects."""

    def test_no_duplicate_injections(self, injector):
        """Same memory shouldn't be injected twice in a session."""
        context1 = injector.get_prompt_context("Fix auth", SessionContext())
        context2 = injector.get_prompt_context("Fix auth again", SessionContext())

        # If same memories relevant, should be de-duped in session
        # Implementation tracks what's been injected

    def test_context_formatting_readable(self, injector, store):
        """Injected context should be well-formatted."""
        store.store(MemoryEntry(
            id="pref-1",
            memory_type=MemoryType.PREFERENCE,
            content="Always use type hints in Python code",
        ))

        context = injector.get_session_start_context()

        # Should have clear sections
        assert "###" in context or "**" in context
        # Should not have raw JSON or code artifacts
        assert "{" not in context or "}" not in context

    def test_graceful_degradation_no_memories(self, injector):
        """Should work gracefully with no memories."""
        context = injector.get_session_start_context()

        # Empty or minimal context, no errors
        assert context == "" or len(context) < 100
```

---

## 10. Rollout Plan

### 10.1 Phase 4.1: Core Infrastructure (Week 1-2)

- [ ] Implement `RelevanceScorer` with multi-factor scoring
- [ ] Implement `QueryBuilder` for query optimization
- [ ] Implement `BudgetManager` for token management
- [ ] Implement `ContextFormatter` for LLM-friendly output
- [ ] Add comprehensive unit tests

### 10.2 Phase 4.2: Injection Engine (Week 3-4)

- [ ] Implement `ContextInjector` core class
- [ ] Implement session start injection
- [ ] Implement per-prompt injection
- [ ] Implement error context injection
- [ ] Add integration tests

### 10.3 Phase 4.3: Learning Capture (Week 5-6)

- [ ] Implement `LearningCapture` for automatic extraction
- [ ] Implement `DeduplicationEngine`
- [ ] Implement `ImportanceScorer`
- [ ] Add pattern detection for decisions/preferences
- [ ] Add tests for capture accuracy

### 10.4 Phase 4.4: OpenCode Integration (Week 7-8)

- [ ] Implement OpenCode plugin hooks
- [ ] Enhance MCP tools for on-demand injection
- [ ] Add configuration options
- [ ] Performance optimization
- [ ] End-to-end testing

### 10.5 Phase 4.5: Polish & Documentation (Week 9)

- [ ] Performance benchmarks and optimization
- [ ] User documentation
- [ ] Migration guide for existing users
- [ ] Beta testing with select users

---

## 11. Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Per-prompt injection latency | < 50ms | Benchmark tests |
| Session start injection latency | < 200ms | Benchmark tests |
| Relevance precision | > 80% | User feedback surveys |
| Token budget compliance | 100% | Automated tests |
| Learning capture accuracy | > 70% | Manual review of captured learnings |
| User satisfaction | > 4.0/5 | Survey after 30 days |
| Memory usage reduction | 20% fewer repeated explanations | Token usage comparison |

---

## 12. Open Questions

1. **Conversation continuation**: How should injection behave when continuing a previous conversation vs starting fresh?

2. **Multi-project memory sharing**: Should users be able to explicitly share memories between projects?

3. **Memory conflicts**: What happens when two memories contradict each other (e.g., old decision vs new decision)?

4. **Real-time learning**: Should we capture learnings during the session or only at session end?

5. **User override**: How can users disable injection for specific prompts without disabling globally?

---

## Appendix A: API Reference

See `sugar/memory/injector.py` for full API documentation.

## Appendix B: Configuration Reference

See section 6 for complete configuration schema.

## Appendix C: Migration Guide

For users upgrading from Phase 3, memory injection is disabled by default. Enable with:

```yaml
memory:
  injection:
    enabled: true
```

No data migration required - existing memories are automatically available for injection.
