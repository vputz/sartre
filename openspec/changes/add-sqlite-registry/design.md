## Context

`MemoryRegistry` realizes the manifest plane in three in-process structures (manifests
by content hash, a pointer table, an append-only per-coordinate log) plus an in-memory
lease map, all under one lock. It is the TLA-backed reference but is ephemeral. This
change ports the same semantics onto SQLite so the manifest plane is durable and
transactional, without changing the `Registry` port.

## Goals / Non-Goals

**Goals:**
- `SqliteRegistry` implementing the full `registry-port` contract, durable across
  restarts, with atomic compare-and-swap `set_pointer`.
- Zero new dependencies (stdlib `sqlite3`).
- Verified by differential property testing against `MemoryRegistry` (the reference),
  not by re-modelling in TLA+.
- `open_local(path)` for a one-directory persistent repository.

**Non-Goals:**
- Multi-node / Postgres registry, connection pooling.
- Cross-process/persistent leases and lease TTLs (leases are process-scoped here;
  reclaiming a dead process's protection is the deferred grace-period work).
- Schema migrations / versioned schema evolution.

## Decisions

### D1: Schema
- `manifests(version TEXT PRIMARY KEY, metadata TEXT, created_at TEXT)` — `metadata` is
  JSON, `created_at` is an ISO-8601 UTC string.
- `entries(version TEXT, path TEXT, content_hash TEXT, size INTEGER, inline BLOB,
  PRIMARY KEY (version, path))` — normalized so `inline` bytes are stored natively (no
  base64). Read back `ORDER BY path`.
- `pointers(coord_name TEXT, coord_env TEXT, name TEXT, version TEXT,
  PRIMARY KEY (coord_name, coord_env, name))`.
- `log(seq INTEGER PRIMARY KEY AUTOINCREMENT, coord_name, coord_env, version, pointer,
  created_at)` — `seq` (a global autoincrement) is the authoritative total order; a
  coordinate's order is that filtered by `coord_*`.

`commit` is content-idempotent via `INSERT OR IGNORE` on `manifests`/`entries` keyed by
the content-hash `version` (`manifest_version(entries)`), so a re-commit is a no-op that
returns the same version.

### D2: Transactions and concurrency
Open with `check_same_thread=False` and `isolation_level=None` (autocommit), and guard
all access with a `threading.Lock` so in-process callers serialize on one connection.
Multi-statement mutations (`commit`, `set_pointer`, `drop_version`) run inside an
explicit `BEGIN IMMEDIATE … COMMIT` (rollback on error), which takes SQLite's reserved
write lock up front — so the compare-and-swap read-then-write in `set_pointer` is atomic
even against another process. `set_pointer` reads the current pointer, raises `Conflict`
if it differs from `expected` (with `expected=None` meaning "must not exist"), else
inserts/updates the pointer and appends the log row — all in one transaction.

### D3: Leases are in-memory and process-scoped
Leases are ephemeral coordination state, not durable facts, so `SqliteRegistry` holds
them in an in-memory map under the same lock — exactly like `MemoryRegistry`. This makes
them process-scoped by construction: a fresh process starts with no leases, so a crashed
process never leaves permanently-held protection in the database. Cross-process lease
coordination (persistent leases + TTL) is deferred to the grace-period work.

### D4: `resolve` entry order is normalized, not contractual
A manifest is a set of `(path, content_hash)` entries; the version is order-independent
(`manifest_version` sorts). `SqliteRegistry.resolve` returns entries `ORDER BY path`.
The differential test compares snapshots on `sorted(entries)` so the two backends agree
regardless of the order entries were passed to `commit`.

### D5: Verification by differential testing, not a new model
The publish/pointer/GC semantics are already proven abstractly (`Publish.tla`,
`GC.tla`) and realized correctly by `MemoryRegistry` (property-tested). Rather than
re-model SQLite, a Hypothesis `RuleBasedStateMachine` applies the *same* random
operation to both a `MemoryRegistry` and a `SqliteRegistry` (`:memory:` db) and asserts,
per step, equal return values and equivalent raised errors. Equivalence to a verified
reference is the correctness argument. A separate durability test uses a file-backed db:
publish, drop the connection, reopen, assert state recovered.

### D6: `open_local(path)`
`open_local(path)` creates `path/`, wires `SqliteRegistry(path/"registry.db")` and
`CasStore(FsspecBlobBackend(LocalFileSystem(), str(path/"blobs")))` into a `Repository`.
Reopening the same path recovers published state (durable registry) and reuses the blob
tree. It is the tangible payoff: a persistent repository in one directory.

## Risks / Trade-offs

- **Single connection + lock serializes registry throughput** → fine for a single-node
  registry; the blob plane (the heavy I/O) still parallelizes. Postgres/pooling is the
  scale path, deferred.
- **`BEGIN IMMEDIATE` can raise "database is locked" under multi-process contention** →
  acceptable and correct (it surfaces as a retryable error); single-process use never
  hits it. Retry/backoff is future hardening.
- **In-memory leases don't coordinate across processes** → documented Non-Goal; GC
  serialization is already the caller's responsibility, and single-process publish/GC is
  fully protected.

## Open Questions

- None blocking. Postgres, persistent leases/TTL, and migrations are explicit future work.
