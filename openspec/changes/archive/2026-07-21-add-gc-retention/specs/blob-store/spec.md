## MODIFIED Requirements

### Requirement: Content-addressed Store interface
The `Store` SHALL expose a content-addressed byte interface: `has(hash)`, `open(hash) -> BinaryIO`, `get_to(hash, dest) -> Path`, `put(data) -> Hash`, `delete(hash)`, and `list() -> Iterable[Hash]` enumerating all stored content hashes. The interface SHALL be stream/path-oriented and MUST NOT require whole-blob materialization into a `bytes` value, so that multi-hundred-megabyte blobs are supported.

#### Scenario: Put returns the content hash
- **WHEN** bytes are written via `put`
- **THEN** the returned key is the self-describing content hash of those bytes

#### Scenario: Idempotent put
- **WHEN** identical bytes are `put` a second time
- **THEN** the same hash is returned and no duplicate stored copy is required

#### Scenario: Large blob materialized to a path
- **WHEN** a large blob is fetched via `get_to`
- **THEN** it is written to a local path without being buffered whole in memory

#### Scenario: Enumerate stored hashes for sweep
- **WHEN** `list` is called
- **THEN** it yields the content hash of every blob currently stored, so a garbage collector can compute the set to sweep

### Requirement: Dumb BlobBackend sub-port
The system SHALL define a `BlobBackend` port operating on opaque keys — `get(key)`, `put(key, data)`, `exists(key)`, `delete(key)`, and `list() -> Iterable[str]` — with no awareness of hashing or content addressing. A `CasStore(backend, hasher)` SHALL implement `Store` over any `BlobBackend`, centralizing hashing and verification so backends remain dumb key/value stores; `Store.list` SHALL delegate to `BlobBackend.list`.

#### Scenario: New backend implements only key/value methods
- **WHEN** a new storage target is added as a `BlobBackend`
- **THEN** it implements only the opaque key/value methods and gains CAS semantics via `CasStore`

#### Scenario: Backend enumerates its keys
- **WHEN** `BlobBackend.list` is called
- **THEN** it yields every stored key, which `CasStore.list` surfaces as content hashes
