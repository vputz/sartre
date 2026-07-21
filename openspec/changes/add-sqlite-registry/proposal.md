## Why

The only `Registry` is `MemoryRegistry` — in-process and ephemeral, gone on restart.
Nothing about the manifest plane survives a process exit, so there is no real
deployment. The blob plane is already durable (any fsspec filesystem) and now
crash-safe. This change adds the missing half: a durable, transactional `Registry`,
giving the first restart-survivable repository.

## What Changes

- New `SqliteRegistry` (stdlib `sqlite3`, zero new dependencies) implementing the full
  `registry-port` contract exactly as specified — `head`, `resolve`, `list_pointers`,
  `list_versions`, `commit`, `set_pointer` (CAS), `list_coordinates`, `list_log`,
  `drop_version`, and the lease surface (`acquire_lease`/`release_lease`/
  `active_leased_hashes`/`active_leased_versions`).
- Schema: `manifests` + a normalized `entries` table (native `inline` bytes),
  `pointers` (CAS via `UPDATE … WHERE version IS :expected`), an append-only `log`
  (autoincrement `seq` = authoritative order), and `lease` + `lease_hash` tables.
- Transactional integrity: `BEGIN IMMEDIATE` around compare-and-swap so a stale
  `set_pointer` conflicts correctly under concurrent/multi-process writers; a process
  lock serializes in-process access with `check_same_thread=False`.
- Durability: opening a `SqliteRegistry` on an existing database file recovers all
  pointers, log, manifests, and (transient) leases are not persisted across process
  restart by design.
- `sartre.open_local(path)` convenience: wires `SqliteRegistry(path/registry.db)` +
  `CasStore(FsspecBlobBackend(local_fs, path/blobs))` into a persistent single-node
  `Repository` living in one directory.
- Verification: a **differential** Hypothesis stateful machine driving identical random
  operation sequences against both `MemoryRegistry` and `SqliteRegistry` and asserting
  identical observable results and raised errors, plus a durability test (write, close,
  reopen, state survives).

## Capabilities

### New Capabilities
- `persistent-registry`: a durable, transactional `Registry` implementation and its
  guarantees — behavioral equivalence to the in-memory reference, transactional CAS,
  and cross-restart durability of the manifest plane. (SQLite now; Postgres later fits
  under the same capability.)

### Modified Capabilities
<!-- none: SqliteRegistry implements the existing registry-port contract unchanged -->

## Impact

- Code: new `src/sartre/sqlite.py` (`SqliteRegistry`); `src/sartre/__init__.py`
  (`SqliteRegistry`, `open_local`); a small `open_local` factory (likely in
  `repository.py` or a new `local.py`).
- Tests: new `tests/test_sqlite_registry.py` — the differential stateful machine, a
  durability round-trip, and targeted CAS/lease/drop cases; an `open_local` end-to-end
  test.
- Dependencies: none (stdlib `sqlite3`).
- Non-goals: multi-node/Postgres registry, connection pooling, lease persistence/TTL
  across restarts, and schema migrations — future work.
- No breaking changes: additive; `MemoryRegistry` and the port are unchanged.
