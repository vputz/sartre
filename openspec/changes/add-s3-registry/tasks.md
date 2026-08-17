## 1. Test harness & de-risking (do first)

- [ ] 1.1 Confirm the moto conditional-write behavior the design rests on: a moto-server + s3fs fixture (reuse `ThreadedMotoServer` from `tests/test_cloud_backend.py`) where a second `open(key, "xb")` on an existing key raises `FileExistsError` and leaves the body unchanged. (Spike already green — lock it in as a real test/fixture.)
- [ ] 1.2 A reusable `s3_registry` test fixture: an `S3Registry` over a moto-server-backed s3fs with `skip_listings_cache`/`skip_instance_cache` so the CAS path reads authoritative state.

## 2. TLA: version-drop vs concurrent promote

- [ ] 2.1 Model the transaction-less race in `openspec/changes/add-s3-registry/model/` (extend the GC family): a version `v` that is committed, out-of-retention, and being `drop_version`'d while a `set_pointer` targets `v`. Invariant: no pointer ever targets a reclaimed manifest.
- [ ] 2.2 Model the candidate closure — a shared put-if-absent `dropping/<v>` marker: GC writes the marker, re-scans for events referencing `v`, aborts (deletes marker) if any appeared; `set_pointer` refuses a version with a live marker. Show it upholds the invariant; try to break the naive (no-marker) version to prove the marker necessary.
- [ ] 2.3 Run through the `tla-verifier` agent (SANY → smoke → exhaustive → coverage gate); capture the verdict + any counterexample. Finalize the mechanism in `design.md` (resolve the open question).

## 3. `S3Registry` core (read + CAS write)

- [ ] 3.1 `src/sartre/s3.py`: object layout + key helpers (`manifests/sha256/<hex>.json`, `coords/<name>/<env>/pointers/<p>/<seq>.json`, `tombstones/sha256/<hex>`), zero-padded seq, JSON codec for manifests (entries incl. `inline`, metadata, created_at) and pointer events (`seq, version, from, actor, reason, at`). Constructed from an fsspec filesystem + root prefix (mirrors `FsspecBlobBackend`).
- [ ] 3.2 `commit(coord, entries, metadata)`: client-side `manifest_version`, then put-if-absent the manifest object; a `FileExistsError` is the benign idempotent no-op. Returns the version.
- [ ] 3.3 `set_pointer(..., *, expected, actor, reason)`: the CAS loop — authoritative tail LIST (cache-bypassed) → `current`/`seq` → `expected` check (`Conflict`) → manifest-exists check (`NotFound`) → put-if-absent next event → `FileExistsError` ⇒ re-read & retry. Refuse a version with a live `dropping/<v>` marker (per §2).
- [ ] 3.4 `head`/`resolve`: tail read (LIST small prefix + GET) for Head/Alias; `resolve` then GETs the immutable manifest. `Pin` resolves against the coordinate's events. No manifest scan in `head`.

## 4. Enumeration, history, drop

- [ ] 4.1 `list_pointers` (current tail per pointer, tombstone-aware), `list_versions` (distinct logged versions minus tombstoned), `list_coordinates` (delimited LIST of `coords/`).
- [ ] 4.2 `list_log` / `list_pointer_history`: read all pointer-stream events, merge by event time; project as `LogEntry` (version, derived seq, created_at, actor, reason) and `PointerMove` (name, from, to, actor, reason, at) respectively.
- [ ] 4.3 `drop_version(v)`: the TLA-verified guarded protocol from §2 — write/observe the `dropping/<v>` marker, confirm no pointer targets `v`, delete the manifest object, write `tombstones/<v>`; never prune event objects. Idempotent.
- [ ] 4.4 Lease surface as degenerate no-ops (`acquire_lease` → token, `renew_lease` → True, `release_lease` → no-op, `active_leased_*` → empty). Document that GC blob-window safety comes from blob-grace.

## 5. Opener & packaging

- [ ] 5.1 `open_s3(url, *, blob_url=None, storage_options=None, cache_dir=None) -> Repository` in `src/sartre/s3.py` (or `src/sartre/cloud.py`): assemble `S3Registry` + S3 `CasStore` (optionally `CachingStore` when `cache_dir`), blobs defaulting to `<repo>/blobs`. Export `S3Registry`/`open_s3` from `sartre/__init__.py`.
- [ ] 5.2 One-time conditional-write probe in `open_s3`: attempt a put-if-absent collision against a scratch key; raise a clear error if the endpoint does not enforce it (S3-compatible endpoints). CLI addressing (`config.py`) grows an `s3://` opener path so `--repo s3://…` / a profile resolves to `open_s3`.

## 6. Tests

- [ ] 6.1 Behavioral unit tests over the moto-backed `S3Registry`: commit idempotency, `set_pointer` CAS success/stale-`Conflict`/uncommitted-`NotFound`, `head`/`resolve`, log/history provenance, drop + tombstone + history-retained.
- [ ] 6.2 Property (Hypothesis): the CAS sequence is gap-free; concurrent racers yield exactly one winner and one `Conflict`; `set_pointer(render)`-style provenance roundtrips.
- [ ] 6.3 Extend the differential machine (or a parallel one) with `S3Registry` as an arm: agree with the reference backend on `head`/`resolve`/`list_pointers`/`list_versions` at every step, and on `list_log`/`list_pointer_history` modulo tombstoned history.
- [ ] 6.4 A focused concurrency test for the §2 race: a promote racing a `drop_version` never leaves a pointer targeting a reclaimed manifest (both outcomes — promote-wins/drop-aborts or drop-wins/`NotFound` — accepted).
- [ ] 6.5 `open_s3` e2e over moto: single-URL publish → show/ls/cat/checkout/point/history; and the non-conforming-endpoint probe error (simulate by disabling conditional enforcement).

## 7. Gates

- [ ] 7.1 `ruff` clean, `pyright` clean, full default suite green (S3 tests skip cleanly when s3fs/moto absent, like the existing cloud tests).
- [ ] 7.2 `openspec validate add-s3-registry --strict` passes.
