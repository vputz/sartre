## 1. Schema & connection

- [x] 1.1 Create `src/sartre/sqlite.py` with `SqliteRegistry(db_path, hasher=DEFAULT_HASHER)`: open with `check_same_thread=False`, `isolation_level=None`; a `threading.Lock`; an in-memory lease map.
- [x] 1.2 Create the schema on open (idempotent `CREATE TABLE IF NOT EXISTS`): `manifests`, `entries`, `pointers`, `log` per design D1.
- [x] 1.3 Add a private `_tx()` context manager running `BEGIN IMMEDIATE … COMMIT` (rollback on error) under the lock, and a `close()` method.

## 2. Read surface

- [x] 2.1 `head(coord, ref)`: resolve `Head`/`Alias` via `pointers`; `Pin` requires the version to appear in the coordinate's log (else `NotFound`); missing pointer → `NotFound`.
- [x] 2.2 `resolve(coord, ref)`: join manifest + entries (`ORDER BY path`), rebuild `Snapshot` (parse `metadata` JSON, `created_at` ISO string, native `inline` bytes).
- [x] 2.3 `list_pointers(coord)`, `list_versions(coord)` (distinct versions in log/seq order), `list_coordinates()`, `list_log(coord)` (`LogEntry` in seq order).

## 3. Write surface

- [x] 3.1 `commit(coord, entries, metadata)`: compute `manifest_version`; `INSERT OR IGNORE` manifest + entries in a `_tx()`; return the version (content-idempotent).
- [x] 3.2 `set_pointer(coord, name, version, *, expected)`: in a `_tx()`, read current; raise `Conflict` on mismatch; `NotFound` if version not committed; insert/update pointer and append the log row.
- [x] 3.3 `drop_version(version)`: in a `_tx()`, raise `Conflict` if any pointer targets it; delete its log rows across all coordinates and its manifest+entries; idempotent.

## 4. Lease surface & convenience

- [x] 4.1 `acquire_lease(version, hashes)`/`release_lease`/`active_leased_hashes`/`active_leased_versions`: in-memory map under the lock (process-scoped, not persisted), mirroring `MemoryRegistry`.
- [x] 4.2 Add `open_local(path) -> Repository` (in `sartre/local.py` or `repository.py`) wiring `SqliteRegistry` + `CasStore(FsspecBlobBackend(LocalFileSystem, path/blobs))`.
- [x] 4.3 Re-export `SqliteRegistry` and `open_local` from `src/sartre/__init__.py`.

## 5. Tests

- [x] 5.1 `tests/test_sqlite_registry.py`: targeted cases — commit idempotency, `set_pointer` CAS success/conflict, `Pin`/`NotFound`, `drop_version` refuses a pointer target and prunes logs, lease accessors.
- [x] 5.2 Durability round-trip: file-backed `SqliteRegistry`, publish, `close()`, reopen on the same file, assert pointers/versions/manifests recovered; leases empty after reopen.
- [x] 5.3 `open_local(path)` end-to-end: publish, drop the repo, `open_local(path)` again, resolve + read blobs from the reopened directory.
- [x] 5.4 Differential Hypothesis stateful machine: apply identical random `commit`/`set_pointer`/`drop_version`/lease/read sequences to a `MemoryRegistry` and a `SqliteRegistry(:memory:)`; assert equal results and equivalent raised errors per step (compare snapshots on `sorted(entries)`).

## 6. Gates

- [x] 6.1 `pyright` clean, `ruff` clean, full test suite green.
- [x] 6.2 `openspec validate add-sqlite-registry` passes.
