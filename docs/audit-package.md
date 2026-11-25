# Audit packages

*Status: implemented. A scoped, self-verifying **directory** of evidence for a
time/project range: event export, task slice, verification evidence, trace
summaries, observed AIBOM, adapter coverage, a claims matrix and a human README,
all hashed in a package manifest and optionally HMAC-signed.*

Related: [`audit.md`](audit.md) for the underlying canonical JSONL export/verify
format, [`evidence.md`](evidence.md) for verification records,
[`traces.md`](traces.md) for trace summaries, and [`aibom.md`](aibom.md) for the
observed inventory included in the package.

`docs/audit.md` describes exporting **one event slice** as JSONL plus a manifest.
An audit package is the higher-level bundle for handing evidence to an auditor or
client: several scoped projections of the same local history, plus a claim matrix
that says what is and is not proven.

---

## 1. Package contents

A package is a directory with format `sugar-audit-package-v1`:

| File | Contents |
|---|---|
| `events.jsonl` | Canonical, id-ordered event export for the scope (payloads redacted by the package profile) |
| `events.manifest.json` | Event export manifest: count, first/last id, SHA-256, filters, truncation, optional HMAC |
| `tasks.json` | Scoped board task metadata (no task file contents) |
| `evidence.json` | Scoped verification evidence events + summary |
| `traces.json` | Up to 100 scoped trace summaries with bounded span details |
| `aibom.json` | Observed model/harness/tool/MCP/artifact inventory from the scoped events |
| `coverage.json` | Adapter coverage snapshot (24h) |
| `claims.json` | Claim matrix with evidence references, statuses and limitations |
| `README.md` | Human-readable scope, contents, method, limitations and verify command |
| `package.manifest.json` | SHA-256/size for every package file, scope, Sugar version, optional package HMAC |

The package manifest contains **bare filenames only**; it does not include task
file contents, credentials or commercial/planning content. It contains task and
event metadata and must be handled as sensitive.

Build behavior:

- read-only SQLite reads against the event spine and board DB; no writes to
  either, no new datastore;
- the package is assembled in a sibling temporary directory and swapped into
  place, so a failed build never leaves a partial package; `--force` replaces an
  existing directory (the previous package is preserved if replacement fails);
- event payloads are passed through Sugar's full redaction profile before
  hashing/export (`redact_payloads=True`); task fields are bounded and redacted;
- no LLM generation is used anywhere in the package.

---

## 2. Build

```bash
# All retained history for the default board/events DB
sugar audit package --out /secure/audit/2026-11

# Scoped to a project and time range
sugar audit package --out audit-pkg --project sugar \
  --since 2026-11-01 --until 2026-11-30

# Optional HMAC signature: the option is the *name* of the env var holding the key
SUGAR_AUDIT_HMAC_KEY="$(cat ~/.sugar/audit-hmac.key)" \
  sugar audit package --out audit-pkg --sign-key-env SUGAR_AUDIT_HMAC_KEY

# Machine-readable build summary
sugar audit package --out audit-pkg --json
```

| Option | Default | Notes |
|---|---|---|
| `--out DIR` | required | Output package directory |
| `--project` | all | Exact project scope |
| `--since` / `--until` | unbounded | ISO-8601 bounds for events/tasks |
| `--db PATH` | `$SUGAR_EVENTS_DB` or `~/.sugar/events.db` | Event spine |
| `--board-db PATH` | project config `storage.database` | Board task DB |
| `--sign-key-env VAR` | none | Name of the env var holding the HMAC key (never the key itself) |
| `--force` | off | Replace an existing package directory |
| `--json` | off | Print the build summary as JSON instead of text |

The summary prints out dir, manifest path, event/task/evidence/trace counts,
AIBOM component count, claims status counts, signed state (key id if signed),
limitations count and the verify command.

---

## 3. Verify offline

