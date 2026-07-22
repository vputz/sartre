## Context

The lease discipline (change `add-gc-retention`) closes the publish/GC race: GC may
collect a blob only if it is unreferenced by every retained manifest **and** named by
no live lease, and it must not drop a committed-but-unpointed manifest a live lease
protects. That was verified in `GC.tla` for a lease held **permanently** for the
publish's whole duration, and the leases were implemented **in process memory**
(`_SqlRegistry._leases`, `MemoryRegistry`'s lease map).

That is correct for a single-process local repository, where GC and publish share one
`Registry` instance. It is **not** correct for the shared, multi-writer registry shipped
in `add-cloud-backend`: two writers over one Postgres DSN hold their leases in separate
Python processes, so a GC process reads an *empty* lease set and can sweep another
writer's in-flight blob. Cloud GC is therefore not yet safe.

Fixing that means leases must live **in the registry**. Durable leases then need a TTL,
or a crashed publisher's lease pins its blobs forever (an unreclaimable leak). A TTL, in
turn, can lapse while a publish is still live — which reopens the very race the lease
closed, unless publish re-checks its lease before the irrevocable steps. A separate,
weaker backstop (grace-period) covers writers that never took a lease.

This design was settled model-first. Two TLA+ models under `model/`, both run through
the pinned TLC protocol (`.claude/skills/tla-verify`), are its proof basis.

## Goals / Non-Goals

**Goals:**
- Make GC provably safe when many writers share one registry.
- Bound lease lifetime with a TTL so crashed publishers cannot leak storage.
- Keep the verified `BlobSafe`/`TipSafe` invariants under lease *expiry*.
- Provide a blob-only grace-period backstop for non-participating writers.
- Preserve observational equivalence of the SQL and memory registries when leases are
  unexpired (the differential machine stays green).

**Non-Goals:**
- Object-store-native leases (S3 conditional-PUT) — leases are registry-side only.
- A distributed/synchronized clock: correctness rests on a *single* registry's `now()`.
- Connection pooling and a schema-migration framework (additive DDL only).
- Protecting the commit→advance *manifest* window with grace — that is the lease's job;
  grace is a blob backstop only.

## Decisions

### D1. Leases live in the registry, keyed by a registry-assigned id
A `leases` table (`lease_id`, `version`, `hashes` JSON, `expires_at`) in the shared SQL
schema; `MemoryRegistry` keeps an equivalent map. `active_leased_hashes()` /
`active_leased_versions()` become `SELECT … WHERE expires_at > now()`. This is the
actual multi-writer fix: a GC process sees every writer's live roots.
*Alternative rejected:* a lock table / advisory lock — a lease is a **claim of roots**
(union semantics), not mutual exclusion; locks would serialize independent publishes.

### D2. The registry clock is the sole authority
`expires_at` is computed and compared with the registry's `now()` (SQL `now()` /
`CURRENT_TIMESTAMP`; an injectable clock in `MemoryRegistry`), never a client clock.
Writers and GC hosts may have skewed clocks; only the registry's matters. `acquire`,
`renew`, and the active-root filter all evaluate time server-side.
*Alternative rejected:* client-supplied timestamps — skew would silently shrink or
inflate the protection window across hosts.

### D3. Publish self-check is the safety mechanism; heartbeat is only liveness
`GCLease.tla` decouples lease *validity* from publish progress: an adversarial
`Expire(p)` may revoke a lease mid-publish, and only unexpired leases are GC roots.

- With `SelfCheck = FALSE` (TTL, no re-check) the model **fails** `BlobSafe`: a lease
  lapses after the blob is put, GC sweeps it, and publish commits over the hole
  (20-state counterexample).
- With `SelfCheck = TRUE` — publish re-verifies the lease is still valid immediately
  before `commit` **and** before `set_pointer`, aborting if lapsed — `BlobSafe` and
  `TipSafe` hold under **every** interleaving, including "expire one step after
  acquire."

