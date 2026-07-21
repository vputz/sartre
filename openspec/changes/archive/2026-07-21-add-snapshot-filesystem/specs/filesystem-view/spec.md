## MODIFIED Requirements

### Requirement: Addressing and integration affordances
`SnapshotFS` SHALL be obtainable from a resolved snapshot (e.g.
`repo.snapshot_fs(snapshot)`) as a standard fsspec `AbstractFileSystem`, and SHALL
be passable directly as the `filesystem=` argument to fsspec-aware consumers,
which then read artifacts by logical path while bytes are served from the
content-addressed store. It SHALL additionally be registerable under a protocol
enabling `sartre://` URL addressing and SHALL support fsspec affordances including
a key/value mapper and, optionally, FUSE mounting; the URL-registration, mapper,
and FUSE affordances MAY be delivered in a later change.

#### Scenario: Object-form addressing via an fsspec consumer
- **WHEN** a `SnapshotFS` is passed as the `filesystem` argument to an fsspec-aware
  reader (e.g. a parquet reader) referencing a logical path
- **THEN** the reader accesses the file by its logical path and its bytes are
  resolved by content hash through the store

#### Scenario: URL addressing
- **WHEN** a file is opened via a `sartre://` URL referencing a coordinate, ref, and logical path
- **THEN** the correct blob is resolved and returned through the filesystem view
