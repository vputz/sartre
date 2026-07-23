## MODIFIED Requirements

### Requirement: Repository composes a Registry and a Store
The system SHALL provide a `Repository` facade constructed from one `Registry` and one `Store`. The facade SHALL expose the read surface — `head`, `resolve`, `open(snap, path)`, `fetch_all(snap)` — a `snapshot_fs(snap)` factory returning a read-only fsspec filesystem bound to that snapshot, a `checkout(snap, dest)` operation materializing the whole tree under a caller-chosen directory, a `publish` operation, a `point(coord, name, version, *, expected)` compare-and-swap pointer move, thin enumeration delegators (`list_coordinates()`, `list_log(coord)`, `list_pointers(coord)`), and a `gc(policy) -> GCResult` operation reclaiming storage by mark-and-sweep, delegating manifest concerns to the registry and byte concerns to the store.

#### Scenario: Open materializes one entry
- **WHEN** `open(snap, path)` is called
- **THEN** the entry's `content_hash` is resolved from the snapshot and its bytes are materialized through the store

#### Scenario: Resolve carries no blob bytes
- **WHEN** `resolve` returns a snapshot
- **THEN** no blob has been downloaded to produce it

#### Scenario: Snapshot filesystem is bound to the version
- **WHEN** `snapshot_fs(snap)` is called
- **THEN** it returns a read-only fsspec filesystem whose listings come from `snap`'s manifest and whose reads resolve bytes through the store

#### Scenario: Checkout lays out the tree under the destination
- **WHEN** `checkout(snap, dest)` is called
- **THEN** every entry is written at its logical path under `dest`, fetched concurrently and deduped against the cache, and nothing is written outside `dest`

#### Scenario: GC reclaims through the facade
- **WHEN** `gc(policy)` is called
- **THEN** it reclaims unreferenced blobs and out-of-retention manifests via mark-and-sweep and reports what was dropped

#### Scenario: Enumeration delegates to the registry
- **WHEN** `list_coordinates()`, `list_log(coord)`, or `list_pointers(coord)` is called
- **THEN** it returns the registry's coordinates, commit log, or pointer map without fetching any blob

## ADDED Requirements

### Requirement: Compare-and-swap pointer move
The `Repository` SHALL provide `point(coord, name, version, *, expected)` that moves a mutable pointer (head or a named alias) to an already-committed `version`, changing only the pointer plane — no blob upload and no new manifest. It SHALL delegate to the registry's compare-and-swap `set_pointer`, so it advances only if the pointer's current value equals `expected` and raises a typed conflict otherwise; `expected=None` requires the pointer to not already exist. It SHALL raise `NotFound` if `version` is not a committed version. This is the promotion/rollback primitive: promote an existing version to a channel, re-point an alias, or move head back.

#### Scenario: Move a pointer to a committed version
- **WHEN** `point(coord, "stable", v, expected=current)` is called and `v` is committed and the pointer currently equals `current`
- **THEN** the `stable` pointer advances to `v` and no blob is uploaded

#### Scenario: Stale expected is rejected
- **WHEN** `point` is called with an `expected` that no longer matches the pointer's current value
- **THEN** it raises a typed conflict and leaves the pointer unchanged

#### Scenario: Refuse to point at an uncommitted version
- **WHEN** `point` targets a version that has not been committed
- **THEN** it raises `NotFound` and does not move the pointer
