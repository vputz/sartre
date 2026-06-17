## 1. Package scaffolding & dependencies

- [ ] 1.1 Add `fsspec` to core dependencies in `pyproject.toml`; keep `s3fs`/`gcsfs` in optional extras alongside existing `delta`/`s3`
- [ ] 1.2 Create the module layout: `src/sartre/ports.py` (interfaces), `src/sartre/paths.py` (normalization), and a `tests/` mirror

## 2. Domain model (`artifact-model`)

- [ ] 2.1 Define `Coordinate(name, env)` as a frozen dataclass
- [ ] 2.2 Define the sealed `Ref` union: `Head`, `Alias(name)`, `Pin(version)`
- [ ] 2.3 Define `Entry(path, content_hash, size, inline: bytes | None)` and `Snapshot(coord, version, created_at, metadata, entries)` as frozen dataclasses
- [ ] 2.4 Define `Hash`/`Version` type aliases and the `Hasher` Protocol; provide the self-describing `"<algo>:<digest>"` key helpers with `sha256` default
- [ ] 2.5 Tests: ref semantics (Pin immutable / Head mutable shape), self-describing key round-trip, one-entry vs many-entry snapshot shape

## 3. Path model (`path-model`)

- [ ] 3.1 Implement `normalize_path()`: coerce separators/NFC/leading-trailing/`//`; reject `.`/`..`, NUL/control chars; portable-charset check with posix escape hatch
- [ ] 3.2 Implement manifest-level case-collision validation (full-path casefold incl. directory components; folded-set cardinality equals raw-set)
- [ ] 3.3 Tests: coercion cases, traversal rejection, NUL rejection, case-only collision rejection, case-distinct-but-separate acceptance

## 4. Registry port (`registry-port`)

- [ ] 4.1 Define the `Registry` Protocol: `head`, `resolve`, `list_pointers`, `list_versions`, `commit`, `set_pointer(expected=...)`
- [ ] 4.2 Document the contracts as docstrings: cheap `head` (no manifest scan), atomic `resolve`, immutable `commit`, CAS `set_pointer`; mark `commit` content-idempotency `# TBD (version-id format)`
- [ ] 4.3 Define typed errors: `NotFound`, `Conflict` (and a shared base)

## 5. Blob store ports (`blob-store`)

- [ ] 5.1 Define the `Store` Protocol (`has`/`open`/`get_to`/`put`/`delete`, stream/path-oriented) and the dumb `BlobBackend` Protocol (`get`/`put`/`exists`/`delete`, opaque keys)
- [ ] 5.2 Define `IntegrityError`; document verify-on-download and the thread-safety + per-hash-lock + atomic-rename cache contract
- [ ] 5.3 Specify the optional `get_many(keys)` batch hook on `BlobBackend`
- [ ] 5.4 Mark `CasStore`, `CachingStore`, and `FsspecBlobBackend` as interface stubs (signatures + docstrings only; implementations land in a follow-up change)

## 6. Filesystem view & repository facade (`filesystem-view`, `repository-facade`)

- [ ] 6.1 Define the `SnapshotFS` interface (read-only fsspec `AbstractFileSystem`): `ls`/`info`/`exists` from manifest, lazy `_open` by path→hash; writes raise (stub)
- [ ] 6.2 Define the `Repository` facade signatures: `head`/`resolve`/`open`/`fetch_all`/`checkout`/`publish`, with the publish ordering intent in docstrings (full protocol deferred)
- [ ] 6.3 Define the `AsyncRepository` wrapper signatures delegating to the sync core via `asyncio.to_thread`

## 7. Verification

- [ ] 7.1 `uv run pyright` passes over `src/sartre` (interfaces are internally consistent and Protocol-typed)
- [ ] 7.2 `uv run ruff check` and `uv run pytest` pass (path-model and model tests green)
- [ ] 7.3 Run `openspec validate add-core-ports`; confirm interface-only scope and that deferred items (Delta/S3 adapters, publish transaction, GC, version-id format) are not implemented here
