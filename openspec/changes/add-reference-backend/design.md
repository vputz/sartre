## Context

The ports, version identity, and publish protocol are specified and (for publish)
TLA+-verified, but nothing runs. This change provides the first concrete
implementation — in-memory — to validate the composition and re-check the publish
invariants against real code with property-based tests.

## Goals / Non-Goals

**Goals:**
- A runnable `head → resolve → open → fetch_all → publish` over in-memory storage.
- Real `CasStore`/`CachingStore`/`FsspecBlobBackend` bodies, backend-agnostic.
- Property-based verification mirroring the publish-transaction TLA+ model.

**Non-Goals:**
- `SnapshotFS` / checkout (follow-up).
- Persistence: local-filesystem and Delta/S3 backends (later, same ports).
- Garbage collection.

## Decisions

### D1 — Blob in-memory via fsspec memory + `FsspecBlobBackend`
Implement `FsspecBlobBackend` for real and run it over an fsspec memory
filesystem, rather than a one-off dict backend — this exercises the designed
"any fsspec fs is a backend" path from day one. Test isolation is by a unique root
prefix per test (the memory fs store is process-global).

### D2 — `MemoryRegistry` realizes the manifest plane with three structures
- `pointers: dict[(name, env, pointer) -> version]` — current tips; `head` reads
  one entry (cheap-`head`).
- `manifests: dict[version -> tuple[Entry, ...]]` — content-addressed by
  `manifest_version`, so `commit` is idempotent and dedup is structural.
- `log: list[record]` per coordinate — append-only `(seq, version, created_at,
  pointer, metadata)`; `list_versions` reads it in order.
`commit` + `set_pointer` together model the atomic "append log row + CAS pointer."
A lock makes the registry's mutations atomic in-process (the stand-in for Delta's
transactional commit).

### D3 — `CachingStore` concurrency: per-hash lock + atomic write
`get_to` takes a lock keyed by content hash so concurrent fetches of the same blob
download once; the cache file is written to a temp path then atomically renamed.
`open` returns a handle on the materialized, verified local file (so random access
is a local seek). Verify-on-download only; the cache is trusted thereafter.

### D4 — `Repository.publish` = the verified protocol, fail-fast
`start = head(...)`; `store.put` each blob (skip if `has`); `version =
commit(...)`; `set_pointer(version, expected=start)`. On `Conflict`, raise — no
retry, no clobber. `fetch_all` fans entries across a `ThreadPoolExecutor`
(blob I/O releases the GIL); a single shared `CachingStore` makes concurrent
same-blob fetches safe via D3.

### D5 — Property-based tests mirror the TLA+ model
Plain Hypothesis for the algebra (`manifest_version` permutation-invariance,
publish→resolve round-trip, cross-version cache reuse). A `RuleBasedStateMachine`
drives random `publish`/`resolve`/`promote`/(simulated) `crash` sequences across a
few coordinates and asserts the model's invariants on the live system: the tip is
always a committed manifest with all blobs present (no dangling tip), concurrent
publishes leave exactly one winner, and a retried (crashed) publish converges.

## Risks / Trade-offs

- **fsspec memory fs is process-global** → cross-test bleed. Mitigation: unique
  root prefix per test (D1); a fixture can also clear the store.
- **In-process "atomicity" via a lock is not the real backend** → the lock models
  Delta's transaction; the contracts (atomic resolve, CAS) are what the property
  tests check, so a future Delta backend is judged against the same invariants.
- **Simulated crash in tests is not a real process kill** → modelled as
  "abandon a publish partway and restart it," which is the interleaving the TLA+
  `Crash` action abstracts; sufficient to exercise idempotent convergence.

## Open Questions

- Should `MemoryRegistry` expose the as-of/time-travel query now, or defer until a
  consumer needs it (it is contract-specified but unused by the read core)?
- Is a tiny `MemoryBlobBackend` (dict) worth adding alongside `FsspecBlobBackend`
  for the simplest tests, or is the fsspec-memory path enough?
