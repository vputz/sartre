## MODIFIED Requirements

### Requirement: Publish ordering through the facade
`publish` SHALL accept its files as `Mapping[str, bytes]` or `Mapping[str, Path]` (both re-readable sources; read-once streams are not supported). `publish` SHALL derive each blob's content hash and the manifest version by streaming each source through the hasher (bounded memory, no whole-blob `bytes` value), then — as today — upload blobs to the store before recording the manifest, **skipping any blob the durable store already holds**, then `commit` the manifest, then advance the target pointer via compare-and-swap. Publish SHALL determine "already held" against the durable store the manifest is read back from — it SHALL pass each source's already-computed content hash to `Store.put` as `known_hash` (which consults the durable target) rather than relying on `Store.has`, so a blob present only in a local cache but absent from the remote is still uploaded. Each upload SHALL itself stream (the store's `stage`/`promote` seam), so a source larger than memory is published without being buffered whole. The lease over `(version, hashes)` SHALL be acquired before any blob is uploaded, preserving the ordering the garbage-collection lease discipline was verified under; because the lease protects the whole hash set, a blob found already present cannot be swept before the manifest commits.

#### Scenario: Existing blobs are not re-uploaded
- **WHEN** publishing a manifest whose blobs are already stored durably
- **THEN** those blobs are not staged or uploaded again before the manifest is committed

#### Scenario: Locally-cached but remote-absent blob is still uploaded
- **WHEN** publishing through a `CachingStore` where a blob is present in the local cache but absent from the remote (durable) store
- **THEN** that blob is uploaded to the remote before the manifest is committed, so the committed manifest never references a blob missing from the durable store

#### Scenario: Large files publish in bounded memory
- **WHEN** `publish` is given `Path` sources larger than available memory
- **THEN** each source is hashed and uploaded in streaming passes without being buffered whole in memory

#### Scenario: Bytes and path sources both accepted
- **WHEN** `publish` is given files as `bytes` values or as `Path` values
- **THEN** each is published with its correct content hash and size (`len` for bytes, file size for a path)
