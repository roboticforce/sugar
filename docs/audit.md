# Audit evidence exports

Status: shipped. Related: [`local-dashboard.md`](./local-dashboard.md) §11 (event
spine), [`board-server.md`](./board-server.md) (exposing the board on a network).

Sugar's event spine (`~/.sugar/events.db`, `$SUGAR_EVENTS_DB`) is an append-only
SQLite table of task transitions and harness telemetry. `sugar audit export`
turns a time/kind slice of that table into two files that can be handed to an
auditor or a client:

- `<name>.jsonl` — one canonical JSON object per line, ordered by event id;
- `<name>.manifest.json` — count, first/last event id, SHA-256 of the JSONL
  bytes and, optionally, an HMAC-SHA256 signature.

The exporter only **reads** the spine (SQLite `mode=ro`). It does not upload
anything, does not write to the spine, and does not add a second datastore.

```
sugar audit export --out audit/2026-11.jsonl
sugar audit verify audit/2026-11.jsonl audit/2026-11.manifest.json
```

## 1. What an export proves — and what it does not

**With the hash alone (unsigned export):**

- The exported JSONL bytes are exactly the bytes that the manifest describes:
  editing, adding or deleting any line is detectable as a SHA-256 mismatch.
- The count and first/last event ids in the manifest match the file's contents,
  so a missing prefix/suffix or a gap shows up as a mismatch.
- Every line is canonical JSON (sorted keys, compact separators, UTF-8) and
  event ids are strictly increasing, so hand-editing that preserves the hash is
  still detectable.

**With a signature (signed export):**

- The holder of the HMAC key vouched for this manifest (and therefore for the
  JSONL hash, filters, count and id range) — tampering with the manifest itself
  is detected too.
- Anyone who has the same key can re-sign a different export, so a signature
  only distinguishes *holders of the key* from everyone else.

**What no local export can prove:**

- **No trusted timestamping.** The manifest `generated_at` and event `ts` are
  Sugar's local clock; the operator can set either. To prove *when* an export
  existed, publish/anchor the manifest SHA-256 somewhere you do not control
  (commit it to an externally witnessed repo, use an RFC 3161 timestamp
  authority, email it, etc.). Do that if the use case is compliance, not just
  sharing.
- **No proof that events were not pruned or exported selectively.** Retention
  (`$SUGAR_EVENTS_RETENTION_DAYS`) deletes old rows by design, and ids are
  monotonic but not dense: gaps are normal after pruning. The manifest describes
  *this export*, not the entire history. A chain of overlapping exports plus an
  external anchor is the closest local approximation.
- **No protection against a fully malicious operator** with database access:
  they can edit `events.db`, delete it, or sign a fabricated export if they hold
  the key. HMAC is not a notary and not WORM storage.
- **Not provider billing reconciliation, not SOC 2, not a legal opinion.** It is
  evidence tooling; the policy around it is yours.

## 2. Commands

### Export

```bash
# Everything currently in the spine
sugar audit export --out /secure/audit/2026-11.jsonl

# Time range (ISO-8601; UTC canonicalized; inclusive bounds)
sugar audit export --out audit.jsonl \
  --since 2026-11-01T00:00:00Z --until 2026-11-30T23:59:59Z

# Only selected kinds
sugar audit export --out audit.jsonl \
  --kinds task.created,task.completed,task.failed

# Safety cap (sets manifest.truncated when more rows matched)
sugar audit export --out audit.jsonl --limit 50000

# Signed (the HMAC key comes from an environment variable *name*)
SUGAR_AUDIT_HMAC_KEY="$(cat ~/.sugar/audit-hmac.key)" \
  sugar audit export --out audit.jsonl --sign-key-env SUGAR_AUDIT_HMAC_KEY

# Machine-readable summary for scripts
sugar audit export --out audit.jsonl --json
```

- `--db PATH` overrides the spine path (default `$SUGAR_EVENTS_DB`, else
  `~/.sugar/events.db`).
