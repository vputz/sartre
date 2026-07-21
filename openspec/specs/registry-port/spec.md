# registry-port Specification

## Purpose
TBD - created by archiving change add-core-ports. Update Purpose after archive.
## Requirements
### Requirement: Cheap pointer read
The `Registry` SHALL expose `head(coord, ref=Head())` returning the version id a ref currently resolves to. This operation SHALL be a single cheap pointer read and MUST NOT fetch or scan a manifest.

#### Scenario: Polling reads one pointer
- **WHEN** `head` is called to detect whether the tip has moved
- **THEN** it returns a version id from a single pointer lookup without enumerating manifest entries

### Requirement: Atomic resolve to a manifest
The `Registry` SHALL expose `resolve(coord, ref=Head()) -> Snapshot`. Resolution SHALL be atomic: it MUST NOT return a partially published manifest. If the ref cannot be resolved, the call SHALL raise rather than return an empty or partial result.

#### Scenario: Never observe a half-published version
- **WHEN** a publish is in progress and `resolve` runs concurrently
- **THEN** `resolve` returns either the fully previous version or the fully new version, never a mixture

#### Scenario: Unresolvable ref raises
- **WHEN** `resolve` is called for a coordinate or ref that does not exist
- **THEN** the call raises a typed not-found error

### Requirement: Pointer and version enumeration
The `Registry` SHALL expose `list_pointers(coord) -> Mapping[name, version]` and
`list_versions(coord) -> Sequence[version]` for reproducibility and operational
tooling. `list_versions` SHALL return the coordinate's versions in commit-log
order (oldest to newest by the log's authoritative sequence).

#### Scenario: Enumerate pointers
- **WHEN** `list_pointers` is called for a coordinate with `head` and
  `production` pointers
- **THEN** it returns a mapping including both pointer names and their current
  version ids

#### Scenario: Versions enumerated in commit order
- **WHEN** `list_versions` is called for a coordinate with three logged versions
- **THEN** they are returned in the order they were committed, oldest first

### Requirement: Immutable manifest commit
The `Registry` SHALL expose `commit(coord, entries, metadata) -> Version` that
records a new immutable manifest version. Committing SHALL NOT by itself advance
any mutable pointer. Committing SHALL be **content-idempotent**: committing the
same set of `(path, content_hash)` entries SHALL return the same `Version` and
SHALL NOT create a duplicate manifest, regardless of `metadata`, entry order, or
coordinate.

#### Scenario: Commit does not move a pointer
- **WHEN** `commit` records a new version
- **THEN** existing pointers continue to resolve to their prior versions until
  explicitly advanced

#### Scenario: Re-committing identical entries is idempotent
- **WHEN** `commit` is called twice with the same `(path, content_hash)` entries
- **THEN** both calls return the same `Version` and no duplicate manifest is stored

### Requirement: Compare-and-swap pointer update
The `Registry` SHALL expose `set_pointer(coord, name, version, *, expected)` that atomically advances a mutable pointer only if its current value equals `expected`. On mismatch the call SHALL raise a typed conflict error and leave the pointer unchanged.

#### Scenario: Successful CAS
- **WHEN** `set_pointer` is called with `expected` equal to the pointer's current version
- **THEN** the pointer advances atomically to the new version

#### Scenario: Conflicting CAS is rejected
- **WHEN** two publishers call `set_pointer` with the same `expected` and one has already advanced the pointer
- **THEN** the second call raises a conflict error and does not change the pointer

### Requirement: Repository-wide enumeration for GC
The `Registry` SHALL expose `list_coordinates() -> Sequence[Coordinate]` enumerating
every coordinate it holds, and `list_log(coord) -> Sequence[LogEntry]` returning that
coordinate's commit log in authoritative sequence order, each entry carrying at least
`(version, seq, created_at)`. These enable a garbage collector to compute roots across
the whole repository and to apply `keep_last_n` (by order) and `keep_within(age)` (by
`created_at`) retention.

#### Scenario: Enumerate all coordinates
- **WHEN** `list_coordinates` is called on a registry holding two coordinates
- **THEN** it returns both, so GC can union roots across them

#### Scenario: Log carries commit time and order
- **WHEN** `list_log` is called for a coordinate with three commits
- **THEN** it returns entries in sequence order, each exposing the version and its `created_at`

### Requirement: Drop an out-of-retention manifest
The `Registry` SHALL expose `drop_version(version)` that removes a manifest record no
longer wanted by retention. Because manifests are global and content-addressed (a
version may be shared across coordinates by promotion), the operation is repository-wide:
it SHALL refuse (raise a typed conflict) if the version is the current target of any
pointer in any coordinate, prune the version from every coordinate's commit log, and
remove the manifest record. Dropping SHALL be idempotent: dropping an absent version is
a no-op.

#### Scenario: Refuse to drop a pointer target
- **WHEN** `drop_version` is called for a version any pointer currently resolves to
- **THEN** it raises a conflict and leaves the manifest in place

#### Scenario: Drop is idempotent
- **WHEN** `drop_version` is called for a version already absent
- **THEN** it is a no-op

### Requirement: Blob lease surface
The `Registry` SHALL expose a lease surface coordinating in-flight publishes with GC:
`acquire_lease(version, hashes) -> LeaseId`, `release_lease(lease_id)`,
`active_leased_hashes() -> Set[Hash]` (the union of hashes under all live leases), and
`active_leased_versions() -> Set[Version]` (the versions under all live leases). GC
SHALL treat leased hashes as protected blobs and leased versions as retained manifests,
so that both an in-flight publish's uploaded blobs (before its manifest is committed)
and its committed manifest (before its pointer advances) are safe from collection. A
lease not released (e.g. a crashed publisher) SHALL keep its version and hashes
protected.

#### Scenario: Leased hashes and version are reported as roots
- **WHEN** a lease is acquired over a version and its hashes
- **THEN** `active_leased_hashes` includes those hashes and `active_leased_versions`
  includes that version, until the lease is released

#### Scenario: Release removes protection
- **WHEN** a lease is released
- **THEN** its hashes and version no longer appear unless held by another live lease

