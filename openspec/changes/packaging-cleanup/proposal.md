## Why

Before the first (test) publish to PyPI, the package should describe only what it actually provides. `pyproject.toml` advertises a `delta` extra (`deltalake`, `pyarrow`) for a Delta-Lake registry that was never built and has been explicitly superseded by `S3Registry` (shared multi-writer on plain object storage) — so it promises a capability that does not exist. The package also exposes no `__version__`, which downstream code and a future `--version` flag will want.

## What Changes

- **Remove the `delta` optional-dependency extra** from `pyproject.toml` (and drop `deltalake`/`pyarrow` from the dev group if they were there only for it). **BREAKING** for anyone installing `sartre[delta]` — but nothing implemented ever used it (pre-first-publish, no released consumers).
- **Expose `sartre.__version__`**, sourced from installed package metadata (`importlib.metadata.version("sartre")`) so `pyproject`'s `version` stays the single source of truth, with a `PackageNotFoundError` fallback for raw checkouts. Version remains `0.1.0`.
- **Note in the design memo** (`binary-artifact-repo-design.md`, framed as "Delta + S3") that the SQL and S3-native registries superseded the Delta plan, so the memo stops implying a Delta backend is forthcoming.

## Capabilities

### New Capabilities
- `distribution`: how the package presents itself for installation — its optional-dependency extras reflect only implemented backends, and it exposes its version at runtime as `sartre.__version__` sourced from package metadata.

### Modified Capabilities
<!-- none: no registry/store/CLI behavior changes; the `cli` extra and s3/gcs/postgres extras are untouched. -->

## Impact

- **Code**: `src/sartre/__init__.py` (add `__version__`); `pyproject.toml` (remove the `delta` extra + any dev-group `deltalake`/`pyarrow`); `binary-artifact-repo-design.md` (one-line note).
- **APIs**: additive — new module attribute `sartre.__version__`. No registry/store/CLI/GC behavior change; `import sartre` stays dependency-clean (`importlib.metadata` is stdlib).
- **Packaging**: the `delta` extra disappears from the distribution metadata; `cli`/`s3`/`gcs`/`postgres` extras unchanged.
- **Gates**: `uv build` clean, `twine check` PASSES, `sartre.__version__ == "0.1.0"` when installed, ruff + pyright clean, full suite green.
