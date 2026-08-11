# persistent-registry Specification

## Purpose
Define a durable, transactional `Registry` implementation — persisting the manifest plane
(manifests, pointers, commit log) across process restarts — that is observationally
equivalent to the in-memory reference, and the `open_local` convenience that assembles a
persistent single-node repository in one directory. SQLite realizes it now; a shared
backend (Postgres) fits under the same capability later.
## Requirements
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

### Requirement: Persistent single-node repository convenience
The system SHALL provide `open_local(path) -> Repository` that assembles a persistent
single-node repository under one directory: a `SqliteRegistry` for the manifest plane and
a content-addressed `Store` over the local filesystem for the blob plane. Reopening the
same path SHALL recover the repository's published state.

#### Scenario: Local repository round-trips across reopen
- **WHEN** an artifact is published via `open_local(path)`, then a fresh `open_local(path)`
  is created on the same directory
- **THEN** the artifact resolves and its blobs are readable from the reopened repository

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

### Requirement: Durable, registry-clock TTL leases
Leases SHALL be stored durably in the registry (a `leases` table in the SQL backends; an
equivalent expiry-aware structure in the reference backend) so that every process sharing
the registry observes the same set of live leases. Each lease SHALL carry an `expires_at`
computed from the **registry's own clock** at `acquire`/`renew` time; the active-root
queries SHALL filter on `expires_at > now()` evaluated by the registry, never by a client
clock. A lease SHALL be reclaimed by expiry: once its ttl elapses it ceases to protect
its version and hashes, so a crashed publisher cannot pin storage indefinitely. Durable
leases SHALL NOT change observational equivalence for unexpired leases — a sequence of
operations that never lets a lease expire SHALL behave identically to the reference
backend.

#### Scenario: A live lease is visible across registry instances
- **WHEN** one registry instance acquires a lease over a shared database, and a second
  instance over the same database queries active leases before the ttl elapses
- **THEN** the second instance reports the lease's hashes and version as protected

#### Scenario: A lapsed lease stops protecting across instances
- **WHEN** the lease's ttl elapses against the registry clock with no renewal
- **THEN** every instance's `active_leased_hashes`/`active_leased_versions` omits it, so
  GC on any instance may reclaim the blobs

#### Scenario: Expiry uses the registry clock, not the caller's
- **WHEN** leases are acquired and queried
- **THEN** `expires_at` is computed and compared using the registry's clock, so writers
  and GC hosts with skewed local clocks agree on which leases are live

### Requirement: Durable commit provenance and pointer-move history
The persistent registry SHALL store commit provenance and pointer-move history durably. The commit-log table SHALL include `actor` and `reason` columns written in the commit transaction. A `pointer_moves` table SHALL record one row per successful pointer move — `(coordinate, pointer, from_version, to_version, actor, reason, at)` — inserted within the same transaction as the pointer compare-and-swap, using the backend's timestamp/`NOW` dialect hooks for `at`. Both SHALL survive process restart and SHALL be readable via `list_log` and `list_pointer_history`.

#### Scenario: Provenance survives a restart
- **WHEN** a version is committed and a pointer moved with an actor and reason, then the registry is reopened
- **THEN** `list_log` reports the commit's actor and reason and `list_pointer_history` reports the move's from/to versions, actor, reason, and time

#### Scenario: Move history is written under the CAS transaction
- **WHEN** a pointer move succeeds
- **THEN** exactly one `pointer_moves` row is committed atomically with the pointer advance; a rejected move commits no row

