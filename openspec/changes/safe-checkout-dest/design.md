## Context

`checkout` never prunes, so it silently leaves stale files when a version drops one. git users expect either clone semantics (refuse a non-empty target) or switch semantics (index-driven prune). sartre has no index, so the switch model isn't safely available — the clone model is. See the proposal.

## Goals / Non-Goals

**Goals:** make the safe case the default (empty/new dest); keep the overlay behavior available explicitly; a clear, typed refusal.

**Non-Goals:** no prune/mirror/`--delete` (unsafe without an index); no change to `fetch_all`, `open`, or `snapshot_fs`.

## Decisions

- **Clone model, not switch model.** Refuse a non-empty `dest` by default; there is no index to drive safe pruning, and deleting the caller's files to "match the version" is a footgun. The documented way to update an existing checkout is a fresh directory (then swap).
- **`overwrite=False` keyword, default-refuse.** The emptiness check runs before `_layout`, so a refusal writes nothing (fail-closed). `overwrite=True` reproduces today's overlay exactly. Keyword-only, so existing positional callers targeting fresh dirs are unchanged.
- **"Empty" means: does not exist, or exists as a directory with no entries.** An existing non-directory at `dest`, or a non-empty directory, triggers the refusal. Hidden files count as contents (a `.git`/dotfile-populated dir is non-empty).
- **`PathError`, not a new exception.** It's the existing path-domain error and the CLI already funnels it to a clean stderr message + non-zero exit — no new type or CLI plumbing needed.
- **Overlay stays overlay.** With `overwrite=True`, extraneous files are deliberately left; we do not silently upgrade to sync. The spec scenario pins this so "overwrite" is never mistaken for "mirror".

## Risks / Trade-offs

- **Behavior change for callers that re-checked-out into a populated dir** → they now get `PathError`; fix is `overwrite=True`/`--force` or a fresh dir. Pre-1.0, acceptable; call it out in the proposal as BREAKING.
- **`--force` still leaves stale files (overlay)** → intended and documented; the *default* now protects the unwary, and `--force` is an explicit "I want overlay" signal. Not a silent trap anymore.

## Migration Plan

None beyond code. Update any test/helper that reused a non-empty checkout dir to either use a fresh dir or pass `overwrite=True`. Rollback is dropping the emptiness check.