```bash
sugar audit verify-package /secure/audit/2026-11
SUGAR_AUDIT_HMAC_KEY="$(cat ~/.sugar/audit-hmac.key)" \
  sugar audit verify-package /secure/audit/2026-11 --sign-key-env SUGAR_AUDIT_HMAC_KEY
sugar audit verify-package /secure/audit/2026-11 --json
```

Verification checks:

- package format and manifest presence/validity;
- every required file exists and every listed file is a bare filename with
  matching size and SHA-256;
- JSON payload files parse;
- the event export manifest and JSONL still pass the existing
  `sugar audit verify` checks (canonical JSON, ordering, count/first/last ids,
  hash, optional HMAC);
- the optional package-manifest HMAC with the supplied key.

Exit code is non-zero on any mismatch, with a `•` list of errors. `--json`
emits a structured result (`ok`, `files_checked`, signed/key info, errors).
Verification is offline: no server, network or provider API is contacted.

---

## 4. What a package proves — and what it does not

**Proves (with the caveats under "does not"):**

- the listed files are exactly the files the package manifest describes; edits,
  additions, deletions or truncation of a listed file are detectable;
- the event export is a contiguous, canonical, id-ordered slice whose
  count/first/last id and hash match its manifest;
- when signed, the holder of the HMAC key vouched for the package manifest (and
  therefore the hashes, scope, filters and file list), and tampering with the
  manifest itself is detected;
- the claim matrix records, with references and limitations, which claims the
  package's contents support and which are only partial/not proven.

**Does not prove:**

- **Completeness.** The package is scoped by project/time and bounded by safety
  limits. Events older than retention, tasks not in the scoped board, or records
  beyond limits are legitimately absent. The claims matrix and limitations say
  where this applies.
- **Trusted time or order.** `generated_at` and event timestamps are local
  clocks; a package has no trusted timestamp/notary. Publish/anchor the manifest
  hash externally if time/existence must be independently witnessed.
- **Protection against a malicious operator.** Someone with the source databases
  and signing key can build a different package. HMAC is symmetric, not a public
  notary; without the key the package is hash-verified only.
- **WORM/immutability.** A package is a directory on disk; copy it to
  object-lock/WORM storage and back it up if that matters. Sugar never uploads
  it.
- **Truthfulness of the underlying events.** Evidence is only as good as what
  harnesses recorded; redaction removes secrets/oversized values but does not
  make payloads authoritative.
- **Legal/compliance certification.** It is evidence tooling, not a SOC 2,
  ISO 42001, EU AI Act or legal opinion.
- **AIBOM/standard certification.** `aibom.json` is an observed inventory, and
  the CycloneDX/SPDX-shaped projections are not certified standards documents
  (see [`aibom.md`](aibom.md)).

---

## 5. Claims matrix

`claims.json` (format `sugar-audit-claims-v1`) is the package's honesty layer.
Each claim has a status and evidence references:

| Claim | Status logic |
|---|---|
| Scoped event log was exported with a hash manifest | `proven` unless the export was truncated (`partial`) |
| Board tasks for the requested scope are included | `proven` unless the board DB was unavailable or the task list was truncated (`partial`) |
| Verification evidence is present for the scope | `proven` when ≥1 evidence event matched, else `not_proven` |
| Trace structure for the scope is reconstructable | `proven` when ≥1 scoped trace had events, else `not_proven` |
| Observed AI/model/tool inventory is included | `proven` when ≥1 AIBOM component was observed, else `not_proven` |
| Adapter coverage snapshot is included | `proven` when coverage has harnesses, else `partial` |
| Package hashes and optional HMAC are self-verifying | `proven`; without a signing key the limitation says hash-only |

Statuses are `proven`, `partial` or `not_proven`. The README and
`claims.json.limitations` list the concrete gaps (truncation, unavailable board
DB, empty evidence/traces/AIBOM, empty coverage, no signing key). A client should
read the matrix rather than treating package existence as a certificate.

