# Observed AIBOM (v0)

*Status: implemented and read-only. This page documents Sugar's **observed
runtime AI Bill of Materials**: what actually ran according to the event spine,
not a standards-complete or declared AIBOM.*

Related: [`telemetry-adapters.md`](telemetry-adapters.md) for the events it is
derived from, [`traces.md`](traces.md) for per-session context,
[`audit-package.md`](audit-package.md) for exporting it inside a scoped evidence
package, and [`audit.md`](audit.md) for the underlying audit exports.

Sugar's AIBOM answers a narrow, useful question: **which models, harnesses,
tools, MCP servers and artifacts did Sugar observe in this scope?** It is
assembled from telemetry that reached the append-only event spine. It does not
scan repositories, read model weights or training data, or claim CycloneDX/SPDX
conformance.

---

## 1. What it observes

The inventory schema is `sugar.aibom.v0`:

```json
{
  "schema": "sugar.aibom.v0",
  "generated_at": "2026-11-25T02:46:01Z",
  "scope": {"project": "sugar", "since": "2026-11-01T00:00:00Z"},
  "components": [
    {
      "type": "model",
      "name": "deepseek-flash",
      "events": 12,
      "projects": ["sugar"],
      "first_seen": "2026-11-09T17:30:00Z",
      "last_seen": "2026-11-10T18:02:11Z",
      "evidence_links": [
        {"trace_id": "dsh:11111111-2222-3333-4444-555555555555",
         "span_id": "event:42"}
      ]
    },
    {"type": "harness", "name": "dsh", "events": 87, "projects": ["sugar"],
     "first_seen": "…", "last_seen": "…", "evidence_links": ["…"]},
    {"type": "tool", "name": "bash", "events": 34, "…": "…"},
    {"type": "mcp_server", "name": "filesystem", "events": 5, "…": "…"},
    {"type": "artifact", "name": "/Users/me/Dev/app/report.md", "events": 1, "…": "…"}
  ],
  "coverage": {
    "by_harness": {"claude-code": 3, "dsh": 87},
    "by_fidelity": {"L1": 2, "L2": 18, "L3": 70}
  },
  "limitations": [
    "Observed runtime inventory only; not a complete declared AIBOM",
    "No model weights, training data, licenses, datasets or suppliers",
    "MCP provenance is partial: explicit mcp_server fields or mcp__<server>__ tool names only",
    "Only telemetry that reached the event spine is represented (adapters may be absent)",
    "No file contents, prompts, tool arguments, secrets or hashes beyond reported names/paths",
    "v0 is not CycloneDX or SPDX compliant and makes no standards-conformance claim"
  ],
  "events_scanned": 100,
  "limit": 5000,
  "component_limit": 200,
  "truncated": false
}
```

Component derivation rules:

| Type | Derived from | Notes |
|---|---|---|
| `model` | Any event carrying `model`/`model_name`/`model_id` (usage/run telemetry) | Exact reported string; canonical nested or flat payloads tolerated |
| `harness` | The spine `harness` column | `events` counts all scanned events for that harness |
| `tool` | `tool.started` (or a completion without a start) | Counts executions, so a started+completed pair is one tool use; tool name from `tool_name`/`tool`/`name` |
| `mcp_server` | Explicit `mcp_server` field **or** a tool named `mcp__<server>__<tool>` | Ordinary/ambiguous tool names never invent a server |
| `artifact` | `artifact.created` paths | Paths only; pathless artifact events are skipped |

`evidence_links` point back to the spine: `trace_id` comes from the event's
`trace_id` column or `<harness>:<session_id>` (matching
[`traces.md`](traces.md)); `span_id` comes from `span_id`/`tool_use_id`/
`tool_call_id`, falling back to the event id. At most 5 distinct links are kept
per component.

`coverage.by_harness` and `coverage.by_fidelity` are event counts, not component
counts (`L0`–`L3`; unknown fidelity is not counted). Components are deduped by
`(type, name)`, sorted by type then name, and capped at 200; the event scan is
bounded by `limit` (default 5000, max 50000) and reports `truncated` when more
events matched. No prompts, tool arguments, file contents or secrets are
included — only names, paths and ids.

