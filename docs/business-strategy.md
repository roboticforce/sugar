# Sugar business strategy: positioning, income, growth

*Working strategy document — 2026-11-09. Companion to [agent-teams-market.md](agent-teams-market.md) (market,
validation experiments), [agent-teams-architecture.md](agent-teams-architecture.md) (boundaries) and
[adapters.md](adapters.md) (integration strategy). Every revenue claim here is a hypothesis until the validation
experiments run; the point of this document is to make the bets explicit and testable.*

---

## 1. Positioning

> **Sugar is the coordination and evidence layer for agent teams — any harness, one board, full history.**

Three pillars, in order of defensibility:

1. **Coordinate** — durable claims, leases, dependencies and retries, so agents don't collide and crashed work
   comes back. (Already built: `TaskStore` port, atomic claims, heartbeats, reaper, dependency gating.)
2. **Trust** — provenance and an append-only event spine: who asked, which agent/harness did it, what happened,
   with retries and leases. This is the paid foundation.
3. **Connect** — MCP, A2A, webhooks and adapters, local-first by default, so Claude Code, Codex, Goose, Hermes,
   DSH and custom scripts all speak the same board.

**What Sugar is not:** a model vendor, an agent runtime/cloud coding agent, a code host, or another kanban UI.
That neutrality is the product: *you don't have to pick a winner to coordinate the fleet.*

Messaging to test:

- "Mission control for your agent fleet — with receipts."
- "One board. Any agent. Full history."
- "Know who did what, what it cost, and whether it was allowed."

Avoid "AI factory" as the lead message for now — it is the enterprise upsell narrative once the controls exist
(§5), not the wedge.

## 2. Who pays, and why

**Primary ICP (revenue):** AI-native product teams and small engineering orgs running **5–30 coding agents**
across mixed harnesses (Claude Code + Codex + internal scripts), plus **on-prem/regulated teams** that cannot
send repo data to a vendor cloud.

**Secondary ICP (enterprise, later):** engineering organizations with production-touching agents that need
audit, cost attribution, approval gates and SSO.

**Anti-ICP:** solo users running one agent (free tier; they need a good single-agent experience, not
coordination); enterprises shopping an "AI factory" suite before the controls exist.

**Willingness-to-pay ladder (highest first):**

1. **Audit/evidence** — "Who did what, can I prove it?" Compliance, client work, incident review.
2. **Cost attribution** — "Where is the money going?" Teams spending $5–50k/month on agents want per-task/
   agent/harness spend.
3. **Control** — dependencies, approvals, quotas, policy, SSO/RBAC. The pain that grows with fleet size.

Kanban visibility alone is not a purchase trigger: Claude Code Agent Teams and Vibe Kanban give that away. The
paid layer is evidence + cost + control.

## 3. Monetization

**Open-core, three tiers, self-hosted before hosted.**

| Tier | What's included | Price (hypothesis) | Why |
|---|---|---|---|
| **Free / local** (AGPL) | CLI, memory, local dashboard, macOS app, one board, scoped tokens for local/team experimentation | $0 | Adoption engine; keeps the dev-loved local story |
| **Team — self-hosted** | Remote board, scoped tokens (done), **RBAC/namespaces, audit export, retention controls, cost attribution, Postgres option, Helm/Docker compose, email support** | **$15–25/seat/mo** or **$6–15k/yr** for small teams | First revenue; no multi-tenant hosting burden; sells to teams that won't use a vendor cloud |
| **Cloud** (later) | Managed board, multi-tenant, hosted audit retention, usage dashboard, SLA | $20–30/seat/mo + retention add-ons | Only after self-hosted validation; recurring, lower support per dollar at scale |
| **Enterprise** | SSO/SCIM, SIEM/audit exports, quotas and cost policies, approval workflows, on-prem support, security review, SLA | **$50k+/yr** | Where "AI factory" language fits; long sales cycle, high ACV |

Additional income streams, in order of near-term realism:

1. **Paid design partners** ($5–15k) for the Team edition — fastest honest revenue and roadmap input.
2. **Commercial dual license** for the AGPL core (companies embedding Sugar in proprietary products). Classic
   open-core revenue, and it removes the "AGPL is radioactive" objection in enterprise deals.
3. **Support/onboarding** for design partners (scoped, not a consultancy).
4. **Marketplace/partner revenue later** (cloud marketplaces, harness vendors bundling Sugar) — distribution,
   not a primary revenue line.

Do **not** price per task: it punishes usage and is hard to forecast. Seat + retention/volume is cleaner.

**Open-core boundary to publish now:**

- Free: local-first board, memory, CLI, dashboard, macOS app, MCP server, local/team scoped tokens, community
  support.
- Paid: RBAC/SSO, audit retention/exports at scale, cost attribution dashboards, Postgres/HA deployment,
  cloud hosting, enterprise support/SLA, premium integrations (GitHub/Linear/Jira two-way), approval policies.

## 4. Growth engine

**Developer-led adoption → design partners → Team licenses → Cloud → Enterprise.**

