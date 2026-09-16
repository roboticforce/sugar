"""Sugar memory provider plugin for Hermes.

Sugar is a local-first persistent memory layer for AI coding agents: decisions,
preferences, error patterns, file context, research, outcomes, and guidelines
stored in SQLite on the user's machine (project ``.sugar/memory.db`` plus global
``~/.sugar/memory.db``), with project-first search that reserves slots for
cross-project guidelines.

Provider name: ``sugar`` - activate with ``memory.provider: sugar`` in Hermes
config (or via ``hermes memory setup``). The ``sugarai`` package must be
installed in the same Python environment as Hermes
(``pip install sugarai==<release>``); nothing is ever sent off the machine.

Layout follows the standalone memory-provider contract: ``register(ctx)`` calls
``ctx.register_memory_provider(...)``. Heavy imports are lazy so module import
stays cheap and ``hermes plugins validate`` can probe this file in a bare env.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

try:
    # Hermes >= 0.20: recall indicator + trivial-prompt gate are core helpers.
    from agent.memory_provider import (
        RecallStatus,
        is_trivial_prompt,
        spawn_context_thread,
    )
except ImportError:  # Hermes 0.19 and older: provide local fallbacks
    import re as _re
    import threading as _threading

    RecallStatus = None  # type: ignore[assignment,misc]

    _TRIVIAL_RE = _re.compile(
        r"^(yes|no|ok|okay|sure|thanks|thank you|y|n|yep|nope|yeah|nah|"
        r"hi|hey|hello|yo|sup|continue|go ahead|do it|proceed|got it|cool|"
        r"nice|great|done|next|lgtm|k)[\s!?.:;,]*$",
        _re.IGNORECASE,
    )

    def is_trivial_prompt(text: Optional[str]) -> bool:
        stripped = (text or "").strip()
        if not stripped or stripped.startswith("/"):
            return True
        return bool(_TRIVIAL_RE.match(stripped))

    def spawn_context_thread(
        target,
        *,
        name: str,
        daemon: bool = True,
        args: tuple = (),
        kwargs: Optional[Dict[str, Any]] = None,
    ):
        return _threading.Thread(
            target=target, args=args, kwargs=kwargs or {}, name=name, daemon=daemon
        )


logger = logging.getLogger(__name__)

# Tool schemas the agent sees. Names are prefixed "sugar_" so they cannot
# collide with Hermes built-ins or other providers' tools.
_STORE_SCHEMA = {
    "name": "sugar_store",
    "description": (
        "Store a durable learning in Sugar's local memory: a decision, "
        "preference, error pattern, research finding, or outcome. Use after "
        "completing work or when the user states something worth remembering. "
        "All data stays on this machine."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "What to remember. Be specific and detailed.",
            },
            "memory_type": {
                "type": "string",
                "description": "One of: decision, preference, error_pattern, research, outcome, guideline, file_context.",
                "default": "decision",
            },
            "tags": {
                "type": "string",
                "description": "Optional comma-separated tags, e.g. 'deploy,postgres'.",
            },
            "scope": {
                "type": "string",
                "description": "'project' (default) for this project only, 'global' for knowledge that applies everywhere.",
                "default": "project",
            },
        },
        "required": ["content"],
    },
}

_SEARCH_SCHEMA = {
    "name": "sugar_search",
    "description": (
        "Search Sugar memory by meaning. Project memories rank first and "
        "global guidelines always surface. Call this at the start of a task "
        "and before making decisions that past sessions may already have "
        "covered."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query.",
            },
            "limit": {"type": "integer", "description": "Maximum results (default 5)."},
        },
        "required": ["query"],
    },
}

_LIST_SCHEMA = {
    "name": "sugar_list_recent",
    "description": "List the most recent memories, optionally filtered by type.",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_type": {
                "type": "string",
                "description": "Optional filter: decision, preference, error_pattern, research, outcome, guideline, file_context.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum entries (default 10).",
            },
        },
        "required": [],
    },
}

_SYSTEM_PROMPT_BLOCK = (
    "You have persistent local memory via Sugar (tools: sugar_store, "
    "sugar_search, sugar_list_recent). All memory is stored on this machine "
    "only. At the start of non-trivial work, use sugar_search to surface "
    "prior decisions, error patterns, and guidelines for the topic. After "
    "completing work or when the user states a durable preference or "
    "correction, record it with sugar_store (scope 'global' for "
    "cross-project standards, 'project' for context specific to this "
    "project). Types map what you learned: decision, preference, "
    "error_pattern, research, outcome, guideline, file_context."
)

VALID_TYPES = (
    "decision",
    "preference",
    "error_pattern",
    "research",
    "outcome",
    "guideline",
    "file_context",
)


def _load_provider_config() -> Dict[str, Any]:
    """Env vars provide defaults; $HERMES_HOME/sugar.json overrides. Mirrors the
    mem0 layering so a partial JSON file cannot silently drop env settings."""
    config: Dict[str, Any] = {
        "project_dir": os.environ.get("SUGAR_PROJECT_DIR", "").strip(),
        "sync_turns": os.environ.get("SUGAR_SYNC_TURNS", "").strip().lower()
        in ("1", "true", "yes"),
        "search_limit": int(os.environ.get("SUGAR_SEARCH_LIMIT", "5") or "5"),
    }
    try:
        from hermes_constants import get_hermes_home

        config_file = Path(get_hermes_home()) / "sugar.json"
        if config_file.exists():
            stored = json.loads(config_file.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                for key in config:
                    if key in stored and stored[key] not in (None, ""):
                        config[key] = stored[key]
                config["project_dir"] = str(config["project_dir"]).strip()
        return config
    except (
        Exception
    ) as e:  # no hermes_constants in the validate probe env, bad JSON, ...
        logger.debug("sugar.json config load failed (%s); using env defaults", e)
        return config


class SugarMemoryProvider(MemoryProvider):
    """Hermes MemoryProvider backed by Sugar's local-first memory stores.

    Lifecycle: one GlobalMemoryManager per provider instance, opened on first
    use, closed on shutdown. ``prefetch`` returns the cached recall block and
    ``queue_prefetch`` refreshes it on a context-bound background thread, per
    the MemoryProvider contract ("recall in the background, return cache").
    """

    def __init__(self) -> None:
        self._manager: Any = None
        self._lock = threading.Lock()
        self._session_id: str = ""
        self._agent_context: str = "primary"
        self._config: Dict[str, Any] = {}
        self._cached_block: str = ""
        self._cached_count: int = 0
        self._unavailable: str = ""

    @property
    def name(self) -> str:
        return "sugar"

    # -- Availability ---------------------------------------------------------

    def is_available(self) -> bool:
        """sugarai importable in this interpreter? Config/deps only, no network."""
        try:
            import sugar.memory  # noqa: F401

            return True
        except Exception as e:
            self._unavailable = (
                f"the 'sugarai' package is not importable in Hermes's Python "
                f"environment (pip install sugarai): {e}"
            )
            return False

    def unavailable_reason(self) -> str:
        return self._unavailable

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "project_dir",
                "description": "Directory whose .sugar/memory.db is the project store (default: auto-detect from the working directory; leave empty for global memory only).",
                "type": "text",
                "env_var": "SUGAR_PROJECT_DIR",
            },
            {
                "key": "sync_turns",
                "description": "Also store a compact research note per conversation turn (off by default; deliberate stores via sugar_store are preferred).",
                "type": "boolean",
                "default": False,
                "env_var": "SUGAR_SYNC_TURNS",
            },
            {
                "key": "search_limit",
                "description": "Default number of recalled memories per turn.",
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "env_var": "SUGAR_SEARCH_LIMIT",
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        config_file = Path(hermes_home) / "sugar.json"
        existing: Dict[str, Any] = {}
        if config_file.exists():
            try:
                existing = json.loads(config_file.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing.update(values)
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    # -- Lifecycle ------------------------------------------------------------

    def _get_manager(self) -> Any:
        if self._manager is not None:
            return self._manager
        from sugar.memory import MemoryStore
        from sugar.memory.global_store import GlobalMemoryManager

        project_store = None
        project_dir = str(self._config.get("project_dir") or "").strip()
        if project_dir:
            project_store = MemoryStore(str(Path(project_dir) / ".sugar" / "memory.db"))
        else:
            # Same walk-up as the Sugar MCP server: nearest .sugar/ wins.
            cwd = Path.cwd()
            sugar_dir = cwd / ".sugar"
            if not sugar_dir.exists():
                for parent in cwd.parents:
                    potential = parent / ".sugar"
                    if potential.exists():
                        sugar_dir = potential
                        break
            if sugar_dir.exists():
                project_store = MemoryStore(str(sugar_dir / "memory.db"))
        self._manager = GlobalMemoryManager(project_store=project_store)
        return self._manager

    def initialize(self, session_id: str, **kwargs) -> None:
        if not self.is_available():
            return
        self._session_id = session_id
        self._agent_context = str(kwargs.get("agent_context") or "primary")
        self._config = _load_provider_config()
        try:
            self._get_manager()
            logger.info(
                "Sugar memory provider initialized (session %s)", session_id[:8]
            )
        except Exception as e:
            logger.warning("Sugar memory provider failed to initialize: %s", e)
            self._unavailable = str(e)

    def shutdown(self) -> None:
        with self._lock:
            if self._manager is not None:
                try:
                    self._manager.close()
                except Exception as e:
                    logger.debug("Sugar manager close failed: %s", e)
                self._manager = None

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        # Keep the manager (and any warm embedder) alive across sessions; the
        # process-level shutdown() closes it.
        return

    # -- Recall ---------------------------------------------------------------

    def system_prompt_block(self) -> str:
        return _SYSTEM_PROMPT_BLOCK

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return the cached recall block; search synchronously on the first
        turn (SQLite/FTS is millisecond-fast; a warm [memory]-extra embedder
        loads once and is then reused)."""
        with self._lock:
            if self._cached_block:
                return self._cached_block
        if is_trivial_prompt(query) or not self.is_available():
            return ""
        return self._run_recall(query)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if is_trivial_prompt(query) or not self.is_available():
            return
        spawn_context_thread(
            self._run_recall, name="sugar-memory-prefetch", args=(query,)
        )

    def recall_status(self) -> Optional[Any]:
        if RecallStatus is None or not self._cached_block:
            return None
        return RecallStatus(provider_label="Sugar", count=self._cached_count)

    def _run_recall(self, query: str) -> str:
        try:
            results = self._search(
                query, limit=int(self._config.get("search_limit", 5) or 5)
            )
        except Exception as e:
            logger.debug("Sugar recall failed: %s", e)
            return ""
        if not results:
            block = ""
            count = 0
        else:
            lines = ["Relevant context from Sugar memory (local, persistent):"]
            for r in results:
                lines.append(f"- [{r['type']}] {r['content'].strip()}")
            block = "\n".join(lines)
            count = len(results)
        with self._lock:
            self._cached_block = block
            self._cached_count = count
        return block

    # -- Per-turn write -------------------------------------------------------

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        turn_author: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Opt-in compact turn note (SUGAR_SYNC_TURNS / sugar.json sync_turns).
        Off by default: Sugar's model is deliberate stores, not transcript dumps."""
        if not self._config.get("sync_turns"):
            return
        if self._agent_context != "primary":
            return
        text = (user_content or "").strip()
        if not text or is_trivial_prompt(text):
            return
        snippet = text[:500]
        try:
            self._store(
                content=f"Conversation turn (session {self._session_id[:8]}): {snippet}",
                memory_type="research",
                tags="hermes,auto-sync",
                scope="global",
            )
        except Exception as e:
            logger.debug("Sugar sync_turn store failed: %s", e)

    # -- Tools ----------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [_STORE_SCHEMA, _SEARCH_SCHEMA, _LIST_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        args = args or {}
        try:
            if tool_name == "sugar_store":
                return json.dumps(
                    self._store(
                        content=str(args.get("content", "")),
                        memory_type=str(args.get("memory_type") or "decision"),
                        tags=args.get("tags"),
                        scope=str(args.get("scope") or "project"),
                    )
                )
            if tool_name == "sugar_search":
                return json.dumps(
                    self._search(
                        query=str(args.get("query", "")),
                        limit=int(
                            args.get("limit")
                            or self._config.get("search_limit", 5)
                            or 5
                        ),
                    )
                )
            if tool_name == "sugar_list_recent":
                return json.dumps(
                    self._list_recent(
                        memory_type=args.get("memory_type"),
                        limit=int(args.get("limit") or 10),
                    )
                )
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            logger.error("Sugar tool %s failed: %s", tool_name, e)
            return json.dumps({"error": str(e)})

    # -- Internals ------------------------------------------------------------

    def _store(
        self,
        content: str,
        memory_type: str = "decision",
        tags: Any = None,
        scope: str = "project",
    ) -> Dict[str, Any]:
        import uuid

        from sugar.memory import MemoryEntry, MemoryType
        from sugar.memory.types import MemoryScope

        content = (content or "").strip()
        if not content:
            return {"error": "content is required"}
        try:
            mem_type = MemoryType(memory_type)
        except ValueError:
            mem_type = MemoryType.DECISION
        try:
            mem_scope = MemoryScope(scope)
        except ValueError:
            mem_scope = MemoryScope.PROJECT

        metadata: Dict[str, Any] = {}
        if tags:
            if isinstance(tags, (list, tuple)):
                tag_list = [str(t).strip() for t in tags if str(t).strip()]
            else:
                tag_list = [t.strip() for t in str(tags).split(",") if t.strip()]
            if tag_list:
                metadata["tags"] = tag_list
        if self._session_id:
            metadata["session_id"] = self._session_id[:8]

        # Non-primary agent contexts (subagents, cron, flush) never write.
        if self._agent_context != "primary":
            return {
                "status": "skipped",
                "reason": f"agent_context={self._agent_context} is read-only",
            }

        manager = self._get_manager()
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            memory_type=mem_type,
            content=content,
            summary=content[:100] if len(content) > 100 else None,
            metadata=metadata,
        )
        actual_scope = mem_scope
        note = None
        if mem_scope == MemoryScope.PROJECT and manager.project_store is None:
            actual_scope = MemoryScope.GLOBAL
            note = "No Sugar project found - stored in global memory instead."
        entry_id = manager.store(entry, actual_scope)
        result: Dict[str, Any] = {
            "status": "stored",
            "id": entry_id[:8],
            "type": mem_type.value,
            "scope": actual_scope.value,
        }
        if note:
            result["note"] = note
        return result

    def _search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        from sugar.memory import MemoryQuery

        query = (query or "").strip()
        if not query:
            return []
        manager = self._get_manager()
        results = manager.search(MemoryQuery(query=query, limit=limit), limit=limit)
        return [
            {
                "content": r.entry.content,
                "type": r.entry.memory_type.value,
                "score": round(float(r.score), 3),
                "scope": r.scope,
                "id": r.entry.id[:8],
                "created_at": (
                    r.entry.created_at.isoformat() if r.entry.created_at else None
                ),
            }
            for r in results
        ]

    def _list_recent(
        self, memory_type: Any = None, limit: int = 10
    ) -> List[Dict[str, Any]]:
        from sugar.memory import MemoryType

        manager = self._get_manager()
        type_filter = None
        if memory_type:
            try:
                type_filter = MemoryType(str(memory_type))
            except ValueError:
                type_filter = None
        entries = manager.list_memories(memory_type=type_filter, limit=limit)
        return [
            {
                "id": e.id[:8],
                "type": e.memory_type.value,
                "content": (
                    e.content[:200] + "..." if len(e.content) > 200 else e.content
                ),
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ]


def register(ctx) -> None:
    """Hermes plugin entry point."""
    ctx.register_memory_provider(SugarMemoryProvider())
