# in-memory-backend Specification

## Purpose
Define the ephemeral, single-process reference backend that makes the blob and
manifest ports runnable end-to-end. It is the executable oracle for the port
contracts — suitable for tests and local use — and the substrate against which
the publish-transaction invariants are property-checked on live code.

## Requirements
### Requirement: In-memory reference backend
The library SHALL provide an ephemeral, single-process reference backend — a
memory-backed `Store` and an in-memory `Registry` — that implements the blob and
manifest ports and upholds their contracts. It SHALL be suitable for tests and
local use, and SHALL hold all state in process (no persistence).

#### Scenario: Publish then resolve round-trips
- **WHEN** an artifact is published to a coordinate and then resolved at `Head`
- **THEN** the resolved snapshot's entries equal the published entries and each
  entry's bytes are retrievable through the store

#### Scenario: Content-idempotent commit and dedup
- **WHEN** the same entries are published twice
- **THEN** both publishes resolve to the same version and the manifest is stored
  once

### Requirement: Runnable read core over the reference backend
`Repository` SHALL implement `head`, `resolve`, `open`, and `fetch_all` against
the reference backend. `fetch_all` SHALL materialize a multi-entry snapshot,
fetching uncached blobs concurrently, and SHALL re-download only blobs absent from
the cache when resolving a version that shares blobs with an already-fetched one.

#### Scenario: Cross-version cache reuse
- **WHEN** version v2 shares blobs with an already-fetched v1 and is fetched
- **THEN** only the blobs whose hashes are absent from the cache are downloaded

#### Scenario: Integrity is verified on download
- **WHEN** a blob's stored bytes are corrupted and then read
- **THEN** the read raises an integrity error rather than returning bad bytes

### Requirement: Publish protocol upheld by the implementation
`Repository.publish` SHALL implement the full-replacement, fail-fast protocol —
store blobs, commit the manifest, then compare-and-swap the pointer guarded by the
tip read at start — and SHALL raise on a conflicting concurrent advance without
clobbering or retrying.

#### Scenario: Concurrent publishers, one wins
- **WHEN** two publishes target the same pointer from the same starting tip
- **THEN** exactly one succeeds and advances the pointer and the other raises a
  conflict, leaving the pointer at exactly one published version

### Requirement: Property-based verification of publish invariants
The reference backend SHALL be covered by property-based tests, including a
stateful machine that drives randomized publish, resolve, promote, and
crash-and-retry sequences and asserts the publish-transaction invariants on the
live system: the tip is always a committed manifest whose blobs are all present
(no dangling reference), and a retried publish converges to a consistent state.

#### Scenario: No reachable state has a dangling tip
- **WHEN** the stateful test explores randomized operation sequences including
  interrupted-and-retried publishes
- **THEN** in every observed state the current tip resolves to a fully present
  manifest, matching the `PointerSafe` invariant verified by the TLA+ model
