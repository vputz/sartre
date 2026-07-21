## 1. TLA+ model (verify the discipline first)

- [x] 1.1 Write `openspec/changes/add-gc-retention/specs/garbage-collection/model/GC.tla`: concurrent publish (lease bracket: acquire → put blobs → commit → advance → release) and GC (mark roots incl. leased → sweep), with a `Crash` action and a `LeaseDisabled` bug toggle.
- [x] 1.2 Define invariants: `TypeOk`, `BlobSafe` (every committed manifest's blobs ⊆ stored). Add `GC_fixed.cfg` and `GC_buggy.cfg` (LeaseDisabled).
- [x] 1.3 Run via the pinned `run_tlc.sh` with ABSOLUTE cfg paths (use the tla-verifier agent): fixed cfg PASS, buggy cfg CHECK_FAILED on `BlobSafe`. Record a short model README with the run protocol and results.

## 2. Port additions

- [x] 2.1 `blob-store`: add `Store.list() -> Iterable[Hash]` and `BlobBackend.list() -> Iterable[str]` to `ports.py`.
- [x] 2.2 `registry-port`: add `list_coordinates()`, `list_log(coord) -> Sequence[LogEntry]` (version/seq/created_at), `drop_version(version)` (repo-wide; refuse any pointer target, prune all logs), `acquire_lease(version, hashes) -> LeaseId`, `release_lease(lease_id)`, `active_leased_hashes() -> Set[Hash]`, `active_leased_versions() -> Set[Version]` to `ports.py`. Define the `LeaseId`/`LogEntry` types.

## 3. Reference backend

- [x] 3.1 `store.py`: implement `FsspecBlobBackend.list()` (enumerate under root) and `CasStore.list()` (delegate); `CachingStore.list()` (union local/remote or remote as source of truth — document which).
- [x] 3.2 `memory.py`: implement `list_coordinates`, `list_log` (expose `created_at`), `drop_version(version)` (refuse any pointer target → `Conflict`; prune all logs; remove manifest record; idempotent), and the lease map `dict[LeaseId, (version, frozenset[hashes])]` (`acquire_lease`/`release_lease`/`active_leased_hashes`/`active_leased_versions`) under the existing lock.

## 4. GC + publish lease bracket

- [x] 4.1 `repository.py`: add `RetentionPolicy` (tags always; `keep_last_n`, `keep_within`) and `GCResult` (dropped manifests/blobs) dataclasses.
- [x] 4.2 Implement `Repository.gc(policy, *, clock=...) -> GCResult`: compute roots → mark live blobs (resolve retained versions) ∪ leased → `drop_version` non-retained unpointed → sweep `store.list()` deleting unreferenced.
- [x] 4.3 Bracket `Repository.publish` in a lease: pre-hash payloads → build entries → derive version → `acquire_lease(version, hashes)` before upload, `release_lease` in a `finally` after the pointer advance.
- [x] 4.4 `__init__.py`: re-export `RetentionPolicy`, `GCResult` (and confirm `gc` reachable).

## 5. Tests

- [x] 5.1 `tests/test_gc.py`: unreferenced blob reclaimed; shared blob retained; pointer/tag target always protected; old unpointed version dropped with its unique blobs.
- [x] 5.2 Retention knobs: `keep_last_n` keeps exactly the newest N; `keep_within(age)` keeps by injected `clock`.
- [x] 5.3 Idempotency: second `gc` with no writes drops nothing; deleting an absent blob is a no-op.
- [x] 5.4 Lease/race: with a lease held over an in-flight publish's blobs, `gc` does not collect them; after release + no manifest, they become collectable. `drop_version` refuses a pointer target.
- [x] 5.5 Hypothesis stateful machine (mirrors `GC.tla`): randomized publish/gc/crash; `@invariant` asserts `BlobSafe` (every tracked committed manifest resolves to fully present blobs) after every step.

## 6. Gates

- [x] 6.1 `pyright` clean, `ruff` clean, full test suite green.
- [x] 6.2 `openspec validate add-gc-retention` passes.
