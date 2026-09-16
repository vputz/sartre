## Context

Packaging honesty before the first PyPI (test) publish. Two small, independent edits plus a doc note; no runtime behavior changes to any backend. See the proposal for motivation.

## Goals / Non-Goals

**Goals:** remove the unimplemented `delta` extra; expose `sartre.__version__` with pyproject as the single source of truth; keep `import sartre` dependency-clean; correct the design memo's Delta framing.

**Non-Goals:** no `sartre --version` CLI flag (later); no registry/store/GC/CLI behavior change; no changes to the `cli`/`s3`/`gcs`/`postgres` extras.

## Decisions

- **`__version__` from `importlib.metadata`, not a hardcoded literal.** `from importlib.metadata import version, PackageNotFoundError` (stdlib — no new dep) keeps `pyproject`'s `version` the single source of truth and avoids drift. Wrap in `try/except PackageNotFoundError` returning a sentinel (e.g. `"0.0.0+unknown"`) so importing from a raw checkout (no installed dist metadata) never raises. `__version__` is a module attribute, not added to `__all__` (dunder convention).
- **Remove the extra outright, don't deprecate.** Pre-first-publish with no released consumers, so a deprecation path would be ceremony for nobody; delete the `delta` extra and any dev-group `deltalake`/`pyarrow` present only for it.
- **Memo gets a note, not a rewrite.** A one-line "superseded by the SQL and S3-native registries" note under the Delta framing is enough to stop it implying a Delta backend is coming; a full rewrite is out of scope.

## Risks / Trade-offs

- **`sartre[delta]` breaks for anyone relying on it** → mitigated: nothing implemented ever used it and there are no released consumers (pre-first-publish).
- **`importlib.metadata.version` returns the *installed* version, which can differ from a working tree during editable dev** → acceptable and standard; the test asserts a non-empty PEP 440-ish string rather than pinning an exact value, and the installed value is authoritative.

## Migration Plan

None required — additive attribute plus a metadata removal. Rollback is re-adding the extra. No data or schema involved.
