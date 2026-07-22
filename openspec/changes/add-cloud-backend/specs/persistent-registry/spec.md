## MODIFIED Requirements

### Requirement: Durable transactional registry
The system SHALL provide durable, SQL-backed `Registry` implementations that persist the
manifest plane — manifests and their entries, pointers, and the commit log — across
process restarts: `SqliteRegistry` for single-node use and `PostgresRegistry` for a
shared, multi-writer database. Both SHALL implement the full `registry-port` contract
unchanged and share one query implementation, differing only in database dialect.
Mutations SHALL be transactional: a mutation either takes full effect or none of it, and
a compare-and-swap `set_pointer` SHALL be atomic under concurrent writers.

#### Scenario: State survives a restart
- **WHEN** artifacts are published through a file-backed `SqliteRegistry` (or a
  `PostgresRegistry`), the process ends, and a new registry is opened on the same
  database
- **THEN** the recovered registry resolves the same pointers, versions, and manifests

#### Scenario: Compare-and-swap is atomic
- **WHEN** a `set_pointer` runs with an `expected` value that no longer matches the
  current pointer (a concurrent writer advanced it)
- **THEN** it raises a conflict and leaves the pointer unchanged, with no partial write

### Requirement: Behavioral equivalence to the reference backend
Each SQL `Registry` (`SqliteRegistry` and `PostgresRegistry`) SHALL be observationally
equivalent to the in-memory reference `MemoryRegistry`: for any sequence of registry
operations, both SHALL return the same results and raise the same typed errors
(`Conflict`, `NotFound`) at the same steps. This equivalence SHALL be verified by a
property-based differential test that drives identical random operation sequences against
the SQL backend and the reference.

#### Scenario: Differential sequences agree
- **WHEN** the same random sequence of `commit`/`set_pointer`/`drop_version`/lease/read
  operations is applied to a `MemoryRegistry` and a SQL registry
- **THEN** every operation returns equal results and raises equivalent errors on both

## ADDED Requirements

### Requirement: Multi-writer compare-and-swap
`set_pointer` SHALL perform its compare-and-swap as a single conditional write rather than
a read-then-write: it advances the pointer only if the stored value still equals
`expected` (with `expected = None` meaning the pointer must not yet exist), detecting a
losing race by the write affecting no row. This SHALL hold under concurrent writers
against a shared registry, so that of two publishers advancing the same pointer from the
same starting version exactly one succeeds and the other observes a conflict.

#### Scenario: Concurrent publishers, one wins
- **WHEN** two writers call `set_pointer` with the same `expected` against a shared
  registry and one commits first
- **THEN** the first succeeds and advances the pointer, and the second raises a conflict
  without overwriting the winner

### Requirement: Cloud repository convenience
The system SHALL provide `open_cloud(registry_dsn, blob_url, *, cache_dir=None) ->
Repository` that assembles a shared repository from a `PostgresRegistry` on
`registry_dsn` and a content-addressed `Store` over the fsspec filesystem named by
`blob_url` (e.g. an `s3://` URL). When `cache_dir` is given, blob reads SHALL be served
through a `CachingStore` backed by a local on-disk cache over the remote store.

#### Scenario: Cloud repository publishes and resolves
- **WHEN** a repository assembled by `open_cloud` publishes an artifact and later resolves
  it
- **THEN** the manifest is served from the shared registry and the blobs from the
  object-store blob plane, with the local cache satisfying repeat reads when configured
