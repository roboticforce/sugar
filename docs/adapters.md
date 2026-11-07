# Adapter architecture: how Sugar gets everywhere without building everything

*Companion to [agent-teams-architecture.md](agent-teams-architecture.md) (Wardley map, bounded contexts) and
[agent-teams-market.md](agent-teams-market.md) (positioning, validation).*

---

## 1. Why adapters are the leverage

Sugar's moat is the **ledger** (tasks, claims, leases, dependencies, provenance, events). Everything around it —
the tools people already use — is commodity. The strategic move is therefore not "build every integration", but
**make the ledger trivially reachable from every tool**, and let the tools meet us at the contract.

Adapters do three things at once:

1. **Distribution.** Each adapter is a door into someone else's workflow: Claude Code users, Goose users, DSH,
   Hermes, GitHub-centric teams, Slack-centric teams, A2A-compatible agents. A small team can't build deep
   integrations for all of them; it *can* build cheap, well-tested translators.
2. **Contract gravity.** The more adapters exist, the more the Sugar contract becomes the expected shape of "a
   task", "a claim", "an event". That is how a coordination layer becomes infrastructure.
3. **Focus.** Adapters absorb the commoditised ends of the value chain (UI conventions, chat, issue trackers,
   protocols) so engineering stays on the custom left side of the Wardley map: correctness, provenance, audit.

The rule that makes this safe: **adapters are dumb**. They translate; they never decide. The moment two adapters
disagree about what "claimed" means, the ledger loses the trust that is the entire product.

---

## 2. Taxonomy

**By direction**

- **Inbound** — external work/events become Sugar commands (`create`, `claim`, `complete`, `fail`, `comment`,
  `link artifact`).
- **Outbound** — Sugar events become external effects (webhook POST, OTel span, Slack message, GitHub PR status).
- **Bidirectional** — both, with an explicit rule for **which side is the source of truth per concept**.

**By level**

- **Protocol adapters** — MCP, A2A, HTTP/JSON+SSE, CLI, webhooks. These are thin and long-lived.
- **Integration adapters** — GitHub, Hermes Kanban, Linear/Jira, Slack, CI. These have vendor-specific quirks and
  are the ones that rot.
- **Runtime adapters** — harness plugins for Goose/DSH/Claude/Codex that expose Sugar's tools inside a harness.

**Current and planned catalog**

| Adapter | Direction | Status | Translation boundary |
|---|---|---|---|
| CLI | both | **shipped** | commands ↔ core |
| MCP memory server | both | **shipped** | MCP tools/resources ↔ memory context |
| MCP task server | both | **shipped** | MCP tools/resources ↔ core (claims, status) |
| HTTP/SSE board API | both | **shipped** (dashboard) | REST + SSE ↔ core; UI consumes it |
| Hermes Kanban board source | bidirectional | **shipped** | board `task_events` → spine; writes via `hermes kanban` (board stays the writer) |
| GitHub issue discovery | inbound | **shipped** | open issues → tasks (polling) |
| A2A endpoint | bidirectional | **planned (P1)** | A2A Task ↔ Sugar task; Agent Card ↔ registry |
| Outbound webhooks | outbound | **planned (P0/P1)** | spine event → signed HTTP POST |
| OTel exporter | outbound | **planned (P1)** | spine → spans/metrics/cost attributes |
| GitHub two-way + PR artifacts | bidirectional | **planned (P1)** | issue state ↔ task state; PR link ↔ artifact |
| Slack/Discord notifications | outbound | **via webhooks** | event → message |
| Goose / DSH / Claude / Codex plugins | inbound/runtime | **partial** | tool calls / shell ↔ core |
| CI trigger (failed pipeline → task) | inbound | **later** | webhook → task |
| Email-to-task | inbound | **later** | mail → task (security-sensitive) |
| Linear / Jira sync | bidirectional | **later** | issue ↔ task (design partners only) |

Order matters: protocol adapters (A2A, webhooks) create pull; vertical integrations create support burden. Ship
the former first.

---

## 3. Non-negotiable adapter rules

1. **No direct store access.** Adapters call core commands or the contract; no SQLite/Postgres imports in
   adapter code.
2. **One writer per concept.** Sugar owns task state; the external system owns its own state (e.g. the Hermes
   board owns board state; GitHub owns issue state). Syncing is *reflection*, not co-ownership.
3. **Idempotency.** Every inbound external identifier maps to a deterministic key (`external_system +
   external_id`). Replaying an event must not duplicate work. Outbound deliveries carry an idempotency key.
4. **Capability declaration.** An adapter announces what it supports (`claim`, `complete`, `artifacts`,
   `comments`, `sync-status`), so surfaces can degrade honestly.
5. **Scoped identity.** Each adapter runs as its own actor with its own token/permissions; provenance records
   adapter name + external id + actor.
6. **Best-effort and isolated.** An adapter failure never blocks the core, a claim, or the UI. Retry with backoff;
   dead-letter after N attempts; emit an `adapter.failed` event.
7. **Emit spine events.** `adapter.synced`, `adapter.failed`, `adapter.delivered` appear in the activity feed and
   audit export. Adapter behaviour must be observable with the same tools as task behaviour.
8. **No secrets in the ledger.** Tokens live in config/keychain and are referenced by name; never in tasks,
   events, comments or artifacts.
9. **Rate limits and backpressure per adapter.** The core gets a bounded queue, not a vendor's API limits.
10. **Approvals stay in the ledger.** A human approval for agent work is a recorded ledger approval — never an
    approval message relayed through an adapter (relayed approval claims are untrusted input).

---

## 4. The canonical mapping (what every adapter implements)

