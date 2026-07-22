## MODIFIED Requirements

### Requirement: Content-addressed Store interface
The `Store` SHALL expose a content-addressed byte interface: `has(hash)`, `open(hash) -> BinaryIO`, `get_to(hash, dest) -> Path`, `put(data) -> Hash`, `delete(hash)`, `mtime(hash) -> float | None`, and `list() -> Iterable[Hash]` enumerating all stored content hashes. The interface SHALL be stream/path-oriented and MUST NOT require whole-blob materialization into a `bytes` value, so that multi-gigabyte blobs are supported. `put` SHALL consume its source in a single streaming pass and MUST NOT buffer the whole blob in memory: it hashes while writing and names the blob by the resulting content hash.

#### Scenario: Put returns the content hash
- **WHEN** bytes are written via `put`
- **THEN** the returned key is the self-describing content hash of those bytes

#### Scenario: Idempotent put
- **WHEN** identical bytes are `put` a second time
- **THEN** the same hash is returned and no duplicate stored copy is required

#### Scenario: Put streams without buffering the whole blob
- **WHEN** a blob larger than available memory is written via `put`
- **THEN** it is stored without ever holding the whole blob in a single `bytes` value

#### Scenario: Large blob materialized to a path
- **WHEN** a large blob is fetched via `get_to`
- **THEN** it is written to a local path without being buffered whole in memory

#### Scenario: Enumerate stored hashes for sweep
- **WHEN** `list` is called
- **THEN** it yields the content hash of every blob currently stored, so a garbage collector can compute the set to sweep

### Requirement: Dumb BlobBackend sub-port
The system SHALL define a `BlobBackend` port operating on opaque keys — `get(key)`, `stage(data) -> str`, `promote(staging_key, final_key)`, `exists(key)`, `delete(key)`, `mtime(key) -> float | None`, and `list() -> Iterable[str]` — with no awareness of hashing or content addressing. `stage` SHALL stream `data` to a reserved staging key and return it; `promote` SHALL atomically make the staged bytes appear at `final_key`. There SHALL be exactly one write path (`stage` then `promote`); the backend SHALL NOT expose a key-first `put`. A `CasStore(backend, hasher)` SHALL implement `Store` over any `BlobBackend`, centralizing hashing and verification so backends remain dumb key/value stores: `CasStore.put` SHALL stage the source through a hashing pass, learn the content hash at end-of-stream, then `promote` it (or `delete` the staging object when the hash is already present). `Store.list` SHALL delegate to `BlobBackend.list`.

#### Scenario: New backend implements only key/value methods
- **WHEN** a new storage target is added as a `BlobBackend`
- **THEN** it implements only the opaque key/value methods (`stage`/`promote`/`get`/`exists`/`delete`/`list`/`mtime`) and gains CAS semantics via `CasStore`

#### Scenario: Hashing happens in CasStore, not the backend
- **WHEN** `CasStore.put` stores a blob
- **THEN** the backend streams the bytes to staging with no knowledge of the hash, and `CasStore` computes the content hash and drives `promote` to the hash-named key

#### Scenario: Backend enumerates its keys
- **WHEN** `BlobBackend.list` is called
- **THEN** it yields every stored key, which `CasStore.list` surfaces as content hashes

### Requirement: Verify on download
`CasStore` SHALL verify integrity for whole-blob reads and SHALL NOT claim verification for partial reads. When a blob is materialized whole (`get_to`, and the cache back-fill that `CachingStore` performs), `CasStore` SHALL hash the bytes as they are copied and, at end-of-stream, reject the read with a typed integrity error if the bytes do not hash to the requested key, leaving no verified copy behind. Random-access `open(hash)` MAY return the backend's native seekable handle without per-read verification, because a partial read cannot be checked against a whole-blob content hash; integrity for such reads relies on the backend's own checksums or on materializing locally first (see `CachingStore`).

#### Scenario: Corrupted bytes rejected on whole-blob fetch
- **WHEN** bytes fetched via `get_to` (or a `CachingStore` cache back-fill) do not hash to the requested key
- **THEN** the read raises a typed integrity error and no verified/cached copy is retained

#### Scenario: Random-access open is served unverified
- **WHEN** a blob is opened for random access via `open(hash)` against a remote backend
- **THEN** a seekable handle is returned that serves range reads without hashing the whole blob, so a partial read does not raise an integrity error

### Requirement: Concurrency and atomic cache writes
The `Store` and `BlobBackend` ports SHALL be safe to call concurrently from multiple threads. Writing SHALL be atomic via `stage` then `promote`: `stage` SHALL stream bytes to a reserved staging key, and `promote` SHALL make them appear at the final key in one step (a rename), so a blob appears at its key only once fully written. A crashed or failed write SHALL leave at most an orphaned staging object (reclaimable by `sweep_temp`) and MUST NOT leave a partial blob at its content hash. `promote` SHALL be idempotent: if the final key already exists, the staged object SHALL be discarded rather than overwritten. Because `CasStore.put` and the `CachingStore` cache back-fill both write through the backend, cache entries inherit this atomicity — `CachingStore` uses a per-hash lock so concurrent fetches of the same blob neither double-download nor expose a partially written cache file.

#### Scenario: Concurrent fetch of the same blob
- **WHEN** two threads fetch the same uncached hash simultaneously
- **THEN** the blob is downloaded at most once and both threads observe a fully written cache file

#### Scenario: Failed write leaves no partial blob
- **WHEN** a `stage`/`promote` sequence fails or crashes partway through writing the bytes
- **THEN** no object exists at the target content hash, and `has(hash)` reports the blob absent

#### Scenario: Concurrent identical-content writers converge
- **WHEN** two writers stage identical content to different staging keys and both promote to the same content hash
- **THEN** the final blob is present exactly once and both writers succeed, the loser of the race discarding its staging object

#### Scenario: Blob appears only when complete
- **WHEN** a concurrent reader checks a hash while another thread is writing it
- **THEN** it observes either no blob or the fully written blob, never a truncated one
