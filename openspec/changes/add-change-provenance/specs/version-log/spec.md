## MODIFIED Requirements

### Requirement: Append-only per-coordinate commit log
The manifest plane SHALL maintain an append-only commit log, partitioned per
coordinate, recording one row per tip event with at least `(name, env, seq,
version, created_at, pointer, actor, reason, metadata)`. `seq` SHALL be a
monotonically increasing per-coordinate sequence providing the authoritative
order; the log SHALL be append-only — existing rows are never mutated. `actor`
and `reason` record who committed the version into the coordinate and why;
`metadata` remains reserved for domain payload and SHALL NOT carry the commit
reason.

#### Scenario: Publishing appends a log row
- **WHEN** a new version becomes a coordinate's pointer tip
- **THEN** a new log row is appended with the next `seq` for that coordinate, the
  version's hash, and the commit's `actor` and `reason`, leaving prior rows unchanged

## ADDED Requirements

### Requirement: Append-only per-coordinate pointer-move history
The manifest plane SHALL maintain an append-only pointer-move history, partitioned per coordinate, recording one row per successful pointer move with at least `(name, env, pointer, from_version, to_version, actor, reason, at)`. The history SHALL be append-only and SHALL record moves in the order they occur. A failed compare-and-swap SHALL append no row.

#### Scenario: A pointer move appends a history row
- **WHEN** a coordinate's `stable` alias is advanced from `v1` to `v2`
- **THEN** a history row is appended for `stable` with `from_version=v1`, `to_version=v2`, and the move's actor, reason, and time, leaving prior rows unchanged

#### Scenario: Failed move appends nothing
- **WHEN** a pointer move is rejected by compare-and-swap
- **THEN** no history row is appended
