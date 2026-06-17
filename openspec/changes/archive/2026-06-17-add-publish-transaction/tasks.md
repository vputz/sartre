## 1. Audit

- [x] 1.1 Audit the publish design for TLA+-shaped risk; record the verdict table in `design.md` (verdict: MODEL — concurrent writers + crash + ordering/idempotency claim)

## 2. Model

- [x] 2.1 Write `model/Publish.tla`: full-replacement, fail-fast protocol with `Begin`/`PutBlobs`/`Commit`/`Advance`(CAS)/`Crash` actions, a `PointerFirst` bug toggle, and a source-comment refinement trail to `publish()`
- [x] 2.2 Write invariants: `TypeOk`, `PointerSafe` (no dangling tip), `LogConsistent` (pointer/log in sync)
- [x] 2.3 Write `Publish_fixed.cfg` (correct order, expect PASS) and `Publish_buggy.cfg` (pointer-first, expect CHECK_FAILED), bounded to 2 publishers

## 3. Verify

- [x] 3.1 Run `Publish_fixed.cfg` through the pinned TLC protocol via the `tla-verifier` agent — expect PASS with full action coverage
- [x] 3.2 Run `Publish_buggy.cfg` — expect CHECK_FAILED on `PointerSafe` with a short counterexample
- [x] 3.3 Record both results in `design.md` and `model/README.md`

## 4. Specify

- [x] 4.1 Write the `publish-transaction` spec: full-replacement, ordering, idempotent steps + crash recovery, fail-fast CAS, atomic pointer+log, GC interlock hook
- [x] 4.2 Confirm `openspec validate add-publish-transaction` passes
