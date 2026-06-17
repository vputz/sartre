## Why

`add-core-ports` left the version-id format deliberately open, marking
`Registry.commit` content-idempotency as TBD. That single decision shapes
`commit`, `head`'s change-detection, dedup, cross-env promotion, and garbage
collection — and it gates the publish-transaction and GC work that comes next.
We have settled it: a version is the **content hash of its manifest**, and
ordering/history lives in a **separate append-only log** rather than in the id.

## What Changes

- Define `Version` as the **content hash of the canonical manifest entries** —
  self-describing like blob keys, hashed over the sorted `(path, content_hash)`
  pairs only, **excluding** `size`, `inline`, metadata, and the coordinate.
  (Those are derived from, or decoration around, the content.)
- Make `Registry.commit` **content-idempotent**: committing identical entries
  returns the same `Version`. This un-marks the TBD and makes the publish path
  idempotent end-to-end (idempotent blobs → idempotent manifest → CAS pointer).
- Separate **identity from ordering**: the mutable pointer holds the current tip
  (keeping `head` cheap); a new append-only **commit log** records tip events
  with a monotonic `seq` (authoritative order) and `created_at` (time queries).
- `list_versions` returns versions in **commit-log order**; an **as-of** query
  over the log resolves "the version a pointer pointed at, at/just before time
  T" to a `Version` the caller can then `Pin`. (Per memo §3, dates remain data
  used by an ops query — they are not a core addressing axis; `resolve` stays
  `Head`/`Alias`/`Pin`.)
- Confirm **env-in-coordinate** with **promotion = repoint**: because the id is
  content-derived and coordinate-independent, the same files have the same
  version in every env, so promotion appends a log row and CAS-moves the pointer
  at an already-global manifest hash — no copy.
- Add a concrete, tested `manifest_version(entries)` helper computing the
  canonical hash.

## Capabilities

### New Capabilities
- `version-log`: the append-only, per-coordinate commit log — `seq` + `created_at`
  tip events — that provides ordered `list_versions`, as-of time-travel, and the
  reachability source GC will later read; plus promotion-as-repoint semantics.

### Modified Capabilities
- `artifact-model`: `Version` is no longer an unspecified opaque id; it is the
  content hash of the canonical manifest entries, with a defined hashing boundary.
- `registry-port`: `commit` becomes content-idempotent; `list_versions` is
  ordered by the commit log.

## Impact

- **New code**: `manifest_version()` (in `sartre.hashing` or `sartre.model`) plus
  tests; docstring/contract updates in `ports.py` (un-TBD `commit`) and the
  `Version` alias in `model.py`.
- **No backend yet**: the commit-log table shape, the as-of query, and promotion
  are specified as contracts; they are exercised once a Registry backend exists.
- **Schema note (design only, §7.6)**: pointer/log tables key on `(name, env, …)`
  as two columns, not a composite string.
- **Deferred (explicit non-goals)**: the publish transaction that makes
  "append log row + CAS pointer" atomic (next change, first TLA+ target) and
  garbage collection.
