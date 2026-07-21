## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: Publish holds a blob lease
`Repository.publish` SHALL acquire a lease over its version and blob hashes before uploading them and SHALL release the lease after the publish completes, so that a concurrent `gc` treats the in-flight blobs as protected and the in-flight version as retained. The lease SHALL span from before the first blob upload through the pointer advance; if the publish fails or crashes, the lease MAY remain held (its version and blobs stay protected).

#### Scenario: Publish protects its blobs from concurrent GC
- **WHEN** `publish` is uploading blobs and committing a manifest while `gc` runs
- **THEN** the publish's blobs are under a live lease and `gc` does not collect them
