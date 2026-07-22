## 1. Shared SQL base

- [x] 1.1 Refactor `src/sartre/sqlite.py`: extract `_SqlRegistry` holding all query logic (schema, reads, `commit`, `set_pointer`, `drop_version`, enumeration, lease map, lock). Subclass hooks: `_PLACEHOLDER`, `_SEQ_DDL`, `_connect()`, `_tx()`, and a `_q()` placeholder translator.
- [x] 1.2 `SqliteRegistry(_SqlRegistry)`: `_PLACEHOLDER="?"`, autoincrement DDL, `sqlite3` connect (`check_same_thread=False`, autocommit), `_tx()` = `BEGIN IMMEDIATE`/`COMMIT`.
- [x] 1.3 Rewrite `set_pointer` CAS as a conditional write (D2): `INSERT … ON CONFLICT DO NOTHING` for `expected=None`, `UPDATE … WHERE version=?` otherwise; 0 rows affected → `Conflict`. Keep the manifest-exists `NotFound` check and log append in the same `_tx()`.

## 2. Postgres backend

- [x] 2.1 Add optional `postgres` extra (`psycopg[binary]`) to `pyproject.toml`; add `moto` and `s3fs` to the dev group.
- [x] 2.2 New `src/sartre/postgres.py`: `PostgresRegistry(_SqlRegistry)` — `_PLACEHOLDER="%s"`, `BIGSERIAL` seq DDL, `psycopg.connect(dsn, autocommit=True)`, `_tx()` = `conn.transaction()`. Guard the import so the module errors clearly if `psycopg` is absent.
- [x] 2.3 Re-export `PostgresRegistry` from `__init__.py` (lazy/optional if psycopg missing — import inside a try or a thin shim).

## 3. Cloud convenience

- [x] 3.1 Add `open_cloud(registry_dsn, blob_url, *, cache_dir=None, storage_options=None) -> Repository` (in `sartre/cloud.py`): `url_to_fs(blob_url)` → `FsspecBlobBackend` → `CasStore` remote; wrap in `CachingStore` with a local on-disk cache when `cache_dir` is set; `PostgresRegistry(registry_dsn)`.
- [x] 3.2 Re-export `open_cloud` from `__init__.py`.

## 4. Tests

- [x] 4.1 Regression: the existing SQLite differential machine and all registry tests pass unchanged after the CAS refactor (behavior identical).
- [x] 4.2 `tests/` Postgres fixture: start a throwaway `postgres:16` container via docker, wait for readiness, yield a DSN, tear down; `skipif` docker or `psycopg` unavailable.
- [x] 4.3 Run the differential Hypothesis machine against `PostgresRegistry(dsn)` (parametrized/gated by the fixture); assert equivalence to `MemoryRegistry`.
- [x] 4.4 `tests/test_s3_blobs.py`: with `moto` mock S3 + `s3fs`, drive `FsspecBlobBackend` over `s3://bucket/prefix` — put/get/list/atomic-put round-trip; `skipif` moto/s3fs unavailable.
- [x] 4.5 `open_cloud` smoke: assemble against the docker Postgres + a mock/memory blob URL, publish + resolve + read; gated on availability.

## 5. Gates

- [x] 5.1 `pyright` clean, `ruff` clean, full default test suite green (Postgres/S3 groups skip cleanly without docker/psycopg/moto).
- [x] 5.2 Run the Postgres + S3 groups locally (docker available) to confirm they pass, not just skip.
- [x] 5.3 `openspec validate add-cloud-backend` passes.
