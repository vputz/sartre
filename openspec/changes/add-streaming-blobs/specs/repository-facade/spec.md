## MODIFIED Requirements

### Requirement: Publish ordering through the facade
`publish` SHALL accept its files as `Mapping[str, bytes]` or `Mapping[str, Path]` (both re-readable sources; read-once streams are not supported). `publish` SHALL derive each blob's content hash and the manifest version by streaming each source through the hasher (bounded memory, no whole-blob `bytes` value), then — as today — upload blobs to the store before recording the manifest, skipping blobs already present (`has`), then `commit` the manifest, then advance the target pointer via compare-and-swap. Each upload SHALL itself stream (the store's `stage`/`promote` seam), so a source larger than memory is published without being buffered whole. The lease over `(version, hashes)` SHALL be acquired before any blob is uploaded, preserving the ordering the garbage-collection lease discipline was verified under.

#### Scenario: Existing blobs are not re-uploaded
- **WHEN** publishing a manifest whose blobs are already stored
- **THEN** those blobs are not uploaded again before the manifest is committed

#### Scenario: Large files publish in bounded memory
- **WHEN** `publish` is given `Path` sources larger than available memory
- **THEN** each source is hashed and uploaded in streaming passes without being buffered whole in memory

#### Scenario: Bytes and path sources both accepted
- **WHEN** `publish` is given files as `bytes` values or as `Path` values
- **THEN** each is published with its correct content hash and size (`len` for bytes, file size for a path)
