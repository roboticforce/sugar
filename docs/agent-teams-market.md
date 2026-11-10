# Sugar for agent teams: market research, positioning, and demand validation

*Research memo — 2026-11-06. Companion to [agent-teams.md](agent-teams.md) (what Sugar can already do),
[agent-teams-architecture.md](agent-teams-architecture.md) (Wardley map, architecture, domain boundaries),
[adapters.md](adapters.md) (adapter playbook and catalog) and
[mission-control.md](mission-control.md) (the dashboard/app/spine). Competitor counts are a snapshot taken on
2026-11-06 and will drift.*

---

## TL;DR

The "agent teams" market is real, but the **coordination UI** and **single-harness orchestration** layers are
being commoditized fast (Claude Code Agent Teams is free but experimental; Vibe Kanban has 28.6k stars and a
cloud with teams). Sugar should **not** compete there.

Sugar's defensible wedge is the layer nobody owns yet: a **local-first, harness-agnostic, durable coordination
board** — tasks with claims, leases and dependencies; provenance for every actor; an append-only event history;
one machine-readable contract that any agent, harness or human can speak (MCP today, A2A/HTTP next).

> **One board. Any agent. Full history.**

The question to answer before building a hosted team edition is not "can we build it" (we can) but **"will
anyone run more than one agent against one board and care that it is durable?"** The cheapest way to find out is
a **fake-door waitlist + 10–15 problem interviews + a concierge 3-agent crew**, all shippable in ~2 weeks with
existing Sugar assets. Decision gate at the end of this document.

---

## 1. Method and caveats

- Desk research: vendor docs, GitHub metadata, PyPI download stats, Linux Foundation announcements, NVIDIA's
  "AI factory" material. Sources at the bottom.
- No user interviews yet. Every statement about demand below is a **hypothesis with a test attached**, not a
  finding.
- Public signals (stars, downloads) are noisy: Vibe Kanban's 28.6k stars, Task Master's 28.3k stars and Sugar's
  100 stars measure *developer attention*, not revenue or retention.
- Sugar's own numbers are tiny as of this memo (see §2.4). Treat the project as pre-traction: the goal of the
  next 30 days is to learn, not to scale.

---

## 2. The landscape

### 2.1 Four layers, and who is fighting where

