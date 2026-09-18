## MODIFIED Requirements

### Requirement: Append-only per-coordinate pointer-move history
The manifest plane SHALL maintain an append-only pointer-move history, partitioned per coordinate, recording one row per successful pointer move with at least `(name, env, pointer, from_version, to_version, actor, reason, at)`. A pointer deletion SHALL be recorded as a move with `to_version` unset (null), preserving the pointer's prior value in `from_version`. The history SHALL be append-only and SHALL record moves in the order they occur. A failed compare-and-swap SHALL append no row.

#### Scenario: A pointer move appends a history row
- **WHEN** a coordinate's `stable` alias is advanced from `v1` to `v2`
- **THEN** a history row is appended for `stable` with `from_version=v1`, `to_version=v2`, and the move's actor, reason, and time, leaving prior rows unchanged

#### Scenario: A deletion appends a null-target row
- **WHEN** a coordinate's `staging-abc` pointer holding `v3` is deleted
- **THEN** a history row is appended for `staging-abc` with `from_version=v3` and `to_version` null, leaving prior rows unchanged

#### Scenario: Failed move appends nothing
- **WHEN** a pointer move is rejected by compare-and-swap
- **THEN** no history row is appended
