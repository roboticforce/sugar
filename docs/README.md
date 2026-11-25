# Sugar Documentation

Welcome to the Sugar documentation! Sugar is mission control for your agent fleet:
one local-first task board with atomic claims and leases, an append-only event spine for provenance, cost and
audit, and shared memory that follows you across projects. Claude Code, Codex, Cursor, Goose, OpenCode and any
MCP-capable harness can claim the same work and leave the same durable history - without sending your code,
knowledge or task state to a vendor cloud.

New here? Start with **[Mission Control](mission-control.md)** for the control plane, then the
[Quick Start](user/quick-start.md) and the [CLI reference](user/cli-reference.md).

## 📚 Documentation Structure

### 👥 User Documentation (`docs/user/`)
Documentation for developers who want to **use** Sugar in their projects:

- **[Installation Guide](user/installation-guide.md)** - Complete installation and setup instructions
- **[Quick Start Guide](user/quick-start.md)** - Get up and running in 5 minutes
- **[Memory System](user/memory.md)** - Persistent semantic memory - the foundation for intelligent development
- **[Execution Context](user/execution-context.md)** - Where and how to run Sugar correctly
- **[CLI Reference](user/cli-reference.md)** - All Sugar commands and options
- **[GitHub Integration](user/github-integration.md)** - Connect Sugar to GitHub issues and PRs
- **[Examples](user/examples.md)** - Real-world usage examples
- **[Configuration Best Practices](user/configuration-best-practices.md)** - Essential config patterns and exclusions
- **[Agent SDK Guide](user/agent-sdk.md)** - Native Claude Agent SDK integration (v3.0)
- **[Task Hooks Guide](task-hooks.md)** - Pre/post execution hooks for automation
- **[Thinking Capture Guide](thinking-capture.md)** - View Claude's reasoning during execution
- **[Model Routing Guide](dev/model-routing-guide.md)** - Intelligent model selection by task
- **[Troubleshooting](user/troubleshooting.md)** - Common issues and solutions
- **[FAQ](user/faq.md)** - Frequently asked questions

### 🎛️ Mission Control & Agent Teams

Documentation for watching and steering agents, and for running several harnesses
against one board:

- **[Mission Control](mission-control.md)** - Dashboard, native macOS app, provenance and the event spine: the whole control plane in one place
- **[Web Mission Control](web-mission-control.md)** - The browser dashboard on any OS: Tasks, live Activity over SSE, Coverage, Evidence, near-real-time latency and team access, with screenshots and a live clip
- **[Live web demo](https://roboticforce.io/sugar/demo)** - Interactive, simulated dashboard: Tasks, live Activity, Coverage and Evidence with clickable task details (no install)
- **[Local Dashboard API](local-dashboard.md)** - Frozen REST/SSE contract, board sources (`--board sugar|kanban`), provenance fields, event-spine schema
- **[Sugar as a Team Bus](agent-teams.md)** - Code-grounded analysis: what Sugar already does for multi-agent coordination, the gaps, and the phased plan
- **[Agent Teams: Architecture & Domain Boundaries](agent-teams-architecture.md)** - Wardley map (build vs. adopt vs. commodity), C4-style architecture, DDD bounded contexts, trust boundaries, and the investment split
- **[Adapters](adapters.md)** - The adapter playbook: taxonomy, catalog, non-negotiable rules, canonical mapping, conformance kit and the order to build them in
- **[Storage Port](storage-port.md)** - `TaskStore` contract, the backend-agnostic conformance suite, SQLite-specific semantics, and what a Postgres backend must implement
- **[Board Server](board-server.md)** - Share one board: remote binding, scoped `read`/`write` tokens, browser cookie exchange, TLS-proxy deployment and threat model
- **[Usage & Cost](usage.md)** - Harness-reported usage/cost events, `/api/usage`, `sugar usage`, and the Activity panel: "where is the money going?"
- **[Spend Attribution](spend-attribution.md)** - Task cwd/project provenance, grouping by model/project/task, optional operator price-table estimates ("reported" vs "estimated"), CLI/API examples and limits
- **[Trace Explorer](traces.md)** - Per-session traces and spans (`/api/traces`), Timeline (actor swimlanes, call tree, inspector) and Graph modes, derived insights, DSH multi-agent enrichment and limitations
- **[Telemetry](telemetry.md)** - Live agent activity events (`tool.invoked`, `run.started`, `run.ended`, `agent.activity`, `usage.reported`), `sugar emit`, and the MCP `sugar_emit_event` tool
- **[Telemetry Adapters](telemetry-adapters.md)** - Phase 1b plug-and-play capture: canonical `sugar.telemetry.v1` envelope, L0-L3 fidelity, `sugar connect`/`sugar hook`/`sugar doctor`, Claude Code/Cursor/Codex/Goose/DSH adapters, OTLP protobuf+JSON receiver (`/v1/traces`, `/v1/logs`), ADK/OpenAI/Claude SDK helpers, coverage API/UI, redaction
- **[Harness Verification](harness-verification.md)** - Real-machine integration checks for Claude Code, Codex, Goose, Cursor, OpenCode, Hermes Kanban and DSH: `scripts/verify_harnesses.py`, what PASS/SKIP mean, and the current verified matrix
- **[Trends](trends.md)** - Time-bucketed work analytics (`/api/trends`, `sugar trends`): throughput, failures, cycle time and spend by agent, harness or project
- **[Evidence Ledger](evidence.md)** - First-class verification records: `POST/GET /api/evidence`, `sugar evidence record|list|summary`, the browser Evidence panel, verification/policy/approval/artifact kinds and how they export with audit
- **[Observed AIBOM](aibom.md)** - Observed runtime AI inventory from the event spine (`/api/aibom`, `sugar aibom`), plus honest CycloneDX 1.6/SPDX 2.3-shaped projections and their caveats
- **[Audit Exports](audit.md)** - Tamper-evident JSONL exports of the event spine (manifest with first/last id, count, SHA-256, optional HMAC) plus `sugar audit verify` for compliance and client evidence
- **[Audit Packages](audit-package.md)** - Scoped, self-verifying evidence directory: events/tasks/evidence/traces/AIBOM/coverage/claims/README with a hashed package manifest and `sugar audit verify-package`

### 🛠️ Developer Documentation (`docs/dev/`)
Documentation for developers who want to **contribute** to Sugar:

- **[Local Development Setup](dev/local-development.md)** - Install and test Sugar locally (before PyPI)
- **[Contributing Guide](dev/contributing.md)** - How to contribute to Sugar
- **[Testing Guide](dev/testing.md)** - Running and writing tests
- **[Release Process](dev/release-process.md)** - How releases are created

## 🚀 Quick Links

- **New to Sugar?** → Start with the [Quick Start Guide](user/quick-start.md)
- **Installing Sugar?** → Follow the [Installation Guide](user/installation-guide.md)  
- **Need help?** → Check [Troubleshooting](user/troubleshooting.md) or [FAQ](user/faq.md)
- **Want to watch your agents?** → Open [Mission Control](mission-control.md)
- **Want to contribute?** → Read the [Contributing Guide](dev/contributing.md)

## 📖 External Resources

- **[GitHub Repository](https://github.com/roboticforce/sugar)** - Source code and issue tracking
- **[Claude Code CLI Documentation](https://docs.anthropic.com/claude-code)** - Required dependency
- **[PyPI Package](https://pypi.org/project/sugar/)** - Package distribution (coming soon)

## 💬 Getting Help

- **Issues**: Report bugs or request features on [GitHub Issues](https://github.com/roboticforce/sugar/issues)
- **Discussions**: Ask questions on [GitHub Discussions](https://github.com/roboticforce/sugar/discussions)
- **Contact**: Reach out to [contact@roboticforce.io](mailto:contact@roboticforce.io)

---

**Sugar** - Mission control for your agent fleet
Created by [Steven Leggett](https://github.com/cdnsteve) | [RoboticForce](https://roboticforce.io)