| Layer | What it is | Players (examples) | Signal | What it means for Sugar |
|---|---|---|---|---|
| **Single-harness orchestration** | One vendor's sessions coordinating with each other | Claude Code Agent Teams (experimental, free), Claude subagents, Codex cloud tasks | Shipped, free, experimental; strong for parallel independent work | Do not compete. Be the layer *under* it: persistent board + cross-harness |
| **Agent-native work UI** | Kanban/board UI for coding agents, git worktrees, review | [Vibe Kanban](https://vibekanban.com/docs) (28.6k★), Conductor/Terragon-class apps, IDE agents | Very crowded, strong UX, multi-harness, cloud teams | Compete only on *durability + provenance*, not on UI polish |
| **Code-level orchestration frameworks** | Graphs, crews, message buses, durable execution | LangGraph, CrewAI, AutoGen/Microsoft Agent Framework, OpenAI Agents SDK, Google ADK, Temporal/Inngest | Mature developer tooling; not a human board | Integrate, don't replace: Sugar is the *work ledger*, frameworks are the *executors* |
| **Protocols & interop** | Agent→agent, tool→model, observability | MCP (tools/context), [A2A](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year) (150+ orgs, LF, "first production-ready open standard for agent interoperability"), OTel GenAI | A2A reached production use in a year; enterprise cares | An A2A-compatible Sugar endpoint is distribution, not a moat by itself |

Two adjacent categories matter for pricing patience: **human task managers** (Linear/Jira issues with agent
integration — buyers already pay here) and **cloud coding agents** (Devin, Codex, Jules, Factory, Copilot coding
agent — they bundle coordination, so Sugar's wedge is the teams that *won't* move their repo to a vendor cloud).

### 2.2 Claude Code Agent Teams: the most important competitor to understand

From the current docs: agent teams are **experimental and disabled by default**
(`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`). One session is the lead, teammates get their own context windows,
there is a **shared task list** and **inter-agent messaging**, and you can talk to a teammate directly. The docs
are unusually candid about limits: coordination around **session resumption, task coordination and shutdown** is
a known-limitation area, teams cost significantly more tokens, and "for sequential tasks, same-file edits, or
work with many dependencies, a single session or subagents are more effective."

Read that as: Anthropic is validating the *category* for free — but only inside Claude Code, only within a
session's lifetime, with no persistent cross-agent ledger, no cross-harness identity, and no human-facing audit
trail. Those are exactly the gaps Sugar's queue, provenance and event spine fill.

### 2.3 Vibe Kanban: the UI competitor to respect

Vibe Kanban supports Claude Code, Codex, Copilot, Gemini CLI, Amp, Cursor CLI, OpenCode, Factory Droid, CCR and
Qwen Code; organises work in workspaces built on git worktrees; has review flows, a browser-testing feature,
remote access, a cloud offering with organisations/team members/projects/issues/kanban, self-hosting via Docker
Compose, GitHub and Azure Repos integrations, and its own MCP server. ~28.6k stars, ~3k forks, created mid-2025.

It is **UI-first**: the board is the review surface for agent work. Sugar should not fight it there. Sugar's story
is **state-first**: the board *is* the queue — durable, transactional, auditable, claimable by any harness, and
readable by both a human dashboard and a protocol endpoint.

### 2.4 Sugar's current signal (baseline, 2026-11-06)

| Signal | Value | Reading |
|---|---|---|
| GitHub stars / forks / watchers | 100 / 17 / 2 | Pre-traction |
| GitHub traffic (14 days) | 186 views (113 uniques); 1,389 clones (449 uniques) | Clones inflated by CI/agents; views modest |
| PyPI downloads, last month / week / day | 674 / 431 / 362 | Day spike is release day; baseline is ~4–24/day |
| Releases | 3.10.5, 3.10.6 (+dev), 3.10.7.dev0 | Shipping cadence is healthy |
| Multi-agent evidence in product | provenance (`created_by`/`claimed_by`), leases, `parent_task_id`, `blocked_by`, `assigned_agent`, event spine | The primitives exist; enforcement does not |

Interpretation: there is a small, active install base and a lot of *clone* interest from people wiring agents.
That is the audience to interview.

---

## 3. What is already commoditized vs. genuinely open

**Commoditized (do not build a moat here):**
- A kanban UI over agent runs.
- Spawning parallel sessions inside one vendor's product.
- Prompt libraries and "agent profiles".
- Basic run logs per session.

**Open (Sugar can own):**
1. **Cross-harness claims with durability semantics.** Any harness (Claude Code, Codex, Goose, Hermes, DSH,
   custom scripts) claims work atomically; a crashed worker's lease expires and the task returns to the board;
   dependencies are enforced. Nobody in the consumer ecosystem does this well.
2. **Provenance and audit as a first-class primitive.** Every task records *who* created it (human/agent/system,
   session, harness), *who* claimed it, and every transition lands in an append-only event spine. This is what
   makes agent work reviewable, debuggable and billable.
3. **Local-first / no-cloud posture.** For teams that can't send repo data to a vendor cloud (regulated,
   enterprise, defense, research), a durable board on their own hardware is the product.
4. **A stable contract between schedulers and agents.** MCP for tool access, A2A/HTTP for task exchange; Sugar can
   be the neutral hub that mirrors tasks to a Hermes board, a GitHub issue, or a framework queue.

---

## 4. Sugar's honest inventory

### 4.1 What exists (and is usable today)

- Durable SQLite work queue: priorities, statuses (`pending/active/completed/failed/held`), atomic claim via
  `BEGIN IMMEDIATE`, attempts counter.
- Provenance and leases: `created_by`, `created_by_kind` (`human/agent/system`), `created_by_session`,
  `claimed_by`, `lease_expires_at`; identity resolution via `$SUGAR_ACTOR`/`$SUGAR_AGENT_ID`.