Inbound adapters translate to these operations (the same set the CLI/MCP/HTTP contract exposes):

```
create(task)
claim(task_id, worker_id, lease_seconds)
heartbeat(task_id, worker_id)
complete(task_id, artifacts[], result_summary)
fail(task_id, reason)
comment(task_id, author, body)          # hand-offs, review notes
link_artifact(task_id, kind, url)       # PR, commit, file, trace
sync_status(task_id, external_state)    # reflection only, ACL-guarded
```

Outbound adapters subscribe to spine event kinds:

```
task.created / task.claimed / task.heartbeat / task.completed
task.failed / task.held / task.released / task.lease_expired
feedback.submitted / adapter.*
```

**Status mapping is lossy — be explicit.** Example for the Hermes Kanban source (already shipped):

| External | Sugar | Rule |
|---|---|---|
| `triage` / `todo` | `pending` | |
| `in_progress` (with `claim_lock`) | `active` | claim lock becomes `claimed_by` |
| `done` | `completed` | |
| `blocked` | `held` | block reason becomes hold reason |
| `archived` | hidden from listings | not a Sugar status; delete archives |
| unknown | pass through | never guess; show it in the UI as-is |

The same table (per adapter, versioned) is the **first thing to write** when adding an adapter, and the first
thing to test.

---

## 5. Conformance: what "done" means for an adapter

No adapter ships without passing a shared checklist. Proposed location: `tests/adapters/` with fixtures that any
adapter implementation (including third-party) can run.

- **Claim atomicity** — N concurrent claimers, exactly one wins, no double-active task.
- **Lease expiry** — claim → no heartbeat → reaper requeues with `attempts += 1` and an event.
- **Dependencies** — a blocked task is never claimable; unblocking releases it exactly once.
- **Idempotent replay** — same external event twice → one task/event, no duplicates.
- **Provenance round-trip** — external actor/identity survives into `created_by`/`claimed_by`.
- **Event emission** — every mutation produces exactly one spine event of the right kind.
- **Secret hygiene** — tokens never appear in task payloads, events, comments or logs.
- **Failure isolation** — a crashing adapter does not block claim/complete; it emits `adapter.failed` and
  retries with backoff.
- **Rate-limit behaviour** — adapter backs off and queues rather than hammering the vendor or the core.

A `sugar adapters test <name>` command (future) should run this against a locally configured adapter, so
community adapters can prove themselves.

---

## 6. Economics, levels and risks

**Levels** keep the long tail from becoming a support tax:

| Level | Meaning | Who maintains |
|---|---|---|
| **Reference** | in-repo, conformance-tested, documented in this repo | Sugar core team |
| **Verified** | third-party package, passes the conformance kit, listed in docs | Maintainer, reviewed by core |
| **Community** | third-party, no guarantees | Their users |

**Why it's cheap:** the core contract is stable, so a translation is days of work, not weeks. Each adapter is
also a marketing surface and a source of evidence about what the market actually needs (which adapters get
used, which fail, which are requested).

**Why it's dangerous:** semantic drift (two status mappings disagree), security surface (inbound writes must be
authenticated and scoped), vendor churn (GitHub/A2A/Linear APIs change), and maintenance load. Mitigations: the
conformance kit, per-adapter levels, dead-letter queues, and a **strict rule that adapters never touch the core
state machine directly**.

**Metrics to watch per adapter:** activation (configured ≥ once), inbound/outbound event volume, error rate,
dead-letter volume, time-to-first-synced-task. If an adapter has users but no maintainer, promote it to
Reference or drop it.

---

## 7. Code shape (to avoid an adapter framework monster)

```
sugar/adapters/
  base.py        # AdapterInfo, InboundAdapter/OutboundAdapter protocols
  registry.py    # entry-point discovery: "sugar.adapters" group
  conformance.py # shared assertions (optional CLI: sugar adapters test)
```

- Adapters are **entry-point plugins** (`sugar.adapters` group), installed separately — the local-first core
  install must not grow vendor SDK dependencies.
- The dashboard discovers configured adapters lazily and exposes `/api/meta` capabilities; the UI never assumes
  an adapter exists.
- Config lives under `.sugar/config.yaml` (`adapters:` section), secrets by reference.
- A `sugar adapters list` / `sugar adapters test` CLI keeps it operable without a UI.

Guardrail: if an adapter needs to "fix" the core semantics, the fix belongs in the core — not in the adapter.

---

## 8. Recommended order

1. **A2A endpoint** (P1): distribution into agent ecosystems; also the best forcing function for a clean
   canonical mapping (Task/status/artifacts). Adopt the spec; don't extend it.
2. **Outbound webhooks + OTel** (P0/P1): lets other people build the long tail (Slack, Linear, Grafana, PagerDuty)
   without us shipping each integration. Webhooks are also the delivery mechanism for CI/notification use cases.
3. **GitHub two-way + PR artifacts** (P1): meets existing users where they already work; discovery already
   exists, so this is mostly status/artifact mapping.
4. **Harness plugins** (Goose/DSH/Claude/Codex) as thin shells over MCP — distribution into harness communities.
5. **Vertical sync (Linear/Jira)** only with design partners: highest support cost, lowest contract leverage.

---

## 9. Open questions

1. Entry-point packaging and version-compatibility policy for third-party adapters?
2. Which external statuses are irreducibly lossy, and how do we display "not representable" honestly?
3. Webhook delivery semantics: at-least-once + idempotency keys, or a durable outbox with replay?
4. A2A auth: per-agent bearer tokens, OAuth client credentials, or both?
5. Do verified community adapters get a compatibility guarantee, or only a conformance badge?
6. Who owns adapter support — core team for Reference adapters only, and a clear "community" label otherwise?
