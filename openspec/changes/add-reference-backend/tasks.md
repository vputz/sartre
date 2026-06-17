## 1. Dependencies

- [x] 1.1 Add `hypothesis` to the `dev` dependency group; `uv sync`

## 2. Blob plane implementation

- [x] 2.1 Implement `FsspecBlobBackend` (`get`/`put`/`exists`/`delete`) over an fsspec filesystem rooted at a prefix
- [x] 2.2 Implement `CasStore`: `put` (hash via `Hasher`, write under the self-describing key, idempotent), `has`, `open`, `get_to` (verify-on-download against the key's algorithm), `delete`; raise `IntegrityError` on hash mismatch
- [x] 2.3 Implement `CachingStore`: `get_to`/`open` serve from `local` else fetch `remote` and populate `local`; per-hash lock + temp-file-then-atomic-rename; trust cache after download
- [x] 2.4 Tests: put/has/get round-trip, idempotent put, integrity failure on corruption, cross-version cache reuse, concurrent same-blob fetch downloads once

## 3. Manifest plane implementation

- [x] 3.1 Implement `MemoryRegistry`: pointers dict, manifests keyed by `manifest_version` (dedup), append-only per-coordinate commit log (`seq` + `created_at`); a lock guards mutations
- [x] 3.2 Implement `head` (single pointer read), `resolve` (atomic; raise `NotFound`), `list_pointers`, `list_versions` (commit-log order)
- [x] 3.3 Implement `commit` (content-idempotent) and `set_pointer` (CAS on `expected`, raise `Conflict`; atomic with the log append)
- [x] 3.4 Tests: cheap head, atomic resolve / NotFound, idempotent commit, CAS success + conflict, ordered list_versions

## 4. Repository read core + publish

- [x] 4.1 Implement `Repository.head`/`resolve`/`open` (entry → content hash → store, inline entries served directly)
- [x] 4.2 Implement `fetch_all` with a `ThreadPoolExecutor` fan-out over a shared `CachingStore`
- [x] 4.3 Implement `Repository.publish`: blobs (skip `has`) → `commit` → `set_pointer(expected=start)`; fail-fast on `Conflict`
- [x] 4.4 Tests: publish→resolve→open round-trip, fetch_all parallel materialization, publish conflict surfaces

## 5. Property-based verification

- [x] 5.1 Hypothesis properties: `manifest_version` permutation-invariance (extend existing), publish→resolve round-trip, cache reuse
- [x] 5.2 `RuleBasedStateMachine`: random `publish`/`resolve`/`promote`/crash-and-retry across a few coordinates; invariants — no dangling tip (`PointerSafe`), single-winner under concurrent publish, convergence after retried publish
- [x] 5.3 Cross-reference the stateful test to `openspec/specs/publish-transaction/model/Publish.tla` in a comment (refinement anchor)

## 6. Verification

- [x] 6.1 `uv run pyright` clean
- [x] 6.2 `uv run ruff check` and `uv run pytest` (incl. Hypothesis) pass
- [x] 6.3 `openspec validate add-reference-backend`; confirm SnapshotFS/checkout, persistence, and GC remain unimplemented
