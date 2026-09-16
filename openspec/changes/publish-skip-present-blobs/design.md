## Context

`publish` pass 2 uploads every source unconditionally; `CasStore.put` stages (uploads) then `promote` discards-if-present. The `repository-facade` "Publish ordering" requirement already says blobs already present are skipped — so the code violates its own spec. `promote` keeps the *existing* blob and discards the freshly-staged bytes, so the re-upload also buys no integrity (verification is on read via `get_to`). Pass 1 already computes every content hash, and `acquire_lease(version, hashes)` runs before pass 2 — so the hash to skip on is in hand, and a present blob is a GC root under the live lease.

## Goals / Non-Goals

**Goals:** stop re-uploading blobs the durable store already holds; skip the redundant pass-2 re-hash of present sources; make the skip correct for `CachingStore` (durable, not local-cache).

**Non-Goals:** no change to the two-pass lease ordering; no trusting `known_hash` for naming on the upload path; no content-diffing beyond whole-blob content-address dedup.

## Decisions

- **`Store.put(data, *, known_hash=None)`, not a new method.** A single optional keyword keeps the port small and backward-compatible. When `known_hash` is present and the store's durable target holds it, return it without touching `data`; otherwise upload and name by the true bytes (the stream is still hashed — `known_hash` is a *skip hint*, never trusted for naming).
- **The skip is per-store "durable presence," pushed into the store.**
  - `CasStore.put`: `if known_hash is not None and self.backend.exists(known_hash): return known_hash`.
  - `CachingStore.put`: forward `known_hash` to `self.remote.put(data, known_hash=known_hash)`. The remote is a `CasStore`, so its `backend.exists` is the durable check; the local cache is never consulted for the skip. This is the crux — `CachingStore.has` is `local OR remote`, so using it would wrongly skip a blob present only locally but missing from the remote (e.g. GC reclaimed it from the remote while a local copy lingers), committing a manifest that dangles on a cold cache. Forwarding to `remote.put` makes the trap unrepresentable.
- **publish pairs sources with their pass-1 hashes.** Build `path -> content_hash` from `entries` (paths are already normalized to match `normalized`'s keys) and call `store.put(stream, known_hash=hash)`. A present blob then skips both the upload and the second read/re-hash of the source.
- **Safety rests on the existing lease, unchanged.** The lease over `(version, hashes)` is acquired before pass 2 and re-verified before commit and before advance; a blob found present is in that hash set, so GC cannot sweep it mid-publish. No new concurrency reasoning — this is why no TLA work is needed.

## Risks / Trade-offs

- **A caller could pass a wrong `known_hash`.** On the *present* path this is a caller bug (they'd skip uploading a blob they claimed exists) — but publish derives `known_hash` from the same bytes it streams, so it is always correct there. On the *absent* path the upload still hashes the real bytes and names by them, so a wrong hint can never mis-name a stored blob. Spec pins this.
- **`CachingStore` no longer populates its local cache on publish** — it never did (writes went to remote; the cache is read-through). Unchanged.

## Migration Plan

Additive keyword; no data or schema change. Existing `put(data)` calls keep working. Rollback is dropping the `known_hash` branch.