---

## 6. Scope and limits

| Area | Limit / behavior |
|---|---|
| Events | Up to 50,000 exported per package (`_max_events()`); truncation is recorded and the claim becomes `partial` |
| Tasks | Up to 5,000 board tasks; truncation recorded |
| Evidence | Up to 5,000 evidence events |
| Traces | Up to 100 trace summaries with span details |
| Coverage | A 24h adapter snapshot from `GET /api/coverage`-equivalent data; it is **not** narrowed by the package's project/since/until scope (noted in limitations) |
| AIBOM | Built from the same scoped event slice, so it inherits adapter coverage/bounds and v0 limitations |
| Retention | The package does not extend the spine; export before pruning for durable history |
| Machines | One local spine/board DB per package; no cross-machine merge or global ordering |

If the board DB is unavailable, `tasks.json` is empty and the task claim becomes
`partial` rather than failing the whole build — the event slice is still useful.

---

## 7. Redaction and sensitivity

- The package's event export uses Sugar's **full redaction profile**
  (`sugar/telemetry/redact.py`): secret-looking keys/values, auth headers and
  oversized values are removed/truncated before hashing; the plain
  `sugar audit export` command does **not** redact by default, so prefer the
  package for external evidence sharing unless you have reviewed the export.
- Task records are bounded and redacted (`title`, `description`, `result`,
  `context`); no task file contents are included.
- Redaction is best-effort pattern/key based; it cannot guarantee that no
  sensitive value survives inside free-form telemetry. Review before sharing.
- Absolute paths may appear inside task/event metadata; the package manifest
  itself uses bare filenames only.
- Keep the directory owner-only, encrypt at rest when it leaves the machine, and
  never commit packages to a repository. Treat signing keys separately from the
  package (the CLI receives the env-var *name*, never the key value, and does not
  write or log it).
- The package README states the sensitivity and the verify command; ship that
  alongside the directory.

---

## 8. Relationship to the other exports

| Tool | What it is |
|---|---|
| `sugar audit export` / `verify` | One canonical JSONL slice + manifest (hash/HMAC); no task/trace/AIBOM bundle, no package redaction by default |
| `sugar audit package` / `verify-package` | Scoped, multi-file evidence directory with claims/README/manifest; uses the export internally with redaction |
| `sugar aibom --format cyclonedx|spdx` | Standalone observed inventory projections (not a package) |
| `sugar evidence record` | Individual verification records in the spine; included in the package's `evidence.json` |
| `GET /api/traces` | Live derived traces; included as `traces.json` summaries/spans when scoped events exist |

---

## 9. Limitations summary

- No trusted timestamping, remote notary, WORM storage or remote verification.
- No completeness guarantee: scopes and safety caps truncate legitimately;
  retention may have pruned the source.
- No protection against a malicious operator who holds the DB and signing key;
  hash-only packages have no signer non-repudiation.
- The package is only as trustworthy as the local event spine and the harnesses
  feeding it; redaction is best-effort, not a guarantee.
- The claims matrix is a structured disclosure of what the files support, not a
  compliance certificate or legal opinion.
- Not a standards-certified AIBOM and not an SPDX/CycloneDX conformance artifact;
  the AIBOM projection lives in `aibom.json` as observed v0.

---

## 10. Related documentation

- [`audit.md`](audit.md) — canonical JSONL export/verify format (the package's
  event component).
- [`evidence.md`](evidence.md) — verification records included in
  `evidence.json`.
- [`traces.md`](traces.md) — trace summaries/spans included in `traces.json`.
- [`aibom.md`](aibom.md) — observed inventory included in `aibom.json`.
- [`spend-attribution.md`](spend-attribution.md) — usage/cost events included in
  the event export.
- [`telemetry-adapters.md`](telemetry-adapters.md) — adapter coverage and
  redaction settings that shape the package.
