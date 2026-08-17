## Why

Today the only shared, multi-writer manifest plane is `PostgresRegistry` — which means running a database. But S3 gained the primitives to be a safe multi-writer store on its own: **put-if-absent** (`If-None-Match:*`, GA Aug 2024) and **compare-and-swap** (`If-Match`, GA Nov 2024), on top of strong read-after-write + LIST consistency (2020). That makes a registry possible with **just an S3 bucket** — no database, no DynamoDB lock table, no special bucket provisioning — and it stays inside the fsspec/s3fs stack the blob plane already uses.

## What Changes

- Add **`S3Registry`**: a `Registry` implemented directly on object storage via fsspec/s3fs, using **put-if-absent as the only write primitive** (never an overwrite). A pointer's history is an append-only stream of immutable, zero-padded event objects; advancing a pointer is a compare-and-swap by writing the next sequence number with put-if-absent — the loser of a race gets a `FileExistsError` and retries (verified: s3fs surfaces the 412 as `FileExistsError`; moto models it in-process).
- Add **`open_s3(url, *, blob_url=None, storage_options=…)`** — a one-URL opener assembling `S3Registry` + the S3 blob `Store`, mirroring `open_local`/`open_cloud`. The `s3` extra already carries the deps.
- **Lease-free by design.** S3 exposes no registry clock, so durable TTL leases don't translate; the lease methods are degenerate no-ops. The publish/GC blob race is closed by the **existing blob-grace backstop** (already TLA-verified) — reused unchanged, no new mechanism.
- **A TLA-verified guard for the one race S3's lack of transactions genuinely reopens**: dropping an out-of-retention version *concurrently with a promotion of that same version*. The SQL backend is serialized by row visibility; a transaction-less object store is not. This change models that race in TLA and implements the minimal closure before enabling version reclamation on S3.
- **History is forever.** Because events are immutable, `drop_version` reclaims the manifest + blobs and writes a **tombstone** rather than pruning log events. GC reclaims heavy data; the lightweight audit log persists — consistent with the append-only provenance model.

## Capabilities

### New Capabilities
- `s3-registry`: a content-addressed manifest plane on plain S3 (any fsspec S3 filesystem) — put-if-absent commits, append-only per-pointer event streams with gap-free compare-and-swap, cheap `head`, atomic `resolve`, append-only forever history with tombstone-based reclamation, degenerate leases with blob-grace safety, a transaction-less version-drop guard, and the `open_s3` opener. Satisfies the existing `Registry` port and is differential-tested against the in-memory reference backend.

### Modified Capabilities
<!-- none: the change is purely additive. The Registry port is satisfied as-is; blob-grace
     in garbage-collection is reused unchanged (manifest liveness is NOT delegated to grace);
     open_s3 lives in the new capability alongside open_local/open_cloud's siblings. -->

## Impact

- **Code**: new `src/sartre/s3.py` (`S3Registry`) and an `open_s3` opener; new `openspec/specs/s3-registry`; a TLA model under the change's `model/`. No changes to `_sql.py`, `memory.py`, `repository.py`, or the GC algorithm.
- **APIs**: additive — a new registry class and opener behind the unchanged `Registry` port. `Repository`, `gc`, and the CLI addressing already work over any `Registry`.
- **Deps**: none new — `s3` extra (`boto3`, `s3fs`) already exists; `moto[s3,server]` (dev) drives the in-process differential tests.
- **Provisioning**: none for real AWS S3 (conditional writes are on by default, all regions, all general-purpose buckets; strong read-after-write since 2020). S3-compatible endpoints (MinIO/R2/Ceph) are probed for conditional-write support and rejected with a clear error if absent.
- **Behavioral note**: on S3, GC does not prune history events (tombstones instead) — so `list_log` retains events for dropped versions where the SQL backend would not. Differential equivalence is asserted modulo tombstoned history.
