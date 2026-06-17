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
The `Registry` SHALL expose `list_pointers(coord) -> Mapping[name, version]` and `list_versions(coord) -> Sequence[version]` for reproducibility and operational tooling.

#### Scenario: Enumerate pointers
- **WHEN** `list_pointers` is called for a coordinate with `head` and `production` pointers
- **THEN** it returns a mapping including both pointer names and their current version ids

### Requirement: Immutable manifest commit
The `Registry` SHALL expose `commit(coord, entries, metadata) -> Version` that records a new immutable manifest version. Committing SHALL NOT by itself advance any mutable pointer. Whether committing identical entries returns the same version (content-idempotency) depends on the version-id format and is left to a follow-up decision.

#### Scenario: Commit does not move a pointer
- **WHEN** `commit` records a new version
- **THEN** existing pointers continue to resolve to their prior versions until explicitly advanced

### Requirement: Compare-and-swap pointer update
The `Registry` SHALL expose `set_pointer(coord, name, version, *, expected)` that atomically advances a mutable pointer only if its current value equals `expected`. On mismatch the call SHALL raise a typed conflict error and leave the pointer unchanged.

#### Scenario: Successful CAS
- **WHEN** `set_pointer` is called with `expected` equal to the pointer's current version
- **THEN** the pointer advances atomically to the new version

#### Scenario: Conflicting CAS is rejected
- **WHEN** two publishers call `set_pointer` with the same `expected` and one has already advanced the pointer
- **THEN** the second call raises a conflict error and does not change the pointer

