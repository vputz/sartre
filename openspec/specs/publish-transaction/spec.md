# publish-transaction Specification

## Purpose
TBD - created by archiving change add-publish-transaction. Update Purpose after archive.
## Requirements
### Requirement: Full-replacement publish
A publish SHALL supply the complete manifest for a coordinate; it replaces the
target pointer's contents rather than amending the current tip. Partial/
incremental updates SHALL be performed client-side as read-modify-publish.

#### Scenario: Publish replaces the manifest
- **WHEN** a publish provides a full set of entries for a coordinate and pointer
- **THEN** the resulting version's manifest is exactly those entries, independent
  of what the pointer previously referenced

### Requirement: Blobs-before-manifest-before-pointer ordering
A publish SHALL store all referenced blobs, THEN commit the manifest, THEN
advance the pointer — in that order. A pointer SHALL NOT be advanced to a version
whose manifest is not yet committed or whose blobs are not all stored.

#### Scenario: No dangling tip
- **WHEN** a publish is at any point before its pointer advance
- **THEN** the pointer still references the previous version, and no reader can
  resolve the new version through the pointer

#### Scenario: Ordering verified by model
- **WHEN** the pointer is advanced before the manifest is committed (wrong order)
- **THEN** the tip references an uncommitted manifest — a state the TLA+ model
  (`model/Publish.tla`, `Publish_buggy.cfg`) reaches and flags as a `PointerSafe`
  violation, while the correct order holds `PointerSafe` across all interleavings

### Requirement: Idempotent steps and crash recovery
Each publish step SHALL be idempotent: storing an already-present blob and
committing identical entries are no-ops returning the existing identity. A
publish interrupted by a crash SHALL be recoverable by re-running it from the
start, with no write-ahead recovery log. A crash before the pointer advance SHALL
leave only unreferenced blobs and/or an orphan manifest (collectable by GC) and
SHALL NOT leave any observable half-published state.

#### Scenario: Re-running a crashed publish converges
- **WHEN** a publish crashes after storing blobs but before committing, and is
  then re-run
- **THEN** the re-run reuses the stored blobs, commits the manifest, advances the
  pointer, and the final state is as if the crash had not occurred

#### Scenario: Crash leaves only collectable orphans
- **WHEN** a publish crashes after committing the manifest but before advancing
  the pointer
- **THEN** the pointer is unchanged and the orphan manifest/blobs are eligible for
  garbage collection, with no reader able to observe the uncommitted version

### Requirement: Fail-fast compare-and-swap concurrency
A publish SHALL advance the pointer with a compare-and-swap guarded by the tip it
observed at the start. On a conflicting concurrent advance, `set_pointer` SHALL
raise `Conflict` and the publish SHALL surface it without silently overwriting
the concurrent version and without automatic retry.

#### Scenario: Concurrent publishers, one wins
- **WHEN** two publishers concurrently publish to the same pointer, both having
  read the same starting tip
- **THEN** exactly one compare-and-swap succeeds and advances the pointer, and the
  other raises `Conflict`, leaving the pointer at exactly one published version

### Requirement: Atomic pointer advance and log append
Advancing the pointer and appending the commit-log row SHALL be a single atomic
commit: a reader SHALL never observe the pointer moved without the corresponding
log entry, nor a log entry whose pointer move did not take effect.

#### Scenario: Pointer and log stay consistent
- **WHEN** a pointer advance succeeds
- **THEN** the commit log has a new last entry equal to the new tip, and the two
  are never observed out of sync (verified as `LogConsistent` in the model)

### Requirement: Publish/GC interlock deferred with a hook
The publish protocol SHALL document that a concurrent garbage collector must not
delete a blob written by an in-flight publish whose manifest is not yet committed.
The resolution (e.g. a grace period excluding recently written blobs) is specified
by the garbage-collection capability; this capability SHALL NOT leave the race
unaddressed silently.

#### Scenario: In-flight blob is protected from concurrent GC
- **WHEN** a publish has stored a blob but not yet committed its manifest, and a
  garbage collection runs concurrently
- **THEN** the GC policy (defined by the garbage-collection capability) SHALL NOT
  delete that blob, per the grace-period hook recorded here

