# artifact-model Specification

## Purpose
TBD - created by archiving change add-core-ports. Update Purpose after archive.
## Requirements
### Requirement: Coordinate addressing
The system SHALL identify an artifact by a `Coordinate` of `(name, env)`, where `name` is the logical artifact name and `env` is the environment selector. The `env` SHALL be part of the coordinate (not the ref), so that `Head`/`Alias`/`Pin` carry the same meaning in every environment.

#### Scenario: Distinct environments are distinct coordinates
- **WHEN** the same `name` is requested under `env="dev"` and `env="release"`
- **THEN** the two `Coordinate` values are not equal and resolve through independent pointer lineages

### Requirement: Ref union
The system SHALL provide a sealed `Ref` union with exactly three immutable variants: `Head` (the mutable tip), `Alias(name)` (a named mutable pointer), and `Pin(version)` (an immutable version id). A `Ref` SHALL select WHICH version of a coordinate is addressed, never WHAT it contains.

#### Scenario: Pin is immutable
- **WHEN** a `Pin(version)` is resolved at two different times with no other change
- **THEN** both resolutions return the same immutable version

#### Scenario: Head is mutable
- **WHEN** the tip advances after a publish
- **THEN** resolving `Head` returns the new version while a previously captured `Pin` still returns the old version

### Requirement: Entry and Snapshot manifest model
A `Snapshot` SHALL represent a resolved immutable version as `(coord, version, created_at, metadata, entries)`, where `entries` is a tuple of `Entry(path, content_hash, size)` and MAY carry optional inline bytes for small files. A `Snapshot` SHALL contain manifest data only — no heavy blob bytes are fetched to produce it.

#### Scenario: Resolving yields manifest without blob bytes
- **WHEN** a snapshot is produced for a version
- **THEN** every entry exposes its `path`, `content_hash`, and `size` without any blob being downloaded

#### Scenario: A single-file artifact is a one-entry manifest
- **WHEN** an artifact contains exactly one file
- **THEN** its snapshot has exactly one `Entry`, identical in shape to any entry of a many-file manifest

### Requirement: Self-describing content-hash identity
A `content_hash` SHALL be a self-describing string of the form `"<algo>:<digest>"`. The system SHALL support a pluggable `Hasher` with `sha256` as the default algorithm, and SHALL allow keys produced by different algorithms to coexist within one manifest.

#### Scenario: Key declares its algorithm
- **WHEN** content is hashed with the default hasher
- **THEN** the resulting key begins with `sha256:` followed by the hex digest

#### Scenario: Mixed-algorithm manifest is valid
- **WHEN** a manifest contains both a `sha256:` key and a `blake3:` key
- **THEN** each entry is resolvable using the algorithm named in its own key

### Requirement: Opaque version identifier
A `Version` SHALL be the **content hash of the canonical manifest entries**: the
self-describing `"<algo>:<digest>"` hash (same `Hasher` family as blob keys) of
the entries' sorted `(path, content_hash)` pairs, domain-separated so a version
can never equal a raw blob key. The hash SHALL exclude `size`, inline bytes,
`metadata`, and the coordinate. Consumers SHALL treat the value as opaque and
MUST NOT parse or order versions by structure; ordering comes from the commit log.

#### Scenario: Identical entries hash to the same version
- **WHEN** two manifests contain the same set of `(path, content_hash)` entries
- **THEN** they have the same `Version`, regardless of entry order, `size`,
  inline bytes, metadata, or coordinate

#### Scenario: A changed entry changes the version
- **WHEN** one entry's `content_hash` or `path` differs between two manifests
- **THEN** the two manifests have different `Version` values

#### Scenario: Versions compared by identity
- **WHEN** two version identifiers are compared
- **THEN** equality is by exact value and no structural ordering is assumed

