## ADDED Requirements

### Requirement: Delete a mutable pointer through the facade
`Repository` SHALL expose `delete_pointer(coord, name, *, actor="unknown", reason=None)` that removes a mutable alias via the Registry's compare-and-swap deletion: it reads the pointer's current value and deletes only if unchanged, surfacing `Conflict` on a concurrent move. It SHALL refuse to delete `head` — a coordinate's identity, not a removable label — raising a clear error without touching the registry. Deleting an absent alias SHALL be a no-op. Deletion removes only the label; manifests and blobs remain until reclaimed by retention GC.

#### Scenario: Delete a spent staging pointer
- **WHEN** `repo.delete_pointer(coord, "staging-abc")` is called for an existing staging alias
- **THEN** the alias no longer resolves, and the version it pointed to is untouched (still resolvable by id until GC)

#### Scenario: Refuse to delete head
- **WHEN** `repo.delete_pointer(coord, "head")` is called
- **THEN** it raises an error explaining head cannot be deleted, and no registry write occurs

### Requirement: Base-CAS promotion of a staged version
`Repository` SHALL expose `promote(coord, staged, *, pointer="head", actor="unknown", reason=None)` that advances `pointer` to the version `staged` resolves to, compare-and-swapping against that version's recorded base (`derived_from`) rather than the pointer's current value — so a concurrent publish that advanced the pointer since the version was staged SHALL cause `Conflict`, never a silent lost update. When the staged version records no `derived_from` (a hand-made alias or a pre-feature stage), `promote` SHALL refuse with a clear message rather than guess, unless the caller opts into a plain last-writer move. `AsyncRepository` SHALL expose an awaitable wrapper for both `delete_pointer` and `promote`.

#### Scenario: Promote a cleanly-staged version
- **WHEN** a version was staged from base `b` (head still at `b`) and `repo.promote(coord, staged)` is called
- **THEN** head advances to the staged version (CAS against `b` succeeds)

#### Scenario: Promote conflicts when head moved during verification
- **WHEN** a version was staged from base `b`, another publish then advanced head to `c`, and `repo.promote(coord, staged)` is called
- **THEN** it raises `Conflict` and head still resolves to `c` (the concurrent publish is not clobbered)

#### Scenario: Promote refuses a version with no recorded base
- **WHEN** `repo.promote(coord, staged)` is called for a version that records no `derived_from`
- **THEN** it refuses with a message explaining the base is unknown, unless a last-writer override is requested
