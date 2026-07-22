## 1. Backend seam: stage/promote, drop put

- [x] 1.1 `src/sartre/ports.py`: `BlobBackend` — replace `put(key, data)` with `stage(data: BinaryIO) -> str` and `promote(staging_key: str, final_key: str) -> None`; document the single-write-path + idempotent-promote contract. Update `Store.open` doc to state random-access reads are unverified.
- [x] 1.2 `src/sartre/store.py`: refactor `FsspecBlobBackend.put` into `stage` (stream `data` → `.tmp/{uuid}` via `copyfileobj`, return staging key) and `promote` (`fs.mv` staging → final; if final exists, discard staging). Keep `_staging_path`/`_TEMP_DIR`/`sweep_temp` as-is.
- [x] 1.3 `src/sartre/store.py`: add `_HashingReader(inner, hasher)` — a `BinaryIO`-ish wrapper whose `read(n)` reads from `inner`, feeds the hasher, and returns the chunk; exposes `key()` (content hash at EOF) and `length()` (bytes seen).

## 2. Streaming CasStore

- [x] 2.1 `CasStore.put(data)`: `hr = _HashingReader(data, hasher)`; `staging = backend.stage(hr)`; `key = hr.key()`; `backend.delete(staging)` if `backend.exists(key)` else `backend.promote(staging, key)`; return `key`. No whole-blob buffer.
- [x] 2.2 `CasStore.get_to(hash, dest)`: stream `backend.get(hash)` → `dest` through a `_HashingReader`; at EOF, if `key != hash` delete `dest` and raise `IntegrityError`. Bounded memory. `_load`/whole-blob helper reworked to stream.
- [x] 2.3 `CasStore.open(hash)`: return `backend.get(hash)` (native seekable handle) directly — **no** verification (honest split).
- [x] 2.4 `CachingStore`: `put` streams through to `remote` then back-fills `local` without a whole-blob buffer; reads materialize+verify into `local` (via `get_to`/ensure-local) so seeks are served from the verified local copy. Keep the per-hash lock.

## 3. Streaming publish

- [x] 3.1 `src/sartre/repository.py`: `publish` signature → `files: Mapping[str, bytes] | Mapping[str, Path]`. Normalize/case-check paths as today; a per-source `_open()` helper yields a fresh stream (`io.BytesIO(bytes)` or `path.open("rb")`) and `_size()` gives `len`/`Path.stat().st_size` — sources are re-readable, so each is opened once per pass.
- [x] 3.2 Two-pass (T): pass 1 streams each source through `self._hasher.hash(...)` → `entries` + `version` + `hashes` (no whole-blob buffer, no storage); `acquire_lease(version, hashes, ttl)`; pass 2 `store.put(_open(src))` for each (streaming stage+promote, dedup by `has`); `commit` → `set_pointer` CAS → release. Keep the heartbeat + pre-commit/pre-advance self-checks and `finally` release. Ordering (lease before upload) is unchanged from today.
- [x] 3.3 No new `Store` API: publish uses the streaming `store.put` (which drives `stage`/`promote`). Confirm dedup — a blob whose hash is already present is a no-op via `promote`'s exists-guard, so re-`put` of a present blob does not re-upload payload beyond the (skippable) stage; keep the existing `has`-skip if cheap.

## 4. SnapshotFS

- [x] 4.1 `src/sartre/fs.py`: `_open` returns the store's seekable handle for the hash (unverified for partial reads); update the module/method docstring contract (drop "integrity-verified"; note `CachingStore` gives verified seeks).

## 5. Tests

- [x] 5.1 Migrate existing `BlobBackend.put`/atomic-write tests (`tests/test_store.py`) to `stage`/`promote`; keep the crash-safety + orphan-`sweep_temp` coverage.
- [x] 5.2 Property-based (Hypothesis): round-trip integrity over random blob sizes and chunk boundaries — `put` then `get_to` reproduces bytes and the key.
- [x] 5.3 Honest-split test: flip a byte in the backend object → `get_to`/`checkout` raise `IntegrityError` (and delete the partial dest); `open()`+seek returns the (corrupt) bytes without raising.
- [x] 5.4 Bounded-memory: publish/fetch a `Path` source larger than a set threshold using a backend/reader that would fail if the whole blob were buffered (or assert peak via a counting wrapper).
- [x] 5.5 Concurrent identical-content publish: two publishes of the same bytes both succeed; blob present exactly once; loser discards staging.
- [x] 5.6 Publish accepts `Path` and `bytes` sources; `size` correct from both; existing publish/GC/lease tests stay green after the reorder.

## 6. Gates

- [x] 6.1 `pyright` clean, `ruff` clean, full default suite green (Postgres/S3 groups skip cleanly without infra).
- [x] 6.2 Run the S3 (moto) group to confirm streaming `stage`/`promote` + unverified `open` range-read behave over a real fsspec object backend.
- [x] 6.3 `openspec validate add-streaming-blobs --strict` passes.
