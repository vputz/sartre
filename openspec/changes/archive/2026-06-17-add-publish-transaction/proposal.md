## Why

The read core, version identity, and ordering model are settled, but the **write
path** — how a publish stores blobs, commits a manifest, and advances a pointer
without ever exposing a half-published state — is only sketched (memo §7.5). It
is the most correctness-critical part of the system: a wrong step ordering or a
mishandled concurrent publish corrupts what every reader sees. This change pins
the protocol and **proves its core safety invariant with a TLA+ model** before
any backend implements it.

## What Changes

- Specify the **publish transaction** as **full-replacement, fail-fast**:
  1. `store.put` each blob (idempotent; skip if `has`),
  2. `commit` the manifest (content-idempotent → version),
  3. `set_pointer(version, expected=start)` — atomic compare-and-swap.
  The ordering **blobs → manifest → pointer** is mandatory; the CAS is the only
  atomic primitive the protocol relies on.
- Define the **crash-safety** contract: a crash before the pointer advance leaves
  only GC-collectable orphans (unreferenced blobs / orphan manifests) and **no
  observable half-published state**; a crashed publish is recovered by re-running
  it (at-least-once + idempotency = exactly-once effect), needing no recovery log.
- Define **concurrency** behavior: the CAS guards on the tip read at start; on a
  conflict `set_pointer` raises (fail-fast) and `publish` surfaces it — no
  silent last-writer-wins, no auto-retry. The caller decides whether to re-read
  and re-publish.
- Land a **verified TLA+ model** (`model/Publish.tla`) proving `PointerSafe` (no
  dangling tip) and `LogConsistent` hold under all interleavings of concurrent
  publishers and crashes — with a `PointerFirst` bug toggle that demonstrates the
  wrong ordering violating `PointerSafe`.
- Record the **publish/GC race** as a requirement to be solved by the GC change,
  leaving an explicit grace-period hook.

## Capabilities

### New Capabilities
- `publish-transaction`: the publish protocol — step ordering, the CAS pointer
  advance, crash-safety, idempotent retry, fail-fast concurrency, and the
  invariants (no dangling tip, pointer/log consistency) verified by the TLA+ model.

### Modified Capabilities
<!-- None — this adds the write-path protocol; the read-side specs are unchanged. -->

## Impact

- **New artifacts**: `openspec/changes/add-publish-transaction/model/`
  (`Publish.tla`, `Publish_fixed.cfg`, `Publish_buggy.cfg`, `README.md`) — the
  verified model, promoted to the living spec on archive.
- **No implementation code**: this change is design + formal model only. The
  `Repository.publish` body and a backend that realizes the atomic CAS land with
  the reference-backend change, where the protocol's invariants are re-checked
  against real code with Hypothesis stateful tests.
- **Deferred (explicit non-goals)**: garbage collection and the publish/GC race
  (next TLA+ candidate); the in-memory/Delta backend implementation.
