## Why

The `repository-facade` "Publish ordering" requirement already mandates uploading "skipping blobs already present," with a scenario "Existing blobs are not re-uploaded." **The implementation does not honor it:** publish pass 2 calls `store.put(stream)` for every source unconditionally; `CasStore.put` *stages* (the actual upload) and only then `promote`s — which, when the hash already exists, discards the freshly-staged bytes and keeps the existing blob. So dedup is at-rest, the wire cost is paid on every publish, and it grows with the corpus. For whole-tree coordinates (most files unchanged between versions) this re-uploads everything to store nothing. It's a spec-conformance bug, and — since `promote` keeps the existing blob — the re-upload buys zero integrity (integrity is enforced on read by `get_to`).

## What Changes

- **`Store.put` gains an optional `known_hash`** — when supplied and the store's own **durable** target already holds it, `put` returns immediately without consuming or uploading the source; otherwise it uploads exactly as today (stream → stage → promote, naming the blob by its true bytes). Backward-compatible (`known_hash=None` is today's behavior).
- **`publish` pass 2 passes each source's pass-1 hash** as `known_hash`, so already-stored blobs skip both the upload *and* the redundant second read/re-hash. Safe because the lease over `(version, hashes)` is held before pass 2, so a present blob is a GC root through commit and advance.
- **Spec correctness refinement:** the skip MUST test the **durable** store, not `CachingStore.has` (which is `local OR remote`). A blob cached locally but absent from the remote must still be uploaded — otherwise the committed manifest references a blob missing from the source of truth. `CachingStore.put` forwards `known_hash` to `remote.put`, so the durable (`backend.exists`) check is the only one consulted for the skip.

## Capabilities

### Modified Capabilities
- `blob-store`: `Store.put` / `CasStore.put` gain the optional `known_hash` durable-presence short-circuit (skip staging when the backend already holds it; otherwise stage→promote as before).
- `repository-facade`: the "Publish ordering" requirement is corrected to skip on **durable** presence rather than `has`, with a scenario pinning that a locally-cached-but-remote-absent blob is still uploaded.

## Impact

- **Code**: `src/sartre/store.py` (`CasStore.put`/`CachingStore.put` + `Store` protocol signature), `src/sartre/ports.py` (`Store.put` signature), `src/sartre/repository.py` (publish pass 2 pairs sources with pass-1 hashes).
- **APIs**: additive keyword `known_hash` on `Store.put`; existing callers unaffected. The observable win: publishing a manifest whose blobs already exist uploads nothing.
- **Correctness**: closes the spec/impl gap for "Existing blobs are not re-uploaded" and spec's out the `CachingStore` local-cache trap so it can't be reintroduced.
- **Gates**: ruff + pyright clean, full suite green, `openspec validate --strict` passes.
