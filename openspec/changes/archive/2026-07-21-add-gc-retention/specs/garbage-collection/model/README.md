# Garbage collection — TLA+ model

Formal model of sartre's garbage collector racing concurrent publishes. It verifies
that the **lease discipline** (a publish holds a `(version, hashes)` lease for its whole
duration) plus **re-validation at sweep time** is what keeps GC from ever deleting a blob
a committed manifest references, under every interleaving of publishers, GC, and crashes.

## Files

- `GC.tla` — the model. One coordinate / one pointer, retention keeps only the pointer
  target (`keep_last_n = 0`, the config that most stresses the race). Each publisher `p`
  publishes an opaque version `p` needing one blob `p`, bracketed by a lease. GC is
  mark-and-sweep **split** into `GCMark` (snapshot candidates) and `GCSweep` (delete,
  re-validating against current state). `LeaseDisabled` is a `CONSTANT` bug toggle.
- `GC_fixed.cfg` — `LeaseDisabled = FALSE` (lease on). Expected **PASS**.
- `GC_buggy.cfg` — `LeaseDisabled = TRUE` (no lease). Expected **CHECK_FAILED**.

## Invariants

- `TypeOk` — type-correctness (anti-vacuity guard).
- `BlobSafe` — every committed manifest's blobs are all stored
  (`∀ v ∈ committed: BlobsOf(v) ⊆ storedBlobs`). The blob lease protects the
  put→commit window that would otherwise break this.
- `TipSafe` — the pointer never dangles (`tip = NoTip ∨ (tip ∈ committed ∧
  BlobsOf(tip) ⊆ storedBlobs)`). The version lease protects the commit→advance window
  (GC must not drop a just-committed, not-yet-pointed manifest an in-flight publish will
  point to).

## Design bug found (and fixed) by TLC

The first model consulted the lease only at `GCMark` and had `GCSweep` trust the stale
snapshot. TLC falsified `GC_fixed` with a 9-state trace:

> `Begin p1` → `PutBlobs p1` → `Crash p1` (blob orphaned, lease released) → `GCMark`
> (blob marked sweepable) → `Begin p1` + `PutBlobs p1` (**content-addressed idempotent
> reuse** — the put is a no-op, the blob is now live under a *fresh* lease) → `GCSweep`
> (stale mark still deletes it) → `Commit p1` → `BlobSafe` broken.

Because sartre is content-addressed, a blob marked for deletion can be reused/adopted by
a new leased publish in the mark→sweep window. The fix: **`GCSweep` re-validates against
current state** — drop a candidate manifest only if still unretained now, delete a
candidate blob only if referenced by no surviving committed manifest and named by no live
lease now. The mark only *bounds* the candidate sets. The reference `Repository.gc`
mirrors this (recompute the live set immediately before the delete loop).

## Results (pinned TLC protocol: SANY → smoke → exhaustive → coverage)

| Config | Verdict | Detail |
|---|---|---|
| `GC_fixed.cfg` | **PASS** | Exhaustive: 1420 distinct states, depth 20, queue empty. `TypeOk`/`BlobSafe`/`TipSafe` hold across all interleavings, including the reuse and commit-after-mark adoption cases. |
| `GC_buggy.cfg` | **CHECK_FAILED** | `BlobSafe` violated in a 6-state trace: `Begin → PutBlobs → GCMark → GCSweep` deletes the uploaded blob in the unprotected pre-commit window, then `Commit` dangles. |

**Coverage note:** in `GC_fixed`, all eight actions fire. `GCAbort` shows `0` *new
distinct* states but 1301 total firings — it is the exact inverse of `GCMark` (discards
the mark, returning to the already-visited pre-mark state), so it contributes no new
state by construction. This trips the coverage gate's first-column heuristic; it is a
false positive, not dead code. Judge `GCAbort` by total firings, not distinct states.

**Conclusion:** the lease discipline is load-bearing, but only together with
re-validation at delete time. The lease alone (checked at mark) is insufficient for a
content-addressed store; TLC surfaced the reuse race before any code was written.

## Running

The runner `run_tlc.sh` `cd`s into the module directory, so a **relative** cfg path
silently misfires and reports a spurious `CHECK_FAILED`. Always pass an **absolute** cfg
path. Run each config through SANY parse → smoke (simulate) → exhaustive → coverage.
