## Context

`SqliteRegistry` implements the full registry-port on SQLite with a per-connection lock
and `BEGIN IMMEDIATE` transactions; it is verified observationally equivalent to the
TLA-backed `MemoryRegistry` by a differential Hypothesis machine. A cloud deployment needs
the same manifest plane on a **shared, multi-writer** database. The blob plane already
works on S3 (any fsspec filesystem via `FsspecBlobBackend`, with crash-safe atomic put),
so the new work is a Postgres registry and the assembly convenience — reusing the SQL
logic rather than rewriting it.

## Goals / Non-Goals

**Goals:**
- One shared SQL registry implementation, specialized per dialect (`SqliteRegistry`,
  `PostgresRegistry`).
- Atomic multi-writer compare-and-swap.
- `open_cloud` assembling a Postgres + object-store repository.
- Real validation: differential machine vs. a dockerized Postgres; S3 blobs vs. moto.

**Non-Goals:**
- Object-store-native registry (pointers/log as S3 objects via conditional PUT).
- Connection pooling, async drivers, schema migrations/versioning.
- Cross-process lease persistence/TTL (leases stay process-scoped, as in `SqliteRegistry`).

## Decisions

### D1: Extract a shared `_SqlRegistry` base; dialect is a thin seam
All query logic (schema, reads, `commit`, `set_pointer`, `drop_version`, enumeration) moves
to `_SqlRegistry`. Subclasses supply only:
- `_PLACEHOLDER` — `"?"` (sqlite) or `"%s"` (psycopg); the base translates its `?`-style
  SQL via a trivial `_q()` (our SQL contains no literal `?`).
- `_SEQ_DDL` — `INTEGER PRIMARY KEY AUTOINCREMENT` (sqlite) vs `BIGSERIAL PRIMARY KEY`
  (postgres) for the `log.seq` column; the rest of the schema is identical.
- `_connect()` and a `_tx()` context manager (sqlite: autocommit + `BEGIN IMMEDIATE`;
  postgres: `psycopg` connection with `autocommit=True` for reads and an explicit
  `transaction()` block for write groups).
- Lease map, lock, and all method bodies live in the base. `created_at` is an ISO-8601
  string in a `TEXT` column on both dialects (portable, avoids driver datetime quirks).

### D2: Compare-and-swap as a conditional write
Replace `set_pointer`'s read-current-then-write with a single conditional statement,
correct under multi-writer contention without a read-modify-write window:
- `expected is None`: `INSERT INTO pointers(...) VALUES (...) ON CONFLICT (coord_name,
  coord_env, name) DO NOTHING`; if it affected 0 rows the pointer already exists → conflict.
- `expected` set: `UPDATE pointers SET version=? WHERE coord+name AND version=?`; if it
  affected 0 rows the current value differs from `expected` (or the row is absent) →
  conflict.

Both run inside the write transaction alongside the manifest-exists check and the log
append. `ON CONFLICT DO NOTHING` and conditional `UPDATE` are supported identically by
SQLite and Postgres. This also *strengthens* `SqliteRegistry` (no read-then-write), and
the existing differential machine proves the behavior is unchanged.

### D3: `PostgresRegistry(dsn)` via psycopg 3
`psycopg` (v3, the maintained driver) in a new optional `postgres` extra. `autocommit=True`
connection; `RLock` for in-process serialization mirroring `SqliteRegistry`; `_tx()` uses
`conn.transaction()`. `rowcount` on the cursor drives the CAS decision, same as sqlite3.
Leases stay in-memory/process-scoped (Non-Goal to persist).

### D4: `open_cloud` assembly and caching
`open_cloud(registry_dsn, blob_url, *, cache_dir=None, storage_options=None)`:
`fsspec.core.url_to_fs(blob_url, **storage_options)` yields the filesystem + root; wrap in
`FsspecBlobBackend` → `CasStore` (the remote). With `cache_dir`, wrap as
`CachingStore(local=CasStore(FsspecBlobBackend(local_fs, cache_dir)), remote=remote)` — the
exact composition the caching layer was designed for (local disk cache over S3). Registry is
`PostgresRegistry(registry_dsn)`.

### D5: Real validation, gated on availability
- **Postgres**: a pytest fixture starts a throwaway Postgres container
  (`docker run -d postgres:16 …`), waits for readiness, yields a DSN, and tears it down.
  The differential machine is parametrized to also run against `PostgresRegistry(dsn)`.
  The whole group is `skipif` docker or `psycopg` is unavailable, so the default suite
  stays green without them.
- **S3**: `moto`'s mock S3 (added to dev) + `s3fs`; a test drives `FsspecBlobBackend`
  over `s3://` and asserts atomic put / list / round-trip on real object-store semantics.

## Risks / Trade-offs

- **Docker/psycopg not present in CI** → Postgres tests skip; the shared base is still
  fully covered by the always-on SQLite differential machine, so the dialect seam is the
  only unverified surface when skipped. Mitigation: the seam is tiny and is exercised
  whenever docker is available (it is, here).
- **`ON CONFLICT` / conditional-UPDATE rowcount semantics** differ subtly across drivers →
  Mitigation: both are checked by the differential machine (SQLite always, Postgres when
  available).
- **In-memory leases don't coordinate across processes** in a shared deployment →
  documented Non-Goal; GC serialization is the caller's responsibility and single-process
  publish/GC stays fully protected. Persistent leases + TTL are the follow-up.

## Open Questions

- None blocking. Object-store-native registry, pooling, migrations, and persistent leases
  are explicit future work.
