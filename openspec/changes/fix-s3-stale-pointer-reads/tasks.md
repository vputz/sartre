## 1. Refresh the mutable listings on read paths

- [x] 1.1 `_resolve_ref` (Head/Alias): call `_pointer_scan(coord, name, refresh=True)` so a resolve re-lists the pointer stream.
- [x] 1.2 `list_pointers`: re-list the `pointers/` directory and scan each pointer with `refresh=True`.
- [x] 1.3 `_all_events`: re-list the `pointers/` directory and each pointer prefix with `refresh=True` (covers `list_versions`, `list_log`, `list_pointer_history`).
- [x] 1.4 Tombstone freshness on reads: ensure `_pointer_scan`'s tombstone skip and `list_versions`' `_tombstoned_versions` see current tombstones (invalidate the tombstone path/namespace on the resolve path), so a long-lived reader honors a concurrent GC and never resolves a reclaimed manifest.
- [x] 1.5 Confirm the write/CAS paths and immutable content reads (manifest/blob fetches) are unchanged — only mutable listings refresh.

## 2. Regression tests (moto, one process, shared fsspec instance)

- [x] 2.1 Two `S3Registry`/`Repository` handles over one moto bucket (same `url_to_fs` instance): resolve head with A, publish with B, resolve head with A again — A sees the new version. Assert it fails against the pre-fix code path (e.g. without the refresh) and passes after.
- [x] 2.2 `list_pointers` on handle A reflects a pointer advanced by handle B after A's first read.
- [x] 2.3 Pin: a version published by B after A's first resolve is resolvable by `Pin` on A (not `NotFound`).
- [x] 2.4 Tombstone: a version B drops after A's first read is honored by A's next resolve (skips to last valid / `NotFound`, never the reclaimed manifest).

## 3. Gates

- [x] 3.1 `ruff` clean, `pyright` clean, full default suite green (incl. existing S3 tests).
- [x] 3.2 `openspec validate fix-s3-stale-pointer-reads --strict` passes.
- [x] 3.3 Note in `design.md` whether the existing `S3Drop`/`PointerDrop` models need any wording about live-tail reads (expected: no model change; they already assume it).
