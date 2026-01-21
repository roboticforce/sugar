#!/usr/bin/env python3
"""
Sugar Memory Token Savings Demo

This script demonstrates how Sugar's memory system saves tokens
compared to re-explaining context in every Claude Code session.

Run: python examples/token_savings_demo.py
"""

from pathlib import Path
import sys

# Add sugar to path if running from examples dir
sys.path.insert(0, str(Path(__file__).parent.parent))

from sugar.memory.store import MemoryStore
from sugar.memory.types import MemoryQuery, MemoryType


def estimate_tokens(text: str) -> int:
    """Estimate token count (~1.3 tokens per word for English)."""
    if not text:
        return 0
    return int(len(text.split()) * 1.3)


def main():
    print("=" * 70)
    print("🧠 SUGAR MEMORY: TOKEN SAVINGS DEMONSTRATION")
    print("=" * 70)

    # Try to find a project with memories (check local first, then global)
    possible_paths = [
        Path.cwd() / ".sugar" / "memory.db",  # Project-local first
        Path.home() / ".sugar" / "memory.db",  # Global fallback
    ]

    db_path = None
    for path in possible_paths:
        if path.exists():
            db_path = path
            break

    if not db_path:
        print("\n⚠️  No memory database found. Creating demo with sample data...\n")
        demo_with_sample_data()
        return

    print(f"\n📂 Using memory database: {db_path}\n")
    demo_with_real_data(db_path)


def demo_with_sample_data():
    """Demonstrate savings with hypothetical data."""

    # Typical project context a developer might explain
    sample_context = {
        "architecture": """
            Our app uses FastAPI backend with PostgreSQL, Next.js frontend with TypeScript.
            Authentication via Clerk, deployed on Digital Ocean with Kamal.
            CI/CD through GitHub Actions, tests required before merge.
        """,
        "database": """
            Main tables: users, organizations, projects, tasks, comments.
            Using Alembic for migrations, always create reversible migrations.
            Foreign keys use ON DELETE CASCADE for child records.
        """,
        "conventions": """
            Conventional commits (feat:, fix:, docs:), gitflow branching.
            Python uses Black + Ruff, TypeScript uses ESLint + Prettier.
            PR reviews required, no direct pushes to main.
        """,
        "domain": """
            Users belong to organizations, organizations have projects.
            Projects contain tasks with status workflow: todo → in_progress → done.
            Comments support @mentions which trigger notifications.
        """,
    }

    # Compressed memory summaries (what Sugar stores)
    memory_summaries = {
        "architecture": "FastAPI + PostgreSQL backend, Next.js + TS frontend, Clerk auth, DO + Kamal deploy",
        "database": "Tables: users, orgs, projects, tasks, comments. Alembic migrations, reversible only",
        "conventions": "Conventional commits, gitflow, Black/Ruff for Python, ESLint/Prettier for TS",
        "domain": "Users → Orgs → Projects → Tasks (todo/in_progress/done). Comments with @mentions",
    }

    print("📋 SCENARIO: Developer explains project context to Claude Code\n")
    print("-" * 70)

    total_full = 0
    total_memory = 0

    for topic, full_text in sample_context.items():
        full_tokens = estimate_tokens(full_text)
        memory_tokens = estimate_tokens(memory_summaries[topic])
        total_full += full_tokens
        total_memory += memory_tokens

        print(f"  {topic.upper()}:")
        print(f"    Full explanation: ~{full_tokens} tokens")
        print(f"    Memory summary:   ~{memory_tokens} tokens")
        print(f"    Compression:      {(1 - memory_tokens/full_tokens)*100:.0f}% smaller")
        print()

    print("-" * 70)
    print(f"  TOTALS:")
    print(f"    All context (full): ~{total_full} tokens")
    print(f"    All summaries:      ~{total_memory} tokens")
    print(f"    Compression:        {(1 - total_memory/total_full)*100:.0f}% reduction")

    print_savings_projection(total_full, total_memory)


