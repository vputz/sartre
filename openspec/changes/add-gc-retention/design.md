## Context

The reference backend is runnable end-to-end (publish, read core, SnapshotFS), but
the store only grows — nothing reclaims blobs orphaned when versions age out. Blobs
are content-addressed and shared, so reclamation is mark-and-sweep over
`roots → manifests → blobs`, not per-version deletion. GC races publish: publish
orders `blobs → manifest → pointer`, so a freshly uploaded blob is unreferenced by
any manifest for the `put`→`commit` window; a concurrent sweep would delete it and
strand the imminent commit. The prior publish-transaction work verified `PointerSafe`
(no dangling tip) via TLA+; this change adds the dual guarantee `BlobSafe` (no
committed manifest references an absent blob) under concurrent GC.

## Goals / Non-Goals

**Goals:**
- `Repository.gc(policy)` mark-and-sweep across all coordinates, idempotent and
  interrupt-safe.
- A retention policy: always-protect pointers/tags, plus `keep_last_n` and
  `keep_within(age)`.
- Close the publish/GC race with a **lease discipline**, and verify `BlobSafe` in
  TLA+ (`model/GC.tla`) plus a Hypothesis stateful mirror.
- Minimal, honest port additions to enumerate blobs and roots.

**Non-Goals:**
- Lease **TTL / grace-period** reclamation of crashed-publisher orphans (the
  degenerate lease); a crashed publish safely leaks until then.
- The timed `g ≥ d` clock lemma.
- History-record (manifest/log) compaction beyond dropping out-of-retention manifests.
- Concurrent multi-GC coordination (one GC pass at a time is assumed).

## Decisions

### D1: Lease spans the whole publish and covers the version *and* its blobs
There are **two** exposure windows, and modeling surfaced that a blob-only lease closes
only the first:
- `put`→`commit`: a blob is in the store before any manifest references it. A concurrent
  sweep would collect it; the commit then dangles.
- `commit`→`advance`: a manifest is committed before its pointer targets it. If a second
  publisher commits in between (pushing this version out of `keep_last_n`), GC would drop
  this version as "old unpointed history"; the advance then points at a dropped manifest.

So the lease names **`(version, hashes)`**: `publish` calls `acquire_lease(version, hashes)`
before the first upload and releases after the pointer advance. GC's roots are
`pointers/tags ∪ keep_last_n ∪ keep_within ∪ active_leased_versions` (manifests) and
`⋃ BlobsOf(retained) ∪ active_leased_hashes` (blobs). A blob is collectable iff
unreferenced by every retained manifest **and** unleased; a committed manifest is
droppable iff not retained **and** not leased. This is the discipline `GC.tla` verifies.

