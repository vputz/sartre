## Why

Staging a derived version is a CLI-only convention today: `publish-over --stage` generates a random `staging-<uuid>` pointer, advances it instead of head, and records `derived_from` in the version metadata so `promote` can base-CAS it — but the library `publish_over` has no `stage` concept at all. A library consumer building a repair path has to reverse-engineer the convention (pick a pointer name, stash `derived_from`, then `promote`); following the release notes, which grouped `--stage` next to the library signature, leads straight to a `TypeError`. And the random pointer name means a failed verify plus a re-run orphans the previous staging pointer (our `delete-pointer` cleanup only fires on a successful `promote`), littering the coordinate.

## What Changes

- **`Repository.publish_over` gains `stage: bool = False`.** When true it derives onto an auto-generated staging pointer instead of `pointer`, records `derived_from=<base version>` in the version metadata, and reports the staging pointer it used. Staging becomes a first-class library operation, not a CLI-only convention — one code path shared by the CLI and library.
- **`PublishOverResult` gains `staged_pointer: str | None = None`** (the staging pointer name when `stage=True`, else `None`) — so a library caller can discover and later `promote`/`delete-pointer` it. Additive, defaulted field.
- **The staging pointer name is content-derived, not random:** `staging-<hash of (base version, the affected logical paths)>`. Re-running the same repair reuses its pointer (idempotent) rather than creating a new one, matching sartre's content-addressed grain and avoiding orphaned staging pointers.
- **`publish-over --stage` becomes a thin pass-through** to `publish_over(stage=True)` — dropping the CLI's own uuid generation and `derived_from` injection, so CLI and library agree on naming and metadata by construction.

## Capabilities

### New Capabilities
<!-- none — extends existing capabilities -->

### Modified Capabilities
- `repository-facade`: `publish_over` gains `stage`; `PublishOverResult` gains `staged_pointer`; staging (auto-named content-derived pointer + `derived_from`) is a library operation.
- `cli`: `publish-over --stage` is backed by the library `stage` flag and uses the content-derived, idempotent staging pointer name.

## Impact

- **APIs:** additive `stage` keyword on `publish_over`; additive `PublishOverResult.staged_pointer` field. No breaking change (both defaulted).
- **Behavior:** `--stage` pointer names change from random to content-derived; a re-run of the same repair now reuses the staging pointer instead of creating a fresh one.
- **Code:** `repository.py`, `cli/ops.py`, `cli/app.py`; tests. No new concurrency protocol (staging still advances a non-head pointer via the existing CAS), so no new TLA.
- **Non-goals:** no change to `promote`, `delete-pointer`, or `publish_over`'s derive semantics; no change to how head advances.
