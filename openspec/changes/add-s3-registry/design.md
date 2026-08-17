## Context

The `Registry` port needs: atomic compare-and-swap `set_pointer`, content-idempotent `commit`, atomic `resolve`, an append-only per-coordinate log, the pointer-move history, enumeration, `drop_version`, and a lease surface. `MemoryRegistry` and `_SqlRegistry` (SQLite/Postgres) satisfy it. We want a third backend that runs on **just S3**, using the fsspec/s3fs stack the blob plane already uses.

The enabler is recent: S3 **put-if-absent** (`If-None-Match:*`, GA Aug 2024) and **compare-and-swap** (`If-Match`, GA Nov 2024), over strong read-after-write + LIST consistency (2020). A throwaway spike (moto server + s3fs, the repo's existing `ThreadedMotoServer` fixture) confirmed the load-bearing behavior: a second `open(key, "xb")` on an existing key raises **`FileExistsError`** ("…pre-conditions…did not hold"), the stored body is unchanged, and moto models the 412 in-process — so the CAS is testable in the fast lane.

## Goals / Non-Goals

**Goals:**
- A safe multi-writer `Registry` on plain S3, no database, no lock table, no special bucket provisioning.
- Stay inside fsspec/s3fs; use only put-if-absent for writes (never an overwrite).
- Satisfy the existing port; differential-test against the in-memory reference backend.
- Reuse the already-verified blob-grace for GC safety; add TLA only where S3's lack of transactions genuinely requires it.

**Non-Goals:**
- Portability of the CAS across every fsspec backend (conditional-write is inherently S3-specific; the base fsspec API has no CAS verb).
- A registry clock / durable TTL leases (S3 exposes no server time).
- Solving the pre-existing orphan-manifest cleanup gap (tracked separately; unchanged here).
- High-write-rate hot coordinates (per-coordinate write cadence is assumed low, as for any artifact registry).

## Decisions

### C2 — append-only per-pointer event streams
Each pointer is an append-only stream of immutable, zero-padded event objects; the object layout:

```
s3://bucket/<repo>/
  manifests/sha256/<hex>.json               immutable · put-if-absent · shared across coords
  coords/<name>/<env>/pointers/<p>/000001.json  immutable move event · put-if-absent
                                                 { seq, version, from, actor, reason, at }
  tombstones/sha256/<hex>                    put-if-absent marker · "version GC-dropped"
  blobs/…                                    the CasStore (unchanged)
```

A pointer's events **are** its move-history and its slice of the commit log — the same objects, two projections. Chosen over the single-object-per-coordinate design (mutable `state.json` + `If-Match` overwrite) because C2 uses only put-if-absent (the clean s3fs verb; overwrite-CAS is not first-class), has O(1) writes (no read-modify-write of growing history), gives per-pointer concurrency and a self-contained cheap `head` (LIST a small prefix — no mutable cache), and its append-only nature matches the provenance model. Trade: per-coordinate log order is a **timestamp merge across pointer streams**, not a strict transactional `seq` (only cosmetic under cross-host clock skew; `keep_last_n` at the boundary is unaffected), and cold enumeration (`list_log`) costs O(events) GETs (a later best-effort checkpoint object can amortize it — deferred).

### put-if-absent is the only write; `FileExistsError` is the CAS signal
`set_pointer(name, version, *, expected)`:
```
loop:
  tail = LIST coords/n/e/pointers/<name>/   (authoritative — NOT a cached listing)
  seq, current = (max key → GET → .version) or (0, None)
  if current != expected:  raise Conflict            # the port's CAS semantics
  if not HEAD manifests/<version>:  raise NotFound
  try: open(pointers/<name>/{seq+1}.json, "xb").write(event)
  except FileExistsError:  continue                  # lost the race → re-read tail, retry
  return
```
Two racers on `seq+1` → S3 grants exactly one; the loser `FileExistsError`s, re-reads, and now its `expected` fails → `Conflict`. Gap-free (you can only write N+1 having observed N). Enforcement is **server-side**, so a stale client cache can never cause a lost write.

### The tail-read must bypass the fsspec listing cache
`s3fs` caches directory listings (`use_listings_cache`). Put-if-absent protects against lost *writes* even under a stale cache, but a stale **tail-read** can misjudge `current` vs `expected` (a spurious `Conflict`, or a wasted 412 retry). So the CAS tail LIST MUST read authoritative S3 (`invalidate_cache`/`skip_instance_cache`), never a cached listing. This is a spec scenario, not just an implementation note.

### Leases are degenerate; blob-grace covers the publish window (no manifest-grace)
S3 has no clock, so `acquire_lease → dummy id`, `renew_lease → True`, `active_leased_{hashes,versions} → ∅`. Safety for the publish window comes from the **existing blob-grace backstop**, reused unchanged.

**Correction captured during proposal (important):** an earlier idea — a *manifest-grace* backstop by manifest mtime — is both **unnecessary and ineffective**, and is dropped:
- *Unnecessary for the publish window.* GC is **log-driven**: `drop_version`'s domain is versions that appear in some coordinate's log, and in C2 a version enters the log **atomically with becoming a pointer target** (the log event *is* the pointer move). So a freshly-published version is never "logged but unretained" during its own publish — there is no manifest to protect there. Its blobs (uploaded before any manifest references them) are covered by blob-grace, exactly as today.
- *Ineffective for the real race.* The genuinely dangerous window is dropping an **old** version concurrent with **promoting** it — and an old version's manifest mtime is old, so an mtime-grace would never protect it.

### The one race S3 reopens: version-drop vs concurrent promote → TLA
Promoting an out-of-retention version at the same moment GC drops it:
```
  GC: "no pointer targets v_old" → true           promote: "manifest v_old exists" → true
  GC: DELETE manifests/v_old + tombstone           promote: append event  stable→v_old
  ⇒ stable points at a deleted manifest → resolve → NotFound.
```
The SQL backend is bounded by transaction/row visibility (drop deletes the manifest row → a concurrent `set_pointer`'s manifest check sees it gone → `NotFound`; or the pointer insert is seen by drop → `Conflict`). A **transaction-less object store has no such serialization**, so this needs an explicit, verified protocol. Candidate closure (to confirm in TLA): a **two-phase CAS'd drop** — GC writes a `dropping/<v>` marker with put-if-absent, then re-scans for events referencing `v` and aborts (deletes the marker) if any appeared; `set_pointer` refuses to target `v` if a `dropping/<v>` marker exists. The marker is the shared serialization point that transactions gave us for free. **This change models the race in TLA (extending the GC family) and picks the minimal sufficient closure before enabling version reclamation on S3** — the same TLA-first discipline used for the lease/grace work.

### History is forever (tombstones, not pruning)
Immutable events can't be pruned, so `drop_version` reclaims the manifest + blobs and writes a `tombstones/<v>` marker; enumeration filters tombstoned versions so `list_versions`/`list_pointers` match SQL observationally, while the raw event log persists (tiny objects, far fewer than blobs — a slow, bounded metadata cost). Aligns with the append-only provenance history already shipped.

### `open_s3` and provisioning
```
open_s3("s3://bucket/repo")                 registry: repo/{manifests,coords,tombstones}; blobs: repo/blobs
open_s3("s3://bucket/repo", blob_url=…, storage_options=…)   split planes; one fsspec config
```
Real AWS S3 needs **no provisioning** (conditional writes on by default, all regions/buckets; strong RAW since 2020; IAM = Get/Put/Delete/List on the prefix; versioning NOT required). S3-compatible endpoints vary, so `open_s3` performs a one-time **conditional-write probe** and raises a clear error if the endpoint doesn't enforce put-if-absent.

## Risks / Trade-offs

- **Cold enumeration is O(events) GETs** (`list_log`, GC mark) → mitigate by parallelizing (as blob fetches already are); a best-effort per-coordinate checkpoint object is a documented future optimization, not v1.
- **`head` is LIST+GET, not a single GET** → bounded by a pointer's own move count (small); an optional overwritten `HEAD` hint is a future optimization.
- **Per-coordinate log order is timestamp-derived** → differential test asserts order per the merge rule, not strict SQL `seq`; only cross-host near-simultaneous commits differ, cosmetically.
- **S3-compatible endpoints** may lack conditional writes → the `open_s3` probe fails fast with guidance.
- **Metadata (event) objects are never reclaimed** → tiny and bounded relative to blobs; accepted as the price of append-only history.

## Migration Plan

Purely additive — a new backend and opener behind the unchanged port; no existing data, schema, or GC behavior changes. Rollback = don't ship the module. The TLA-verified drop guard gates only S3 version reclamation; the rest of the registry is independently shippable.

## Open Questions

- **Final drop-guard mechanism** — the two-phase CAS'd `dropping/<v>` marker is the leading candidate; the TLA model may prefer a per-version generation object or an accept-and-`fsck`-repair posture. Resolved by the model before implementing `drop_version`.
- **Where `open_s3` lives** — its own `s3-registry` requirement here (self-contained), vs. joining `open_local`/`open_cloud` under persistent-registry. Leaning self-contained.
