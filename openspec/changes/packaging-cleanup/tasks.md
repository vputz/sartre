## 1. Remove the `delta` extra

- [ ] 1.1 `pyproject.toml`: delete the `[project.optional-dependencies] delta = [...]` entry (`deltalake`, `pyarrow`); leave `cli`/`s3`/`gcs`/`postgres` intact.
- [ ] 1.2 `pyproject.toml`: remove `deltalake`/`pyarrow` from the dev dependency group if they appear there solely for the removed extra (leave anything a test actually needs).

## 2. Expose `sartre.__version__`

- [ ] 2.1 `src/sartre/__init__.py`: set `__version__` via `importlib.metadata.version("sartre")` with a `PackageNotFoundError` fallback to a sentinel (e.g. `"0.0.0+unknown"`). Stdlib only; not added to `__all__`.

## 3. Design memo note

- [ ] 3.1 `binary-artifact-repo-design.md`: add a one-line note that the SQL (SQLite/Postgres) and S3-native registries superseded the original Delta plan, so the memo no longer implies a Delta backend is forthcoming.

## 4. Tests

- [ ] 4.1 A unit test asserting `sartre.__version__` is a non-empty string (PEP 440-ish); when installed in the test env it equals the distribution version.

## 5. Gates

- [ ] 5.1 `ruff` clean, `pyright` clean, full default suite green.
- [ ] 5.2 `uv build` succeeds and `twine check dist/*` PASSES; a core-only isolated import still succeeds and reports `__version__`.
- [ ] 5.3 `openspec validate packaging-cleanup --strict` passes.