- Orchestration metadata: `parent_task_id`, `stage`, `blocked_by` (JSON array), `context_path`, `assigned_agent`,
  plus an `orchestrate` flag and a task-orchestrator module.
- Interfaces: CLI (`sugar add/run/...`), MCP task server (add/list/view/update/status/remove/resources), loopback
  dashboard REST + SSE, native macOS app, Kanban board source (Hermes).
- Observability: append-only event spine (`~/.sugar/events.db`), activity feed, per-task provenance badges.
- Memory: cross-session memory DB (the original product) that agents can query through MCP.
- Guardrails: quality gates, protected paths, agent profiles, Ralph iteration loops.

### 4.2 What is missing for real team use (ranked by risk of a bad experience)

| Gap | Why it bites | Effort | Phase |
|---|---|---|---|
| **`blocked_by` is stored but not enforced** in the claim path | Two agents can claim a dependent task; "parallel" becomes "premature" | S | P0 |
| **No lease heartbeat or expiry reaper** | A crashed agent strands its task forever; the board lies about what's in flight | S–M | P0 |
| **`assigned_agent` is metadata, not routing** | A specialist agent has no way to say "give me only frontend tasks" | S | P0 |
| **No comments/hand-offs/artifacts** | Agent-to-agent context transfer happens out of band (chat, files); no review trail | M | P0/P1 |
| **No auth, namespaces or user model** | LAN or remote use is unsafe; the dashboard is loopback-only by design today | M | P1 |
| **No multi-machine server (Postgres/remote)** | SQLite is excellent on one box, workable on one shared disk, wrong for a fleet | L | P1 |
| **No A2A/HTTP agent API with scopes** | Other ecosystems must use MCP or the UI; A2A is where the protocol gravity is | M | P1 |
| **No OTel/cost accounting** | "AI factory" buyers ask for tokens, cost and traces before they ask for a board | M | P2 |
| **No schedule fairness/rate limits/quotas** | 5 agents hammering one provider is a cost incident | M | P2 |

### 4.3 What to explicitly *not* build

- Another generic kanban UI (Vibe Kanban wins on polish; we should integrate or stay minimal).
- A prompt/agent-framework runtime (LangGraph/Agents SDK own that).
- A hosted SaaS before the local multi-agent loop is proven and loved.
- "Swarm" branding with no correctness story — swarms fail on coordination, and that is precisely what we can
  fix.

---

## 5. The product concept (if the signal is positive)

**Sugar Board — a coordination plane for agent crews.**

P0 — *Correctness on one machine* (2–3 weeks):
- Enforce `blocked_by` in `get_next_work`; add `--agent` filtering on `assigned_agent`/capability tags.
- Lease renewal (`sugar heartbeat`) and an expiry reaper (requeue with attempt increment + event).
- Comments/handoff notes and artifact links on tasks (SQLite tables + dashboard/MCP/CLI surfaces).
- `sugar board` server mode over the dashboard API with scoped bearer tokens (local + LAN opt-in).
- `sugar stats --json`: local, shareable summary (distinct actors, tasks/agent, reaper events) for opt-in research.
- Dashboard/app: agent view (who is online, what they hold, lease countdown), dependency view.

P1 — *Team/hybrid* (4–6 weeks):
- Remote board (Postgres) + namespaces, RBAC, audit log; A2A-compatible endpoint and webhooks; OTel export;
  GitHub PR/issue two-way links; Hermes/DSH/Goose adapter kits.

P2 — *"AI factory"* (only with design partners):
- Scheduling/fairness, quotas and cost accounting, SSO, retention policies, SLAs, on-prem packaging.

Open-core assumption to validate: the local single-board product stays AGPL; team/hosted features (multi-user,
RBAC, audit retention, SSO) are commercial.

---

## 6. Positioning

**Recommended (wedge):** *"Sugar is the durable board for agent crews: any harness, one queue, full provenance —
local-first."*

Alternatives considered:

| Positioning | Verdict |
|---|---|
| "GitHub for agent swarms" | Too broad; GitHub is code hosting, our job is coordination. Attracts the wrong comparison |
| "AI factory control plane" | Right long-term story for enterprises; premature now, raises enterprise expectations we can't serve |
| "Kanban for AI agents" | Crowded; invites direct Vibe Kanban comparison on their strengths |
| "Mission control for agent teams" | Best for demos; keep as the *feeling*, use the durable-board language for the *claim* |

**Who to target first (ICP):**
- Developers/leads running **2–8 heterogeneous coding agents** on one machine or a small LAN (Mac Studio, homelab,
  Hermes/DSH setups), who already feel collisions/duplicate work/lost context.
- Small engineering teams (3–15 people) who want agent work auditable but will not send their repo to a vendor
  cloud.
- **Anti-ICP for now:** enterprises shopping "AI factory" suites; solo users running one agent (they don't need
  coordination; they need a better single-agent experience).

**Messaging tests to run (headline variants):**
1. "One board. Any agent. Full history."
2. "Run a crew of coding agents without losing track of who did what."
3. "The durable queue for agent teams — local-first, no cloud."

---

## 7. How to find out if users are interested (the plan)

Ordered by cost (cheapest first) and time (about 2 weeks to first read). Reuse what exists: the Next.js marketing
site and Resend are already wired; the new feedback flow is live; the provenance DB can produce local stats.

**E1 — Fake-door waitlist page (2 days to build, 2 weeks to read).**
- Page at `roboticforce.io/sugar/teams` (or `/sugar#teams`): positioning variants A/B/C, short demo GIF, CTA
  "Join the agent-teams early access" → email capture (explicit consent, store only email + variant + referrer).
- Measure: visits → signup by channel; time on page; which headline wins.
- **Threshold to continue:** ≥8% signup from targeted community traffic, ≥100 signups in 14 days.

**E2 — Community probes (1 week, $0).**
- One Show HN ("Sugar: a local-first task board for multiple coding agents"), posts in r/ClaudeAI, r/LocalLLaMA,
  r/AI_Agents, and the Goose/Hermes/DSH/Claude Code Discords.
- Measure: signups per channel (UTM), comment themes, qualified "I run N agents" replies.
- **Threshold:** ≥3 independent people describing a real multi-agent collision/duplication problem unprompted.

**E3 — Problem interviews, 10–15 (1–2 weeks).**
- Recruit from waitlist + cloners/issues. Use The Mom Test: ask about the *last time* they ran two agents, what
  broke, what they used, what they'd pay to avoid it; do not pitch.
- Scorecard: agents run/week, number of harnesses, pain severity (1–5), current workaround, willingness to switch.
- **Threshold:** ≥6 of 10 report a coordination failure in the last 30 days; ≥4 across ≥2 harnesses.

**E4 — Concierge 3-agent crew, 2–3 teams (2 weeks).**
- We manually run a 3-agent crew on Sugar (mixed harnesses) for their real repo, with the dashboard visible.
- Measure: time to first claimed task, tasks completed/agent/day, human interventions/hour, "would you miss it if
  it disappeared" (Sean Ellis-style), bugs found.
- **Threshold:** 2 of 3 teams ask to keep using it after the trial.

**E5 — Instrumented beta with opt-in sharing (2–4 weeks).**
- Ship `sugar stats --json` (local, no network) + the P0 correctness fixes; invite waitlist to a beta.
- Ask users to optionally send the stats file; it contains actor counts, claim/lease/reaper events, per-board
  activity — never task content.
- Measure: D1/D7 retention of the beta, distinct actors per board per week, reaper/expiry events, multi-harness
  boards as a share of active boards.
- **Threshold:** ≥30% of beta boards weekly-active at week 4 with ≥2 actors; ≥5 boards mixing harnesses.

**E6 — Pricing/packaging fake door (1 week).**
- On the waitlist page, after signup: "Team edition (hosted board, SSO, audit retention) [$X/user/mo]" with
  "Notify me" and a 4-question survey for design partners.
- **Threshold:** ≥3 of 5 design partners name a plausible price; ≥2 would pre-pay or sign an LOI.