Publish can name the version up front because the version is `manifest_version(entries)`
and each entry's `content_hash` is computable by hashing the payload *before* upload — so
`publish` pre-hashes payloads (via the repository's hasher), builds entries, derives the
version, leases `(version, hashes)`, then uploads (a re-hash on `put` is idempotent).

*Why not grace-period now?* An untimed TLA+ model of a raw grace-period fails correctly
(unbounded publish delay → window lapses mid-publish → dangling). The explicit lease is
the discipline that is provable without a timing assumption; grace-period is its
degenerate, timeout-based realization and is deferred. See the `gc-lease-discipline`
project note.

*Crash semantics:* a crashed publish never releases its lease, so its blobs stay
protected (safe, leaks). Reclaiming them needs a TTL — deferred.

### D2: Lease lives on the Registry, as an in-memory set in the reference
The lease is coordination state shared between publish and GC, alongside pointers and
the log — so it belongs on the `Registry` port (`acquire_lease`/`release_lease`/
`active_leased_hashes`/`active_leased_versions`), not the dumb byte store. The reference
`MemoryRegistry` holds `dict[LeaseId, tuple[Version, frozenset[Hash]]]` under its
existing lock; the `active_leased_*` accessors union the versions/hashes. A real backend
would back this with a lease table (and later a TTL).

### D2a: GC re-validates at delete time; the scan only bounds candidates
*(Added after TLC falsified the naïve split.)* A GC that scans a live set then deletes
what the scan marked is **unsafe** for a content-addressed store: because `put` is
idempotent, a blob marked sweepable can be reused — and re-leased — by a new publish in
the scan→delete window, and the stale delete would drop a now-live blob. So the sweep
**re-reads current state immediately before deleting**: a candidate manifest is dropped
only if still unretained *now*, and a candidate blob is deleted only if referenced by no
surviving committed manifest **and** named by no live lease *now*. The scan's role is
purely to bound the candidate set (so the delete phase re-checks a small set, not the
whole store). Concretely, `gc` recomputes the live set right before the delete loop
rather than trusting the marking snapshot. This was proven necessary by `GC.tla`
(counterexample: `put` → crash → mark → reuse-under-fresh-lease → stale sweep).

### D3: Retention computes a live manifest set, then a live blob set
`gc` (1) enumerates roots: for each coordinate, all pointer targets, plus the newest
`keep_last_n` by `list_log` order, plus every version with `created_at ≥ now − age`;
(2) resolves each retained version to mark its entries' `content_hash` into `live_blobs`,
unions `active_leased_hashes`; (3) drops each non-retained, unpointed version via
`drop_version`; (4) sweeps: for `h in store.list()`, if `h not in live_blobs`, `delete`.
Verify-before-delete uses the single live set computed in the pass. `now` is injected
(a `clock` callable) so `keep_within` is testable without wall-clock flakiness.

### D4: Enumeration additions are the minimal sweep surface
`Store.list()` / `BlobBackend.list()` yield stored keys (the sweep domain).
`Registry.list_coordinates()` and `list_log(coord)` (with `created_at`) drive roots and
retention. `drop_version(version)` is repo-wide (manifests are global and content-addressed,
shared across coordinates by promotion): it removes an out-of-retention manifest, refusing
any live pointer target and pruning the version from every coordinate's log (GC additionally
never offers a leased version to `drop_version`). GC's drop domain is the distinct versions
across all coordinate logs; a crashed-publisher orphan (committed but never logged) is
unreachable via `head`/`alias`/`Pin`, so its record is harmless and its blobs are still
swept as unreferenced. Nothing here fetches blob bytes.

### D5: Two focused TLA+ models, not one merged monster
Keep `Publish.tla` (proves `PointerSafe`) as-is; add `GC.tla` modeling concurrent
publish (with the lease bracket) + GC + crash, proving `BlobSafe` (∀ committed manifest,
its blobs ⊆ stored). Model blobs/manifests abstractly (small finite sets), toggle a
`LeaseDisabled` constant to demonstrate the model *fails* without the lease (a
counterexample where GC collects an in-flight blob) — the same bug-toggle discipline as
`Publish.tla`. Run via the pinned `run_tlc.sh` protocol with absolute cfg paths.

### D6: Hypothesis stateful machine mirrors GC.tla
Extend the publish state machine with `gc` and `crash` rules over the live backend;
`@invariant` asserts every tracked committed manifest resolves to fully present blobs
(`BlobSafe`) after every step, mirroring the model on real code — consistent with the
project's property-testing preference.

## Risks / Trade-offs

- **Leaked blobs from crashed publishes** → accumulate until a TTL exists. Mitigation:
  documented Non-Goal; safety is preserved; the follow-up grace-period reclaims them.
- **`keep_within` depends on `now`** → wall-clock in policy, not in the race closure.
  Mitigation: inject `clock`; the race closure (lease) is timing-independent and is the
  part under formal proof.
- **`store.list()` on a huge backend is O(all blobs)** → GC is inherently a full scan.
  Mitigation: acceptable for an operational batch job; incremental GC is future work.
- **Single-GC assumption** → two concurrent GC passes are out of scope. Mitigation:
  documented; a real deployment serializes GC (a repo-level lock).

## Open Questions

- None blocking. Lease-TTL/grace-period design and the timed lemma are deferred by
  explicit decision ("expand later").
