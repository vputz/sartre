# version-log Specification

## Purpose
TBD - created by archiving change define-version-id. Update Purpose after archive.
## Requirements
### Requirement: Append-only per-coordinate commit log
The manifest plane SHALL maintain an append-only commit log, partitioned per
coordinate, recording one row per tip event with at least `(name, env, seq,
version, created_at, pointer, metadata)`. `seq` SHALL be a monotonically
increasing per-coordinate sequence providing the authoritative order; the log
SHALL be append-only — existing rows are never mutated.

#### Scenario: Publishing appends a log row
- **WHEN** a new version becomes a coordinate's pointer tip
- **THEN** a new log row is appended with the next `seq` for that coordinate and
  the version's hash, leaving prior rows unchanged

### Requirement: Identity separated from ordering
A `Version` (a content hash) SHALL carry no intrinsic order; ordering SHALL come
only from a row's position (`seq`) in the commit log. The mutable pointer table
SHALL hold only the current tip per pointer so that `head` remains a single-row
read; the full ordered history SHALL live in the commit log.

#### Scenario: head reads the pointer, not the log
- **WHEN** `head` is called
- **THEN** it reads the current tip from the pointer table without scanning the
  log

#### Scenario: Order derives from seq, not from the version value
- **WHEN** two versions are compared for recency
- **THEN** the later one is the one with the greater `seq` in the log, not
  determined from the version hashes themselves

### Requirement: As-of time-travel lookup
The commit log SHALL support resolving, for a coordinate and pointer, the version
that was the tip at or before a given timestamp, returning a `Version` the caller
can `Pin`. `created_at` SHALL be used for this time query only and SHALL NOT be
treated as an ordering authority. This is an operational query over the log, not
a core addressing axis; `resolve` continues to accept only `Head`/`Alias`/`Pin`.

#### Scenario: Resolve the tip as of a past time
- **WHEN** an as-of query is made for a pointer with a timestamp falling between
  two logged tip events
- **THEN** it returns the version of the earlier event (the tip at that time),
  which the caller may then resolve via `Pin`

### Requirement: Promotion is a repoint
Promoting a version from one coordinate to another (e.g. `dev` → `release`) SHALL
append a log row under the target coordinate referencing the existing version
hash and advance the target pointer to it. The manifest and its blobs SHALL be
shared by identity, never copied.

#### Scenario: Cross-env promotion shares content
- **WHEN** a version published under `models@dev` is promoted to `models@release`
- **THEN** `models@release` gains a log row and its pointer moves to the same
  version hash, with no manifest or blob bytes copied