**E7 — Ecosystem pull (continuous).**
- Ship adapters (Hermes plugin, Goose extension, DSH profile, Claude Code plugin) and an A2A endpoint early.
- Measure: installs, mentions, inbound integration PRs; watch Vibe Kanban/Claude team issue trackers for
  coordination complaints we can answer.

**Decision gate (end of week 4–6):**

| Result | Decision |
|---|---|
| Waitlist ≥100, ≥6/10 interviews with real pain, ≥2 teams retained, ≥1 pricing signal | Build P0 + hosted P1; hire/allocate for team edition |
| Waitlist ≥100 but pain is "nice to have" / single-harness | Stay local-first: polish single-board correctness and the macOS app; sell the *durable queue* as a solo/small-team reliability feature |
| Waitlist <50, no unprompted multi-agent pain | Stop investing in "agent teams"; keep Mission Control as a differentiator for Sugar's core memory/queue product |

**Instrumentation guardrails:** no telemetry by default (the privacy policy promises none). Any shared metric is
explicit, opt-in, content-free, documented, and revocable. Prefer stats files and interviews over silent pings.

---

## 8. Risks

1. **Commoditization from above.** Claude Code Agent Teams (free) and Vibe Kanban (free + cloud) keep expanding.
   Mitigation: cross-harness + durability + provenance + local-first, and protocol bridge (MCP/A2A).
2. **UI comparison trap.** Reviewers will compare screenshots with Vibe Kanban. Mitigation: lead with the ledger
   metaphor and data model, not the board.
3. **Hosted-team cost.** Multi-tenant auth, RBAC, SOC2-shaped questions, support. Mitigation: design partners,
   on-prem first, AGPL core.
4. **SQLite ceiling.** Multi-host coordination needs Postgres; don't pretend otherwise.
5. **"Swarm" hype hangover.** Buyers burned by multi-agent demos will ask about failure handling; that's our
   strength — lead with reaper/leases/retries and audit.
6. **AGPL/commercial tension** for a coordination layer that enterprises may want to embed.
7. **Trust surface.** A board with claims and approvals is a security boundary; the approval-relay caveat now
   documented by Claude (relayed approvals are untrusted input) applies to anything we build for human
   sign-off — design explicit human-approval records, not chat relays.

---

## 9. Open questions (research next)

- Do teams run agents on one machine/shared disk, or across machines? (Changes P0 vs P1 ordering.)
- Is the buying trigger a collision/duplication, a cost spike, or an audit/compliance need?
- Per-seat, per-agent, or per-agent-hour pricing?
- Do harness vendors (Anthropic/OpenAI/Google) intend to expose a neutral task-exchange API, or will A2A be the
  only interoperable path?
- How much of the "AI factory" language is enterprise procurement vs. real workflow?

---

## Sources

- [Claude Code — Orchestrate teams of Claude Code sessions](https://code.claude.com/docs/en/agent-teams) (experimental, disabled by default; shared tasks, messaging, known coordination/resumption limitations)
- [Vibe Kanban docs](https://vibekanban.com/docs) and [BloopAI/vibe-kanban](https://github.com/BloopAI/vibe-kanban) (~28.6k★, multi-agent, cloud teams, self-hosting, MCP server)
- [claude-task-master](https://github.com/eyaltoledano/claude-task-master) (~28.3k★, task-management MCP)
- [A2A Protocol one-year milestones — Linux Foundation](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year) (2026-04-09; 150+ organisations, "first production-ready open standard for agent interoperability")
- [NVIDIA — Agentic AI in the AI Factory](https://docs.nvidia.com/ai-enterprise/planning-resource/ai-factory-white-paper/latest/agentic-ai-in-the-factory.html)
- [Sycamore raises $65M for agentic AI (Economic Times)](https://economictimes.indiatimes.com/tech/technology/agentic-ai-startup-sycamore-raises-65-million-led-by-coatue-lightspeed/printarticle/129923947.cms)
- Sugar baseline: GitHub API (stars/forks/traffic) and [pypistats.org](https://pypistats.org/packages/sugarai) snapshots, 2026-11-06
- Sugar capabilities: [agent-teams.md](agent-teams.md), [mission-control.md](mission-control.md), [local-dashboard.md](local-dashboard.md)
