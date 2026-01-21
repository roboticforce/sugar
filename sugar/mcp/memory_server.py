"""
Sugar Memory MCP Server

Provides MCP (Model Context Protocol) server for Sugar memory system,
allowing Claude Code and other MCP clients to access persistent memory.

Uses FastMCP for simplified server implementation.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Check for FastMCP availability
try:
    from mcp.server.fastmcp import FastMCP

    FASTMCP_AVAILABLE = True
except ImportError:
    try:
        from fastmcp import FastMCP

        FASTMCP_AVAILABLE = True
    except ImportError:
        FASTMCP_AVAILABLE = False
        FastMCP = None


def get_memory_store():
    """Get memory store from Sugar project context."""
    from sugar.memory import MemoryStore

    # Try to find .sugar directory
    cwd = Path.cwd()
    sugar_dir = cwd / ".sugar"

    if not sugar_dir.exists():
        # Check parent directories
        for parent in cwd.parents:
            potential = parent / ".sugar"
            if potential.exists():
                sugar_dir = potential
                break

    if not sugar_dir.exists():
        raise RuntimeError("Not in a Sugar project. Run 'sugar init' first.")

    memory_db = sugar_dir / "memory.db"
    return MemoryStore(str(memory_db))


def create_memory_mcp_server() -> "FastMCP":
    """Create and configure the Sugar Memory MCP server."""
    if not FASTMCP_AVAILABLE:
        raise ImportError(
            "FastMCP not available. Install with: pip install 'sugarai[memory]'"
        )

    mcp = FastMCP("Sugar Memory")

    @mcp.tool()
    async def search_memory(query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Search Sugar memory for relevant context.

        Use this to find previous decisions, preferences, error patterns,
        and other relevant information from past sessions.

        Args:
            query: Natural language search query
            limit: Maximum results to return (default: 5)

        Returns:
            List of matching memories with content, type, and relevance score
        """
        from sugar.memory import MemoryQuery

        try:
            store = get_memory_store()
            search_query = MemoryQuery(query=query, limit=limit)
            results = store.search(search_query)
            store.close()

            return [
                {
                    "content": r.entry.content,
                    "type": r.entry.memory_type.value,
                    "score": round(r.score, 3),
                    "id": r.entry.id[:8],
                    "created_at": (
                        r.entry.created_at.isoformat() if r.entry.created_at else None
                    ),
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"search_memory failed: {e}")
            return [{"error": str(e)}]

    @mcp.tool()
    async def store_learning(
        content: str,
        memory_type: str = "decision",
        tags: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Store a new learning, decision, or observation in Sugar memory.

        Use this to remember important information for future sessions:
        - Decisions made during implementation
        - User preferences discovered
        - Error patterns and their fixes
        - Research findings

        Args:
            content: What to remember (be specific and detailed)
            memory_type: Type of memory (decision, preference, research, error_pattern, file_context, outcome)
            tags: Optional comma-separated tags for organization

        Returns:
            Confirmation with memory ID
        """
        import uuid
        from sugar.memory import MemoryEntry, MemoryType

        try:
            store = get_memory_store()

            # Validate memory type
            try:
                mem_type = MemoryType(memory_type)
            except ValueError:
                mem_type = MemoryType.DECISION

            # Parse tags
            metadata = {}
            if tags:
                metadata["tags"] = [t.strip() for t in tags.split(",")]

            entry = MemoryEntry(
                id=str(uuid.uuid4()),
                memory_type=mem_type,
                content=content,
                summary=content[:100] if len(content) > 100 else None,
                metadata=metadata,
            )

            entry_id = store.store(entry)
            store.close()

            return {
                "status": "stored",
                "id": entry_id[:8],
                "type": mem_type.value,
                "content_preview": (
                    content[:100] + "..." if len(content) > 100 else content
                ),
            }
        except Exception as e:
            logger.error(f"store_learning failed: {e}")
            return {"error": str(e)}

    @mcp.tool()
    async def get_project_context() -> Dict[str, Any]:
        """
        Get current project context summary from Sugar memory.

        Returns an organized summary of:
        - User preferences (coding style, conventions)
        - Recent decisions (architecture, implementation choices)
        - Known error patterns and fixes
        - File context (what files do what)

        Use this at the start of a task to understand project context.
        """
        from sugar.memory import MemoryRetriever

        try:
            store = get_memory_store()
            retriever = MemoryRetriever(store)
            context = retriever.get_project_context(limit=10)
            store.close()

            return context
        except Exception as e:
            logger.error(f"get_project_context failed: {e}")
            return {"error": str(e)}

    @mcp.tool()
    async def recall(topic: str) -> str:
        """
        Get memories about a specific topic, formatted as readable context.

        Similar to search_memory but returns formatted markdown suitable
        for injection into prompts or context.

        Args:
            topic: The topic to recall information about

        Returns:
            Markdown-formatted context from relevant memories
        """
        from sugar.memory import MemoryQuery, MemoryRetriever

        try:
            store = get_memory_store()
            retriever = MemoryRetriever(store)

            search_query = MemoryQuery(query=topic, limit=5)
            results = store.search(search_query)
            store.close()

            if not results:
                return f"No memories found about: {topic}"

            return retriever.format_for_prompt(results, max_tokens=1500)
        except Exception as e:
            logger.error(f"recall failed: {e}")
            return f"Error recalling memories: {e}"

    @mcp.tool()
    async def list_recent_memories(
        memory_type: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        List recent memories, optionally filtered by type.

        Args:
            memory_type: Optional filter (decision, preference, research, error_pattern, file_context, outcome)
            limit: Maximum memories to return (default: 10)

        Returns:
            List of recent memories
        """
        from sugar.memory import MemoryType

        try:
            store = get_memory_store()

            type_filter = None
            if memory_type:
                try:
                    type_filter = MemoryType(memory_type)
                except ValueError:
                    pass

            entries = store.list_memories(
                memory_type=type_filter,
                limit=limit,
            )
            store.close()

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
        except Exception as e:
            logger.error(f"list_recent_memories failed: {e}")
            return [{"error": str(e)}]

    @mcp.resource("sugar://project/context")
    async def project_context_resource() -> str:
        """
        Current project context from Sugar memory.

        This resource provides a markdown summary of the project's
        preferences, recent decisions, and error patterns.
        """
        from sugar.memory import MemoryRetriever

        try:
            store = get_memory_store()
            retriever = MemoryRetriever(store)
            context = retriever.get_project_context(limit=10)
            output = retriever.format_context_markdown(context)
            store.close()

            return (
                output
                if output
                else "# No project context available yet\n\nUse `store_learning` to add memories."
            )
        except Exception as e:
            return f"# Error loading project context\n\n{e}"

    @mcp.resource("sugar://preferences")
    async def preferences_resource() -> str:
        """User coding preferences stored in Sugar memory."""
        from sugar.memory import MemoryType

        try:
            store = get_memory_store()
            preferences = store.get_by_type(MemoryType.PREFERENCE, limit=20)
            store.close()

            if not preferences:
                return "# No preferences stored yet\n\nUse `store_learning` with type='preference' to add preferences."

            lines = ["# User Preferences", ""]
            for p in preferences:
                lines.append(f"- {p.content}")

            return "\n".join(lines)
        except Exception as e:
            return f"# Error loading preferences\n\n{e}"

    return mcp


def run_memory_server(transport: str = "stdio"):
    """Run the Sugar Memory MCP server."""
    if not FASTMCP_AVAILABLE:
        raise ImportError(
            "FastMCP not available. Install with: pip install 'sugarai[memory]'"
        )

    mcp = create_memory_mcp_server()

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        raise ValueError(f"Unsupported transport: {transport}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_memory_server()