- `--out` may be any path ending in `.jsonl` (or any name); the manifest is
  written next to it as `<name>.manifest.json` (for `foo.jsonl` →
  `foo.manifest.json`, otherwise `foo.manifest.json` appended).
- Existing files are **not** overwritten unless `--force` is passed. Keep old
  exports — they are your chain of custody.
- The exporter streams rows in pages (`id` keyset pagination, 500 rows/page),
  so large ranges do not load the whole database into memory. All pages of one
  export are read inside a single SQLite read transaction, i.e. one consistent
  WAL snapshot even while the dashboard keeps appending events.
- `--kinds` takes a comma-separated list; `--since`/`--until` accept full
  ISO-8601 timestamps or date-only values (midnight UTC). `since <= until`
  is required. Both bounds are inclusive.
- `--limit N` is a safety guard (`1..1000000`). When the slice has more matching
  events than `N`, the export stops after `N` and `manifest.truncated` is
  `true`. Prefer splitting into smaller exports over silently truncated ones.

### Verify

```bash
# Hash-only verification (unsigned export)
sugar audit verify audit.jsonl audit.manifest.json

# Signed export: same key, via env var name
SUGAR_AUDIT_HMAC_KEY="$(cat ~/.sugar/audit-hmac.key)" \
  sugar audit verify audit.jsonl audit.manifest.json --sign-key-env SUGAR_AUDIT_HMAC_KEY

# JSON for scripts; exit code 0 = verified, 1 = mismatch/unreadable
sugar audit verify audit.jsonl audit.manifest.json --json
```

Exit codes: `0` verified; `1` mismatch, missing key, malformed file or any
verification failure (the reason is printed). A signed manifest verified without
`--sign-key-env` fails: an unchecked signature is not a pass.

Verification is offline — it needs only the two files (plus the key for signed
exports). It recomputes SHA-256 over the JSONL bytes, checks `count`,
`first_id`/`last_id`, canonical JSON per line and strictly increasing ids, then
checks the HMAC over the manifest core when signed.

## 3. Manifest schema (`sugar-audit-v1`)

```json
{
  "format": "sugar-audit-v1",
  "generated_at": "2026-11-09T19:11:53Z",
  "sugar_version": "3.10.6.dev0",
  "source": "sugar-event-spine",
  "first_id": 1,
  "last_id": 4,
  "count": 4,
  "sha256": "60233910ca58f81aa2b7f84c41250a44d79d4d7686f395373a4ebe42925b8e9f",
  "truncated": false,
  "filters": {
    "since": null,
    "until": null,
    "kinds": [],
    "limit": null
  },
  "signed": {
    "alg": "hmac-sha256",
    "key_id": "af08bdec638650b7",
    "signature": "0b1e…64 hex…"
  }
}
```

- `first_id` / `last_id` are the ids of the first/last exported events, or `null`
  for an empty export (`count: 0`, JSONL is a zero-byte file).
- `sha256` is the SHA-256 (lowercase hex) of the complete JSONL file bytes,
  including trailing newlines.
- `filters` records exactly what slice was requested (`kinds` sorted, `null`/`[]`
  when unfiltered). It is authenticated by the signature but not re-checked
  against a database during offline verification.
- `truncated` says whether `--limit` stopped the export early.
- `signed` is present only for signed exports. `alg` is `hmac-sha256`.
  `key_id` is the first 16 hex characters of `SHA-256(key)` — a fingerprint that
  identifies a key without revealing it (so key rotation can be audited).
- `source` is the origin kind (`sugar-event-spine`); the exporter intentionally
  does not embed absolute paths or hostnames beyond what events already carry.

### Canonical JSONL

Each line is `json.dumps(event, sort_keys=True, separators=(",", ":"),
ensure_ascii=False)` UTF-8 bytes plus `\n`. Event fields are the spine's event
shape from `local-dashboard.md` §11: `id`, `ts`, `kind`, `actor`, `actor_kind`,
`harness`, `machine`, `session_id`, `task_id`, `run_id`, `project`, `trace_id`,
`source`, `payload` (JSON object), `ingested_at`. Timestamps are the UTC
`...Z` strings stored in the spine.

