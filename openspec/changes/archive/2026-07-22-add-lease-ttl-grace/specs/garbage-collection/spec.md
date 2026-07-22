## MODIFIED Requirements

### Requirement: Blob lease closes the publish/GC race
GC SHALL treat a **live (unexpired)** lease's hashes as protected blobs and its version
as a retained manifest: a blob SHALL be collectable only if unreferenced by every
retained manifest **and** named by no live lease, and a committed manifest SHALL NOT be
dropped while its version is covered by a live lease. A **lease** is a registry-held
claim naming a version and a set of content hashes, valid until its TTL expires against
the registry clock — it is not a lock and grants no exclusive access; the active leased
set is the **union** across all live leases, so a version or hash released or expired
under one lease stays protected while any other live lease still names it. Because a lease
may **expire** while a publish is still in flight, an in-flight publish SHALL re-verify
its lease is still live immediately before committing its manifest and before advancing
its pointer, aborting rather than proceeding if the lease has lapsed (see the
repository-facade publish requirement); GC's safety therefore depends only on honoring
live leases as roots, not on any lease surviving for a whole publish.

#### Scenario: Concurrent GC does not collect an in-flight publish's blob
- **WHEN** a publish has uploaded a blob but not yet committed its manifest, holds a live
  lease over it, and `gc` runs concurrently
- **THEN** the blob is under the live lease and `gc` does not delete it, so the subsequent
  commit references present bytes

#### Scenario: Expired lease no longer blocks collection
- **WHEN** a publish's lease has expired (its ttl elapsed with no renewal) and `gc` runs
- **THEN** `gc` may collect the now-unleased blobs, and the publish — detecting its lapsed
  lease at its next self-check — aborts instead of committing over them

#### Scenario: Crashed publish's blobs are reclaimed after expiry
- **WHEN** a publish crashes after uploading blobs without releasing its lease
- **THEN** those blobs stay protected until the lease's ttl elapses, after which `gc`
  reclaims them, so a dead publisher cannot pin storage indefinitely

### Requirement: BlobSafe verified for concurrent publish and GC
The interaction of publish and GC under the lease discipline SHALL be modeled in TLA+ and
checked to satisfy `BlobSafe` (in every reachable state, every committed manifest's blobs
are all present in the store) and `TipSafe` (the pointer target's manifest and blobs are
present). The permanent-lease discipline is modeled in `model/GC.tla`. Lease **expiry**
and the publish **self-check** SHALL be modeled in `model/GCLease.tla`: with the
self-check the invariants hold under every publish/expire/GC interleaving, and without it
the model SHALL exhibit the reopened race. The **grace-period** backstop SHALL be modeled
on a discrete clock in `model/GCGrace.tla`, establishing that `grace > max_publish_gap`
preserves `BlobSafe` (while GC still reclaims aged-out blobs) and that `grace <=
max_publish_gap` breaks it. The reference backend SHALL additionally be covered by a
property-based stateful machine that asserts `BlobSafe` on the live system across
randomized publish / gc / crash-and-retry sequences.

#### Scenario: No reachable state drops a referenced blob
- **WHEN** the model checker or the stateful machine explores interleavings of publish
  (with lease acquire, expiry, and self-check) and gc
- **THEN** no reachable state leaves a committed manifest referencing an absent blob, nor
  the pointer targeting an absent manifest or blob

## ADDED Requirements

### Requirement: Grace-period backstop for unleased writers
`gc` SHALL accept an optional `grace` duration and SHALL retain any blob whose store
mtime is younger than `grace`, independent of leases, so that a writer which uploads a
blob without taking a lease is protected across its put→commit window. Grace SHALL default
to zero (lease-only behavior). Grace is a **blob-only** backstop: it SHALL NOT be relied
on to keep a committed-but-unpointed manifest alive (that is the lease's role). Its
correctness rests on the operational assumption that `grace` exceeds the maximum
put→commit duration of any unleased writer; blobs older than `grace` and unreferenced by
any retained manifest SHALL remain collectable so that GC still reclaims abandoned uploads.

#### Scenario: Recently written blob is retained without a lease
- **WHEN** a blob was written more recently than `grace` and is referenced by no committed
  manifest and named by no live lease, and `gc` runs
- **THEN** `gc` retains the blob

#### Scenario: Aged-out abandoned blob is reclaimed
- **WHEN** an uploaded blob is older than `grace`, referenced by no manifest, and named by
  no live lease
- **THEN** `gc` reclaims it, so grace does not turn abandoned uploads into a permanent leak