def demo_with_real_data(db_path: Path):
    """Demonstrate savings with actual memory data."""

    store = MemoryStore(str(db_path))
    conn = store._get_connection()
    cursor = conn.cursor()

    # Get memory statistics
    cursor.execute("""
        SELECT
            COUNT(*) as count,
            SUM(LENGTH(content)) as total_content,
            SUM(LENGTH(COALESCE(summary, ''))) as total_summary,
            memory_type
        FROM memory_entries
        GROUP BY memory_type
    """)

    stats = cursor.fetchall()

    if not stats or all(s[0] == 0 for s in stats):
        print("📭 No memories stored yet. Run Sugar to build context!\n")
        demo_with_sample_data()
        return

    print("📊 YOUR MEMORY STATISTICS:\n")
    print("-" * 70)

    grand_total_content = 0
    grand_total_summary = 0

    for count, content_bytes, summary_bytes, mem_type in stats:
        content_bytes = content_bytes or 0
        summary_bytes = summary_bytes or 0

        # Estimate tokens from character count (~4 chars per token)
        content_tokens = content_bytes // 4
        summary_tokens = summary_bytes // 4

        grand_total_content += content_tokens
        grand_total_summary += summary_tokens

        compression = (1 - summary_tokens/content_tokens)*100 if content_tokens > 0 else 0

        print(f"  {mem_type or 'general'}: {count} memories")
        print(f"    Full content:  ~{content_tokens:,} tokens")
        print(f"    Summaries:     ~{summary_tokens:,} tokens")
        print(f"    Compression:   {compression:.0f}% smaller")
        print()

    print("-" * 70)
    print(f"  GRAND TOTAL:")
    print(f"    All content:    ~{grand_total_content:,} tokens")
    print(f"    All summaries:  ~{grand_total_summary:,} tokens")

    if grand_total_content > 0:
        compression = (1 - grand_total_summary/grand_total_content)*100
        print(f"    Compression:    {compression:.0f}% reduction")

    # Use summary size for memory retrieval estimate
    avg_retrieval = grand_total_summary // len(stats) if stats else 100
    print_savings_projection(grand_total_content // len(stats), avg_retrieval)

    # Show example searches
    print("\n" + "=" * 70)
    print("🔍 EXAMPLE: TARGETED RETRIEVAL vs LOADING EVERYTHING")
    print("=" * 70)

    # Get a sample memory to show
    cursor.execute("SELECT content, summary FROM memory_entries LIMIT 1")
    row = cursor.fetchone()

    if row:
        content, summary = row
        content_tokens = estimate_tokens(content) if content else 0
        summary_tokens = estimate_tokens(summary) if summary else 0

        print(f"\n  Single memory retrieval:")
        print(f"    Full content: ~{content_tokens} tokens")
        print(f"    Summary only: ~{summary_tokens} tokens")
        print(f"    You save: ~{content_tokens - summary_tokens} tokens per retrieval")

        if content:
            preview = content[:100] + "..." if len(content) > 100 else content
            print(f"\n  Content preview: {preview}")
        if summary:
            print(f"  Summary: {summary}")


def print_savings_projection(full_context_tokens: int, memory_tokens: int):
    """Print token savings projections over time."""

    print("\n" + "=" * 70)
    print("💰 TOKEN SAVINGS PROJECTION")
    print("=" * 70)

    # Scale up to realistic project size if sample data is small
    # Real projects have 1000-5000 tokens of context
    if full_context_tokens < 500:
        scale_factor = 10
        full_context_tokens *= scale_factor
        memory_tokens *= scale_factor
        print(f"\n  (Scaled up {scale_factor}x to simulate real project size)\n")

    # Assume: without memory, user explains ~30% of context per session
    # With memory: 1-2 targeted retrievals per session (just what's needed)
    context_per_session = int(full_context_tokens * 0.3)
    retrieval_per_session = int(memory_tokens * 0.4)  # Only retrieve what's relevant

    savings = context_per_session - retrieval_per_session
    savings_pct = (savings / context_per_session * 100) if context_per_session > 0 else 0

    print(f"""
  Assumptions:
  • Without memory: You re-explain ~30% of project context per session
  • With memory: Targeted retrieval of just what's needed (~40% of summaries)

  Per-session comparison:
  • Without memory: ~{context_per_session} tokens
  • With memory:    ~{retrieval_per_session} tokens
  • Savings:        ~{savings} tokens ({savings_pct:.0f}% reduction)
""")

    print("  Cumulative savings over time:")
    print("  " + "-" * 50)

    sessions_list = [10, 50, 100, 500]
    for sessions in sessions_list:
        without = context_per_session * sessions
        with_mem = retrieval_per_session * sessions
        saved = without - with_mem

        # Cost at ~$15/M tokens (Claude pricing ballpark)
        cost_saved = saved * 15 / 1_000_000

        print(f"  {sessions:>3} sessions: ~{saved:>6,} tokens saved (${cost_saved:.2f})")

    print("""
  📈 The more you use Claude Code, the more you save!

  Additional benefits not captured here:
  • Faster responses (less context to process)
  • More consistent answers (authoritative memory)
  • Better recall of project decisions and patterns
""")


if __name__ == "__main__":
    main()