The model therefore establishes a clean separation: **renewal/heartbeat is a liveness
device** (safety holds with zero renewals — a lapsed lease merely forces an abort);
**the self-check is what carries safety.** In code, `renew_lease` returns a boolean
(`UPDATE … WHERE lease_id=? AND expires_at > now()`, rowcount==1) and doubles as the
self-check: publish calls it before commit and before the pointer CAS, and on `False`
releases and raises a retryable error. Both guards are required — commit protects
`BlobSafe`, advance protects `TipSafe`; the model breaks if either is dropped.

### D4. Grace-period is a blob-only backstop with an explicit timed lemma
For a writer that takes no lease, GC retains any blob younger than `grace`
(store mtime via `fs.info`). `GCGrace.tla` models this on a discrete clock (blobs carry
`bornAt`; a publish's put→commit gap is bounded by `MaxGap`, the operational max publish
duration `d`):

- `Grace > MaxGap` → `BlobSafe` holds **and** GC still reclaims abandoned uploads once
  they age past `grace` (proven non-vacuous — GCSweep actively deletes).
- `Grace <= MaxGap` → `BlobSafe` **fails** (7-state timed counterexample: blob ages to
  `grace` mid-publish, is swept, commit-over-hole).

This discharges the `g >= d => BlobSafe` lemma deferred in `add-gc-retention`. Grace
protects only the put→commit *blob* window; it cannot keep a committed-but-unpointed
manifest alive, so it does **not** replace the lease — it is a safety net for external
writers, carrying the operational assumption `grace >= max_publish_duration`.

### D5. Composition of the two mechanisms is safe by monotonicity
The two models are verified independently rather than as one combined model. GC's live
set is `referenced ∪ leased-and-unexpired ∪ younger-than-grace`. Each mechanism only
**adds** to that set; neither ever causes a deletion the other wouldn't. Since `BlobSafe`
and `TipSafe` are preserved by *growing* the retained set, adding grace to the lease-GC
cannot break the lease guarantees and vice-versa — no combined model is needed.

### D6. Default ttl/grace keep public signatures stable
`acquire_lease` gains `ttl`; `Repository.publish` picks a default ttl and heartbeat
interval (`ttl/2`); `gc`/`RetentionPolicy` gains an optional `grace` (default 0 =
lease-only, preserving current behavior for callers who don't set it). No
`open_local`/`open_cloud`/`publish`/`gc` default call site changes.

## Risks / Trade-offs

- **A publish slower than its ttl aborts and retries.** → Heartbeat renews every `ttl/2`;
  ttl is chosen well above expected upload time. Safety is unaffected either way (D3);
  this is a throughput knob.
- **Grace rests on an unverifiable operational bound** (`grace >= max_publish_duration`)
  and on store-mtime/clock agreement. → Keep grace a *backstop*, not the primary path;
  document and (operationally) monitor publish upload→commit gaps. Participating writers
  use the lease, which needs no duration bound.
- **Clock skew across writers.** → Neutralized by D2: only the registry clock is trusted.
- **Reversing the process-scoped lease guarantee is spec-breaking.** → It is an internal
  contract (lease surface), not a public API; default call signatures are preserved (D6),
  and the differential machine pins observational equivalence for unexpired leases.
- **Two TLC models trip the coverage gate on `GCAbort` (0 distinct states).** → A benign
  artifact identical to the canonical `GC.tla` (GCAbort only resets the mark, discovering
  no new state); the invariants are the real verdict.

## Migration Plan

1. Add the `leases` table with `CREATE TABLE IF NOT EXISTS` (additive; old DBs gain it on
   next open). No data migration — leases are ephemeral by nature.
2. Ship `acquire_lease(ttl)`, `renew_lease`, expiry-filtered active roots; keep the old
   `acquire_lease(version, hashes)` shape working via a default ttl.
3. Wire `publish` heartbeat + self-check; wire `gc` grace window (default 0).
4. Rollback is trivial: the table is additive and grace defaults to lease-only behavior.

## Open Questions

- Default ttl and heartbeat interval values (operational tuning, not correctness).
- Whether to expose `grace` on `open_cloud` directly or only on `gc(policy)`.
