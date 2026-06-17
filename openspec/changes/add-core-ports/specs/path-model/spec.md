## ADDED Requirements

### Requirement: Single canonical normalization function
The system SHALL provide one `normalize_path()` that is the sole source of truth for logical path canonicalization and validation. It SHALL be applied when a path enters a manifest (at write time) and by any consumer that maps logical paths to a filesystem.

#### Scenario: Same function governs write and view
- **WHEN** a path is validated at publish and later interpreted by the filesystem view
- **THEN** both apply the identical `normalize_path()` rules

### Requirement: Canonical path form
`normalize_path()` SHALL produce `/`-separated, relative paths with no leading slash, no trailing slash, and no empty segments (collapsing repeated slashes). It SHALL convert backslash separators to `/` and normalize Unicode to NFC. These coercions SHALL be applied silently.

#### Scenario: Formatting coerced
- **WHEN** the input is `\Models\\x\` or `models//x/`
- **THEN** the canonical result is `models/x` (NFC, forward slashes, no leading/trailing/empty segments)

### Requirement: Reject path traversal segments
`normalize_path()` SHALL reject any path containing a `.` or `..` segment. Such segments SHALL NOT be resolved or rewritten; the path SHALL be rejected with a typed error.

#### Scenario: Traversal rejected
- **WHEN** a path contains `..` (e.g. `../../etc/passwd`)
- **THEN** normalization raises and the path is not admitted to a manifest

### Requirement: Reject illegal characters
`normalize_path()` SHALL reject NUL and control characters and SHALL validate against a conservative portable character set. A posix-only escape hatch MAY relax the portable-charset check while still rejecting NUL and control characters.

#### Scenario: NUL rejected
- **WHEN** a path contains a NUL or control character
- **THEN** normalization raises regardless of the escape-hatch setting

### Requirement: Case-sensitive identity with no case-only collisions in a manifest
Path identity SHALL be case-sensitive: two paths differing only in case are distinct keys. However, a single manifest MUST NOT contain two distinct paths that are equal under full-path case-folding (including directory components). Such a manifest SHALL be rejected at write time.

#### Scenario: Case-distinct paths are distinct keys
- **WHEN** a manifest contains `README` and the producer also tracks `readme` as a different file in a different manifest
- **THEN** the two are treated as different keys and neither is merged

#### Scenario: Case-only collision within one manifest rejected
- **WHEN** a single manifest would contain both `README` and `readme` (or `Foo/a` and `foo/b`)
- **THEN** the commit is rejected so the manifest stays checkout-safe on case-insensitive filesystems
