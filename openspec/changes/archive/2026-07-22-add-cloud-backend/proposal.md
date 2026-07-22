## Why

`SqliteRegistry` gives a durable single-node repository, but a cloud deployment needs a
**shared, multi-writer** manifest plane — many machines publishing and promoting against
one registry. The blob plane is already cloud-ready (any fsspec filesystem, including S3,
with crash-safe atomic writes). This change adds the shared registry (Postgres) and the
convenience to assemble a cloud repository, reusing the SQL logic already proven against
the in-memory reference.

## What Changes

- Refactor `SqliteRegistry` into a shared `_SqlRegistry` base holding all query logic;
  `SqliteRegistry` and a new `PostgresRegistry` become thin subclasses differing only in
  dialect (parameter placeholder, `seq` DDL, connection, transaction).
- **Compare-and-swap as a conditional write** (replaces read-then-write): `set_pointer`
  advances via `UPDATE … WHERE version = :expected` (or `INSERT … ON CONFLICT DO NOTHING`
  for `expected = None`) and treats "no row affected" as a conflict. Atomic under
  concurrent multi-writer contention with no read-modify-write window. Portable across
  SQLite and Postgres; the existing differential machine guards the refactor.
- `PostgresRegistry(dsn)` via `psycopg` (new optional `postgres` extra), implementing the
  full `registry-port` contract on a shared/multi-writer database.
- `open_cloud(pg_dsn, blob_url, *, cache_dir=None, storage_options=None) -> Repository`:
  `PostgresRegistry` + a content-addressed `Store` over the blob URL's fsspec filesystem
  (e.g. `s3://…`), optionally behind a `CachingStore` with a local on-disk cache.
- Verification:
  - The **differential Hypothesis machine** runs against a **real dockerized Postgres**
    (skipped when docker/`psycopg` are unavailable), proving `PostgresRegistry` is
    observationally equivalent to `MemoryRegistry`.
  - S3 blobs validated against a **moto-mocked S3** (added to dev), exercising
    `FsspecBlobBackend` + atomic put over real object-store semantics.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `persistent-registry`: generalize from SQLite-only to a durable SQL registry with
  `SqliteRegistry` (single-node) and `PostgresRegistry` (shared/multi-writer)
  implementations; add the multi-writer compare-and-swap guarantee and the `open_cloud`
  convenience.

## Impact

- Code: `src/sartre/sqlite.py` → shared `_SqlRegistry` base + `SqliteRegistry`; new
  `src/sartre/postgres.py` (`PostgresRegistry`); `src/sartre/local.py` (or a new
  `cloud.py`) for `open_cloud`; `src/sartre/__init__.py` re-exports.
- Deps: new optional `postgres` extra (`psycopg[binary]`); dev adds `moto`, `s3fs`.
- Tests: extend `tests/test_sqlite_registry.py`'s differential machine to also drive a
  Postgres backend behind a docker fixture; new `tests/test_s3_blobs.py` (moto);
  `open_cloud` smoke test.
- Non-goals: object-store-native registry (conditional-PUT on S3), connection pooling,
  schema migrations, and cross-process lease persistence/TTL — future work.
- No breaking changes: additive; `SqliteRegistry`/`open_local` behavior is unchanged
  (the CAS refactor is observably identical, verified by the differential machine).
