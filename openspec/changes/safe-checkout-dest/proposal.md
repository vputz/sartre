## Why

`Repository.checkout(snap, dest)` materializes a version's files under `dest` but never prunes, so checking out a version that *dropped* a file into a directory that still has it silently leaves the stale file behind — the resulting directory is not the version. This surprises anyone with a git mental model: `git clone` refuses a non-empty target, and `git switch` prunes via its index. sartre has no index, so it cannot prune safely (it can't tell its own stale files from the user's unrelated ones). The safe, unsurprising default is the clone model: don't write into a populated directory unless told to.

## What Changes

- **`Repository.checkout` refuses a non-empty destination by default.** A nonexistent or empty `dest` works as today (the common cases); an existing, non-empty `dest` raises a clear typed error naming the directory and the override. `AsyncRepository.checkout` mirrors this.
- **Opt-in overlay via `overwrite=True`** (library) / **`--force`** (CLI) restores today's behavior: write the manifest's files, overwriting collisions, leaving any extraneous files in place — *overlay, not sync*. **BREAKING** (pre-1.0): checkout into a non-empty dir now errors by default instead of overlaying.
- **No prune/mirror mode.** Making `dest` exactly match a version by deleting extras is unsafe without an index, so it is explicitly out of scope; the documented way to update an existing checkout is a fresh directory.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `repository-facade`: `checkout` gains an empty-destination precondition and an `overwrite` override (overlay semantics; no prune).
- `cli`: `sartre checkout` refuses a non-empty destination unless `--force`.

## Impact

- **Code**: `src/sartre/repository.py` (`checkout(..., *, overwrite=False)`, emptiness check before layout; `AsyncRepository.checkout` param), `src/sartre/cli/app.py` + `ops.py` (`checkout --force`). `fetch_all` (fresh temp dir) is unaffected.
- **APIs**: `checkout` grows a keyword-only `overwrite=False`; default behavior changes (refuses non-empty). Existing callers targeting fresh/empty dirs are unaffected.
- **Errors**: the default refusal raises `PathError`, which the CLI already funnels to a clean stderr message + non-zero exit.
- **Gates**: ruff + pyright clean, full suite green (update any test that checked out into a reused non-empty dir), `openspec validate --strict` passes.
