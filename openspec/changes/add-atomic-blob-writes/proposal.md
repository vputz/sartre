## Why

`FsspecBlobBackend.put` currently streams bytes straight to the final key. A crash or
error mid-write leaves a **partial blob at its content hash** — a truncated object that
`has(hash)` reports as present and that only fails later on verify-on-read. The
blob-store spec already requires atomic writes ("write cache entries via a temporary
file followed by an atomic rename"), but we implemented only the per-hash lock and
deferred the rename. This change closes that gap at the level where it actually holds:
the backend `put`.

## What Changes

- `FsspecBlobBackend.put` becomes **atomic**: stream to a reserved staging key
  `{root}/.tmp/{uuid}`, then rename it onto the final `{root}/{content-hash}`. A blob
  therefore appears at its hash only when complete; a crashed or failed upload leaves at
  most an orphaned temp file, never a partial blob.
- Because both `CasStore.put` and the `CachingStore` cache back-fill go through the
  backend `put`, this makes direct storage **and** cache population crash-safe with no
  further changes — closing the deferred "atomic cache writes" requirement.
- `list()` excludes the reserved `.tmp` namespace, so temp files are never seen as
  blobs (by GC or anything else). A `sweep_temp()` helper reclaims orphaned temps on
  demand; leftover temps are otherwise inert.
- Idempotent as before: if the final key already exists, `put` skips the write.
- Property-test the invariant with Hypothesis: after any sequence of puts — including
  ones that fail mid-write — every key present in `list()` holds complete bytes that
  verify against the hash, and no partial blob is ever observable.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `blob-store`: specify backend-level atomic `put` (temp-stage then rename) and the
  reserved temp namespace excluded from `list()`; the existing "atomic cache writes"
  guarantee is now satisfied by the backend rather than deferred.

## Impact

- Code: `src/sartre/store.py` (`FsspecBlobBackend.put`/`list` + `sweep_temp`).
- Tests: extend `tests/test_store.py` with crash-safety and temp-exclusion cases; new
  Hypothesis property for the no-partial-blob invariant.
- Dependencies: none. Uses fsspec's `mv`/rename; atomic on local and memory filesystems,
  and effectively atomic on object stores (an object appears only once its write
  completes).
- Non-goal: the optional `get_many` batch-fetch hook — throughput, not correctness —
  is deferred to the S3 backend change where batching matters.
- No breaking changes: `put`'s signature and idempotent contract are unchanged.
