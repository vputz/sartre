## 1. Store port + implementations

- [ ] 1.1 `src/sartre/ports.py`: `Store.put(self, data, *, known_hash: Hash | None = None) -> Hash` — document the durable-presence short-circuit and that `known_hash` is a skip hint, never used for naming on the upload path.
- [ ] 1.2 `src/sartre/store.py` `CasStore.put`: if `known_hash is not None and self.backend.exists(known_hash): return known_hash`; else unchanged (stage → promote, named by true bytes).
- [ ] 1.3 `src/sartre/store.py` `CachingStore.put`: accept `known_hash` and forward it to `self.remote.put(data, known_hash=known_hash)` — durable (remote) check only; never skip on a local-cache hit.

## 2. Publish

- [ ] 2.1 `src/sartre/repository.py` publish pass 2: build a `path -> content_hash` map from `entries`, and for each `(path, src)` call `self.store.put(stream, known_hash=hash)` — skipping upload and re-hash of already-present blobs. Keep the lease acquired before pass 2 and both self-checks intact.

## 3. Tests

- [ ] 3.1 `CasStore.put(known_hash=h)` with a counting/spy backend: backend already has `h` → zero `stage` calls, returns `h`; backend lacks `h` → normal stage+promote, named by true bytes.
- [ ] 3.2 `put(known_hash=<wrong>)` for an ABSENT blob → stored blob is named by its real content hash, not the bogus hint.
- [ ] 3.3 Publish-level: publish twice with overlapping files over a counting remote (reuse/adapt `_CountingRemote` in `tests/test_snapshot_fs.py`) → the second publish uploads only the new blobs (present ones cause zero stage calls).
- [ ] 3.4 The trap: a `CachingStore` whose `local` holds a blob but whose `remote` does not → publishing a manifest with that blob uploads it to `remote`, and resolving the version through a fresh/cold `CachingStore` (or directly against the remote) succeeds. Asserts the skip is durable, not local.
- [ ] 3.5 Regression: existing publish/streaming tests still pass (idempotent re-publish still returns the same version; bounded-memory path unaffected).

## 4. Gates

- [ ] 4.1 `ruff` clean, `pyright` clean, full default suite green.
- [ ] 4.2 `openspec validate publish-skip-present-blobs --strict` passes.
