## Why

The `Store` port promises blobs are never materialized whole into a `bytes` value,
so multi-hundred-megabyte artifacts work. The reference implementation violates its
own contract: `CasStore.put` and `CasStore._load` `read()` the entire blob into RAM
to hash/verify it, `CachingStore.put` does the same, and `Repository.publish` takes
`Mapping[str, bytes]` — so a 40 GB checkpoint needs ~40 GB of RAM per file. Binary
artifacts are big; this is the gap that blocks the actual use case.

## What Changes

- **Streaming, single-write-path backend seam** — invert content-addressing's
  "hash-then-name" into "stream-then-name". `BlobBackend` gains `stage(data) -> str`
  (stream to a `.tmp/{uuid}` staging key) and `promote(staging_key, final_key)`
  (atomic rename; idempotent). **BREAKING (internal port):** `BlobBackend.put(key,
  data)` is **removed** — there is exactly one write path. `delete` covers discard,
  `sweep_temp` covers orphans. `CasStore` keeps all hashing via a small
  `_HashingReader` tee that rides the same single streaming pass.
- **Bounded-memory `CasStore.put`** — `stage` the source through the tee, learn the
  hash at end-of-stream, then `promote` (or `delete` the staging dup). One pass, no
  whole-blob buffer.
- **Honest read split** — whole-blob consumption (`get_to`/`checkout`/`fetch_all`) is
  verified forward-only (hash while copying to dest, raise `IntegrityError` at EOF,
  delete the partial). Random-access `open(hash) -> BinaryIO` returns the backend's
  **native seekable handle** (e.g. S3 range reads) **unverified** — per-read integrity
  cannot be checked against a whole-blob hash. `CachingStore` is the verified-seek
  path because it materializes and verifies locally first.
- **Streaming publish** — `Repository.publish` accepts `Mapping[str, bytes] |
  Mapping[str, Path]` (both re-readable). It keeps the current lease ordering and just
  streams: **pass 1** hashes each source by streaming it through the hasher (no
  whole-blob buffer), then **acquire the lease**, then **pass 2** uploads each source via
  the streaming `store.put` (`stage`/`promote`), then commit → advance. This drops the
  "pre-hash payloads in RAM up front" step; the only cost is a second read of a local
  `Path`/`bytes` source, negligible against the one network upload.
- **No new TLA** — publish keeps the exact ordering (`lease` before any blob becomes
  GC-visible) the lease discipline was verified under in `GC.tla`/`GCLease.tla`; only
  *how bytes flow* changes (streamed, not buffered), which the GC models never
  constrained. Argued in `design.md`, not re-checked.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `blob-store`: the **Dumb BlobBackend** requirement swaps `put(key, data)` for
  `stage`/`promote`; the **Concurrency and atomic cache writes** requirement restates
  atomicity around `stage`+`promote` (incl. the concurrent-identical-content race);
  the **Content-addressed Store interface** gains a bounded-memory `put` guarantee; and
  **Verify on download** is split — whole-blob reads verified, random-access `open`
  unverified.
- `repository-facade`: **Publish ordering** accepts `bytes | Path` sources and streams
  both the hash pass and the upload pass in bounded memory, keeping the current
  lease-before-upload ordering.
- `filesystem-view`: **Lazy open by logical path** — `SnapshotFS._open` returns a
  seekable backend-served handle that is **not** integrity-verified for partial reads;
  a verified seek is available via `CachingStore` (local materialization).

## Impact

- **Code**: `src/sartre/ports.py` (`BlobBackend`: `stage`/`promote`, drop `put`;
  `Store.open` doc), `src/sartre/store.py` (`_HashingReader`; `FsspecBlobBackend.stage`/
  `promote` refactored out of today's `put`; `CasStore.put`/`_load`/`open`/`get_to`;
  `CachingStore.put`/verified seek), `src/sartre/repository.py` (`publish` source type +
  reorder), `src/sartre/fs.py` (`_open` returns the backend handle, doc contract).
- **Tests**: property-based round-trip over random sizes/chunk boundaries; byte-flip →
  `get_to`/`checkout` raise `IntegrityError` but `open()`+seek does not (proves the
  split); bounded-memory via a large `Path` source; concurrent identical-content
  publish both succeed idempotently. Existing `put(key, data)` backend tests migrate to
  `stage`/`promote`.
- **Non-goals**: a CLI (coming later, out of scope now); async streaming; changing the
  `Hasher`; multipart/parallel upload; delta/chunked dedup (still whole-blob CAS).
