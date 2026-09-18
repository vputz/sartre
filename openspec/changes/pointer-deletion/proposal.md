## Why

`publish-over --stage` lands a derived version on a throwaway `staging-<hex>` pointer, but there is no way to remove that pointer once the stage is verified (or abandoned) — the Registry has never had a pointer-deletion primitive, so staging pointers accumulate forever and no alias can ever be retired. And the `--stage` → verify → promote flow still has a lost-update hole: the direct advance is base-CAS-safe, but promoting a staged version with plain `sartre point` CASes head against its *current* value, not the version's base, so a concurrent publish during verification is silently dropped. `--stage` was shipped ahead of both, with its help pointing here.

## What Changes

- **New `Registry.delete_pointer(coord, name, *, expected, actor, reason)`** on the port and all four backends. Compare-and-swap on `expected` (raises `Conflict` on mismatch). Deleting an absent pointer with `expected=None` is a no-op (idempotent cleanup).
  - SQL: `DELETE FROM pointers WHERE … AND version=?` guarded by `rowcount == 1`, inside the existing `_tx()`.
  - Memory: pop under the lock after the CAS check.
  - S3: a pointer has no row — it is an append-only event stream. Deletion appends a **pointer-tombstone sentinel event** at `seq+1` via the same `_put_if_absent` CAS; `_pointer_scan` treats a tombstone tail as "unset". `resolve`/`list_pointers` skip a tombstoned-tail pointer; `list_pointer_history` retains the delete event (history is forever).
- **`PointerMove.to_version` becomes `Version | None`** (None records a deletion); the SQL `pointer_moves.to_version` column is relaxed to nullable (schema migration). A successful delete appends one `PointerMove` with `to_version=None` + actor/reason.
- **New `Repository.delete_pointer(coord, name, *, actor, reason)`** (reads current value, CAS-deletes) — **refuses `head`** (a coordinate's identity, not a removable label) — plus an `AsyncRepository` wrapper.
- **New `Repository.promote(coord, staged_ref, *, pointer="head", actor, reason)`** — the base-CAS safe promote. Reads the staged version's `derived_from` metadata (its base) and CASes `pointer` against that base, so a publish that moved head during verification → `Conflict`, never a silent lost update. Absent `derived_from` (a hand-made alias or a pre-feature stage) → refuse with a clear message unless forced to a plain last-writer move.
- **`publish-over --stage` now records `derived_from=<base version>`** in the staged version's metadata — the hook `promote` reads. (Today `--stage` records no lineage link.)
- **CLI**: `sartre delete-pointer <coord:alias>` (refuses `head`); `sartre promote <coord[:pointer]> <staging-alias|@version>` (base-CAS promote, then optionally deletes the spent staging pointer). Both mirror publish's `--as`/`-m` flags.
- **TLA**: a new model for the pointer-drop race on the transaction-less S3 store — `delete_pointer` (append tombstone at `seq+1`) racing a concurrent `set_pointer` re-creating/advancing the same pointer name. This is **not** covered by `S3Drop.tla` (that model is *version*-keyed; this is *pointer/event*-keyed).

## Capabilities

### New Capabilities
<!-- none — this extends existing capabilities -->

### Modified Capabilities
- `registry-port`: adds the `delete_pointer` CAS contract; `PointerMove.to_version` becomes optional (None = deletion).
- `repository-facade`: adds `Repository.delete_pointer` (refuses `head`) and `Repository.promote` (base-CAS promote reading `derived_from`); `AsyncRepository` wrappers.
- `s3-registry`: adds pointer-tombstone event semantics (append-only sentinel at `seq+1`; resolve/list skip a tombstoned tail; history retains it).
- `persistent-registry`: `pointer_moves.to_version` relaxed to nullable (schema migration); `delete_pointer` as a single conditional `DELETE`.
- `in-memory-backend`: `delete_pointer` under the lock with CAS.
- `version-log`: pointer-move history records a deletion (`to_version=None`).
- `cli`: `delete-pointer` and `promote` commands; `publish-over --stage` records `derived_from`.

## Impact

- **APIs**: additive `Registry.delete_pointer`, `Repository.delete_pointer`, `Repository.promote`, `AsyncRepository` wrappers; `PointerMove.to_version` widened to `Version | None` (a type change consumers of the history must tolerate).
- **Schema**: `pointer_moves.to_version` becomes nullable (SQLite + Postgres migration).
- **Code**: `ports.py`, `memory.py`, `_sql.py`, `sqlite.py`, `postgres.py`, `s3.py`, `repository.py`, `cli/app.py`, `cli/ops.py`, `cli/refs.py`; new `model/PointerDrop.tla` + configs.
- **Non-goals**: no cascading version GC on pointer delete (versions persist until retention GC); no whole-coordinate deletion; no change to `publish`/`publish_over` semantics beyond `--stage` writing `derived_from`.