- **Adoption:** open source + problem-named content ("agent collisions", "which agent did what", "agent cost per
  task"), one-command local demo, MCP/Claude Code ecosystem integrations, adapter contributions
  (Hermes/DSH/Goose/plugins), and a visible local-first stance. Target HN, r/ClaudeAI, r/LocalLLaMA,
  r/AI_Agents, and the "Mac Studio/homelab agent fleet" crowd — early, loud, and convertible to design partners.
- **Product-led upgrade triggers** (instrument in-product, opt-in/local-first):
  - two or more distinct actors/claims on one board → "you're running a crew; enable Team mode";
  - remote/team access requested (`--allow-remote` usage) → Team;
  - audit export or SSO requested → Enterprise.
  Never silent telemetry; `sugar stats --json` is opt-in and content-free.
- **Design partners:** 3–5 paid teams, weekly cadence, instrumented. Ship their top three asks — almost certainly
  audit export, cost attribution, RBAC.
- **Content moat:** a quarterly **"State of agent teams"** survey/report (neutral party measuring the space) —
  generates the waitlist, press, and leads.
- **Partnerships later:** harness vendors, GPU/homelab hardware sellers, A2A ecosystem, cloud marketplaces.
  "Bundle a Sugar board with your agent fleet" is a natural co-sell.

## 5. Roadmap tied to revenue

| Horizon | Product | Revenue |
|---|---|---|
| **0–3 months** | Validation: landing page + waitlist, 10–15 interviews, 3 paid design partners. Close the `/api/health` info leak, ship **audit export** + **cost/usage attribution**, `sugar stats` opt-in. | $15–45k pilots |
| **3–6 months** | **Sugar Teams v1**: RBAC/namespaces, audit exports + retention, OIDC SSO, Postgres behind the conformance suite, Helm chart, support. Team self-hosted licenses. | 5–10 licenses → $50–120k ARR |
| **6–12 months** | **Cloud control plane**: multi-board/fleet view, hosted board, billing, OTel/cost exporters; GitHub/Linear/Jira adapters; A2A endpoint; approval gates. | 20–40 teams → $150–300k ARR |
| **12–24 months** | **Enterprise "agent factory" plane**: quotas/cost policies, approval workflows, SIEM export, SCIM, SLA, on-prem. | 3–8 enterprises → $250–500k ARR |

The **income unlockers** clearly ordered by demo-to-dollar ratio, all building on what exists:

1. **Cost/token attribution** (event spine → aggregation → API/CLI/dashboard): the "where is the money going"
   demo.
2. **Audit export** (signed JSONL + manifest, verify command, retention guidance): the compliance demo.
3. **RBAC + OIDC SSO** on top of the board-server tokens: required to sell to teams.
4. **Postgres backend** behind `TaskStore` (port + schema ledger already in place): multi-instance/HA, hosted.
5. **Approval gates + quotas** (recorded ledger approvals, never chat relays): the enterprise safety story.

## 6. Revenue model assumptions (sanity check, not a forecast)

Small but plausible path without venture capital:

- Year 1: 3–5 design partners × $5–15k = **$15–75k**.
- Year 2: 8–12 Team self-hosted × $8k avg = **$64–96k** + 2 Cloud/Enterprise × $30k = **$120–160k ARR**.
- Year 3: 30–50 Team × $8–12k + 3–6 Enterprise × $50k+ = **$400–700k ARR**.

The step function is enterprise (audit + control + SLA), not consumer subscriptions. If the agent-teams market
compresses (harness vendors bundle coordination for free), the fallback is a profitable, focused **team board +
audit** product for 50–200 teams, not a platform bet.

**Costs to plan for:** hosting (only after Cloud), support time, SOC 2 readiness (~$20–50k when enterprise
demand appears), security reviews, and founder-led sales time.

## 7. Metrics that matter

- **Top of funnel:** waitlist signups by channel; GitHub stars/traffic; docs → install conversion.
- **Activation:** boards with ≥1 claimed task; **boards with ≥2 distinct actors** (the coordination signal).
- **Retention:** weekly active boards, week-4 retention, tasks per agent per week.
- **Monetization:** design partners signed, Team conversion from waitlist, ARR, NRR, support tickets per account.
- **Product signals for paid features:** usage events posted (cost attribution adoption), audit exports run, SSO
  requests, `--allow-remote` usage.

## 8. Risks and decisions

**Risks**

1. **Bundling from above** — Claude Code Agent Teams, Vibe Kanban and cloud vendors give coordination away.
   Mitigation: cross-harness + durable evidence + local-first + audit/controls they don't prioritize.
2. **UI comparison trap** — never fight Vibe Kanban on UI; be the ledger/API and integrate.
3. **Too early** — agent teams may remain a niche; validation gate decides. Keep burn low.
4. **Support/compliance burden** — do not sell enterprise before design partners force it.
5. **AGPL friction** — dual licensing and an honest open-core boundary.
6. **Security** — a coordination plane is a trust surface; the board server has no TLS/SSO yet, and audit data
   is sensitive. Harden before selling (health minimization first, then SSO/rate limits).

**Decisions to make now**

1. Open-core boundary published (above) — free local, paid team/enterprise.
2. Self-hosted Team before Cloud.
3. Dual license from day one.
4. Lead messaging = coordination + evidence + cost, not "AI factory".
5. Validation experiments gate the hosted rung.

## 9. 30 / 60 / 90 days

**30 days:** landing page + waitlist; 10–15 interviews; 3 paid design partners; ship audit export + cost/usage
attribution; close the `/api/health` leak; publish the open-core boundary.

**60 days:** RBAC + audit retention + SSO on the board server; Postgres adapter behind the conformance suite;
Helm/Docker compose; Team edition pricing page; first license conversations.

**90 days:** 5–10 Team licenses or equivalent pilots; Cloud architecture decision (build/host); OTel + GitHub
adapter; "State of agent teams" survey draft.

---

*This document is deliberately falsifiable: the market memo's experiments decide whether the Cloud/Enterprise
rungs get built. Strategy without willingness-to-pay is fiction; the next 30 days are about buying evidence, not
slides.*
