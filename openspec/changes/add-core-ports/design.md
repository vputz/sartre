## Context

Sartre is a greenfield content-addressed binary artifact repository (see `binary-artifact-repo-design.md`). The memo specifies a read core (`head`/`resolve`/`open`) over two planes — a Delta manifest plane and an S3 blob plane — but leaves the interface boundaries, identity format, path model, and concurrency model open (memo §7). This change pins those as **pure interfaces and contracts**, with no backend implementations, so the design can be validated against a local-filesystem stub before committing to Delta/S3.

Naming convention settled in discussion: the product/category is a **repository**; internally the name-resolution interface is a **registry** and the CAS backend is a **store**.

## Goals / Non-Goals

**Goals:**
- A maximally abstract port boundary: implementing a new backend should be a handful of dumb methods, with all CAS/manifest semantics centralized.
- Capture the identity, path, and concurrency decisions that ossify the public surface, with rationale, before code depends on them.
- First-class read-only filesystem access to a version (logical filenames, lazy blob fetch) and whole-tree checkout.
- A sync core that still parallelizes large multi-file downloads.

**Non-Goals (explicitly deferred):**
- Delta and S3 adapter implementations.
- The publish-transaction crash-safe ordering protocol (blobs → manifest → pointer) and concurrent-publisher conflict handling beyond the CAS primitive — its own change, a TLA+ candidate.
- Garbage collection / retention policy — its own change, a TLA+ candidate.
- The **version-id format** decision (random vs monotonic vs manifest-content-hash). It leaves `Registry.commit` content-idempotency marked TBD here.
- Inline-vs-S3 byte threshold tuning, metadata typing, and the env/cross-env-promotion model (env stays in the Coordinate for now).

## Decisions

### D1 — Two ports + a dumb sub-port; Repository composes them
`Registry` (manifest plane, never sees bytes) and `Store` (CAS, never sees names/versions). `Repository` composes one of each and exposes the read/publish surface. The blob plane is further split: a dumb `BlobBackend` (opaque key/value: `get`/`put`/`exists`/`delete`) and a generic `CasStore(backend, hasher)` that adds hashing and verify **once** over any backend.
- *Why*: a new storage target becomes four trivial KV methods; identity/verify logic isn't duplicated per backend. Matches the memo's "S3 = a dumb bag of bytes."
- *Alternatives*: a single fat `Store` port (rejected — pushes hashing into every adapter); abstracting only at `Repository` and hardcoding Delta+S3 (rejected — blocks the local stub and testing).

### D2 — The local cache is itself a `Store`
`CachingStore(local: Store, remote: Store)`: `get_to` serves from local on hit, else pulls remote and populates local. The memo's "client cache keyed by content hash" (§4.2) *is* the Store contract, so cross-version dedup on reads falls out of composition rather than a bespoke cache layer.
- *Alternative*: a separate `Cache` abstraction (rejected — redundant; a content-addressed cache and a content-addressed store have the same interface).

### D3 — Self-describing content hashes, pluggable `Hasher`
Keys are `"algo:digest"` (e.g. `sha256:…`), multihash-style. Default **sha256** (stdlib, FIPS-approved, no native dep, LFS-adjacent); **blake3** opt-in later for blob-heavy deployments (parallel tree hashing wins on large files). Each key self-declares its algorithm, so old and new algorithms coexist in one manifest without rewrites.
- *Why*: defuses the "hash is baked into every key forever" lock-in; integrity-for-free and idempotent writes from content addressing (memo §4.1).
- *Trade-off*: dedup is per-algorithm (identical bytes under two algos are two keys) — pick one algo per deployment; the format just spares a future migration.

### D4 — Stream/path-oriented Store API; verify on download only
`Store` methods are `has`/`open(BinaryIO)`/`get_to(Path)`/`put(stream)→Hash`/`delete` — never `bytes`-only, because checkpoints are 100 MB–1 GB. Integrity is verified **on download into the cache**, then the content-addressed cache is trusted; `open()` returns a real local file handle so random-access reads (parquet footers, `torch.load`) just seek a local file.
- *Why*: you cannot verify a whole-file hash from a partial/range read; materialize-then-serve resolves the fsspec random-access vs verify tension cleanly.

### D5 — fsspec on both ends
- *Bottom*: `FsspecBlobBackend(fs, root)` implements `BlobBackend` over any fsspec filesystem → free `s3fs`/`gcsfs`/local/`memory` backends.
- *Top*: `SnapshotFS` is a read-only `AbstractFileSystem` over a resolved `Snapshot` — `ls`/`info` come from the manifest (no blob fetch; the cheap-catalog win), `_open` maps path → hash → `Store`. This yields lazy virtual access, `fs.get(recursive=True)` checkout, FUSE mount, `sartre://` URLs, and `get_mapper`, all for free, and works directly with pandas/polars/pyarrow/torch.
- *Why*: fsspec is the de-facto Python filesystem abstraction; bookending with it removes most adapter and presentation code.
- *Dependency*: `fsspec` core is small/pure-Python; heavy backends stay in extras.

