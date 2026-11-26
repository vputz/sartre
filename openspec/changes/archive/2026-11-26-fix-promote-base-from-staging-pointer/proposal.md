# Proposal

## Why

`Repository.promote` recovers its compare-and-swap base from the staged version's
`derived_from` value, which is stored in **shared manifest metadata**. Manifests are
content-addressed and first-writer-wins: metadata is not part of the version hash and is
never rewritten on a re-commit. So whenever a staged tree already exists as a version —
another environment published the identical tree, or an environment reverts to an earlier
tree — the manifest keeps the *first* writer's `derived_from`, not the base this staging was
derived from. `promote` then compare-and-swaps head against the wrong base and raises a
false `Conflict` ("head moved") even though head never moved. This is a production bug (QS1
CI run 45600513590; the downstream team shipped a client-side workaround in
`Quantum-Signals/QS1#2156`).

The root cause is a placement mistake: the derive base is a fact about the *operation*
(what head you derived from), not about the *tree*. It must live somewhere that is not
deduplicated by content.

## What Changes

- `publish_over(stage=True)` SHALL record the derive base (the base's resolved version) on
  the **staging pointer's move event** — a per-operation, append-only, non-deduplicated
  record — instead of relying on the content-addressed manifest's `derived_from` metadata as
  the source of truth for promotion.
- `publish_over(stage=True)` SHALL stop writing `derived_from` into the new version's manifest
  metadata. That field was only ever written by staging, its sole purpose was feeding
  `promote`'s base compare-and-swap, and that base now lives on the pointer-move plane — so it
  is vestigial and, because it is stored in first-writer-wins shared metadata, actively
  misleading. Removal is forward-only — content-addressed manifests are never rewritten, so
  existing manifests keep their stale `derived_from`; the protection is that nothing reads it
  anymore (see design D6).
- `promote(coord, staged)` SHALL recover its compare-and-swap base from the **staging
  pointer's** recorded base rather than from manifest metadata, when `staged` is an alias. The
  caller supplies nothing new — the existing signature keeps working for both the library and
  the standalone `sartre promote <coord:alias>` CLI.
- The registry port SHALL gain `pointer_base(coord, name) -> Version | None`, returning the base
  recorded on a pointer's current advance move as a **single-pointer** read on every backend.
  `promote` uses it for recovery instead of scanning the whole coordinate's move history.
- `promote` SHALL treat "head already points at the target version" as a success no-op — both
  before any base lookup, and when the base compare-and-swap loses to a concurrent move that
  left head at that same version — so a concurrent or duplicate publish of the same
  content-addressed tree is never reported as a conflict.
- When no base can be recovered — `staged` is not an alias (a `Pin` or `Head` ref), or the
  staging pointer records none (created before this change, or a hand-made alias) — `promote`
  SHALL refuse with a clear message unless the existing `force` flag requests a plain
  last-writer move. Recovery is to promote via the staging alias, re-run the derive (idempotent:
  same staging pointer, now recording the base), or `force`.
- The pointer-move record SHALL carry an optional `base` field across the port and every
  backend. SQLite/Postgres gain a nullable column via an additive `ADD COLUMN` migration
  applied on open (no row rewrite, no backfill); S3 event objects gain an optional JSON key
  (schemaless — absence reads as `None`); the in-memory oracle records it in memory.
- **No signature change.** `promote` keeps its current signature; the lost-update guard from
  `publish-over-staging` (0.2.1) is preserved — a head that moved to a *different* tree since
  staging still raises `Conflict`. The one externally visible change beyond the bug fix is the
  removal of `derived_from` from manifest metadata.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `repository-facade`: `promote` recovers the derive base from the staging pointer (not
  shared manifest metadata) and treats head-already-at-target as a success no-op;
  `publish_over(stage=True)` records the derive base on the staging pointer's move and no
  longer writes `derived_from`.
- `registry-port`: `PointerMove` gains an optional `base` field; `set_pointer` accepts and
  records it on the appended move; and the port gains `pointer_base(coord, name)` returning the
  base on a pointer's current advance move via a single-pointer read.
- `version-log`: the append-only pointer-move history row gains the optional `base` field.
- `persistent-registry`: the durable `pointer_moves` table gains a nullable `base_version`
  column (additive `ADD COLUMN` migration on open), and the backend implements the `pointer_base`
  read as a single latest-move-row query.
- `in-memory-backend`: the reference oracle records `base` on pointer moves and implements
  `pointer_base`, so it stays a faithful differential-testing oracle.
- `s3-registry`: pointer-move event objects carry an optional `base` key (schemaless, no
  migration), and `pointer_base` reads it from the pointer's own event stream (skipping a later
  deletion tombstone to the newest advance); included in differential agreement with the
  reference backend.

## Impact

- **Code**: `src/sartre/repository.py` (`promote`, `publish_over` — stop writing
  `derived_from`, record base on the staging move), `src/sartre/ports.py` (`PointerMove`,
  `set_pointer` signature, new `pointer_base` method), `src/sartre/memory.py`,
  `src/sartre/_sql.py` (schema + `_migrate`), `src/sartre/sqlite.py` (additive `ADD COLUMN`),
  `src/sartre/postgres.py` (additive `ADD COLUMN`), `src/sartre/s3.py` (event key). Each backend
  implements the new `pointer_base` read. The `sartre promote` CLI command needs no signature
  change — it keeps passing nothing and recovery is automatic.
- **API**: `promote`'s signature is unchanged. The downstream QS1 workaround (`#2156`) becomes
  optional — the clean `publish_over(stage=True)` + `promote` path works again. `derived_from`
  is removed from manifest metadata; a reader that consulted it (e.g. the QS1 client's current
  test) must stop relying on it.
- **Data**: SQLite/Postgres additive nullable column, auto-migrated on open; no backfill.
  S3 and memory need no migration. Old stores remain readable; old library versions ignore
  the new field. The only active backend in use is S3 (no migration there); the SQL migration
  exists to keep the registry-port contract and the differential test oracle consistent.
- **Release**: a bug fix with one externally visible change (removing `derived_from`), so it
  ships as a **minor** release (`0.3.0`).
