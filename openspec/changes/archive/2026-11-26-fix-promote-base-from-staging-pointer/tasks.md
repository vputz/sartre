# Tasks

## 1. Port and model

- [x] 1.1 Add `base: Version | None = None` to `PointerMove` in `src/sartre/ports.py` and add the optional `base` keyword to the `Registry.set_pointer` protocol signature. Verify: `pyright` clean; `PointerMove(...)` constructs with and without `base`.
- [x] 1.2 Add `pointer_base(coord, name) -> Version | None` to the `Registry` protocol in `src/sartre/ports.py`, documented to return the base of the pointer's newest advance (reading past a later deletion tombstone), else `None`. Verify: `pyright` clean; every backend class satisfies the protocol.

## 2. In-memory backend (reference oracle)

- [x] 2.1 In `src/sartre/memory.py`, record `base` on the appended `PointerMove` in `set_pointer` (only when passed), and implement `pointer_base` by scanning the pointer's in-memory moves for the newest advance carrying a base, past any later deletion. Verify: unit test records a base and reads it back via `pointer_base` and `list_pointer_history`, including after a delete.

## 3. SQL backends (sqlite + postgres)

- [x] 3.1 In `src/sartre/_sql.py`, add the nullable `base_version` column to the `pointer_moves` schema, write it in `set_pointer`, surface it in `list_pointer_history`, and implement `pointer_base` as a single query for the pointer's newest advance row with non-null `base_version`. Verify: unit test on sqlite records and reads a base, and `pointer_base` returns it without scanning history.
- [x] 3.2 Add the additive migration: `ALTER TABLE pointer_moves ADD COLUMN base_version TEXT` in the `_migrate` hook (`src/sartre/sqlite.py` and `src/sartre/postgres.py` as needed), idempotent and non-rewriting. Verify: a test opens a DB created without the column (simulate by dropping it / old schema), reopens, and finds the column present, prior rows intact with `base_version` null.

## 4. S3 backend

- [x] 4.1 In `src/sartre/s3.py`, include the optional `base` key in the appended pointer event object in `set_pointer` (staging advances only), and read it in the event parsers so `list_pointer_history` exposes it; old events without the key read as `None`. Verify: moto test asserts the event JSON carries `base` for a staging advance and `None` otherwise.
- [x] 4.2 Implement `pointer_base` on `S3Registry` by scanning only the named pointer's own event stream for the newest advance's `base`, reading past a later pointer-tombstone, listing no other pointer. Verify: moto test recovers the base from a single pointer's stream, including after the pointer was deleted (tombstone above the advance).

## 5. Repository facade

- [x] 5.1 In `src/sartre/repository.py`, make `publish_over(stage=True)` pass `base=base_snap.version` through `_write_version` to `set_pointer`, and stop writing `derived_from` into manifest metadata. `_write_version` gains an optional `base` parameter defaulting to `None`. Verify: a staged version's manifest metadata no longer contains `derived_from`, and the staging pointer's move records the base.
- [x] 5.2 Rewrite `Repository.promote`: no-op if head already at target (before base lookup); recover the base via `registry.pointer_base` when `staged` is an `Alias`; refuse (naming staging-alias / re-stage / `force`) when `staged` is a `Pin`/`Head` or no base is recorded, unless `force`; on base-CAS `Conflict`, re-read head and return success if it now equals the target, else raise `Conflict`. Drop all `derived_from` reads. Verify: unit tests for each branch (see §6).
- [x] 5.3 Confirm `AsyncRepository.promote` wrapper still delegates correctly after the rewrite. Verify: async test promotes a staged version.

## 6. Tests

- [x] 6.1 Regression (the reported bug), parametrized over memory + sqlite: stage a tree that already exists as a version (cross-environment adoption, and revert-to-earlier-tree) and assert `promote` succeeds and advances head. Verify: tests fail against the pre-fix `derived_from` path and pass after.
- [x] 6.2 Concurrent-duplicate: head already at the target version → `promote` is a success no-op; and the step-2→step-4 race (advance head to the target between the no-op read and the CAS) → `promote` re-checks and returns success, not `Conflict`. Verify: tests cover both the pre-CAS and post-CAS no-op paths.
- [x] 6.3 Lost-update: head moved to a *different* tree since staging → `promote` raises `Conflict`. Verify: test asserts `Conflict` and head unchanged.
- [x] 6.4 Non-alias and no-base: `promote(Pin(v))` / `Head` without `force` refuses; with `force` does a last-writer move; an alias with no recorded base refuses unless `force`. Verify: tests for each.
- [x] 6.5 `pointer_base` tombstone/concurrent-delete: recover the base after the staging pointer was deleted (tombstone above the advance), across memory + sqlite + S3. Verify: tests assert recovery past the tombstone.
- [x] 6.5a Hypothesis property over the `pointer_base` selection logic: for any sequence of pointer events (advances with/without a base, deletions, recreations), `pointer_base` returns the base of the newest advance event and `None` iff no advance recorded one — the one new non-concurrency function, tested against the real code. Verify: `hypothesis` property passes on each backend.
- [x] 6.6 Hypothesis property: for any sequence of stage/promote/delete/publish operations, head only ever advances to a version whose recorded base equals head-at-promote-time, or to a version head already held (no-op); head never silently overwrites a different tree. Verify: `hypothesis` stateful/property test passes.
- [x] 6.7 Migration property/test: a `pointer_moves` table without `base_version` opens, migrates, and reads prior rows with `base_version` null; re-open is a no-op. Verify: test on sqlite (mirror the `to_version` migration test).
- [x] 6.8 Differential: extend the S3-vs-reference differential harness to cover `base` and `pointer_base` over staging advances, deletions, and re-stages. Verify: differential test agrees at every step.

## 7. Formal model

- [x] 7.1 Add `model/Promote.tla` (or extend `model/PublishOver.tla`) modelling `promote` with the no-op head-read (step 2) and base-CAS (step 4) as distinct atomic steps, an interleaving publish between them, and an interleaving concurrent `delete_pointer` between alias-resolution and `pointer_base`; a `BaseSource` toggle (`FromManifest` vs `FromPointer`) plus a dedup toggle. Provide ok + bad configs (bad = manifest-sourced base under dedup, which must CHECK_FAILED). Verify: run via the `tla-verifier` agent — ok config passes non-vacuously, bad configs fail as designed.

## 8. Gates

- [x] 8.1 `ruff check` clean and `pyright` 0 errors across changed files.
- [x] 8.2 Full `pytest` suite green (including the new tests).
- [x] 8.3 `openspec validate fix-promote-base-from-staging-pointer --strict` passes.

## Workflow follow-up

- Bump `pyproject.toml` to `0.3.0` (minor — removing `derived_from` is externally visible) and sync `uv.lock`.
- Commit the change (propose → implement → release split, per the jj workflow), push, and archive the OpenSpec change (`openspec archive fix-promote-base-from-staging-pointer`).
- Cut the gh release with notes covering the `promote` fix and the `derived_from` removal; publish to PyPI.
- Optionally notify the QS1 team that the fix shipped so they can drop the `#2156` workaround (their test asserting `derived_from` needs updating).