### D6 — Sync core; parallelism is a facade concern
The ports are sync and single-item. Parallel multi-file downloads come from a `ThreadPoolExecutor` fan-out in `fetch_all`/`checkout` — blob I/O releases the GIL, so threads genuinely overlap (as in `aws s3 cp`, git-lfs). An optional `BlobBackend.get_many()` hook delegates to fsspec's native concurrent `cat` when available. Async apps get a thin `AsyncRepository` wrapping the sync core via `asyncio.to_thread` — no second implementation.
- *Why*: parallelism ≠ async; threads cover the throughput need without an async-native core, keeping ports trivial to implement.
- *Alternative*: async-native core (rejected — buys nothing for I/O-bound fan-out, doubles surface area, complicates every adapter).

### D7 — Canonical path model, enforced at write time
A single `normalize_path()` is the source of truth, applied when a path enters a manifest and by `SnapshotFS`:
- `/` separator only; relative (no leading/trailing slash; collapse `//`).
- **Reject `.` and `..` segments** — eliminates Zip-Slip/tar-traversal arbitrary-write at the source rather than resolving them.
- Unicode → **NFC**.
- **Case-sensitive identity**, but **reject case-only collisions within a single manifest** (case-fold the whole path incl. directory components; folded set cardinality must equal raw set). Guarantees every valid manifest is checkout-safe on case-insensitive filesystems (APFS, NTFS) without being lossy about case-distinct source files.
- Reject NUL/control chars; conservative portable charset with a posix-only escape hatch.
- **Coerce** the unambiguous (separators, NFC, leading slash, `//`); **reject** the dangerous/ambiguous (`..`, case-collisions, NUL) — coercing the latter would silently change meaning.
- *Why*: a manifest path is simultaneously a logical join key (diffing, blob lookup) and a real filesystem path (checkout/mount); pinning one canonical form at write time keeps diff/FS-view/checkout simple and avoids a path migration later.

### D8 — Atomicity & concurrency as documented contracts, not signatures
- `Registry`: `resolve` never returns a half-published manifest; `set_pointer(expected=…)` is an atomic compare-and-swap (raises on mismatch). Delta upholds these transactionally; a local-fs adapter via temp-file-then-rename. The backend choice (memo §7.4) becomes an invariant, not an interface change.
- `Store`: ports are safe to call concurrently from many threads; `CachingStore` uses a per-hash lock and temp-file-then-atomic-rename so parallel fetches of the same blob neither double-download nor write a torn cache file.

## Risks / Trade-offs

- **Version-id format left open** → `Registry.commit` idempotency stays TBD; the Protocol ships with that one contract line marked, decided in a follow-up before any adapter relies on it.
- **Per-algorithm dedup (D3)** → a mixed-algo fleet won't dedup across algos. Mitigation: one algo per deployment; self-describing keys make a later switch additive, not a rewrite.
- **fsspec in core (D5)** → a new core dependency. Mitigation: it's tiny, pure-Python, and already transitively present in most data stacks; heavy backends remain optional.
- **Case-fold fidelity (D7)** → real case-insensitive filesystems use their own fold tables (Turkish dotless-ı, ß/SS). Mitigation: a conservative Unicode `casefold()` catches all realistic collisions; we don't chase per-FS exactness.
- **Reject-on-`..`/case-collision (D7)** → a pathological source tree can't publish as-is. Accepted: in this domain such trees are essentially always accidents; failing loudly at publish is the desired behavior.
- **Thread fan-out correctness (D6/D8)** → depends on the cache concurrency contract holding. Mitigation: the per-hash-lock + atomic-rename contract is specified now and must be covered by tests in the first implementing change.

## Open Questions

- **Version-id format**: random/ULID vs monotonic vs manifest-content-hash — restructures `head`, dedup, and GC; pairs with the env/promotion question (manifest-hash versions make cross-env promotion nearly free).
- **env in Coordinate vs Ref / cross-env promotion**: is "promote this exact version to release" a real workflow? Determines whether envs share a version namespace.
- **Windows-reserved-name policy**: reject reserved names (`CON`, `NUL`, trailing dots) at publish, or only escape/warn at checkout?
- **`get_many` shape**: how far to push the batch hook before it complicates the dumb-backend contract.
