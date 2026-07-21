## 1. SnapshotFS implementation

- [x] 1.1 In `src/sartre/fs.py`, build the path index in `__init__`: `_files: dict[str, Entry]` keyed by canonical path and `_dirs: set[str]` of all proper path prefixes.
- [x] 1.2 Implement `_strip_protocol`/root handling so `""` addresses the root and `ls("/")`/`ls("")` behave the same.
- [x] 1.3 Implement `info(path)` returning fsspec-shaped dicts (`type`/`size`/`name`) for files and synthetic directories; raise `FileNotFoundError` for unknown paths.
- [x] 1.4 Implement `ls(path, detail)` returning immediate children only (info dicts or names), zero blob fetch.
- [x] 1.5 Implement `exists(path)` from the index; confirm `find`/`glob` work via the inherited walk.
- [x] 1.6 Implement `_open(path, mode)`: map path → `content_hash` → `store.open(hash)` (seekable, verified); reject non-`rb` modes with `PermissionError`.
- [x] 1.7 Keep all mutation methods (`_rm`, `mkdir`, and any write path) raising the read-only error; drop the `_DEFERRED` stub markers.

## 2. Facade affordances

- [x] 2.1 Factor `Repository.fetch_all`'s tree layout into a private helper that takes an explicit destination directory and parallelizes with the thread pool.
- [x] 2.2 Reimplement `fetch_all` as that helper into a `TemporaryDirectory`.
- [x] 2.3 Add `Repository.checkout(snap, dest, *, max_workers=8)`: lay the tree out under `dest`, asserting each resolved target is contained within `dest.resolve()` (raise `PathError` otherwise).
- [x] 2.4 Add `Repository.snapshot_fs(snap) -> SnapshotFS` binding the snapshot to `self.store`.
- [x] 2.5 Add awaitable `checkout` to `AsyncRepository`; update the module docstring (remove the "deferred to a follow-up" note).
- [x] 2.6 Re-export `SnapshotFS` (and confirm `checkout`/`snapshot_fs` reachable) from `src/sartre/__init__.py`.

## 3. Tests

- [x] 3.1 `tests/test_snapshot_fs.py`: listing (`ls`/`info`/`exists`/`find`) is served from the manifest with a store that raises if `open`/`get_to` is called — proves zero blob fetch.
- [x] 3.2 Open round-trip: `fs.open(path).read()` equals published bytes; a `seek` then partial read returns the correct slice.
- [x] 3.3 Synthetic-directory behavior: intermediate prefixes list as directories; nested `ls` returns immediate children only.
- [x] 3.4 Write rejection: `fs.open(path, "wb")`, `fs.mkdir`, `fs._rm` all raise read-only.
- [x] 3.5 `checkout` lays out logical tree under a chosen dir and writes nothing outside it; re-checkout reuses the cache (no re-download).
- [x] 3.6 Property test (Hypothesis): for a random fileset, the set of `fs.find("/")` paths equals the published paths and each opens to its bytes.
- [x] 3.7 Optional interop test guarded by `pytest.importorskip("pyarrow")`: write a parquet artifact, publish it, and read it back via `pq.read_table(path, filesystem=fs)`.

## 4. Gates

- [x] 4.1 `pyright` clean, `ruff` clean, full test suite green.
- [x] 4.2 `openspec validate add-snapshot-filesystem` passes.
