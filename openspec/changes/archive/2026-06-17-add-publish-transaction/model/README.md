# Publish transaction — TLA+ model

Formal model of sartre's publish transaction (full-replacement, fail-fast on
conflict). It verifies that the **blobs → manifest → pointer** ordering is what
guarantees no reader ever follows a dangling reference, under every interleaving
of concurrent publishers and crashes.

## Files

- `Publish.tla` — the model. One coordinate / one pointer; each publisher
  publishes a fixed opaque version needing one blob; the datastore is an atomic
  KV. `PointerFirst` is a `CONSTANT` bug toggle.
- `Publish_fixed.cfg` — `PointerFirst = FALSE` (correct order). Expected **PASS**.
- `Publish_buggy.cfg` — `PointerFirst = TRUE` (pointer advanced first). Expected
  **CHECK_FAILED**.

## Invariants

- `TypeOk` — type-correctness (anti-vacuity guard).
- `PointerSafe` — the tip is always a committed manifest whose blobs are all
  stored (`tip = NoTip ∨ (tip ∈ committed ∧ BlobsOf(tip) ⊆ storedBlobs)`). This
  is the dangling-reference guard.
- `LogConsistent` — every logged tip was committed, and the current tip is the
  log's last entry (pointer/log stay in sync).

## Results (pinned TLC protocol: SANY → smoke → exhaustive → coverage)

| Config | Verdict | Detail |
|---|---|---|
| `Publish_fixed.cfg` | **PASS** | 115 states, depth 16; all invariants hold; all 5 actions fired (Begin 36, PutBlobs 28, Commit 12, Advance 16, Crash 22). |
| `Publish_buggy.cfg` | **CHECK_FAILED** | `PointerSafe` violated in 3 states: `Begin` → `Advance` sets `tip := p1` while `committed = {}` and `storedBlobs = {}` — a dangling tip, no crash required. |

**Conclusion:** the ordering is load-bearing. Advancing the pointer before the
manifest is committed and its blobs stored is reachable by a single uninterrupted
publisher and produces a tip that resolves to a non-existent manifest. The
correct order holds `PointerSafe` and `LogConsistent` across all interleavings,
including `Crash` (which only ever leaves GC-collectable orphans).

## Running

```bash
M="$(git rev-parse --show-toplevel)/openspec/changes/add-publish-transaction/model"
bash .claude/skills/tla-verify/scripts/run_tlc.sh "$M/Publish.tla" "$M/Publish_fixed.cfg"
```

Note: the runner `cd`s into the module directory, so pass the `.cfg` as an
**absolute path** (or a basename), not a path relative to your shell's cwd — a
relative path silently misfires and can report a spurious CHECK_FAILED.

## Scope / abstraction

- **Modelled:** concurrent publishers, the CAS pointer advance, crash + restart,
  the ordering of blob/manifest/pointer steps.
- **Abstracted away:** blob/manifest byte payloads (opaque ids), content hashing
  (`commit` idempotency is modelled as set-union), multiple coordinates/pointers.
- **Out of scope (other changes):** garbage collection and the publish/GC race
  (a grace-period hook is noted in the design, verified with GC later).