---

## 2. CLI

```bash
# Human table (observed inventory + coverage + limitations)
sugar aibom

# Scoped to a project and time range
sugar aibom --project sugar --since 2026-11-01

# Raw v0 JSON
sugar aibom --json

# Exchange-shape projections (see §4)
sugar aibom --format cyclonedx > aibom.cdx.json
sugar aibom --format spdx > aibom.spdx.json
```

| Option | Default | Notes |
|---|---|---|
| `--db`, `--events-db PATH` | `$SUGAR_EVENTS_DB` or `~/.sugar/events.db` | Event spine to read (read-only) |
| `--project` | none | Exact project filter |
| `--since` | none | ISO-8601 lower bound |
| `--limit` | 5000 | Max events to scan (bounded, max 50000) |
| `--format` | `json` | `json` (observed v0), `cyclonedx` (1.6-shaped), `spdx` (2.3-shaped) |
| `--json` | off | Print raw JSON instead of the table; combines with `--format json` only |

`--format cyclonedx|spdx` prints a JSON document; `--json` with a non-json
format is a CLI error. The CLI requires the dashboard extra
(`pip install 'sugarai[dashboard]'`) and never starts a server.

---

## 3. API

```
GET /api/aibom?project=sugar&since=2026-11-01T00:00:00Z&limit=5000&format=json
```

| Query | Default | Notes |
|---|---|---|
| `project` | none | Exact project filter |
| `since` | none | ISO-8601, normalized; invalid → 400 |
| `limit` | 5000 | Bounded event scan, `1..50000`; invalid → 400 |
| `format` | `json` | `json`, `cyclonedx`, `spdx` (case-insensitive); invalid → 400 |

Responses:

- `200 application/json` with the schema above (or the requested projection);
- `400 {"error":"validation_error","detail":…}` for invalid `since`, `limit` or
  `format`;
- `503 {"error":"spine_unavailable"}` when the server has no event spine.

With board tokens configured, `/api/aibom` requires a `read`-scoped token like
every other `/api/*` GET. The same report powers the CLI; there is no second
aggregation path.

---

## 4. Exchange projections: CycloneDX 1.6 and SPDX 2.3

`--format cyclonedx` / `?format=cyclonedx` and `--format spdx` /
`?format=spdx` project the same observed inventory into **standards-shaped
documents**. They are honest observed projections, **not certified or
standard-complete AIBOMs**, and they say so in the document metadata:

- no licenses, versions, suppliers, hashes, datasets or model weights are
  invented;
- missing SPDX fields use `NOASSERTION`;
- no external URLs are fabricated (evidence stays as properties/comments);
- deterministic IDs: the same inventory always produces the same identifiers.

### Deterministic IDs

- Fingerprint: SHA-256 of canonical JSON over `{schema, scope, components,
  coverage, limitations}` (volatile `generated_at`, `events_scanned`, `limit`
  and `truncated` excluded).
- CycloneDX `serialNumber`: `urn:uuid:<UUIDv5(NAMESPACE_URL,
  "https://sugar.dev/aibom/<fingerprint>")>`; component `bom-ref` =
  `sugar:aibom:<type>:<sha256(type\0name)[:12]>`.
- SPDX `documentNamespace`: `https://sugar.dev/spdx/sugar.aibom.v0/<fingerprint>`;
  package `SPDXID` = `SPDXRef-<Type>-<slug>-<sha256(type\0name)[:8]>`.

Timestamps still reflect the request; they are metadata, not identity.

### CycloneDX 1.6 mapping

| Component type | CycloneDX `type` |
|---|---|
| model | `machine-learning-model` |
| harness | `application` |
| tool | `application` |
| mcp_server | `service` |
| artifact | `file` |

