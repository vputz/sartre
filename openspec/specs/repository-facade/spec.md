# repository-facade Specification

## Purpose
TBD - created by archiving change add-core-ports. Update Purpose after archive.
## Requirements
### Requirement: Repository composes a Registry and a Store
The system SHALL provide a `Repository` facade constructed from one `Registry` and one `Store`. The facade SHALL expose the read surface — `head`, `resolve`, `open(snap, path)`, `fetch_all(snap)` — a `snapshot_fs(snap)` factory returning a read-only fsspec filesystem bound to that snapshot, a `checkout(snap, dest)` operation materializing the whole tree under a caller-chosen directory, a `publish` operation, and a `gc(policy) -> GCResult` operation reclaiming storage by mark-and-sweep, delegating manifest concerns to the registry and byte concerns to the store.

#### Scenario: Open materializes one entry
- **WHEN** `open(snap, path)` is called
- **THEN** the entry's `content_hash` is resolved from the snapshot and its bytes are materialized through the store

#### Scenario: Resolve carries no blob bytes
- **WHEN** `resolve` returns a snapshot
- **THEN** no blob has been downloaded to produce it

#### Scenario: Snapshot filesystem is bound to the version
- **WHEN** `snapshot_fs(snap)` is called
- **THEN** it returns a read-only fsspec filesystem whose listings come from `snap`'s manifest and whose reads resolve bytes through the store

#### Scenario: Checkout lays out the tree under the destination
- **WHEN** `checkout(snap, dest)` is called
- **THEN** every entry is written at its logical path under `dest`, fetched concurrently and deduped against the cache, and nothing is written outside `dest`

#### Scenario: GC reclaims through the facade
- **WHEN** `gc(policy)` is called
- **THEN** the facade computes roots via the registry, sweeps unreferenced blobs via the store, and returns a result describing what was dropped

### Requirement: Publish holds a blob lease
`Repository.publish` SHALL acquire a lease over its version and blob hashes, with a TTL,
before uploading them, so that a concurrent `gc` treats the in-flight blobs as protected
and the in-flight version as retained. While the publish runs it SHALL keep the lease
alive with a background heartbeat that renews the lease well within its TTL (a liveness
device; safety does not depend on renewal succeeding). Immediately before committing its
manifest, and again immediately before advancing its pointer, `publish` SHALL re-verify
its lease is still live (via `renew_lease`); if the lease has lapsed it SHALL abort the
publish — releasing the lease and raising a retryable error — rather than commit or
advance over blobs GC may have reclaimed. Because blob puts are content-addressed and
idempotent, a retried publish re-uploads safely. On completion or abort `publish` SHALL
stop the heartbeat and release the lease. If the publish crashes, its lease MAY remain
held until its TTL expires, after which its version and blobs become collectable.

#### Scenario: Publish protects its blobs from concurrent GC
- **WHEN** `publish` is uploading blobs and committing a manifest while `gc` runs, and its
  lease is kept live by the heartbeat
- **THEN** the publish's blobs are under a live lease and `gc` does not collect them

#### Scenario: Publish aborts when its lease lapses before commit
- **WHEN** a publish's lease expires mid-flight (e.g. an upload outran the TTL and a
  heartbeat was missed) and its pre-commit self-check finds the lease lapsed
- **THEN** `publish` aborts with a retryable error and does not commit a manifest over
  possibly-reclaimed blobs

#### Scenario: Publish aborts when its lease lapses before advancing the pointer
- **WHEN** a publish's lease expires after commit but before the pointer CAS, and its
  pre-advance self-check finds the lease lapsed
- **THEN** `publish` aborts with a retryable error and does not point at a manifest whose
  blobs GC may have reclaimed

### Requirement: Publish ordering through the facade
`publish` SHALL upload blobs to the store before recording the manifest, skipping blobs already present (`has`), then `commit` the manifest, then advance the target pointer via compare-and-swap. The detailed crash-safe ordering and conflict-retry protocol is specified in a separate change; this facade SHALL expose the operation with that ordering intent.

#### Scenario: Existing blobs are not re-uploaded
- **WHEN** publishing a manifest whose blobs are already stored
- **THEN** those blobs are not uploaded again before the manifest is committed

### Requirement: Sync core with parallel batch operations
The ports SHALL be synchronous and single-item. Multi-file operations (`fetch_all`, `checkout`) SHALL parallelize downloads across a thread pool with a configurable worker count, relying on blob I/O releasing the GIL. When the backend offers a batch hook, the facade SHALL prefer it over per-item fan-out.

#### Scenario: Parallel multi-file fetch
- **WHEN** `fetch_all` materializes a many-entry snapshot
- **THEN** uncached blobs are fetched concurrently up to the configured worker limit

### Requirement: Optional async wrapper
The system SHALL provide an `AsyncRepository` that wraps the synchronous core, offering awaitable equivalents of the read/publish surface by offloading to a thread (e.g. `asyncio.to_thread`). It SHALL NOT duplicate the core logic in a separate async implementation.

#### Scenario: Async call does not block the event loop
- **WHEN** an async application awaits a repository read
- **THEN** the synchronous work runs off the event loop thread and the loop remains responsive

