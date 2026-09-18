## Why

`publish` is full-replacement: it needs a source for *every* file in the new version. To change one file in a multi-file artifact you must re-supply the whole tree, even though the unchanged files' blobs already live in the store. The wire cost is already gone (dedup), but the *ergonomic* cost — holding or re-fetching the entire corpus just to change one path — remains, and it's an active footgun (a limited full-replacement republish silently orphans the complete corpus). A derive operation removes it: start from an existing version, reuse unchanged entries **by content hash** (no source, no upload, no re-hash), and supply sources only for what changes — producing a **complete, self-describing** version, not a diff.

## What Changes

- **Add `Repository.publish_over(coord, base=HEAD, *, changes={}, remove=(), rename={}, pointer="head", metadata=None, actor="unknown", reason=None) -> PublishOverResult`.** It resolves `base` to a snapshot (manifest only — no blob bytes), seeds the entry set, then:
  - **`changes`** = override an existing path or add a new one (source uploaded, skipping the wire if already present);
  - **`remove`** = drop one or more paths (error if absent from base);
  - **`rename`** = re-key an existing entry to a new path, reusing the **same blob by hash** — no source, no upload.
  Every untouched entry is inherited by content hash. The result is a new immutable version (base never mutated); an empty derive, or an override with identical bytes, yields base's own version id. Lease/commit/advance and provenance are identical to `publish` — the lease covers *all* the new manifest's hashes (changed + inherited), so inherited blobs can't be GC'd mid-derive.
- **Return counts** — `PublishOverResult(version, inherited, replaced, added, removed, renamed)` — so the operation reports what landed instead of a bare id.
- **Add a `sartre publish-over` CLI command**: `[logical=source ...]`, `--from <ref>` (base, default head), `--rm <path>` (repeatable), `--mv <old:new>` (repeatable), `--stage` (land on an auto-generated unique staging pointer instead of head), plus publish's `-p/--pointer`, `--point`, `--as/--author`, `-m/--message`, `--meta`. It prints the inherited/replaced/… summary.
- **Head-based divergence guard (CLI):** if the resolved `base` is not the coordinate's current **head**, refuse unless `--force`; `--force` additionally **requires a `-m` reason** and **prints what is discarded** (versions/paths not in the base) before writing — so advancing off an old base is a stated, visible act, never a bare flag.
- **`--stage`** advances a fresh unique staging pointer (`staging-<hex>`), not head — enabling *derive → verify that staging ref → promote head* so verification precedes any head move. (Deleting a spent staging pointer awaits the sibling `pointer-deletion` change; noted in `--stage`'s help.)

`publish` itself is unchanged.

## Capabilities

### Modified Capabilities
- `repository-facade`: add `publish_over` (derive-from-base reusing unchanged entries by hash, with `changes`/`remove`/`rename`), returning a result with inherited/replaced counts; shares publish's lease/commit/advance ordering.
- `cli`: add a `publish-over` command (with `--from`/`--rm`/`--mv`/`--stage`, the head-divergence guard, and `--force` requiring a reason + discard report).

## Impact

- **Code**: `src/sartre/repository.py` (`publish_over` + factored `_write_version`, `PublishOverResult`), `src/sartre/cli/ops.py` + `app.py` (the command, guard, `--stage`, reporting).
- **APIs**: additive — new method + CLI command; `publish` untouched. Versions remain immutable (always a new version).
- **Concurrency**: no new protocol — same lease ordering + `set_pointer` CAS as `publish`, so no new TLA. (Cross-client staging-pointer safety comes from unique per-op names; pointer deletion is the sibling change.)
- **Gates**: ruff + pyright clean, full suite green, `openspec validate --strict` passes.
