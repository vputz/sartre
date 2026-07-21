## Why

`SnapshotFS` is the payoff of the two-plane design — filenames on the outside,
content-addressing on the inside — but it is still an interface-only stub whose
methods raise. Making it runnable in local scope lets any fsspec-aware consumer
(pyarrow, pandas, polars, safetensors) read a resolved version by logical path
while bytes are served from the content-addressed store, and gives callers a
whole-tree checkout to a directory of their choosing.

## What Changes

- Implement `SnapshotFS` over a resolved `Snapshot` + `Store`:
  - `ls`/`info`/`exists`/`find` served **only** from the manifest (zero blob fetch),
    synthesizing directories from logical-path prefixes.
  - `_open` maps a logical path → `content_hash` → a seekable, verified handle
    through the `Store` (random access is a seek on a materialized blob).
  - All write/create/delete operations raise a read-only error.
- Add facade affordances: `Repository.snapshot_fs(snapshot)` returns the bound
  filesystem; `Repository.checkout(snapshot, dest)` materializes the whole tree
  under a caller-chosen directory (concurrent, cache-deduped, contained — nothing
  escapes `dest`).
- Property/behavioral tests: listings never touch blobs, open round-trips bytes
  and supports seek, checkout containment, and pass-through to a real fsspec
  consumer.
- **Deferred (not in this change):** `sartre://` URL protocol registration, FUSE
  mounting, and the key/value mapper — these need a URL→`Repository` resolver and
  are a follow-up. Addressing in this change is **object-form**: you hand the
  `AbstractFileSystem` to consumers directly.

## Capabilities

### New Capabilities
<!-- none: SnapshotFS behavior is already specified by filesystem-view -->

### Modified Capabilities
- `filesystem-view`: refine the addressing requirement — object-form fsspec
  addressing is delivered now; `sartre://` URL registration, FUSE, and the kv
  mapper are explicitly future/optional.
- `repository-facade`: expand the exposed facade surface to include
  `snapshot_fs(snapshot)` and `checkout(snapshot, dest)`.

## Impact

- Code: `src/sartre/fs.py` (fill the stub), `src/sartre/repository.py`
  (`snapshot_fs`, `checkout`), `src/sartre/__init__.py` (re-exports).
- Tests: new `tests/test_snapshot_fs.py`; a pyarrow/parquet interop test guarded
  by an optional import so the core test run stays dependency-light.
- Dependencies: none required; the interop test uses `pyarrow` only if present.
- No breaking changes: `SnapshotFS` previously raised on every call.
