## Why

`publish` is full-replacement: it needs a source for *every* file in the new version. To change one file in a multi-file artifact you must re-supply the whole tree, even though the unchanged files' blobs already live in the store. The wire cost is already gone (dedup skips unchanged blobs), but the *ergonomic* cost remains — you have to hold or re-enumerate the entire tree just to change one path. A derive operation removes that: start from an existing version, reuse the unchanged entries **by content hash** (no source, no upload, no re-hash), and supply sources only for the paths that change.

## What Changes

- **Add `Repository.publish_over(coord, base=HEAD, *, set={}, remove=(), pointer="head", metadata=None, actor="unknown", reason=None) -> Version`.** It resolves `base` to a snapshot, seeds the entry set from its manifest, applies `remove` then `set` (hashing only the changed sources), and publishes the merged tree as a new immutable version — reusing every unchanged entry by its existing `content_hash`. `AsyncRepository` gets an awaitable wrapper.
- **Reuse the publish lease/commit/CAS discipline unchanged.** The lease is acquired over *all* the new manifest's hashes (changed and reused) before any upload, so reused blobs are GC-protected through commit and advance; only the changed sources are uploaded (with `known_hash` dedup); the two self-checks and the pointer CAS are identical to `publish`. The shared tail is factored so `publish` and `publish_over` don't duplicate it.
- **Add a `sartre publish-over` CLI command** mirroring `publish`'s options (`-p/--pointer`, `--point`, `--as/--author`, `-m/--message`, `--meta`), plus `--from <ref>` (base, default head) and `--rm <path>` (repeatable). `publish` itself is unchanged.

## Capabilities

### Modified Capabilities
- `repository-facade`: add a derive-from-base operation (`publish_over`) that reuses unchanged entries by content hash and shares publish's lease/commit/advance ordering.
- `cli`: add a `publish-over` command for deriving a version from a base, changing only some paths.

## Impact

- **Code**: `src/sartre/repository.py` (`publish_over` + a factored `publish` tail; `AsyncRepository.publish_over`), `src/sartre/cli/ops.py` + `app.py` (the `publish-over` command).
- **APIs**: additive — a new method and CLI command; `publish` semantics untouched. Versions remain immutable (this always creates a *new* version).
- **Concurrency**: no new protocol — same lease ordering as `publish`, over a different entry set, so no new TLA modelling.
- **Gates**: ruff + pyright clean, full suite green, `openspec validate --strict` passes.
