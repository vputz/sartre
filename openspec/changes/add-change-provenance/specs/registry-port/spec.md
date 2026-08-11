## MODIFIED Requirements

### Requirement: Immutable manifest commit
The `Registry` SHALL expose `commit(coord, entries, metadata, *, actor, reason) -> Version` that
records a new immutable manifest version. Committing SHALL NOT by itself advance
any mutable pointer. Committing SHALL be **content-idempotent**: committing the
same set of `(path, content_hash)` entries SHALL return the same `Version` and
SHALL NOT create a duplicate manifest, regardless of `metadata`, `actor`, `reason`,
entry order, or coordinate. The `actor` and `reason` SHALL be recorded on the
coordinate's commit-log event for that version, not on the shared manifest, so
that committing identical content into different coordinates records distinct
provenance per event.

#### Scenario: Commit does not move a pointer
- **WHEN** `commit` records a new version
- **THEN** existing pointers continue to resolve to their prior versions until
  explicitly advanced

#### Scenario: Re-committing identical entries is idempotent
- **WHEN** `commit` is called twice with the same `(path, content_hash)` entries
- **THEN** both calls return the same `Version` and no duplicate manifest is stored

#### Scenario: Commit records event provenance
- **WHEN** `commit` is called with `actor` and `reason`
- **THEN** the coordinate's commit-log event for the returned version carries that `actor` and `reason`

### Requirement: Compare-and-swap pointer update
The `Registry` SHALL expose `set_pointer(coord, name, version, *, expected, actor, reason)` that atomically advances a mutable pointer only if its current value equals `expected`. On mismatch the call SHALL raise a typed conflict error, leave the pointer unchanged, and append no history record. On success it SHALL, within the same transaction, append one pointer-move history record capturing the pointer name, the prior version (`expected`, or none when the pointer did not exist), the new `version`, `actor`, `reason`, and the time.

#### Scenario: Successful CAS
- **WHEN** `set_pointer` is called with `expected` equal to the pointer's current version
- **THEN** the pointer advances atomically to the new version and one history record is appended

#### Scenario: Conflicting CAS is rejected
- **WHEN** two publishers call `set_pointer` with the same `expected` and one has already advanced the pointer
- **THEN** the second call raises a conflict error, does not change the pointer, and appends no history record

## ADDED Requirements

### Requirement: Commit-log entries carry provenance
`LogEntry` SHALL carry `actor` and `reason` in addition to `version`, `seq`, and `created_at`, so that `list_log(coord)` exposes who committed each version into the coordinate and why.

#### Scenario: Log exposes actor and reason
- **WHEN** `list_log` is called for a coordinate with commits by different actors
- **THEN** each returned entry exposes that commit's `actor` and `reason`

### Requirement: Pointer-move history read
The `Registry` SHALL expose `list_pointer_history(coord) -> Sequence[PointerMove]` returning the coordinate's pointer moves in append order (oldest first). Each `PointerMove` SHALL carry the pointer `name`, `from_version` (or `None`), `to_version`, `actor`, `reason`, and `at` timestamp.

#### Scenario: Enumerate a coordinate's pointer moves
- **WHEN** `list_pointer_history` is called for a coordinate whose `head` and `stable` pointers have each moved
- **THEN** it returns every move in order, each exposing its pointer name, from/to versions, actor, reason, and time
