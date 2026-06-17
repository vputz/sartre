## MODIFIED Requirements

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
