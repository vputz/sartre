# Design

## Context

Terms (defined fully in `openspec/specs/`): a **coordinate** is a named line of history that
owns a set of mutable **pointers**; `head` is the primary pointer and an **alias** is any other
named pointer; a **staging pointer** is an ephemeral alias created per `publish_over(stage=True)`;
a **snapshot** (`base_snap`) is a resolved version together with its entries.

See `proposal.md` — Why. The one fact the design leans on: manifests are content-addressed and
first-writer-wins, and manifest metadata is not part of the version hash and is never rewritten
on a re-commit — so a `derived_from` stored there is the first writer's, not this operation's,
whenever the staged tree already exists.

Relevant existing structure this design reuses:

- A **staging pointer** is created fresh by each `publish_over(stage=True)`. Its name is
  content-derived from `(base_version, affected_paths)`, so a derive from a *different* base
  is a *different* staging pointer. These pointers are ephemeral (created → verified →
  promoted → deleted within one publish).
- Pointer moves are an **append-only, per-pointer history** recorded by `set_pointer` and
  exposed via `list_pointer_history`. This history is *not* content-deduplicated, survives
  pointer deletion, and is already carried durably by every backend. `PointerMove` recently
  gained a nullable `to_version` for deletions via the same cross-backend mechanism this
  change follows.

## Goals / Non-Goals

**Goals:**
- `promote(coord, staged)` compare-and-swaps against the *true* derive base even when the
  staged tree already exists as a version — with no new argument required of the caller.
- Preserve the lost-update guard: a head that moved to a *different* tree since staging still
  raises `Conflict`.
- A concurrent/duplicate publish of the *same* tree (head already at the target version) is a
  success no-op, not a conflict.
- Keep the standalone `sartre promote <coord:alias>` CLI working — the human passes nothing.

**Non-Goals:**
- Changing how versions or manifests are hashed, deduplicated, or committed.
- Changing any manifest metadata other than removing the staging-only `derived_from` field.
- Backfilling `base` onto historical pointer-move records.
- Reworking the QS1 client; its workaround (`#2156`) becomes optional once this ships (its
  test that reads `derived_from` is theirs to update).
- Adding a caller-supplied `expected=` override to `promote` (considered and cut as YAGNI — it
  overlaps the existing `Repository.point(..., expected=)` primitive).

## Decisions

### D1 — Record the derive base on the staging pointer's move, not on the manifest

The derive base is a fact about the *operation* (what head you derived from), not about the
*tree*. Its correct home is the per-operation, non-deduplicated pointer-move record.
`publish_over(stage=True)` records `base = base_snap.version` on the move that advances the
staging pointer. `promote` recovers it with a focused single-pointer `pointer_base` lookup
(see D2) instead of from manifest metadata.

**Why the staging pointer and not the manifest.** The staging pointer name already encodes
`(base, affected_paths)`, so distinct bases ⇒ distinct pointers ⇒ distinct recorded bases —
the exact disambiguation the shared manifest cannot provide. The move record survives the
pointer's later deletion (append-only history), so recovery is robust even to a staging pointer
deleted concurrently during `promote` — `pointer_base` reads the newest advance event even when a
later deletion tombstone sits above it (see D2).

**Alternatives rejected:**
- *Seed the staging pointer at its base so the base rides the existing `from_version` field*
  (`None -> base`, then `base -> staged`; `promote` recovers the base from the **seed move** —
  the one whose `from_version` is `None` — which stays correct even when the content-derived
  staging name collides across re-stages). This is correct and would eliminate the new field
  and the SQL migration entirely. Rejected for this change because it converts the staging
  advance from one atomic compare-and-swap into a **two-phase seed+advance with a race on the
  seed**, adding concurrency surface to the one path a bug-fix release should keep conservative;
  because the only backend in active use is S3, so the migration it avoids is one we would not
  run in production anyway; and because `from_version==base` would hold only because we
  deliberately seed it, so a future maintainer could "optimize away" the seemingly pointless
  seed and silently reintroduce the bug. The explicit `base` field keeps staging a single
  compare-and-swap (CAS) and states the base at the call site. (Recorded because it is a genuine,
  cheaper alternative — revisit it if the storage change ever proves costly.)
