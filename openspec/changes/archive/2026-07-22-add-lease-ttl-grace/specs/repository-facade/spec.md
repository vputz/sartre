## MODIFIED Requirements

### Requirement: Publish holds a blob lease
`Repository.publish` SHALL acquire a lease over its version and blob hashes, with a TTL,
before uploading them, so that a concurrent `gc` treats the in-flight blobs as protected
and the in-flight version as retained. While the publish runs it SHALL keep the lease
alive with a background heartbeat that renews the lease well within its TTL (a liveness
device; safety does not depend on renewal succeeding). Immediately before committing its
manifest, and again immediately before advancing its pointer, `publish` SHALL re-verify
its lease is still live (via `renew_lease`); if the lease has lapsed it SHALL abort the
publish — releasing the lease and raising a retryable error — rather than commit or
advance over blobs GC may have reclaimed. Because blob puts are content-addressed and
idempotent, a retried publish re-uploads safely. On completion or abort `publish` SHALL
stop the heartbeat and release the lease. If the publish crashes, its lease MAY remain
held until its TTL expires, after which its version and blobs become collectable.

#### Scenario: Publish protects its blobs from concurrent GC
- **WHEN** `publish` is uploading blobs and committing a manifest while `gc` runs, and its
  lease is kept live by the heartbeat
- **THEN** the publish's blobs are under a live lease and `gc` does not collect them

#### Scenario: Publish aborts when its lease lapses before commit
- **WHEN** a publish's lease expires mid-flight (e.g. an upload outran the TTL and a
  heartbeat was missed) and its pre-commit self-check finds the lease lapsed
- **THEN** `publish` aborts with a retryable error and does not commit a manifest over
  possibly-reclaimed blobs

#### Scenario: Publish aborts when its lease lapses before advancing the pointer
- **WHEN** a publish's lease expires after commit but before the pointer CAS, and its
  pre-advance self-check finds the lease lapsed
- **THEN** `publish` aborts with a retryable error and does not point at a manifest whose
  blobs GC may have reclaimed
