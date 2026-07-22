## Context

The `Store`/`BlobBackend` ports were designed stream-oriented ("MUST NOT require
whole-blob materialization into a `bytes` value"), but the reference implementation
buffers whole blobs: `CasStore.put` (`store.py`) does `payload = data.read()` to
compute the hash before naming; `CasStore._load` does `backend.get(h).read()` to
verify on read; `CachingStore.put` reads the whole payload to write both stores;
`Repository.publish` takes `Mapping[str, bytes]`. The primitives underneath already
stream — `Hasher.hash` reads in 1 MiB chunks, and `FsspecBlobBackend.put` already
streams to `.tmp/{uuid}` via `shutil.copyfileobj` then `fs.mv`. So the work is not to
invent streaming but to stop the CAS/publish layer from collapsing it into `bytes`.

Two structural knots make it more than a `.read()`→loop swap: content-addressing's
chicken-and-egg (you need the hash to name the blob, but you only know it after
reading all bytes), and the fact that **content-hash verification is a whole-blob
property** — a random-access seek (a parquet footer read) cannot be verified against
a whole-blob hash. Both were worked through in explore; this records the decisions.

## Goals / Non-Goals

**Goals:**
- Publish, fetch, and materialize arbitrarily large blobs in bounded memory.
- Keep the port boundary intact: backend moves bytes, `CasStore` owns hashing + CAS.
- Preserve the crash-safe atomic-write guarantee established by add-atomic-blob-writes.
- Preserve GC safety (`BlobSafe`/`TipSafe`) without a new TLA model.

**Non-Goals:**
- A CLI (coming later, deliberately out of scope so it doesn't shape this seam).
- Async streaming; changing the `Hasher`; multipart/parallel upload.
- Delta/chunked dedup — addressing stays whole-blob content hashing.
- Read-once source streams — sources are re-readable (`bytes` or `Path`).

## Decisions

### D1. Backend seam: `stage` + `promote`, `put` removed (Variant A)
`BlobBackend` gains two primitives and drops `put(key, data)`:
- `stage(data: BinaryIO) -> str` — stream `data` to a fresh `.tmp/{uuid}`; return the
  staging key. This is the byte-copy that already lives in the backend today.
- `promote(staging_key, final_key) -> None` — atomically rename staging → final;
  idempotent (if final already exists, discard the staging object).

`CasStore` drives them and keeps hashing via a one-pass tee:
```
CasStore.put(source):
    hr      = _HashingReader(source, hasher)   # read() -> hasher.update() -> passthrough
    staging = backend.stage(hr)                # backend copies; hash rides the stream
    key     = hr.key()                         # known only at end-of-stream
    backend.delete(staging) if backend.exists(key) else backend.promote(staging, key)
    return key
```
`delete` covers discard; `sweep_temp` covers orphans — no extra methods.

*Why not the alternatives:* a single `put_cas(data, hasher)` on the backend pushes
naming-by-hash into the backend, violating the "dumb backend, no hashing/CAS" port
boundary. Having `CasStore` run the copy loop itself (open a staging sink and chunk
into it) keeps hashing in `CasStore` but drags raw byte-copying up out of the backend,
muddying the other half of the boundary. Variant A keeps both halves clean: the
`_HashingReader` *observes* the stream without either side reaching across.

*Why remove `put`:* CAS never knows the key before hashing, so a key-first `put` has
no caller once `CasStore` streams; callers reach the blob plane through `publish` /
`CasStore` (and, later, the CLI). One write path is simpler to reason about and to
spec. `promote` is exactly the atomic `.tmp → key` rename add-atomic-blob-writes
established (`fs.mv`; copy+delete on object stores) — atomicity is inherited, not new.

### D2. Reads: honest split (whole-blob verified, random access unverified)
The same `_HashingReader` powers verified whole-blob reads; random access is served
raw and unverified.
- `get_to`/`checkout`/`fetch_all`: copy `backend.get(key)` → dest through the tee,
  and at EOF assert `hr.key() == key`, else delete the partial dest and raise
  `IntegrityError`. Bounded memory, integrity guaranteed for the materialized file.
- `open(hash) -> BinaryIO`: return the backend's native seekable handle
  (`fs.open(...)` — s3fs issues HTTP range reads). **Not** verified: a partial/seek
  read cannot be checked against a whole-blob hash. Integrity for random access relies
  on the storage layer's own checksums.
- `CachingStore` is the verified-seek path: it materializes+verifies into `local` on
  first read, and serves seeks from that verified local copy.

*Rationale:* verification and cheap random access are mutually exclusive for a
whole-blob hash — you cannot have both. Rather than silently drop verification or
silently pay a full download on every seek, the contract states plainly which reads
are verified. `CachingStore` gives callers who want verified seeks an explicit way to
opt in by materializing locally.

### D3. Publish: `bytes | Path`, two-pass hash-then-upload
`publish` accepts `Mapping[str, bytes] | Mapping[str, Path]` (both re-readable; `size`
from `len` or `Path.stat().st_size`). Because the sources are re-readable, publish keeps
the **current** lease ordering and just streams instead of buffering:
```
   pass 1: stream each source through the hasher → entries, hashes, version  (no storage)
   acquire_lease(version, hashes)
   pass 2: store.put(each source)                → stage+promote (atomic, streaming)
   commit manifest → advance pointer (CAS) → release   (with the self-checks)
```
This drops the current "pre-hash **payloads in RAM** up front" step: pass 1 streams the
source through `Hasher.hash` (bounded memory) instead of `hash(io.BytesIO(all_bytes))`,
and pass 2 streams through `store.put` (the `stage`/`promote` seam) instead of holding a
`bytes`. The only cost versus a single read is a second pass over a *local* source
(`Path`/`bytes`) — negligible against the one network upload in pass 2.

*Alternative rejected (single-read reorder):* stage every source to `.tmp` once (hash
falls out), acquire the lease, then promote — reads the source once, but requires a
two-phase `stage`/`promote` API on the **`Store`** port so publish can interpose the
lease between staging and visibility, and makes `CachingStore` staging awkward. Given
re-readable sources (a decided constraint), the second local read is cheaper than
widening the port; two-pass wins.

### D4. GC safety is unchanged — same ordering as today
Two-pass publish keeps the **exact** ordering the current lease discipline was verified
under: `acquire_lease(version, hashes)` precedes any blob becoming visible (`store.put`
= stage then promote; a blob enters `store.list()` only at promote). So `Begin(lease)`
still precedes `PutBlobs`, exactly as in `GC.tla`/`GCLease.tla`; nothing in the
publish/GC protocol moves. `BlobSafe`/`TipSafe` carry over with no new modelling and no
re-run — the only change here is *how bytes flow* (streamed, not buffered), which the
GC models never constrained.

## Risks / Trade-offs

- **Random-access reads are unverified.** → Explicit in the `filesystem-view` and
  `blob-store` contracts; `CachingStore` offers verified seeks; whole-blob reads remain
  verified. Storage layers (S3, local fs) carry their own integrity.
- **Two publishers stage identical content and race to `promote` the same key.** →
  Safe: identical bytes, and the `exists(key)` guard makes the loser `delete` its
  staging. Stated explicitly as a spec scenario.
- **`sweep_temp` run concurrently with a publish would delete in-flight staging.** →
  Same class as the existing "at most one GC in flight" assumption; `sweep_temp` stays
  a manual, publish-quiescent maintenance op (never called by `gc()`).
- **Removing `BlobBackend.put` is a breaking internal-port change.** → No public API
  breaks (`Store.put(data)`, `publish`, `open_local`/`open_cloud` unchanged for
  callers); only the low-level backend seam and its direct tests migrate to
  `stage`/`promote`.
- **`open()` no longer returns a verified handle.** → `SnapshotFS._open`'s contract is
  updated to say so; consumers needing verification use `get_to`/`checkout` or a
  `CachingStore`.

## Open Questions

- Whether `open()` should offer an opt-in `verify=True` that transparently routes
  through a local materialize-then-serve, or leave that entirely to `CachingStore`
  (current lean: leave it to `CachingStore`, keep `open` a thin unverified handle).
