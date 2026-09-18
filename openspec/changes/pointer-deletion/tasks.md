## 1. TLA: the pointer-drop race (do first)

- [x] 1.1 `openspec/changes/pointer-deletion/model/PointerDrop.tla`: model `delete_pointer` (append a tombstone event at `seq+1` via gap-free put-if-absent CAS) racing a concurrent `set_pointer` re-creating/advancing the same pointer's event stream. Invariant: a reader never resolves a deleted pointer to a stale value, and delete+concurrent-recreate resolves to exactly one coherent tail outcome (the sequence winner), never torn/half-applied. Toggles `SkipTombstonedTail` and `TombstoneIsCasEvent`, each with an ok cfg (TRUE) and a bad cfg (FALSE) showing it is load-bearing (echoing `S3Drop_bad_noskip` / `_bad_order`).
- [x] 1.2 Run through the `tla-verifier` agent (SANY → smoke → exhaustive → coverage). Capture the verdict; record in `design.md` that the ok config holds non-vacuously and each FALSE toggle breaks the invariant.

## 2. History model: nullable move target

- [x] 2.1 `PointerMove.to_version: Version | None` in `ports.py`; update the docstring (None = deletion). Fix every in-tree consumer of `to_version` to tolerate `None` (CLI `history`/`ops.pointer_history`, any formatting).
- [x] 2.2 SQL: relax `pointer_moves.to_version` to nullable via an idempotent on-open migration — Postgres `ALTER COLUMN … DROP NOT NULL`; SQLite rebuild-table (create-new → copy → swap) guarded by a constraint check. Add a test that opening a pre-change DB migrates and keeps prior rows.

## 3. `delete_pointer` on the port + all backends

- [x] 3.1 Add `delete_pointer(coord, name, *, expected, actor, reason)` to the `Registry` Protocol in `ports.py` with the CAS + idempotent-absent + one-PointerMove(to_version=None) contract.
- [x] 3.2 MemoryRegistry: implement under the lock — CAS-check, pop, append the deletion move; absent+`expected=None` → no-op.
- [x] 3.3 SqliteRegistry/PostgresRegistry (`_sql.py`): guarded `DELETE FROM pointers WHERE … AND version=?` (rowcount==1 → CAS; 0 with `expected=None` and no row → no-op, else `Conflict`) inside `_tx()`, plus the `pointer_moves` row with null `to_version`.
- [x] 3.4 S3Registry (`s3.py`): append a pointer-tombstone sentinel event at `seq+1` via `_put_if_absent` (loser retries on `FileExistsError`); extend `_pointer_scan` so a tombstone tail → effective value unset; `_resolve_ref`/`head`/`list_pointers` treat it as `NotFound`/omit; `list_log`/`list_pointer_history` retain the event. A later advance after a tombstone re-creates the pointer.

## 4. Facade: `delete_pointer` + `promote`

- [x] 4.1 `Repository.delete_pointer(coord, name, *, actor, reason)` — read current, CAS-delete; refuse `head` with a clear error before any registry write; absent → no-op. `AsyncRepository` wrapper.
- [x] 4.2 `Repository.promote(coord, staged, *, pointer="head", force=False, actor, reason)` — resolve `staged` to a version, read its `derived_from` metadata; CAS `pointer` against that base (moved → `Conflict`); absent `derived_from` → refuse unless `force` (then plain last-writer `point`). `AsyncRepository` wrapper.
- [x] 4.3 `publish-over --stage` records `derived_from=<base version>` in the staged version's metadata (thread it through the CLI stage path into `publish_over`'s `metadata`).

## 5. CLI

- [x] 5.1 `ops.delete_pointer` + `ops.promote` (framework-free) beside `move_pointer`.
- [x] 5.2 `sartre delete-pointer <coord:alias>` in `app.py` — parse via `refs.parse_ref`+`pointer_name`, refuse `head`, `--as`/`-m`, human + `--json`; conflict → advise retry.
- [x] 5.3 `sartre promote <coord[:pointer]> <staging|@version>` in `app.py` — base-CAS promote, delete the spent staging alias on success, `--force` for absent-base last-writer, `--as`/`-m`, human + `--json`.

## 6. Tests

- [x] 6.1 delete_pointer: delete-then-resolve → NotFound; CAS conflict on concurrent move; idempotent absent no-op; deletion recorded with `to_version=None`; run across all four backends via the equivalence/oracle suite.
- [x] 6.2 Repository: refuse to delete `head`; `promote` clean stage advances head; `promote` with moved head → `Conflict` + no clobber; `promote` absent `derived_from` refuses (and `--force`/`force=True` last-writer path works).
- [x] 6.3 S3-specific: tombstone tail hides the pointer, history retains the delete event, a post-tombstone advance re-creates it; delete racing an advance leaves exactly one coherent outcome.
- [x] 6.4 Property-based (Hypothesis) where it fits: stateful pointer set/delete/promote sequence keeps history append-only and resolve consistent with the last non-tombstone tail.
- [x] 6.5 CLI e2e: `delete-pointer` removes a staging alias and refuses `head`; `--stage` writes `derived_from`; `promote` advances head and cleans up the staging alias, refuses when head moved; `--json` shapes.

## 7. Gates

- [x] 7.1 `ruff` clean, `pyright` clean, full default suite green.
- [x] 7.2 `openspec validate pointer-deletion --strict` passes.
