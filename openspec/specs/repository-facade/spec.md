# repository-facade Specification

## Purpose
TBD - created by archiving change add-core-ports. Update Purpose after archive.
## Requirements
### Requirement: Repository composes a Registry and a Store
The system SHALL provide a `Repository` facade constructed from one `Registry` and one `Store`. The facade SHALL expose the read surface — `head`, `resolve`, `open(snap, path)`, `fetch_all(snap)` — and a `publish` operation, delegating manifest concerns to the registry and byte concerns to the store.

#### Scenario: Open materializes one entry
- **WHEN** `open(snap, path)` is called
- **THEN** the entry's `content_hash` is resolved from the snapshot and its bytes are materialized through the store

#### Scenario: Resolve carries no blob bytes
- **WHEN** `resolve` returns a snapshot
- **THEN** no blob has been downloaded to produce it

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

