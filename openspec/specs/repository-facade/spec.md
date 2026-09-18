# repository-facade Specification

## Purpose
TBD - created by archiving change add-core-ports. Update Purpose after archive.

## Requirements

### Requirement: Repository composes a Registry and a Store
The system SHALL provide a `Repository` facade constructed from one `Registry` and one `Store`. The facade SHALL expose the read surface — `head`, `resolve`, `open(snap, path)`, `fetch_all(snap)` — a `snapshot_fs(snap)` factory returning a read-only fsspec filesystem bound to that snapshot, a `checkout(snap, dest)` operation materializing the whole tree under a caller-chosen directory, a `publish` operation, a `point(coord, name, version, *, expected)` compare-and-swap pointer move, thin enumeration delegators (`list_coordinates()`, `list_log(coord)`, `list_pointers(coord)`, `list_pointer_history(coord)`), and a `gc(policy) -> GCResult` operation reclaiming storage by mark-and-sweep, delegating manifest concerns to the registry and byte concerns to the store.

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
- **WHEN** `list_coordinates()`, `list_log(coord)`, `list_pointers(coord)`, or `list_pointer_history(coord)` is called
- **THEN** it returns the registry's coordinates, commit log, pointer map, or pointer-move history without fetching any blob

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

### Requirement: Publish ordering through the facade
`publish` SHALL accept its files as `Mapping[str, bytes]` or `Mapping[str, Path]` (both re-readable sources; read-once streams are not supported). `publish` SHALL derive each blob's content hash and the manifest version by streaming each source through the hasher (bounded memory, no whole-blob `bytes` value), then — as today — upload blobs to the store before recording the manifest, **skipping any blob the durable store already holds**, then `commit` the manifest, then advance the target pointer via compare-and-swap. Publish SHALL determine "already held" against the durable store the manifest is read back from — it SHALL pass each source's already-computed content hash to `Store.put` as `known_hash` (which consults the durable target) rather than relying on `Store.has`, so a blob present only in a local cache but absent from the remote is still uploaded. Each upload SHALL itself stream (the store's `stage`/`promote` seam), so a source larger than memory is published without being buffered whole. The lease over `(version, hashes)` SHALL be acquired before any blob is uploaded, preserving the ordering the garbage-collection lease discipline was verified under; because the lease protects the whole hash set, a blob found already present cannot be swept before the manifest commits.

#### Scenario: Existing blobs are not re-uploaded
- **WHEN** publishing a manifest whose blobs are already stored durably
- **THEN** those blobs are not staged or uploaded again before the manifest is committed

#### Scenario: Locally-cached but remote-absent blob is still uploaded
- **WHEN** publishing through a `CachingStore` where a blob is present in the local cache but absent from the remote (durable) store
- **THEN** that blob is uploaded to the remote before the manifest is committed, so the committed manifest never references a blob missing from the durable store

#### Scenario: Large files publish in bounded memory
- **WHEN** `publish` is given `Path` sources larger than available memory
- **THEN** each source is hashed and uploaded in streaming passes without being buffered whole in memory

#### Scenario: Bytes and path sources both accepted
- **WHEN** `publish` is given files as `bytes` values or as `Path` values
- **THEN** each is published with its correct content hash and size (`len` for bytes, file size for a path)

### Requirement: Sync core with parallel batch operations
The ports SHALL be synchronous and single-item. Multi-file operations (`fetch_all`, `checkout`) SHALL parallelize downloads across a thread pool with a configurable worker count, relying on blob I/O releasing the GIL. When the backend offers a batch hook, the facade SHALL prefer it over per-item fan-out.

#### Scenario: Parallel multi-file fetch
- **WHEN** `fetch_all` materializes a many-entry snapshot
- **THEN** uncached blobs are fetched concurrently up to the configured worker limit

### Requirement: Optional async wrapper
The system SHALL provide an `AsyncRepository` that wraps the synchronous core, offering awaitable equivalents of the read/publish surface by offloading to a thread (e.g. `asyncio.to_thread`). It SHALL NOT duplicate the core logic in a separate async implementation.

#### Scenario: Async call does not block the event loop
- **WHEN** an async application awaits a repository read
- **THEN** the synchronous work runs off the event loop thread and the loop remains responsive

### Requirement: Compare-and-swap pointer move
The `Repository` SHALL provide `point(coord, name, version, *, expected, actor="unknown", reason=None)` that moves a mutable pointer (head or a named alias) to an already-committed `version`, changing only the pointer plane — no blob upload and no new manifest. It SHALL delegate to the registry's compare-and-swap `set_pointer`, so it advances only if the pointer's current value equals `expected` and raises a typed conflict otherwise; `expected=None` requires the pointer to not already exist. It SHALL raise `NotFound` if `version` is not a committed version. On success it SHALL record the move's `actor` and `reason` in the pointer-move history; when `actor` is omitted it SHALL be recorded as `"unknown"`. This is the promotion/rollback primitive: promote an existing version to a channel, re-point an alias, or move head back.

#### Scenario: Move a pointer to a committed version
- **WHEN** `point(coord, "stable", v, expected=current)` is called and `v` is committed and the pointer currently equals `current`
- **THEN** the `stable` pointer advances to `v`, no blob is uploaded, and a pointer-move record is appended with the call's actor and reason

#### Scenario: Stale expected is rejected
- **WHEN** `point` is called with an `expected` that no longer matches the pointer's current value
- **THEN** it raises a typed conflict, leaves the pointer unchanged, and appends no history record

#### Scenario: Refuse to point at an uncommitted version
- **WHEN** `point` targets a version that has not been committed
- **THEN** it raises `NotFound` and does not move the pointer

### Requirement: Publish carries provenance
The `Repository.publish` operation SHALL accept `actor` and `reason`, threading them to the `set_pointer` that makes the new version the pointer's tip (where provenance is recorded), not to `commit`. `actor` MAY be omitted and SHALL then be recorded as `"unknown"`; `reason` is free text. The publish SHALL NOT place the reason into the manifest `metadata`.

#### Scenario: Publish records the committing actor and reason
- **WHEN** `publish(coord, sources, actor="alice", reason="retrain")` is called
- **THEN** the resulting version's commit-log entry for that coordinate exposes `actor="alice"` and `reason="retrain"`, and `metadata` contains no `message` key

#### Scenario: Omitted actor is recorded as unknown
- **WHEN** `publish` is called without `actor`
- **THEN** the commit is recorded with `actor="unknown"` and succeeds

### Requirement: Checkout requires an empty destination by default
`Repository.checkout(snap, dest, *, overwrite=False)` SHALL materialize the version only into a destination that does not exist or is empty. If `dest` exists and is non-empty and `overwrite` is false, it SHALL raise a typed error (`PathError`) naming the directory and the override, and SHALL write nothing. With `overwrite=True` it SHALL write the manifest's files into `dest`, overwriting any colliding paths and leaving pre-existing extraneous files in place — overlay semantics, not sync: it SHALL NOT delete files absent from the version. `checkout` SHALL NOT provide a prune/mirror mode, because without an index it cannot distinguish its own stale files from the caller's unrelated files. `AsyncRepository.checkout` SHALL expose the same `overwrite` parameter and behavior. `fetch_all`, which uses a fresh temporary directory, is unaffected.

#### Scenario: Fresh or empty destination materializes the exact tree
- **WHEN** `checkout` targets a nonexistent or empty directory
- **THEN** every entry is written at its logical path under `dest` and the directory contents equal the version's tree

#### Scenario: Non-empty destination is refused by default
- **WHEN** `checkout` targets an existing directory that already contains files and `overwrite` is false
- **THEN** it raises `PathError` naming the directory and writes nothing

#### Scenario: Overwrite overlays without pruning
- **WHEN** `checkout(..., overwrite=True)` targets a non-empty directory that holds a file not in the version
- **THEN** the version's files are written (overwriting any collisions) and the extraneous pre-existing file remains — the destination is not reduced to exactly the version

### Requirement: Derive a version from a base
The `Repository` SHALL provide `publish_over(coord, base=HEAD, *, changes={}, remove=(), rename={}, pointer="head", stage=False, metadata=None, actor="unknown", reason=None) -> PublishOverResult` that creates a new immutable version from an existing one. It SHALL resolve `base` to a snapshot and seed the manifest entry set from that snapshot's entries, then apply, in order: `remove` (drop those paths), `rename` (re-key entries), and `changes` (override/add). It SHALL reuse every untouched entry by its existing `content_hash` — such entries require no source and SHALL NOT be uploaded or re-hashed. Paths SHALL be normalized and the case-collision invariant checked on the final merged set.

- `changes` maps a logical path to a source (`bytes` or `Path`) that overrides an existing path or adds a new one; each such source is hashed to build its entry and uploaded (skipping the wire if the durable store already holds it).
- `remove` names one or more paths to drop; removing a path absent from the merged set SHALL raise a typed error.
- `rename` maps an existing `old` path to a `new` path: it SHALL move the base entry (its `content_hash`, `size`, `inline`) to `new` and drop `old`, requiring no source and uploading nothing. Renaming a path absent from the base SHALL raise a typed error.

`publish_over` SHALL follow the same lease/commit/advance discipline as `publish`: acquire the lease over the new version and *all* its blob hashes (changed and inherited) before uploading, so an inherited blob cannot be swept mid-derive; upload only the `changes` sources; re-verify the lease before `commit` and before advancing; then advance the pointer via compare-and-swap, recording `actor`/`reason` on the tip event. It SHALL always create a new version and SHALL NOT mutate the base. It SHALL return a `PublishOverResult` carrying the new `version`, the counts of entries `inherited`, `replaced`, `added`, `removed`, and `renamed`, and `staged_pointer` (the staging pointer name when `stage=True`, otherwise `None`).

When `stage=False` (the default) it advances `pointer`. When `stage=True` it SHALL instead advance a staging pointer it names itself (ignoring `pointer`), record the base's resolved version as `derived_from` in the new version's metadata, and report that pointer as `staged_pointer` — so the derived version can be verified via that pointer and later promoted to head with a base-CAS `promote`. The staging pointer name SHALL be content-derived from the base version and the set of affected logical paths (those changed, removed, or renamed), so that re-running the same derive reuses the same staging pointer rather than creating a new one (idempotent repair), and SHALL be a valid alias segment.

The advance SHALL be a compare-and-swap against the version the derive inherited from: for a same-lineage advance (the advanced pointer is the base), the CAS `expected` SHALL be the base's resolved version, so if that pointer moved between the base read and the advance the operation SHALL raise `Conflict` rather than silently discard the intervening change. A derive SHALL NOT advance a pointer to a version whose base differs from the pointer's value at the moment of advance. (This no-silent-lost-update property is verified in `model/PublishOver.tla`.)

#### Scenario: Stale base loses the CAS
- **WHEN** `publish_over` derives from head `v5` and, before it advances, a concurrent publish moves head to `v6`
- **THEN** the derive's advance raises `Conflict` (its `expected` is `v5`) and head remains `v6` — the derived version does not overwrite `v6`

#### Scenario: Change one file, reuse the rest by hash
- **WHEN** `publish_over(coord, changes={"cfg.json": new_bytes})` derives from a base with `cfg.json` and other files
- **THEN** the new version contains the new `cfg.json` and every other file identical to the base, only `cfg.json`'s blob is uploaded (unchanged blobs are neither uploaded nor re-hashed), the base version is unchanged, and the result reports one entry replaced and the rest inherited

#### Scenario: No-op derive equals the base version
- **WHEN** `publish_over(coord, base=b)` is called with empty `changes`, `remove`, and `rename`
- **THEN** the resulting version id equals `b`'s version (identical entries yield an identical manifest hash)

#### Scenario: Overriding with identical bytes equals the base version
- **WHEN** a path is overridden via `changes` with bytes identical to the base's content for that path
- **THEN** the resulting version id equals the base's version, and no blob is uploaded

#### Scenario: Rename reuses the blob with no upload
- **WHEN** `publish_over(coord, rename={"old.bin": "new.bin"})` derives from a base containing `old.bin`
- **THEN** the new version contains `new.bin` with `old.bin`'s content hash, does not contain `old.bin`, and no blob is uploaded; renaming a path not present in the base raises a typed error

#### Scenario: Remove drops a path
- **WHEN** `publish_over(coord, remove=["old.txt"])` derives from a base containing `old.txt`
- **THEN** the new version does not contain `old.txt`; removing a path not present raises a typed error

#### Scenario: Reused blobs are protected and resolvable
- **WHEN** a derived version is committed and later resolved
- **THEN** its unchanged files are served from the pre-existing blobs, which were held as GC roots under the derive's lease through commit and advance

#### Scenario: Stage lands off head and records its base
- **WHEN** `publish_over(coord, changes={"cfg.json": new_bytes}, stage=True)` derives from head
- **THEN** head is unchanged, the derived version is advanced onto a content-derived staging pointer reported as `staged_pointer`, and that version's metadata records `derived_from` = the base version — so a later `promote` can base-CAS head to it

#### Scenario: Re-running the same staged derive reuses its pointer
- **WHEN** the same `publish_over(..., stage=True)` derive (same base and same affected paths) is run twice
- **THEN** both runs use the same staging pointer name, so a re-run after a failed verify reuses that pointer rather than leaving an orphaned one

### Requirement: Delete a mutable pointer through the facade
`Repository` SHALL expose `delete_pointer(coord, name, *, actor="unknown", reason=None)` that removes a mutable alias via the Registry's compare-and-swap deletion: it reads the pointer's current value and deletes only if unchanged, surfacing `Conflict` on a concurrent move. It SHALL refuse to delete `head` — a coordinate's identity, not a removable label — raising a clear error without touching the registry. Deleting an absent alias SHALL be a no-op. Deletion removes only the label; manifests and blobs remain until reclaimed by retention GC.

#### Scenario: Delete a spent staging pointer
- **WHEN** `repo.delete_pointer(coord, "staging-abc")` is called for an existing staging alias
- **THEN** the alias no longer resolves, and the version it pointed to is untouched (still resolvable by id until GC)

#### Scenario: Refuse to delete head
- **WHEN** `repo.delete_pointer(coord, "head")` is called
- **THEN** it raises an error explaining head cannot be deleted, and no registry write occurs

### Requirement: Base-CAS promotion of a staged version
`Repository` SHALL expose `promote(coord, staged, *, pointer="head", actor="unknown", reason=None)` that advances `pointer` to the version `staged` resolves to, compare-and-swapping against that version's recorded base (`derived_from`) rather than the pointer's current value — so a concurrent publish that advanced the pointer since the version was staged SHALL cause `Conflict`, never a silent lost update. When the staged version records no `derived_from` (a hand-made alias or a pre-feature stage), `promote` SHALL refuse with a clear message rather than guess, unless the caller opts into a plain last-writer move. `AsyncRepository` SHALL expose an awaitable wrapper for both `delete_pointer` and `promote`.

#### Scenario: Promote a cleanly-staged version
- **WHEN** a version was staged from base `b` (head still at `b`) and `repo.promote(coord, staged)` is called
- **THEN** head advances to the staged version (CAS against `b` succeeds)

#### Scenario: Promote conflicts when head moved during verification
- **WHEN** a version was staged from base `b`, another publish then advanced head to `c`, and `repo.promote(coord, staged)` is called
- **THEN** it raises `Conflict` and head still resolves to `c` (the concurrent publish is not clobbered)

#### Scenario: Promote refuses a version with no recorded base
- **WHEN** `repo.promote(coord, staged)` is called for a version that records no `derived_from`
- **THEN** it refuses with a message explaining the base is unknown, unless a last-writer override is requested