- *Caller always supplies `expected=`* (what QS1's workaround does): correct for the library,
  but the bare CLI has nothing to pass. A caller that does track its own base already has
  `Repository.point(..., expected=)`, so a `promote(expected=)` would merely duplicate it — cut
  as YAGNI; add it only if a concrete non-CLI caller later needs it.
- *Drop the base entirely; CAS current head + no-op-if-equal*: reintroduces the lost-update
  race that `publish-over-staging` (0.2.1) closed.
- *Encode the base inside the free-text `reason` field*: avoids a SQL column but is brittle
  (parsing structured data out of free text). Since the migration is the cheap additive kind
  (D3), spend it on a real field.

### D2 — Carry `base` as an optional field on `PointerMove` / `set_pointer`

Add `base: Version | None` to `PointerMove` and an optional `base: Version | None = None`
keyword to `Registry.set_pointer`, threaded to the appended move. It is populated **only for
staging advances**; every other move (publish, plain repoint, delete) records `base=None`,
since there `from_version` already is the CAS base. The spec states `base` is defined only for
staging advances and MUST NOT be consulted for other move kinds. This mirrors the existing
move-kind-dependent `to_version` (null = deletion), so the cross-backend pattern is established.

`Repository.publish_over` passes `base=base_snap.version` down through `_write_version` to
`set_pointer` when `stage=True`; `_write_version` gains an optional `base` parameter
defaulting to `None` so `publish` and non-staged `publish_over` are unaffected.

Recovery is a **single-pointer** read, not a history scan. Add
`Registry.pointer_base(coord, name) -> Version | None` returning the base on that pointer's
newest advance event: on S3 a scan of only that pointer's own event stream, on SQL a single
latest-move row, in memory a dict lookup. It MUST return the base of the newest *advance* event
even when a later deletion tombstone sits above it — unlike alias *resolution*, which stops at a
tombstone and never looks below. This is a standalone robustness property of the port method; the
race that actually exercises it is a *concurrent* `delete_pointer` landing between `promote`'s
alias resolution (D4 step 1) and its `pointer_base` call (D4 step 3a) — a TOCTOU in which the
staged version still exists and its recorded base is still correct, so `promote` can still
complete. (The normal stage→promote→delete cycle does not hit it: `promote` runs while the pointer
is live, and an idempotent re-run re-stages a fresh advance on top and short-circuits at the D4
step-2 no-op.) In the common case this is the same order of cost as resolving the alias; in every
case it reads one pointer's stream, never the whole coordinate. `promote` calls this rather than `list_pointer_history`, which on S3 is
O(all-moves-in-the-coordinate) (it lists every pointer and GETs every event) — a cost the 0.2.2
read-path hardening makes it important to avoid.

*Alternative considered for the recovery shape:* a scoped `list_pointer_history(coord, name=...)`
variant instead of a new method. Rejected — it reuses a name but still returns a full move list
to pull one value, whereas a dedicated `pointer_base` states the single thing `promote` needs and
the backends already have the single-pointer read internally (S3 `_pointer_scan`, a SQL indexed
query). *Alternative considered for the field shape:* a uniform `cas_base` on every move (the
version each move compare-and-swapped against). Rejected as redundant — it would duplicate
`from_version` on every non-staging move forever; the staging-only field is simpler and matches
the `to_version` precedent.

### D3 — Storage: additive everywhere, a migration only for SQL

- **Memory**: a new dataclass field. No persistence, no migration.
- **S3**: the pointer-move event JSON gains an optional `base` key. Schemaless — old events
  without the key read as `base=None`. No migration, like the `to_version` tombstone key.
- **SQLite / Postgres**: a new **nullable** `base_version` column on `pointer_moves`, added
  via `ALTER TABLE pointer_moves ADD COLUMN base_version TEXT` in the existing `_migrate`
  hook on open. This is strictly lighter than the `to_version` migration — that one had to
  *drop a NOT NULL constraint*, which SQLite cannot do in place (hence its table rebuild).
  Adding a brand-new nullable column is a one-line `ADD COLUMN` on both backends: instant, no
  row rewrite, existing rows default to `NULL`, no backfill.

Forward- and backward-compatible: a new library reads an old store (missing column/key →
`None`); an old library reads a new store (ignores the extra column/key).

Note: the SQL migration and the SQL/memory field land on backends (SQLite/Postgres) not in
active production use — only S3 is live. They exist to keep the registry-port contract and the
differential-test oracle uniform across backends, not because production needs them; this is the
accepted price of the explicit-field choice over the rejected seed alternative.

### D4 — `promote` resolution order and no-op

`promote(coord, staged, *, pointer="head", force=False, actor, reason)` — the signature is
unchanged from today:

1. Resolve `staged` → `version`.
2. **No-op success**: if `pointer` already resolves to `version`, return `version`. (A
   concurrent/duplicate publish of the same tree is not a conflict — head is already where we
   want it, which is by definition not a different tree, so the guard is not weakened.) This
   step runs **before** base determination, which narrows the existing "refuse a version with no
   recorded base" behavior to "refuse only when head differs": a no-base version whose target
   head already holds is a success no-op, not a refusal.
3. Determine the CAS base `expected_base`:
   a. when `staged` is an `Alias`: the `base` from that pointer's newest advance event, via the
      focused `registry.pointer_base(coord, name)` lookup (never a whole-coordinate
      `list_pointer_history` scan). The staging pointer is content-named, ephemeral, and
      single-advance, so its base is unambiguous;
   b. otherwise — `staged` is a `Pin` or `Head` (no pointer to read), or the alias records no
      base (created before this change, or hand-made): with `force`, a plain last-writer CAS
      against the pointer's current value; otherwise raise a clear error naming
      promote-via-the-staging-alias, re-staging, or `force` as the recovery. (This narrows
      today's behavior: a `Pin`-by-version promote that formerly recovered a base from the
      version's `derived_from` now has no base, so it must use the staging alias or `force`.)
4. `set_pointer(coord, pointer, version, expected=expected_base, ...)`.
5. If step 4 raises `Conflict`, re-read `pointer`: if it now equals `version`, return `version`
   as a no-op success (a concurrent duplicate publish of the same tree landed between step 2 and
   step 4 — head is where we wanted it); otherwise propagate the `Conflict` (head moved to a
   *different* tree — the lost-update guard). So the "duplicate publish is a no-op" guarantee
   holds across the step 2 → step 4 window, not only before step 2.

There is no `derived_from` fallback: that field is removed (see D6), so the only base source is
the pointer-move record. `force` and the clear-refusal behavior are retained from today.

### D5 — TLA+ coverage

Extend `model/PublishOver.tla` (or a sibling `Promote.tla`) to model a `promote` that races a
concurrent publish, with the staged tree optionally pre-existing. A `BaseSource` toggle
(`FromManifest` vs `FromPointer`) plus a dedup toggle reproduces the bug under the
manifest-sourced base (false conflict / lost update in the dedup case) and shows correctness
under the pointer-sourced base — echoing the ok/bad-config pattern used by `S3Drop` and
`PointerDrop`. (That pattern ships the model with configurations that MUST pass under the correct
toggle settings and MUST *fail* under the buggy ones, so the model is shown to actually catch the
defect it targets rather than passing vacuously.) The model MUST represent `promote`'s no-op
head-read (D4 step 2) and the base-CAS (D4 step 4) as two distinct atomic steps with an
interleaving publish between them, so the step 2 → step 4 race (the spurious-then-retried
`Conflict` of D4 step 5) is in the state space rather than an idealized atomic promote. It SHALL
also allow a concurrent `delete_pointer` of the staging pointer to interleave between the alias
resolution (D4 step 1) and the `pointer_base` read (D4 step 3a), exercising the tombstone-skipping
recovery (D2). To be verified via the `tla-verifier` agent (SANY → smoke → exhaustive → coverage)
as this change's gate; the model is not yet written.

### D6 — Remove `derived_from` from manifest metadata

`publish_over(stage=True)` stops writing `derived_from`. It was written only by staging, its
sole consumer was `promote`'s base-CAS, and that base now lives on the pointer-move plane. The
value it held is a first-writer-wins shared-metadata value that is wrong for any deduped or
reverted tree, so leaving a live writer of it invites a future reader to re-make the bug.
Removal is forward-only: content-addressed manifests are never rewritten, so every
already-committed manifest keeps its stale `derived_from` forever (consistent with the
no-backfill Non-Goal). The actual protection is therefore that `promote` no longer reads
`derived_from` at all — the trap cannot re-fire through the supported path — so an external
reader must stop trusting the field entirely rather than expect it absent. New versions carry no
`derived_from`; the base of a staged version is the staging pointer's move, read via
`pointer_base`.

*Alternative rejected:* keep writing `derived_from` but document it as non-authoritative. Safer
for back-compat, but it leaves a known-misleading field physically present; with only one
consumer (not in active use), clean removal is preferred.

## Risks / Trade-offs

- **A staging pointer staged before this change, promoted after it** has no recorded `base`.
  → Mitigation: `promote` refuses with a clear message; recovery is to re-run the derive
  (idempotent — same content-derived staging name, now recording the base) or `force`. Staging
  pointers are created and deleted within one publish, so surviving an upgrade boundary is
  unusual.
- **`promote` on a `Pin`/`Head` ref (not the staging alias) can no longer recover a base** and
  must use `force` or the staging alias (see D4 step 3b). → Mitigation: the documented workflow
  and the QS1 client promote via the staging alias; the narrowing is specified, not silent.
- **A concurrent duplicate publish of the same tree can surface a retryable `Conflict`** in the
  step 2 → step 4 window (D4 step 5 converts the common case to a no-op, but the CAS can still
  lose the race). → Mitigation: it is never a lost update, and a retry resolves to a no-op.
- **Removing `derived_from`** is an externally visible metadata change (see D6, and the proposal
  Impact for release notes). → Mitigation: one known consumer, not in active use, updates on
  adopt.
- **The `base` field and `pointer_base` span two spec surfaces (`registry-port`, `version-log`)
  plus four backends**, mostly not in production use (see D3). → Mitigation: added uniformly and
  covered by the existing differential-test harness, so the backends cannot silently diverge.

## Migration Plan

1. Ship the additive field (memory/S3 no-op; SQL `ADD COLUMN` on open). No operator action.
2. New stages record `base` on the staging move and no longer write `derived_from`; `promote`
   uses the recorded base. A staging pointer created before the upgrade (no recorded base) is
   refused — recover by re-running the derive or with `force`.
3. Rollback: reverting the library leaves the nullable column/key in place and harmless
   (ignored by the old code). No data rewrite either direction. `derived_from` does not
   reappear on versions staged while the fix was deployed, but that field is not relied upon by
   either version of the code.

## Open Questions

- **Version number** — resolved: ships as a **minor** release (`0.3.0`), because removing the
  documented `derived_from` metadata field is externally visible. No effect on the specs or the
  task breakdown.
