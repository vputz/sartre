## MODIFIED Requirements

### Requirement: Lazy open by logical path
`SnapshotFS` SHALL open a file by translating its logical path to the entry's `content_hash` and fetching through the `Store`. Opening SHALL return a seekable readable handle supporting random access. The handle served for random access SHALL be the `Store`'s streaming handle for that hash and is **not** integrity-verified per read — a partial or seek read cannot be checked against a whole-blob content hash. Callers that require verified bytes SHALL either materialize the whole blob (`get_to`/`checkout`, which verify on download) or back the filesystem with a `CachingStore`, which verifies on local materialization and then serves seeks from the verified local copy.

#### Scenario: Open maps path to hash
- **WHEN** a file is opened by its logical path
- **THEN** its bytes are fetched by content hash through the store

#### Scenario: Random access read is served from a seekable handle
- **WHEN** a consumer seeks within an opened file (e.g. a parquet footer read)
- **THEN** the read is served by seeking the store's handle (a range read on a remote backend), without materializing or hashing the whole blob

#### Scenario: Verified random access via a caching store
- **WHEN** the filesystem is backed by a `CachingStore` and a file is opened
- **THEN** the blob is materialized and verified into the local cache on first read, and subsequent seeks are served from that verified local copy
