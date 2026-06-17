## 1. Contract updates

- [x] 1.1 Update the `Version` docstring in `model.py`: it is the content hash of the canonical manifest entries, with the identity boundary (entries' sorted `(path, content_hash)` only; excludes size/inline/metadata/coordinate)
- [x] 1.2 Un-TBD the `Registry.commit` docstring in `ports.py`: content-idempotent — identical entries return the same `Version`, no duplicate manifest
- [x] 1.3 Note in `Registry.list_versions` docstring that results are returned in commit-log order

## 2. `manifest_version` helper

- [x] 2.1 Implement `manifest_version(entries, hasher=DEFAULT_HASHER) -> Version` in `sartre.hashing`: sort `(path, content_hash)` pairs, domain-separate the serialization, hash with the pluggable `Hasher`, return a self-describing key
- [x] 2.2 Export `manifest_version` from the package `__init__`
- [x] 2.3 Tests: order-independence; independence from `size`/`inline`/`metadata`; sensitivity to a changed `path` or `content_hash`; self-describing prefix; stable empty-manifest hash; value distinct from a raw blob key (domain separation)

## 3. Verification

- [x] 3.1 `uv run pyright` clean over `src/sartre`
- [x] 3.2 `uv run ruff check` and `uv run pytest` pass
- [x] 3.3 `openspec validate define-version-id`; confirm the publish transaction, GC, and the log backend remain unimplemented (contract-only here)