`bomFormat: "CycloneDX"`, `specVersion: "1.6"`, `version: 1`,
`metadata.timestamp`, `metadata.tools.components` (real Sugar version) and
`metadata.properties` carrying the schema, `observed_runtime_only=true`, the
standard-compliance disclaimer, Sugar version, scope, scan counts and every
limitation. Observed component metadata and evidence links are **properties
only** — no `version`/`supplier`/`licenses`/`hashes`/`purl`/`externalReferences`
are added.

### SPDX 2.3 mapping

`spdxVersion: "SPDX-2.3"`, `dataLicense: CC0-1.0`, `SPDXID:
SPDXRef-DOCUMENT`, deterministic `documentNamespace`, `creationInfo` and
`annotations` containing the observed-only disclaimer plus every limitation.
Every observed component becomes a package with required fields `SPDXID`,
`name`, `downloadLocation: NOASSERTION`, `filesAnalyzed: false`,
`licenseConcluded/Declared: NOASSERTION`, `copyrightText: NOASSERTION` and a
comment with observed metadata/evidence. Relationships are
`SPDXRef-DOCUMENT DESCRIBES <package>` only — telemetry does not establish
containment, so no `CONTAINS` relationship is fabricated.

This is deliberately **not** the SPDX 3 AI profile or a CycloneDX ML-BOM: those
would require observed model/dataset versions, hashes, suppliers and licenses
that v0 does not have.

---

## 5. Use with audit packages and traces

- `sugar audit package` includes the scoped observed AIBOM as `aibom.json` and
  records an "Observed AI/model/tool inventory is included" claim in its claims
  matrix (proven when components were observed, not_proven otherwise). See
  [`audit-package.md`](audit-package.md).
- Trace Explorer and the dashboard Audit view show the same observed components
  and evidence links; an artifact component corresponds to an `artifact.created`
  span path in [`traces.md`](traces.md).
- AIBOM evidence is only as good as the adapter coverage; a harness with no
  adapter is absent rather than guessed.

---

## 6. Limitations (v0)

- **Observed, not declared.** It describes what reported to the event spine; a
  component used outside Sugar's telemetry is invisible. It is not a complete
  declared AIBOM and does not replace a repo/environment scanner or manual
  registry.
- **No versions, hashes, licenses, suppliers, datasets or model weights.** Those
  require producers/registries Sugar does not yet collect.
- **MCP provenance is partial.** Only explicit `mcp_server` fields or
  `mcp__<server>__<tool>` names are classified; transport/protocol version and
  server URLs are not observed, so no MCP-specific claims are made.
- **Artifacts are paths.** No file contents or hashes are read; a path is an
  observation, not proof of artifact integrity. Use the evidence ledger/audit
  attachments for content hashes where a producer records them.
- **Retention applies.** The spine prunes old events (default 30 days); the
  inventory only covers retained telemetry. Export before pruning for a durable
  snapshot.
- **Bounded and local.** Per-request `limit`/component caps and one-machine spine
  apply; aggregates across machines need collected exports.
- **Not standards-compliant.** CycloneDX/SPDX outputs are observed projections;
  no certification, conformance testing or legal/AIBOM compliance claim is made.
  In particular there is no SPDX 3 AI-profile output, no CycloneDX ML-BOM model
  card/dataset data, and no digital signature/attestation of the projection
  itself (the audit package's HMAC covers the package files, not an AIBOM
  certificate).
- **No pricing or spend data.** The AIBOM is an inventory; usage/cost lives in
  [`spend-attribution.md`](spend-attribution.md). It intentionally contains no
  pricing or commercial information.

---

## 7. Related documentation

- [`audit-package.md`](audit-package.md) — scoped evidence package including
  `aibom.json` and a claims matrix.
- [`audit.md`](audit.md) — tamper-evident event exports and verification.
- [`traces.md`](traces.md) — observed spans, artifacts and evidence links.
- [`telemetry-adapters.md`](telemetry-adapters.md) — canonical events and
  adapter coverage that feed the inventory.
- [`evidence.md`](evidence.md) — verification evidence
  (`verification.*`, `policy.decision`, `approval.*`, `artifact.created`,
  `commit.linked`) that complements an inventory.
