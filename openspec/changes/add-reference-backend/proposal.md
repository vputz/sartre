## Why

Every capability so far is specified but only stubbed — no code path actually
runs end-to-end. We need a concrete reference implementation to validate that the
ports compose as designed, to make `head → resolve → open → publish` real, and to
re-check the publish-transaction invariants (proven abstractly in TLA+) against
actual code. An in-memory backend is the smallest thing that does all three.

## What Changes

- Implement the **blob plane** bodies: `CasStore` (hashing + verify-on-download
  over any `BlobBackend`), `CachingStore` (cache-as-Store with a per-hash lock +
  atomic write), and `FsspecBlobBackend` (over an fsspec filesystem) — exercised
  with an in-memory fsspec filesystem.
- Implement an in-memory **`MemoryRegistry`**: a dict of pointers (current tips),
  manifests keyed by `manifest_version` (content-addressed, deduped), and an
  append-only commit log (`seq` + `created_at`). It upholds the port contracts —
  cheap `head`, atomic `resolve`, content-idempotent `commit`, commit-log-ordered
  `list_versions`, and compare-and-swap `set_pointer`.
- Implement the **`Repository`** read core and write path: `head`, `resolve`,
  `open`, `fetch_all` (parallel via a thread pool), and `publish` (the
  full-replacement, fail-fast, blobs→manifest→pointer protocol).
- Add **property-based tests** (Hypothesis), including a `RuleBasedStateMachine`
  that drives random `publish`/`crash`/`resolve`/`promote` sequences and asserts
  the same invariants the TLA+ model proved — no dangling tip, single-winner under
  concurrency, convergence under retry — against the real implementation.

## Capabilities

### New Capabilities
- `in-memory-backend`: an ephemeral, single-process `Registry` + memory-backed
  `Store` implementing the ports for tests and local use, plus the property-based
  verification that ties the running code back to the publish-transaction model.

### Modified Capabilities
<!-- None — this implements existing specs (blob-store, registry-port,
     repository-facade, publish-transaction, version-log); no requirements change. -->

## Impact

- **New code**: bodies for `CasStore`/`CachingStore`/`FsspecBlobBackend` in
  `store.py`; a new `MemoryRegistry`; `Repository` read-core + `publish`; a new
  `hypothesis` dev dependency.
- **No SnapshotFS/checkout yet**: the read-only filesystem view and whole-tree
  checkout are deferred to a follow-up (the `filesystem-view` capability stays
  stubbed).
- **No persistence**: state is in-process only; the local-filesystem and Delta/S3
  backends come later, behind the same ports.
- **Deferred (non-goals)**: garbage collection, the local-fs/Delta/S3 backends,
  and `SnapshotFS`/checkout.
