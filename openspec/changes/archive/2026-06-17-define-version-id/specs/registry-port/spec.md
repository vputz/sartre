## MODIFIED Requirements

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
