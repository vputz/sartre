## Why

Leases are currently in-memory and process-scoped, so in a shared/multi-writer
registry (the cloud backend) a GC process cannot see another writer's in-flight
lease — reopening the exact publish/GC `BlobSafe` race the lease discipline was
verified to close. Making cloud GC actually safe requires leases that live in the
registry, and durable leases in turn need a TTL (so a crashed publisher cannot pin
blobs forever) plus a publish self-check (so a TTL that lapses mid-publish cannot
be exploited). A grace-period backstop covers writers that take no lease at all.

## What Changes

- **Durable leases**: leases move from process memory into the registry (a `leases`
  table in the SQL backends; an equivalent expiry-aware map in `MemoryRegistry`), so
  every writer's GC observes every writer's active roots.
- **Registry-clock TTL**: `acquire_lease(version, hashes, ttl)` stamps `expires_at`
  using the registry's own clock; `active_leased_hashes`/`active_leased_versions`
  report only unexpired leases. Add `renew_lease(lease_id, ttl) -> bool` (a
  conditional bump that also answers "is my lease still valid?").
- **Publish heartbeat + self-check**: `Repository.publish` renews its lease on a
  background heartbeat (liveness), and — the safety-critical part — re-verifies the
  lease is still valid immediately before `commit` **and** before `set_pointer`,
  aborting and retrying if it lapsed. Idempotent content-addressed re-put makes retry
  safe.
- **Grace-period backstop**: `gc` gains a `grace` window; GC retains any blob whose
  store mtime is younger than `grace`, protecting the put→commit window of a writer
  that never leased. This is a blob-only safety net carrying the operational
  assumption `grace >= max_publish_duration`.
- **BREAKING** (spec-level, internal API): the "leases are process-scoped" guarantee
  is reversed — leases are now durable and expiry-bounded. `acquire_lease` gains a
  `ttl` parameter. No public `open_local`/`open_cloud`/`publish`/`gc` call signature
  breaks for default callers (ttl and grace get sensible defaults).
- **Verification**: two TLA+ models (`GCLease.tla`, `GCGrace.tla`), already run
  through the pinned TLC protocol, are added under the change's `model/` dir as the
  design's proof basis.

## Capabilities

### New Capabilities
<!-- none — this refines existing lease/GC behavior -->

### Modified Capabilities
- `registry-port`: the **Blob lease surface** requirement gains a TTL — `acquire_lease`
  takes a lease duration, active-root queries exclude expired leases, and a new
  `renew_lease` conditionally extends a still-valid lease.
- `persistent-registry`: the **Lease lifetime is process-scoped** requirement is
  replaced by **durable, registry-clock TTL leases** — leases survive within the shared
  registry and are reclaimed by expiry, not by process exit.
- `garbage-collection`: the **Blob lease** requirement accounts for lease *expiry*
  (only unexpired leases are roots) and adds the publish **self-check** as the safety
  condition; a new **grace-period backstop** requirement retains recently-written blobs;
  the verified-model requirement is extended to cover `GCLease.tla` and `GCGrace.tla`.
- `repository-facade`: the **Publish holds a blob lease** requirement gains heartbeat
  renewal and the self-check-before-commit-and-advance abort semantics.

## Impact

- **Code**: `src/sartre/_sql.py` (leases table + TTL queries + `renew_lease`),
  `src/sartre/sqlite.py` / `src/sartre/postgres.py` (dialect `now()`/clock hooks),
  `src/sartre/memory.py` (expiry-aware lease map + injectable clock),
  `src/sartre/ports.py` (`Registry` lease-surface signatures), `src/sartre/repository.py`
  (`publish` heartbeat + self-check; `gc`/`RetentionPolicy` grace window),
  `src/sartre/store.py` (blob mtime accessor via `fs.info`).
- **Tests**: real-Postgres cross-instance lease visibility + expiry; a property-based
  self-check abort test; a grace-period test on an injected clock + settable-mtime
  backend; the existing differential machine stays green.
- **Docs/specs**: delta specs for the four capabilities above; TLA models under
  `model/`.
- **Non-goals**: object-store-native lease (S3 conditional-PUT), connection pooling, a
  schema-migration framework (additive `CREATE TABLE IF NOT EXISTS` only), any
  cross-registry distributed clock beyond a single registry's `now()`.
