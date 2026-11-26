# Spec Delta

## MODIFIED Requirements

### Requirement: Compare-and-swap pointer advance via put-if-absent
`set_pointer(coord, name, version, *, expected, base=None, actor, reason)` SHALL advance a pointer by appending the next zero-padded event object to that pointer's append-only stream with put-if-absent. The appended event object SHALL carry the optional `base` as a JSON key, written only for staging advances; the key is schemaless, so an event object without it reads as `base=None`. It SHALL raise `Conflict` when the pointer's current value does not equal `expected`, `NotFound` when `version`'s manifest does not exist, and SHALL treat a put-if-absent failure (surfaced by s3fs as `FileExistsError`) as a lost race — re-reading the stream tail and retrying. The sequence SHALL be gap-free: an event at sequence N+1 is written only after N was observed as the tail.

#### Scenario: Exactly one of two racing writers wins
- **WHEN** two writers advance the same pointer from the same observed tail and both attempt the next sequence number
- **THEN** put-if-absent grants exactly one; the loser observes `FileExistsError`, re-reads the tail, and — its `expected` now stale — raises `Conflict` without overwriting the winner

#### Scenario: A staging advance's event carries the base
- **WHEN** `set_pointer` is called with a non-`None` `base` (a staging advance)
- **THEN** the appended event object includes the `base` key; an advance written without `base`, and any older event object lacking the key, reads as `base=None`

#### Scenario: Advancing an uncommitted version is refused
- **WHEN** `set_pointer` targets a version whose manifest object does not exist
- **THEN** it raises `NotFound` and appends no event

## ADDED Requirements

### Requirement: Base recovery from a single pointer's event stream
`S3Registry` SHALL implement `pointer_base(coord, name)` by reading only the named pointer's own event stream and returning the `base` on its newest advance event, or `None` when none was recorded. It SHALL NOT list other pointers or read the whole coordinate's history. It SHALL read past a later deletion-tombstone event to that newest advance.

#### Scenario: Recover a staged base from the pointer's own stream
- **WHEN** a staging pointer's newest advance event recorded `base=b` and `pointer_base(coord, name)` is called
- **THEN** it returns `b` after reading only that pointer's event stream, listing no other pointer

#### Scenario: Recover the base past a deletion tombstone
- **WHEN** a staging pointer recorded `base=b` and a later deletion tombstone event was appended above that advance, and `pointer_base(coord, name)` is called
- **THEN** it returns `b`, reading past the tombstone, even though resolving the pointer would return `NotFound`

### Requirement: Behavioral equivalence for the base field and lookup
`S3Registry`'s `base` recording and `pointer_base` SHALL be covered by the differential test against the in-memory reference backend: over the same sequence of staging advances, deletions, and re-stages, `pointer_base` and the `base` exposed through `list_pointer_history` SHALL agree with the reference backend (modulo the append-only history semantics already specified).

#### Scenario: Differential agreement on base recovery
- **WHEN** the same sequence of staging advances, pointer deletions, and re-stages is applied to the reference backend and `S3Registry`
- **THEN** `pointer_base` and the `base` field in `list_pointer_history` agree at every step
