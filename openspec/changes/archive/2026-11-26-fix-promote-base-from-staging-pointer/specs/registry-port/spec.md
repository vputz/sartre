# Spec Delta

## MODIFIED Requirements

### Requirement: Compare-and-swap pointer update
The `Registry` SHALL expose `set_pointer(coord, name, version, *, expected, base=None, actor="unknown", reason=None)` that atomically advances a mutable pointer only if its current value equals `expected`. On mismatch the call SHALL raise a typed conflict error, leave the pointer unchanged, and write no provenance. On success it SHALL, within the same transaction: (a) stamp `actor`/`reason` on the tip (commit-log) event it appends for the move, and (b) append one pointer-move history record capturing the pointer name, the prior version (`expected`, or none when the pointer did not exist), the new `version`, the optional `base`, `actor`, `reason`, and the time. The `base` records the version a staging advance was derived from; it SHALL be written only for staging advances and SHALL be `None` for every other move. `set_pointer` is the sole write point for change provenance — `commit` records none, since the manifest is content-addressed and shared.

#### Scenario: Successful CAS
- **WHEN** `set_pointer` is called with `expected` equal to the pointer's current version
- **THEN** the pointer advances atomically to the new version, the appended log event carries the `actor`/`reason`, and one pointer-move history record is appended

#### Scenario: Conflicting CAS is rejected
- **WHEN** two publishers call `set_pointer` with the same `expected` and one has already advanced the pointer
- **THEN** the second call raises a conflict error, does not change the pointer, and writes neither a log event nor a history record

#### Scenario: A staging advance records its base
- **WHEN** `set_pointer` is called with a non-`None` `base` (a staging advance)
- **THEN** the appended pointer-move record carries that `base`; a move called without `base` records `base=None`

### Requirement: Pointer-move history read
The `Registry` SHALL expose `list_pointer_history(coord) -> Sequence[PointerMove]` returning the coordinate's pointer moves in append order (oldest first). Each `PointerMove` SHALL carry the pointer `name`, `from_version` (or `None`), `to_version` (`None` when the move is a deletion), `base` (the version a staging advance was derived from, `None` for every non-staging move), `actor`, `reason`, and `at` timestamp.

#### Scenario: Enumerate a coordinate's pointer moves
- **WHEN** `list_pointer_history` is called for a coordinate whose `head` and `stable` pointers have each moved
- **THEN** it returns every move in order, each exposing its pointer name, from/to versions, base, actor, reason, and time

#### Scenario: A deletion is recorded with no target version
- **WHEN** a pointer is deleted via `delete_pointer`
- **THEN** the history gains a move for that pointer with `from_version` set to its prior value, `to_version=None`, and `base=None`, retained in append order like every other move

## ADDED Requirements

### Requirement: Single-pointer base lookup
The `Registry` SHALL expose `pointer_base(coord, name) -> Version | None` returning the derive base recorded on the named pointer's newest advance move, or `None` when it has no advance that recorded a base. It SHALL read only that single pointer's own record and SHALL NOT scan the whole coordinate's pointer-move history. This is the base source `promote` compare-and-swaps a staged version against.

#### Scenario: Return the base of the newest advance
- **WHEN** a staging pointer was advanced recording `base=b`, and `pointer_base(coord, name)` is called
- **THEN** it returns `b`, reading only that pointer's record

#### Scenario: Return the base past a concurrent deletion tombstone
- **WHEN** a staging pointer recorded `base=b` and was then deleted (a tombstone above its advance), and `pointer_base(coord, name)` is called
- **THEN** it still returns `b`, reading past the tombstone to the newest advance — unlike pointer resolution, which stops at the tombstone

#### Scenario: Return None when no base was recorded
- **WHEN** `pointer_base(coord, name)` is called for a pointer whose moves recorded no base (a plain repoint, or a pointer that never existed)
- **THEN** it returns `None`
