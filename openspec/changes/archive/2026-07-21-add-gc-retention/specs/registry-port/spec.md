## ADDED Requirements

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
