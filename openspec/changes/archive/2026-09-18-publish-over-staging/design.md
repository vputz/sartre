## Context

`publish-over --stage` was CLI-only: `app.py` generated `staging-<uuid4>`, injected `derived_from` into the metadata itself, and passed the name as `pointer=`. The library `publish_over` knew nothing of staging. So a library consumer had to reconstruct the convention by hand, and the release notes — grouping `--stage` under the library signature — invited a `TypeError`. A prospective client hit exactly this, and independently improved on our design by making their staging pointer content-derived so a repair re-run reuses it. This change pulls staging into the library and adopts the content-derived name.

## Goals / Non-Goals

**Goals:** staging is a first-class, discoverable library operation shared with the CLI; the staging pointer name is deterministic (idempotent repair, no orphans); `derived_from` is recorded by the one operation, not duplicated in the CLI.

**Non-Goals:** no change to derive/`promote`/`delete-pointer` semantics or to how head advances; no new concurrency protocol (staging still advances a non-head pointer through the existing CAS), so no new TLA.

## Decisions

- **`stage: bool` on `publish_over`, not a separate method.** One derive method with a flag keeps the surface small and the return type stable. `stage=True` overrides `pointer` with a self-generated staging name (documented), records `derived_from`, and reports the name — everything a caller needs to verify then `promote`.
- **Report via `PublishOverResult.staged_pointer: str | None = None`** rather than changing the return type by flag. Additive, defaulted, positional-construction-safe (it trails the existing six fields). `None` when not staging.
- **Content-derived staging name.** `staging-<sha256(base_version + "\n" + "\n".join(sorted(affected_paths)))[:12]>`, where *affected_paths* = the union of `changes` keys, `remove`, and both sides of `rename` — the "repair intent." Same intent against the same base ⇒ same pointer, so a re-run after a failed verify advances that pointer again instead of littering a new one. The 12-hex suffix is a valid alias segment (no `/`). Keying on the *inputs* (not the resulting version, which isn't known until after the derive) is what lets the name be chosen before the advance while staying deterministic.
- **`derived_from` moves into the facade.** The staging path sets `metadata = {**metadata, "derived_from": base_snap.version}` where the base is already resolved, so the CLI no longer computes head separately and CLI/library agree by construction. `promote` reads it unchanged.
- **CLI `--stage` becomes a pass-through.** `app.py` drops its `uuid` import usage and manual `derived_from`, calls `ops.publish_over(..., stage=True)`, and prints `result.staged_pointer`. Behavior the user sees: the staging name is now stable across identical re-runs.

## Risks / Trade-offs

- **`--stage` pointer names change** from random to content-derived → acceptable and an improvement; names were never meant to be memorized, and any existing random staging pointers remain valid (they're just pointers). Documented in the release notes.
- **Two different-content repairs over the same paths+base collide on one staging name** → intended: the later derive advances the same staging pointer (base-CAS from its current staged value), which is the "latest attempt for this repair" semantics; it does not affect head. If a caller wants distinct staging pointers they can vary the affected paths or promote/delete between attempts.
- **`stage=True` ignores `pointer`** → a mild surprise; documented in the signature/spec ("advances a staging pointer it names itself, ignoring `pointer`").

## Migration Plan

Purely additive: a defaulted `stage` keyword and a defaulted `staged_pointer` field. No data or schema change; existing callers and stored data are unaffected. Rollback is dropping the flag/field.
