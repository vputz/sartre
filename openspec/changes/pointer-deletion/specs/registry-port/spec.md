## ADDED Requirements

### Requirement: Delete a pointer via compare-and-swap
The `Registry` SHALL expose `delete_pointer(coord, name, *, expected, actor, reason)` that removes a mutable pointer only if its current value equals `expected`. On a mismatch it SHALL raise `Conflict` and leave the pointer unchanged. Deleting a pointer that does not currently exist with `expected=None` SHALL be a no-op (idempotent cleanup), not an error. A successful deletion SHALL append exactly one `PointerMove` recording the removal (see the pointer-move history requirement). The operation SHALL NOT delete or reclaim any manifest or blob — only the mutable label is removed.

#### Scenario: Delete succeeds when the expected value matches
- **WHEN** `delete_pointer(coord, "staging-abc", expected=v)` is called and `staging-abc` currently resolves to `v`
- **THEN** the pointer no longer resolves, and a pointer-move row recording the removal is appended

#### Scenario: Delete conflicts when the pointer moved
- **WHEN** `delete_pointer(coord, "staging-abc", expected=v)` is called but the pointer has since moved to `v2`
- **THEN** it raises `Conflict` and the pointer still resolves to `v2`

#### Scenario: Deleting an absent pointer is idempotent
- **WHEN** `delete_pointer(coord, "gone", expected=None)` is called and no such pointer exists
- **THEN** it returns without error and appends no history row

## MODIFIED Requirements

### Requirement: Pointer-move history read
The `Registry` SHALL expose `list_pointer_history(coord) -> Sequence[PointerMove]` returning the coordinate's pointer moves in append order (oldest first). Each `PointerMove` SHALL carry the pointer `name`, `from_version` (or `None`), `to_version` (`None` when the move is a deletion), `actor`, `reason`, and `at` timestamp.

#### Scenario: Enumerate a coordinate's pointer moves
- **WHEN** `list_pointer_history` is called for a coordinate whose `head` and `stable` pointers have each moved
- **THEN** it returns every move in order, each exposing its pointer name, from/to versions, actor, reason, and time

#### Scenario: A deletion is recorded with no target version
- **WHEN** a pointer is deleted via `delete_pointer`
- **THEN** the history gains a move for that pointer with `from_version` set to its prior value and `to_version=None`, retained in append order like every other move
