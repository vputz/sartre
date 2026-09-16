## 1. Remove the `delta` extra

- [x] 1.1 `pyproject.toml`: delete the `[project.optional-dependencies] delta = [...]` entry (`deltalake`, `pyarrow`); leave `cli`/`s3`/`gcs`/`postgres` intact.
- [x] 1.2 (no-op — `deltalake`/`pyarrow` were only in the `delta` extra, not the dev group)

## 2. Expose `sartre.__version__`

- [x] 2.1 `src/sartre/__init__.py`: set `__version__` via `importlib.metadata.version("sartre")` with a `PackageNotFoundError` fallback to a sentinel (e.g. `"0.0.0+unknown"`). Stdlib only; not added to `__all__`.

## 3. Design memo note

- [x] 3.1 `binary-artifact-repo-design.md`: add a one-line note that the SQL (SQLite/Postgres) and S3-native registries superseded the original Delta plan, so the memo no longer implies a Delta backend is forthcoming.

## 4. Tests

- [x] 4.1 A unit test asserting `sartre.__version__` is a non-empty string (PEP 440-ish); when installed in the test env it equals the distribution version.

## 5. Gates

- [x] 5.1 `ruff` clean, `pyright` clean, full default suite green.
- [x] 5.2 `uv build` succeeds and `twine check dist/*` PASSES; a core-only isolated import still succeeds and reports `__version__`.
- [x] 5.3 `openspec validate packaging-cleanup --strict` passes.
