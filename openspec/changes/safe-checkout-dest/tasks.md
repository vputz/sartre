## 1. Repository facade

- [ ] 1.1 `src/sartre/repository.py`: `checkout(self, snap, dest, *, overwrite=False, max_workers=8)`. Before `_layout`, if `dest` exists and is a non-empty directory (or exists as a non-directory) and not `overwrite`, raise `PathError` naming `dest` and the `overwrite`/`--force` override; otherwise proceed (create dir, lay out). `overwrite=True` reproduces today's overlay (no pruning).
- [ ] 1.2 `AsyncRepository.checkout`: thread the `overwrite` keyword through to the sync call.

## 2. CLI

- [ ] 2.1 `src/sartre/cli/ops.py`: `checkout(repo, coord, ref, dest, *, overwrite=False)` passes `overwrite` to `repo.checkout` (ops stays Typer-free).
- [ ] 2.2 `src/sartre/cli/app.py`: `checkout` command gains `--force` (→ `overwrite=True`). The default `PathError` refusal is already rendered by the error funnel to stderr + non-zero exit.

## 3. Tests

- [ ] 3.1 `tests/`: `checkout` into a nonexistent and into an empty dir → exact tree materialized.
- [ ] 3.2 `checkout` into a non-empty dir (default) → raises `PathError`, wrote nothing (destination unchanged).
- [ ] 3.3 `checkout(..., overwrite=True)` into a non-empty dir → manifest files written/overwritten, a pre-existing extraneous file still present (overlay-not-sync).
- [ ] 3.4 CLI: `sartre checkout` into a non-empty dir exits non-zero with a message mentioning `--force`; with `--force` it succeeds.
- [ ] 3.5 Update any existing test/helper that checked out into a reused non-empty directory (use a fresh dir or `overwrite=True`).

## 4. Gates

- [ ] 4.1 `ruff` clean, `pyright` clean, full default suite green.
- [ ] 4.2 `openspec validate safe-checkout-dest --strict` passes.
