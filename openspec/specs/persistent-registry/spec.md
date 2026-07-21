# persistent-registry Specification

## Purpose
Define a durable, transactional `Registry` implementation — persisting the manifest plane
(manifests, pointers, commit log) across process restarts — that is observationally
equivalent to the in-memory reference, and the `open_local` convenience that assembles a
persistent single-node repository in one directory. SQLite realizes it now; a shared
backend (Postgres) fits under the same capability later.

## Requirements
### Requirement: Durable transactional registry
The system SHALL provide `SqliteRegistry`, a `Registry` implementation backed by a
SQLite database that persists the manifest plane — manifests and their entries,
pointers, and the commit log — across process restarts. It SHALL implement the full
`registry-port` contract unchanged. Mutations SHALL be transactional: a mutation either
takes full effect or none of it, and a compare-and-swap `set_pointer` SHALL be atomic
under concurrent writers.

#### Scenario: State survives a restart
- **WHEN** artifacts are published through a file-backed `SqliteRegistry`, the process
  ends, and a new `SqliteRegistry` is opened on the same database file
- **THEN** the recovered registry resolves the same pointers, versions, and manifests

#### Scenario: Compare-and-swap is atomic
- **WHEN** a `set_pointer` runs with an `expected` value that no longer matches the
  current pointer (a concurrent writer advanced it)
- **THEN** it raises a conflict and leaves the pointer unchanged, with no partial write

### Requirement: Behavioral equivalence to the reference backend
`SqliteRegistry` SHALL be observationally equivalent to the in-memory reference
`MemoryRegistry`: for any sequence of registry operations, both SHALL return the same
results and raise the same typed errors (`Conflict`, `NotFound`) at the same steps. This
equivalence SHALL be verified by a property-based differential test that drives identical
random operation sequences against both backends.

#### Scenario: Differential sequences agree
- **WHEN** the same random sequence of `commit`/`set_pointer`/`drop_version`/lease/read
  operations is applied to a `MemoryRegistry` and a `SqliteRegistry`
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

### Requirement: Lease lifetime is process-scoped
Leases coordinate in-flight publishes with GC within a running process and SHALL NOT be
required to persist across process restart. On opening a database, `SqliteRegistry` SHALL
start with no live leases, so a crashed process never leaves permanently-held leases in
the database.

#### Scenario: Leases do not persist across restart
- **WHEN** a process acquires a lease and exits without releasing it, and the database is
  reopened
- **THEN** the reopened registry reports no active leases, so GC is not blocked by a dead
  process's lease
