## Context

Identity (`define-version-id`) and the ports (`add-core-ports`) are settled. What
remains on the write side is the publish transaction: the ordered sequence that
stores blobs, commits a manifest, and advances a pointer such that no reader ever
observes a half-published state, even under concurrent publishers and crashes.
This change pins the protocol and verifies its core safety property with TLA+
before implementation, per the project's design-vetting flow.

## Goals / Non-Goals

**Goals:**
- A publish protocol whose crash-safety and concurrency behavior are explicit.
- A machine-checked proof that the step ordering prevents dangling pointers.
- The four publish decisions recorded with rationale.

**Non-Goals:**
- Implementation (the `publish` body + a backend) — rides with the reference
  backend, where these invariants get Hypothesis stateful tests against real code.
- Garbage collection and the publish/GC race (next; a grace-period hook is noted).
- Incremental/amend publishes — full-replacement only here.

## Decisions

### D1 — Full-replacement publishes
A publish supplies the complete manifest; "add one model" is done client-side as
read-modify-publish. *Why*: uniform conflict semantics and a re-publish that
never needs to rebuild against a moved tip. *Alternative*: incremental/amend
(rejected for the core — more surface, rebuild-on-conflict).

### D2 — Ordering: blobs → manifest → pointer (proven load-bearing)
Store all blobs, then commit the manifest, then CAS the pointer. Nothing is
reader-visible until the final atomic pointer advance. *This is the invariant the
TLA+ model verifies* (see Results): the wrong order (pointer first) is reachable
by a single publisher and leaves the tip referencing an uncommitted manifest.

### D3 — Fail-fast concurrency via guarded CAS
`set_pointer(version, expected=tip-at-start)` advances only if the tip is
unchanged; on mismatch it raises `Conflict` and `publish` surfaces it. *Why*: no
silent last-writer-wins; the caller decides whether to re-read and re-publish.
*Alternative*: auto-retry (rejected — for full-replacement it is silent
clobber).

### D4 — Crash recovery by idempotent re-run
Blobs are content-addressed, `commit` is content-idempotent, the pointer move is
CAS — so a crashed publish is recovered simply by re-running it; no write-ahead
recovery log is needed (at-least-once retry + idempotency = exactly-once effect).
A crash before the pointer advance leaves only GC-collectable orphans.

### D5 — Atomic "advance pointer + append log"
The pointer advance and the commit-log append must be one atomic commit (a reader
never sees the tip moved without the log entry, or vice versa). The backend
realizes this transactionally (e.g. Delta's transaction log *is* the commit log;
its commit version supplies `seq`). The model treats it as one atomic action.

## TLA+ audit verdict

| Candidate | Invariants at stake | Abstract state (<6 vars) | Abstracted away | State-space | Verdict |
|---|---|---|---|---|---|
| publish transaction | no dangling tip; pointer/log consistency; no lost update under concurrent CAS; crash leaves only orphans | `storedBlobs`, `committed`, `tip`, `log`, `pub` (per-publisher phase+start); bounds: 2 publishers | blob/manifest bytes (opaque ids), content hashing (`commit` = set union), multiple coordinates | small | **MODEL** |

Multiple interleaving writers + crash + an idempotency/ordering claim — squarely
a TLC problem. Environment actor: the publishers themselves, stepping
nondeterministically with a `Crash` action. Sharpest invariant first:
`PointerSafe`.

## Results

Pinned protocol (SANY → smoke → exhaustive → coverage), `model/Publish.tla`:

| Config | Verdict | Detail |
|---|---|---|
| `Publish_fixed.cfg` (`PointerFirst=FALSE`) | **PASS** | 115 states, depth 16; `TypeOk`/`PointerSafe`/`LogConsistent` hold; all 5 actions fired. |
| `Publish_buggy.cfg` (`PointerFirst=TRUE`) | **CHECK_FAILED** | `PointerSafe` violated in 3 states: `Begin` → `Advance` sets `tip := p1` while `committed={}` and `storedBlobs={}` — a dangling tip, no crash needed. |

The correct ordering holds the safety invariant across every interleaving
including `Crash`; the wrong ordering breaks it for a lone publisher. The model is
the refinement anchor for the eventual `Repository.publish` implementation.

## Risks / Trade-offs

- **Model abstraction** → the model proves the *protocol*, not the code. Mitigation:
  re-check the same invariants against the real implementation with Hypothesis
  `RuleBasedStateMachine` in the reference-backend change.
- **Fail-fast surfaces conflicts to callers** → more caller-side handling, but it
  is the honest behavior; a helper can wrap retry where a caller wants it.
- **Publish/GC race left open** → a concurrent GC could delete a blob an in-flight
  publish just wrote. Deferred to the GC change with a grace-period hook
  ("don't collect blobs younger than T"); flagged so it is not forgotten.

## Open Questions

- Should the `seq`/atomic-log realization be specified per-backend now, or left to
  each backend (Delta-history-as-log vs an explicit log table)?
- Is a built-in bounded-retry `publish` helper worth offering alongside the
  fail-fast core, or left entirely to callers?
