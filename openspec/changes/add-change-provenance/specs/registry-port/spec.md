## MODIFIED Requirements

### Requirement: Compare-and-swap pointer update
The `Registry` SHALL expose `set_pointer(coord, name, version, *, expected, actor="unknown", reason=None)` that atomically advances a mutable pointer only if its current value equals `expected`. On mismatch the call SHALL raise a typed conflict error, leave the pointer unchanged, and write no provenance. On success it SHALL, within the same transaction: (a) stamp `actor`/`reason` on the tip (commit-log) event it appends for the move, and (b) append one pointer-move history record capturing the pointer name, the prior version (`expected`, or none when the pointer did not exist), the new `version`, `actor`, `reason`, and the time. `set_pointer` is the sole write point for change provenance — `commit` records none, since the manifest is content-addressed and shared.

#### Scenario: Successful CAS
- **WHEN** `set_pointer` is called with `expected` equal to the pointer's current version
- **THEN** the pointer advances atomically to the new version, the appended log event carries the `actor`/`reason`, and one pointer-move history record is appended

#### Scenario: Conflicting CAS is rejected
- **WHEN** two publishers call `set_pointer` with the same `expected` and one has already advanced the pointer
- **THEN** the second call raises a conflict error, does not change the pointer, and writes neither a log event nor a history record

## ADDED Requirements

### Requirement: Commit-log entries carry provenance
`LogEntry` SHALL carry `actor` and `reason` in addition to `version`, `seq`, and `created_at`. Because a coordinate's commit-log rows are the tip events appended by `set_pointer`, `list_log(coord)` exposes who made each version a tip of the coordinate (published or promoted it) and why. A manifest that was committed but never made a tip has no such event and therefore no attribution.

#### Scenario: Log exposes actor and reason
- **WHEN** `list_log` is called for a coordinate whose versions were published by different actors
- **THEN** each returned entry exposes that tip event's `actor` and `reason`

### Requirement: Pointer-move history read
The `Registry` SHALL expose `list_pointer_history(coord) -> Sequence[PointerMove]` returning the coordinate's pointer moves in append order (oldest first). Each `PointerMove` SHALL carry the pointer `name`, `from_version` (or `None`), `to_version`, `actor`, `reason`, and `at` timestamp.

#### Scenario: Enumerate a coordinate's pointer moves
- **WHEN** `list_pointer_history` is called for a coordinate whose `head` and `stable` pointers have each moved
- **THEN** it returns every move in order, each exposing its pointer name, from/to versions, actor, reason, and time
