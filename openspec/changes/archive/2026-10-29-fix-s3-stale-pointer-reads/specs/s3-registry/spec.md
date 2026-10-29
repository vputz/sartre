## MODIFIED Requirements

### Requirement: Cheap pointer read and atomic resolve
`head` SHALL resolve a pointer by reading its stream's tail event (a bounded LIST plus one GET) and SHALL NOT fetch or scan a manifest. `resolve` SHALL read the pointer's target version and then the immutable manifest object; because manifests are content-addressed and written before any pointer references them, and S3 reads are strongly consistent, `resolve` SHALL never observe a partially published manifest.

Pointer resolution SHALL observe the **current** tail, never a frozen client-side listing: `head`, `resolve`, `list_pointers`, and `list_versions` SHALL re-list the relevant pointer/event prefix (bypassing or invalidating any cached listing) so that a long-lived reader sharing one object-store filesystem instance sees pointer moves, newly published versions, and tombstones made by other processes after its first read. This is the read-side counterpart of the write path's authoritative tail read; without it a cached listing silently returns a real-but-stale version. Reads of immutable, content-addressed objects (manifests and blobs) MAY remain cached, since they never change.

#### Scenario: head reads a pointer without touching a manifest
- **WHEN** `head` is called for a pointer
- **THEN** it returns the pointer's current version from its event stream without reading any manifest object

#### Scenario: Resolve never sees a half-published version
- **WHEN** `resolve` runs concurrently with a publish
- **THEN** it returns either the fully previous version or the fully new version, never a mixture

#### Scenario: A long-lived reader sees a concurrent publish
- **WHEN** one process resolves a coordinate's head, another process (sharing the same cached object-store filesystem) publishes a newer version advancing that head, and the first process resolves head again
- **THEN** the first process resolves the newer version, not the one cached at its first read

#### Scenario: A long-lived reader sees a newly published version by id
- **WHEN** a version is published after a long-lived reader's first resolve, and that reader resolves it by `Pin`
- **THEN** it resolves rather than raising `NotFound` from a stale version listing

#### Scenario: A long-lived reader honors a concurrent tombstone
- **WHEN** a version is tombstoned (GC-reclaimed) after a long-lived reader's first read, and that reader resolves a pointer whose tail targeted it
- **THEN** the reader observes the tombstone and skips to the last valid version (or `NotFound`), never resolving to the reclaimed manifest
