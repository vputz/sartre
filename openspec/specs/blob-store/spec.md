# blob-store Specification

## Purpose
TBD - created by archiving change add-core-ports. Update Purpose after archive.
## Requirements
### Requirement: Content-addressed Store interface
The `Store` SHALL expose a content-addressed byte interface: `has(hash)`, `open(hash) -> BinaryIO`, `get_to(hash, dest) -> Path`, `put(data) -> Hash`, and `delete(hash)`. The interface SHALL be stream/path-oriented and MUST NOT require whole-blob materialization into a `bytes` value, so that multi-hundred-megabyte blobs are supported.

#### Scenario: Put returns the content hash
- **WHEN** bytes are written via `put`
- **THEN** the returned key is the self-describing content hash of those bytes

#### Scenario: Idempotent put
- **WHEN** identical bytes are `put` a second time
- **THEN** the same hash is returned and no duplicate stored copy is required

#### Scenario: Large blob materialized to a path
- **WHEN** a large blob is fetched via `get_to`
- **THEN** it is written to a local path without being buffered whole in memory

### Requirement: Dumb BlobBackend sub-port
The system SHALL define a `BlobBackend` port operating on opaque keys — `get(key)`, `put(key, data)`, `exists(key)`, `delete(key)` — with no awareness of hashing or content addressing. A `CasStore(backend, hasher)` SHALL implement `Store` over any `BlobBackend`, centralizing hashing and verification so backends remain dumb key/value stores.

#### Scenario: New backend implements only key/value methods
- **WHEN** a new storage target is added as a `BlobBackend`
- **THEN** it implements only the four opaque key/value methods and gains CAS semantics via `CasStore`

### Requirement: Verify on download
`CasStore` SHALL verify that fetched bytes hash to the requested key, using the algorithm named in the key. Verification SHALL occur when bytes are downloaded into the local cache; once cached by content hash, subsequent reads MAY be served without re-verification.

#### Scenario: Corrupted bytes rejected
- **WHEN** fetched bytes do not hash to the requested key
- **THEN** the read raises a typed integrity error and the bad bytes are not retained in the cache

### Requirement: Cache is a Store
The system SHALL provide `CachingStore(local, remote)` that itself implements `Store`. A read SHALL serve from `local` on a hit, otherwise fetch from `remote`, populate `local`, and return the bytes. Because the cache is keyed by content hash, resolving a new version SHALL re-download only blobs whose hashes are not already cached.

#### Scenario: Cross-version cache reuse
- **WHEN** version v2 shares blobs with an already-fetched v1
- **THEN** only blobs with hashes absent from the local cache are downloaded

### Requirement: Concurrency and atomic cache writes
The `Store` and `BlobBackend` ports SHALL be safe to call concurrently from multiple threads. `CachingStore` SHALL use a per-hash lock and write cache entries via a temporary file followed by an atomic rename, so concurrent fetches of the same blob neither double-download nor expose a partially written cache file.

#### Scenario: Concurrent fetch of the same blob
- **WHEN** two threads fetch the same uncached hash simultaneously
- **THEN** the blob is downloaded at most once and both threads observe a fully written cache file

### Requirement: Fsspec-backed blob backend
The system SHALL provide `FsspecBlobBackend(fs, root)` implementing `BlobBackend` over any fsspec `AbstractFileSystem` rooted at a prefix, so that fsspec-supported targets (local, memory, S3, GCS) are usable as blob backends without bespoke adapters.

#### Scenario: Any fsspec filesystem becomes a backend
- **WHEN** an fsspec filesystem is wrapped by `FsspecBlobBackend`
- **THEN** `CasStore` over it provides full content-addressed `Store` behavior

### Requirement: Optional batch fetch hook
A `BlobBackend` MAY implement an optional `get_many(keys)` batch hook. When present, the facade SHALL use it to fetch multiple blobs concurrently (e.g. via a backend's native concurrent read); when absent, the facade SHALL fall back to fanning single-item fetches across a thread pool.

#### Scenario: Native batch used when available
- **WHEN** a backend implements `get_many` and several blobs are requested together
- **THEN** the facade fetches them via the batch hook rather than one-at-a-time

