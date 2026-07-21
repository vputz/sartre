## 1. Atomic backend put

- [x] 1.1 In `FsspecBlobBackend`, add a reserved temp prefix (`{root}/.tmp`); give the backend a `_staging_path()` helper returning `{root}/.tmp/{uuid4}`.
- [x] 1.2 Rewrite `put(key, data)`: if the final key exists, return (idempotent); else stream to a fresh staging path, then `fs.mv(staging, final)`. Remove the staging object in a `finally` if the rename did not consume it (best-effort on failure).
- [x] 1.3 Ensure the `.tmp` directory is created as needed and that `mkdirs`/`auto_mkdir` differences across fsspec backends are handled (create parent before write).

## 2. Listing & temp reclamation

- [x] 2.1 Update `FsspecBlobBackend.list()` to exclude names beginning with `.` (skips the `.tmp` namespace), so staging objects are never yielded as content hashes.
- [x] 2.2 Add `sweep_temp()` enumerating and deleting objects under `{root}/.tmp/`; return the count/keys reclaimed.

## 3. Tests

- [x] 3.1 `tests/test_store.py`: successful put still round-trips; put is idempotent; `list()` returns only real hashes.
- [x] 3.2 Failed put leaves no partial blob: wrap the source so `put` raises mid-write; assert the final key is absent, `has` is False, `list()` omits it, and the `finally` cleaned the staging object (`sweep_temp() == 0`). A later good put of the key succeeds.
- [x] 3.3 `list()` excludes `.tmp`; a planted orphan staging object (simulating a crash before rename) is excluded from `list()`, reclaimed by `sweep_temp()`, and `sweep_temp()` is a no-op when none exist.
- [x] 3.4 Concurrency: many threads put the same and different hashes; assert every listed key verifies (open succeeds) and no partial blob is observed.
- [x] 3.5 Hypothesis property: for a random sequence of puts (some forced to fail mid-write), every key in `list()` opens to bytes that verify against the hash, and forced-failure hashes are absent unless later successfully put.

## 4. Gates

- [x] 4.1 `pyright` clean, `ruff` clean, full test suite green.
- [x] 4.2 `openspec validate add-atomic-blob-writes` passes.