### Signature definition (independent verification)

`signature = HMAC-SHA256(key, core_bytes)`, hex-encoded, where `core_bytes` is:

```
json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
```

and `core` is the manifest object with the `"signed"` key removed entirely. This
is reproducible with any JSON library; see the workflow below.

## 4. Key management

- The CLI accepts only the **name** of an environment variable
  (`--sign-key-env VAR`), never a literal key on the command line. Keys
  therefore do not appear in `argv`, shell history, the manifest, stdout or
  Sugar's logs.
- Use a high-entropy key, e.g. 32 random bytes:
  `openssl rand -hex 32 > ~/.sugar/audit-hmac.key && chmod 600 ~/.sugar/audit-hmac.key`.
- Inject it at runtime (`SUGAR_AUDIT_HMAC_KEY="$(cat …)" sugar audit …`) or from
  a secret manager/direnv. `chmod 600` the key file; never commit it.
- Anyone with the key can forge a valid signed export. Treat it like a signing
  credential: one key per auditor/tenant, rotate on staff changes, keep old key
  IDs in your records so old exports remain verifiable.
- `key_id` is a truncated hash of the key — do not use low-entropy passphrases:
  the fingerprint is brute-forceable offline if the key is guessable.

## 5. Verification workflow (auditor side)

1. Check the manifest `format` is `sugar-audit-v1` and that `sha256`,
   `count`, `first_id`, `last_id` are present.
2. Run `sugar audit verify export.jsonl export.manifest.json [--sign-key-env …]`.
3. Independently, if you do not want to trust Sugar's verifier:
   ```bash
   sha256sum export.jsonl                       # compare with manifest sha256
   wc -l export.jsonl                           # compare with manifest count
   head -c 400 export.jsonl                     # spot-check canonical records
   ```
   For signed exports, reconstruct the core (manifest minus `signed`), canonical
   JSON-encode it with sorted keys and compact separators, and compare
   `openssl dgst -sha256 -hmac "$KEY"` against `signed.signature`.
4. Anchor the manifest hash externally if you need existence-at-time evidence
   (§1).
5. Compare `first_id`/`last_id` with the previous export in your chain: ids must
   move forward (gaps are fine — retention prunes rows).

## 6. Retention and chain of custody

- The spine prunes events older than `$SUGAR_EVENTS_RETENTION_DAYS` (default 30;
  `0` disables pruning) at server startup and hourly. **Run exports before
  pruning**, on a schedule that matches your retention policy, and keep the
  exports (they are the durable record).
- Exports are written owner-only (`0600`) next to each other, but they are not
  automatically rotated, uploaded or archived. Copy them to your own WORM/
  object-lock storage if you need that; Sugar deliberately has no remote upload.
- The manifest's `first_id`/`last_id`/`count` let a chain of exports be compared
  for gaps, but a gap can also be legitimate pruning — record your retention
  policy alongside the exports so an auditor can tell the difference.
- Clearing `~/.sugar/events.db` destroys the local source; exports are then the
  only evidence. Back the spine up like any other audit source if that matters.

## 7. Sensitivity

An export contains **task metadata**: titles, descriptions, actors, harnesses,
project ids, hostnames, session ids and free-form telemetry payloads. Treat it
like application logs:

- keep it `0600`, in an access-controlled directory, encrypted at rest if it
  leaves the machine;
- do not commit exports to a repository (add `*.jsonl` under your audit dir to
  `.gitignore`);
- review/sanitize before sharing with a third party — payloads are supplied by
  harnesses and may contain anything they choose to send.

The exporter itself never writes secrets into exports, never prints the HMAC
key and never logs it. It also does not redact event payloads.

## 8. Non-goals (v1)

- No trusted timestamping/notary, no remote/WORM upload, no remote verification
  service.
- No database mutation, schema change or rollups — this is a read-only snapshot
  format on top of the existing spine.
- No cross-machine merge or global ordering: one export covers one spine file.
- No per-event integrity chain (hash chaining of events is future work); the
  manifest hashes the exported snapshot as a whole.
