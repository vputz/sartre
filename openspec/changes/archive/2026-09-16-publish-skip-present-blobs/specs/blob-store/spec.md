## MODIFIED Requirements

### Requirement: Content-addressed Store interface
The `Store` SHALL expose a content-addressed byte interface: `has(hash)`, `open(hash) -> BinaryIO`, `get_to(hash, dest) -> Path`, `put(data, *, known_hash=None) -> Hash`, `delete(hash)`, `mtime(hash) -> float | None`, and `list() -> Iterable[Hash]` enumerating all stored content hashes. The interface SHALL be stream/path-oriented and MUST NOT require whole-blob materialization into a `bytes` value, so that multi-gigabyte blobs are supported. `put` SHALL consume its source in a single streaming pass and MUST NOT buffer the whole blob in memory: it hashes while writing and names the blob by the resulting content hash.

`put` SHALL accept an optional keyword `known_hash`. When it is provided and the store's own **durable** target already holds that hash, `put` SHALL return `known_hash` immediately without consuming or uploading `data` (wire-level dedup). Otherwise — `known_hash` is `None`, or the durable target lacks it — `put` SHALL upload as usual and name the blob by the hash computed from the actual bytes; it SHALL NOT trust `known_hash` for naming on the upload path. The "durable target" is the store the blob is read back from: `CasStore` consults `backend.exists`; a caching store SHALL consult its remote (durable) store for this skip and SHALL NOT skip on a local-cache hit.

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

#### Scenario: Known-present hash skips the upload
- **WHEN** `put(data, known_hash=h)` is called and the durable backend already holds `h`
- **THEN** no bytes are staged or uploaded and `put` returns `h`

#### Scenario: Known hash absent still uploads and is named by true bytes
- **WHEN** `put(data, known_hash=h)` is called and the durable backend does not hold `h`
- **THEN** `data` is staged and promoted, and the stored blob is named by the hash of its actual bytes, not blindly by `h`

### Requirement: Dumb BlobBackend sub-port
The system SHALL define a `BlobBackend` port operating on opaque keys — `get(key)`, `stage(data) -> str`, `promote(staging_key, final_key)`, `exists(key)`, `delete(key)`, `mtime(key) -> float | None`, and `list() -> Iterable[str]` — with no awareness of hashing or content addressing. `stage` SHALL stream `data` to a reserved staging key and return it; `promote` SHALL atomically make the staged bytes appear at `final_key`. There SHALL be exactly one write path (`stage` then `promote`); the backend SHALL NOT expose a key-first `put`. A `CasStore(backend, hasher)` SHALL implement `Store` over any `BlobBackend`, centralizing hashing and verification so backends remain dumb key/value stores: when it must upload, `CasStore.put` SHALL stage the source through a hashing pass, learn the content hash at end-of-stream, then `promote` it (or `delete` the staging object when the hash is already present); when the caller supplies a `known_hash` the backend already holds, `CasStore.put` MAY skip staging entirely and return it. `Store.list` SHALL delegate to `BlobBackend.list`.

#### Scenario: New backend implements only key/value methods
- **WHEN** a new storage target is added as a `BlobBackend`
- **THEN** it implements only the opaque key/value methods (`stage`/`promote`/`get`/`exists`/`delete`/`list`/`mtime`) and gains CAS semantics via `CasStore`

#### Scenario: Hashing happens in CasStore, not the backend
- **WHEN** `CasStore.put` stores a blob
- **THEN** the backend streams the bytes to staging with no knowledge of the hash, and `CasStore` computes the content hash and drives `promote` to the hash-named key

#### Scenario: Backend enumerates its keys
- **WHEN** `BlobBackend.list` is called
- **THEN** it yields every stored key, which `CasStore.list` surfaces as content hashes
