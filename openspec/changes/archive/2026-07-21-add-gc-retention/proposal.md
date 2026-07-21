## Why

Blobs are content-addressed and shared across versions, so deleting a pointer or
manifest reclaims nothing — bytes become garbage only when no *retained* manifest
references them. Without GC the store grows monotonically. GC is inherently
mark-and-sweep over `roots → manifests → blobs`, and it races with publish: a blob
uploaded by an in-flight publish is not yet referenced by any committed manifest,
so a naive sweep would delete bytes a publish is about to commit. This change adds
GC with a retention policy and closes that race with a verified lease discipline.

## What Changes

- New `garbage-collection` capability: `Repository.gc(policy) -> GCResult`,
  mark-and-sweep across all coordinates.
  - **Roots** = every pointer/tag (tips + named pointers) ∪ `keep_last_n` per
    coordinate ∪ `keep_within(age)` ∪ **currently-leased blobs**.
  - **Sweep** drops out-of-retention manifest records and deletes blobs unreachable
    from roots. Idempotent, interrupt-and-re-run safe, verify-before-delete.
- **Lease discipline** (race closure): `publish` acquires a lease over its blob
  hashes before the first upload and releases it after the publish completes; GC
  treats leased blobs as roots. A blob is collectable only if unreferenced by any
  retained manifest **and** under no live lease. A crashed publish leaves its lease
  held (blobs protected, safe); reclaiming crashed-publisher orphans via a lease
  TTL / grace-period — the *degenerate lease* — is deferred.
- **TLA+**: `model/GC.tla` models concurrent publish (with the lease bracket) and
  GC, proving `BlobSafe` — every committed manifest's blobs are all present — across
  all interleavings. A Hypothesis `RuleBasedStateMachine` mirrors it on live code.
- Port surface additions (see Modified Capabilities).

## Capabilities

### New Capabilities
- `garbage-collection`: mark-and-sweep GC, retention policy, the lease discipline,
  and the `BlobSafe` guarantee (with its TLA+ model).

### Modified Capabilities
- `blob-store`: add `Store.list()` and `BlobBackend.list()` (enumerate keys for the
  sweep).
- `registry-port`: add repo-wide enumeration (`list_coordinates`, `list_log` with
  `created_at`), `drop_version` (remove an out-of-retention manifest, refusing a
  live pointer target), and the lease surface (`acquire_lease`, `release_lease`,
  `active_leased_hashes`).
- `repository-facade`: add `gc(policy)`; `publish` now brackets its work in a blob
  lease.
- `publish-transaction`: add the requirement that publish holds a lease over its
  blobs for the publish duration so a concurrent GC cannot collect them.

## Impact

- Code: `ports.py` (Store/BlobBackend/Registry additions), `store.py` (`list`),
  `memory.py` (enumeration, `drop_version`, in-memory lease set), `repository.py`
  (`gc`, publish lease bracket, `RetentionPolicy`/`GCResult` types), `__init__.py`.
- Specs/model: new `garbage-collection` spec + `model/GC.tla` (+ `.cfg`, run notes).
- Tests: `test_gc.py` (retention, sweep, idempotency), a Hypothesis stateful
  machine asserting `BlobSafe`, and lease/publish-race tests.
- Deferred: lease-TTL / grace-period reclamation of crashed-publisher orphans, and
  the timed `g ≥ d` lemma; manifest/log *history-record* compaction.
- No breaking changes: GC is additive; publish gains an internal lease bracket.
