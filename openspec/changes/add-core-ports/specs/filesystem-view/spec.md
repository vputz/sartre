## ADDED Requirements

### Requirement: Read-only filesystem over a version
The system SHALL provide `SnapshotFS`, an fsspec `AbstractFileSystem` bound to a resolved `Snapshot` and a `Store`, presenting the manifest as a read-only filesystem keyed by logical path. All write/mutation operations SHALL raise.

#### Scenario: Writes rejected
- **WHEN** any write, create, or delete operation is attempted on `SnapshotFS`
- **THEN** the operation raises a read-only error

### Requirement: Listing served from the manifest
`SnapshotFS` SHALL answer `ls`/`info`/`exists` and report sizes using only the snapshot's entries, synthesizing directories from canonical paths. These operations MUST NOT fetch any blob bytes.

#### Scenario: Directory listing without blob fetch
- **WHEN** a directory is listed or a file's size is requested
- **THEN** the result is computed from the manifest with no blob download

### Requirement: Lazy open by logical path
`SnapshotFS` SHALL open a file by translating its logical path to the entry's `content_hash` and fetching through the `Store`. Opening SHALL return a readable handle backed by the content-addressed cache, supporting random access.

#### Scenario: Open maps path to hash
- **WHEN** a file is opened by its logical path
- **THEN** its bytes are fetched by content hash through the store and cached for reuse

#### Scenario: Random access read
- **WHEN** a consumer seeks within an opened file (e.g. a parquet footer read)
- **THEN** the read is served from the locally materialized, verified blob

### Requirement: Whole-tree checkout
The system SHALL support materializing an entire version to a local directory by logical path (e.g. recursive get / `checkout`). Paths SHALL be laid out under the destination using the canonical path model, and the result MUST NOT write outside the destination directory.

#### Scenario: Checkout lays out logical tree
- **WHEN** a version is checked out to a destination directory
- **THEN** files appear at their logical paths under that destination and nowhere outside it

### Requirement: Addressing and integration affordances
`SnapshotFS` SHALL be registerable under a protocol enabling URL addressing (e.g. `sartre://`) and SHALL support fsspec affordances including a key/value mapper and, optionally, FUSE mounting.

#### Scenario: URL addressing
- **WHEN** a file is opened via a `sartre://` URL referencing a coordinate, ref, and logical path
- **THEN** the correct blob is resolved and returned through the filesystem view
