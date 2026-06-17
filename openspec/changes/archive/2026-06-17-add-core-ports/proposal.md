## Why

Sartre needs a stable interface skeleton before any storage backend is written. The design memo (`binary-artifact-repo-design.md`) specifies a read core but leaves the abstraction boundaries, the path model, and the concurrency story open. Pinning these now — as pure interfaces plus contracts — lets us validate the design against a local-filesystem stub and keeps the eventual Delta/S3 backends from leaking through the API. Several of these choices (hash-in-key format, the path model, sync-vs-async) ossify the public surface and are expensive to change once manifests and code exist.

## What Changes

- Introduce the **two-plane port architecture**: a `Registry` (manifest plane — names → versions → manifests, no bytes) and a `Store` (blob plane — content-hash ↔ bytes), composed by a `Repository` facade.
- Define the **domain model** from memo §3: `Coordinate(name, env)`, a sealed `Ref` union (`Head`/`Alias`/`Pin`), `Entry(path, content_hash, size, inline?)`, `Snapshot`. Content hashes are **self-describing** (`"algo:digest"`; sha256 default, blake3 opt-in later) via a pluggable `Hasher`.
- Split the blob plane into a **dumb `BlobBackend`** (opaque key/value: get/put/exists/delete) plus a generic **`CasStore`** that adds hashing + verify once over any backend; make the local cache a `Store` too via a **`CachingStore`** decorator.
- Adopt **fsspec on both ends**: `FsspecBlobBackend` turns any fsspec filesystem into a `BlobBackend` (free s3fs/gcsfs/local/memory backends); `SnapshotFS` exposes a resolved version as a **read-only fsspec filesystem** (listings from the manifest, bytes lazily by hash), enabling virtual access, whole-tree checkout, FUSE mounts, and `sartre://` URLs.
- Commit to a **sync core** with parallelism as a facade concern (thread fan-out for `fetch_all`/`checkout`, since blob I/O releases the GIL), an optional `BlobBackend.get_many()` batch hook, and a thin `AsyncRepository` wrapper via `asyncio.to_thread`.
- Define a **canonical path model** (`normalize_path()`): `/`-relative, no `.`/`..` (kills path traversal), NFC, case-sensitive identity but **reject case-only collisions within a manifest**, enforced at write time.
- Establish cross-cutting **contracts**: atomic `resolve` + compare-and-swap `set_pointer` on the Registry; verify-on-download + thread-safe ports + per-hash-locked, atomically-written cache on the Store.
- Deliverable is **interface only**: `src/sartre/ports.py` (Protocols + dataclasses) and the normalization rules. No backend implementations.

## Capabilities

### New Capabilities
- `artifact-model`: The domain vocabulary and identity model — `Coordinate`, `Ref`/`Head`/`Alias`/`Pin`, `Entry`, `Snapshot`, opaque `Version`, and self-describing `"algo:digest"` content hashes with a pluggable `Hasher`.
- `registry-port`: The manifest-plane interface — `head`/`resolve`/`list_pointers`/`list_versions` reads and `commit`/`set_pointer` writes — with the cheap-`head`, atomic-`resolve`, and compare-and-swap pointer contracts.
- `blob-store`: The content-addressed blob plane — the `Store` CAS port, the dumb `BlobBackend` sub-port, `CasStore` (hash + verify), `CachingStore` (cache-as-Store), `FsspecBlobBackend`, and the verify/thread-safety/atomic-write contracts.
- `path-model`: The canonical logical-path normalization and validation rules (`normalize_path()`), including the case-collision and traversal-rejection policy, enforced at write time.
- `filesystem-view`: `SnapshotFS`, a read-only fsspec `AbstractFileSystem` over a resolved version, plus materialization (`checkout`/recursive get), URL addressing, mapper, and optional FUSE.
- `repository-facade`: The `Repository` composition of a `Registry` + `Store`, its read/publish surface, the sync-core + thread-parallel batch model, and the optional `AsyncRepository` wrapper.

### Modified Capabilities
<!-- None — greenfield project, no existing specs. -->

## Impact

- **New code**: `src/sartre/ports.py` (Protocols + dataclasses, no implementations); a `normalize_path()` utility and its tests.
- **Dependencies**: adds `fsspec` to core (small, pure-Python); heavy backends (`s3fs`, `gcsfs`) remain optional extras alongside the existing `delta`/`s3` extras.
- **No runtime behavior yet**: this change lands interfaces and contracts only; no backend talks to Delta or S3.
- **Deferred (explicit non-goals, see design.md)**: Delta/S3 adapters, the publish-transaction crash-ordering protocol, garbage collection, and the version-id format decision (which leaves `Registry.commit` content-idempotency marked TBD).
