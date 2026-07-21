## ADDED Requirements

### Requirement: Mark-and-sweep garbage collection
The system SHALL provide `Repository.gc(policy) -> GCResult` that reclaims storage
by mark-and-sweep over the reachability graph `roots → manifests → blobs`, across
all coordinates in the repository. Marking SHALL resolve each retained manifest and
collect its entries' `content_hash` values into a live set; sweeping SHALL delete
only blobs absent from the live set. `GCResult` SHALL report what was dropped
(manifests and blobs) for operability.

#### Scenario: Unreferenced blob is reclaimed
- **WHEN** a blob is referenced by no retained manifest and is under no live lease
- **THEN** `gc` deletes it and reports it in the result

#### Scenario: Shared blob is retained
- **WHEN** a blob is referenced by at least one retained manifest
- **THEN** `gc` does not delete it, even if another version that referenced it was dropped

### Requirement: Retention policy defines the roots
The retention policy SHALL determine the root set. Roots SHALL always include every
current pointer and named tag across all coordinates. The policy SHALL additionally
support `keep_last_n` (the newest N versions per coordinate, by commit-log order)
and `keep_within(age)` (versions whose commit time is within a duration of now).
A manifest outside the retained set and targeted by no pointer SHALL be droppable;
its `content_hash`-unique blobs SHALL be reclaimable if no retained manifest shares
them.

#### Scenario: Pointer target is always protected
- **WHEN** a version is the current target of any pointer or tag
- **THEN** `gc` retains its manifest and all its blobs regardless of `keep_last_n`/age

#### Scenario: Old unpointed version is dropped
- **WHEN** a version is outside `keep_last_n` and `keep_within`, and no pointer targets it
- **THEN** `gc` drops its manifest and reclaims any blob no retained manifest references

### Requirement: GC is explicit and caller-triggered
The library SHALL perform garbage collection only when `gc` is invoked; it SHALL NOT
auto-trigger GC (no background thread, scheduler, or on-publish side effect). The
trigger — manual, scheduled, or threshold-based — is the caller's responsibility. The
caller SHALL serialize GC passes; the library assumes at most one GC in flight and does
not coordinate concurrent GC runs.

#### Scenario: No implicit collection
- **WHEN** blobs become unreferenced through publishes and pointer advances, with no
  `gc` call
- **THEN** nothing is deleted; storage is reclaimed only by an explicit `gc` invocation

### Requirement: Blob lease closes the publish/GC race
GC SHALL treat a live lease's hashes as protected blobs and its version as a retained
manifest: a blob SHALL be collectable only if unreferenced by every retained manifest
**and** named by no live lease, and a committed manifest SHALL NOT be dropped while its
version is leased. A **lease** is a registry-held claim naming a version and a set of
content hashes for the lifetime of an in-flight write — it is not a lock and grants no
exclusive access; the active leased set is the **union** across all live leases, so a
version or hash released by one lease stays protected while any other live lease still
names it. A publish SHALL hold one lease over its version and whole blob set for the
duration of the publish (see the publish-transaction capability), closing both the
window before its manifest is committed (blobs uploaded, not yet referenced) and the
window before its pointer advances (manifest committed, not yet a pointer target).

#### Scenario: Concurrent GC does not collect an in-flight publish's blob
- **WHEN** a publish has uploaded a blob but not yet committed its manifest, and `gc`
  runs concurrently
- **THEN** the blob is under the publish's lease and `gc` does not delete it, so the
  subsequent commit references present bytes

#### Scenario: Crashed publish leaves blobs protected
- **WHEN** a publish crashes after uploading blobs without releasing its lease
- **THEN** those blobs remain protected (safe over reclaimed); reclaiming them via a
  lease TTL is out of scope for this capability

### Requirement: GC is idempotent and interrupt-safe
`gc` SHALL be safe to interrupt and re-run: deleting an already-absent blob SHALL be
a no-op, and a second `gc` with no intervening writes SHALL delete nothing further.
Deletion SHALL be verify-before-delete against the live set computed in the same pass.

#### Scenario: Re-running gc is a no-op
- **WHEN** `gc` runs twice with the same policy and no intervening publish
- **THEN** the second run deletes no manifests and no blobs

### Requirement: BlobSafe verified for concurrent publish and GC
The interaction of publish and GC under the lease discipline SHALL be modeled in
TLA+ (`model/GC.tla`) and checked to satisfy `BlobSafe`: in every reachable state,
every committed manifest's blobs are all present in the store. The reference backend
SHALL be covered by a property-based stateful machine that asserts `BlobSafe` on the
live system across randomized publish / gc / crash-and-retry sequences.

#### Scenario: No reachable state drops a referenced blob
- **WHEN** the model checker or the stateful machine explores interleavings of
  publish and gc, including interrupted publishes
- **THEN** in every observed state each committed manifest resolves to fully present
  blobs, matching the `BlobSafe` invariant
