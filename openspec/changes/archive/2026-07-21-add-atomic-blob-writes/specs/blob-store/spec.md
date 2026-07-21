## MODIFIED Requirements

### Requirement: Concurrency and atomic cache writes
The `Store` and `BlobBackend` ports SHALL be safe to call concurrently from multiple threads. `BlobBackend.put` SHALL be atomic: it SHALL stream bytes to a reserved staging key and then rename onto the final key, so a blob appears at its key only once fully written. A crashed or failed `put` SHALL leave at most an orphaned staging object and MUST NOT leave a partial blob at its content hash. Because `CasStore.put` and the `CachingStore` cache back-fill both write through the backend, cache entries inherit this atomicity — `CachingStore` uses a per-hash lock so concurrent fetches of the same blob neither double-download nor expose a partially written cache file.

#### Scenario: Concurrent fetch of the same blob
- **WHEN** two threads fetch the same uncached hash simultaneously
- **THEN** the blob is downloaded at most once and both threads observe a fully written cache file

#### Scenario: Failed put leaves no partial blob
- **WHEN** a `put` fails or crashes partway through writing the bytes
- **THEN** no object exists at the target content hash, and `has(hash)` reports the blob absent

#### Scenario: Blob appears only when complete
- **WHEN** a concurrent reader checks a hash while another thread is writing it
- **THEN** it observes either no blob or the fully written blob, never a truncated one

### Requirement: Fsspec-backed blob backend
The system SHALL provide `FsspecBlobBackend(fs, root)` implementing `BlobBackend` over any fsspec `AbstractFileSystem` rooted at a prefix, so that fsspec-supported targets (local, memory, S3, GCS) are usable as blob backends without bespoke adapters. `put` SHALL stage writes under a reserved `{root}/.tmp/` namespace and rename onto the final key. `list()` SHALL exclude the reserved namespace, so staging objects are never surfaced as content hashes; a `sweep_temp()` operation SHALL reclaim orphaned staging objects on demand.

#### Scenario: Any fsspec filesystem becomes a backend
- **WHEN** an fsspec filesystem is wrapped by `FsspecBlobBackend`
- **THEN** `CasStore` over it provides full content-addressed `Store` behavior

#### Scenario: Staging objects are not listed as blobs
- **WHEN** `list()` is called while an orphaned staging object exists under `.tmp`
- **THEN** the staging object is not yielded, and `sweep_temp()` can reclaim it
