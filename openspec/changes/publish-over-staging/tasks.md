## 1. Library: staging is first-class

- [x] 1.1 `PublishOverResult` gains `staged_pointer: str | None = None` (trailing, defaulted). Export unchanged.
- [x] 1.2 `_staging_pointer_name(base_version, affected_paths)` helper in `repository.py`: `staging-<sha256(base + sorted(affected))[:12]>` (deterministic, valid alias segment).
- [x] 1.3 `Repository.publish_over` gains `stage: bool = False`. When true: compute the affected-paths set (changes ∪ remove ∪ rename old/new), derive the staging name, set `metadata["derived_from"] = base_snap.version`, advance that staging pointer (not `pointer`), and populate `result.staged_pointer`. When false: unchanged behavior.
- [x] 1.4 `AsyncRepository` needs no new method (it wraps `publish`/`gc` reads only, matching the existing `publish_over` omission) — confirm and leave as is.

## 2. CLI: pass-through

- [x] 2.1 `ops.publish_over` gains `stage: bool = False`, threaded to `repo.publish_over`.
- [x] 2.2 `app.py` `publish-over --stage`: drop the `uuid` staging name and manual `derived_from`; call `ops.publish_over(..., stage=True)`, use `result.staged_pointer` in the human/JSON output; keep `also_alias=None` while staging. Remove the now-unused `uuid` import if nothing else uses it.

## 3. Tests

- [x] 3.1 Library: `stage=True` leaves head unchanged, advances the reported `staged_pointer`, records `derived_from`=base; `promote` then advances head to it.
- [x] 3.2 Idempotent name: same base + same affected paths ⇒ same `staged_pointer` across two runs; the second run reuses (does not multiply) the staging pointer. A Hypothesis property: the name is a pure function of (base, affected paths).
- [x] 3.3 CLI: `publish-over --stage` prints a content-derived name; re-running the same repair reports the same staging pointer (no orphan); `promote` cleans it up. Update existing `--stage` tests that assumed a random name.

## 4. Gates

- [x] 4.1 `ruff` clean, `pyright` clean, full default suite green.
- [x] 4.2 `openspec validate publish-over-staging --strict` passes.
