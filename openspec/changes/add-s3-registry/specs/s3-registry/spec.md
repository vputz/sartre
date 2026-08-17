## ADDED Requirements

### Requirement: S3-native registry on plain object storage
The system SHALL provide an `S3Registry` implementing the `Registry` port over an fsspec S3 filesystem, requiring no database, no external lock table, and no special bucket provisioning beyond standard object read/write/list/delete permissions. It SHALL store immutable, content-addressed manifests and per-coordinate mutable state entirely as S3 objects, using **put-if-absent** (`If-None-Match:*`) as its only write primitive — never an overwrite.

#### Scenario: Registry operates against a plain bucket
- **WHEN** an `S3Registry` is opened against an ordinary S3 bucket/prefix with no pre-provisioning
- **THEN** it can commit manifests, advance pointers, and resolve versions using only object put-if-absent, GET, LIST, and DELETE

#### Scenario: Content-idempotent commit
- **WHEN** `commit` is called twice with the same `(path, content_hash)` entries
- **THEN** the manifest object is written once (the second put-if-absent is a benign no-op) and both calls return the same `Version`

### Requirement: Compare-and-swap pointer advance via put-if-absent
`set_pointer(coord, name, version, *, expected, actor, reason)` SHALL advance a pointer by appending the next zero-padded event object to that pointer's append-only stream with put-if-absent. It SHALL raise `Conflict` when the pointer's current value does not equal `expected`, `NotFound` when `version`'s manifest does not exist, and SHALL treat a put-if-absent failure (surfaced by s3fs as `FileExistsError`) as a lost race — re-reading the stream tail and retrying. The sequence SHALL be gap-free: an event at sequence N+1 is written only after N was observed as the tail.

#### Scenario: Exactly one of two racing writers wins
- **WHEN** two writers advance the same pointer from the same observed tail and both attempt the next sequence number
- **THEN** put-if-absent grants exactly one; the loser observes `FileExistsError`, re-reads the tail, and — its `expected` now stale — raises `Conflict` without overwriting the winner

#### Scenario: Advancing an uncommitted version is refused
- **WHEN** `set_pointer` targets a version whose manifest object does not exist
- **THEN** it raises `NotFound` and appends no event

### Requirement: Compare-and-swap reads the authoritative tail, never a cached listing
The `set_pointer` tail read SHALL query authoritative S3 state, bypassing any fsspec directory-listing cache. Put-if-absent enforcement is server-side and prevents lost writes regardless of client caching, but a stale tail read could misjudge the pointer's current value against `expected`; the implementation SHALL therefore invalidate or bypass the listing cache on the CAS path.

#### Scenario: Stale listing cache does not corrupt the CAS
- **WHEN** a prior listing of a pointer stream is cached and the pointer has since advanced
- **THEN** `set_pointer` reads the current tail from authoritative S3 and evaluates `expected` against the true current value

### Requirement: Cheap pointer read and atomic resolve
`head` SHALL resolve a pointer by reading its stream's tail event (a bounded LIST plus one GET) and SHALL NOT fetch or scan a manifest. `resolve` SHALL read the pointer's target version and then the immutable manifest object; because manifests are content-addressed and written before any pointer references them, and S3 reads are strongly consistent, `resolve` SHALL never observe a partially published manifest.

#### Scenario: head reads a pointer without touching a manifest
- **WHEN** `head` is called for a pointer
- **THEN** it returns the pointer's current version from its event stream without reading any manifest object

#### Scenario: Resolve never sees a half-published version
- **WHEN** `resolve` runs concurrently with a publish
- **THEN** it returns either the fully previous version or the fully new version, never a mixture

### Requirement: Append-only, forever history with tombstone reclamation
The commit log and pointer-move history SHALL be the per-pointer event objects themselves, read append-only; the per-coordinate log order SHALL be the merge of pointer streams by event time. `drop_version` SHALL NOT prune event objects; it SHALL reclaim the manifest object and write a version tombstone, and enumeration (`list_versions`, `list_pointers`) SHALL exclude tombstoned versions so results match a pruning backend observationally.

#### Scenario: Dropping a version keeps its history
- **WHEN** `drop_version(v)` reclaims `v`'s manifest
- **THEN** the historical events that referenced `v` remain, a tombstone for `v` is written, and `list_versions` no longer reports `v`

#### Scenario: History survives across processes
- **WHEN** a coordinate's pointers have moved several times and the registry is reopened
- **THEN** `list_pointer_history` returns every move in order from the persisted event objects

### Requirement: Lease-free operation with blob-grace safety
Because S3 exposes no registry clock, `S3Registry` SHALL implement the lease surface as degenerate no-ops (`acquire_lease` returns a token, `renew_lease` returns true, `active_leased_hashes`/`active_leased_versions` return empty). Publish/GC safety for the blob window SHALL rely on the existing blob-grace backstop (blob store mtime), not on leases. The registry SHALL NOT rely on any manifest-mtime grace.

#### Scenario: GC over an S3 repository is safe without leases
- **WHEN** a publish uploads blobs and advances a pointer while `gc` runs with a `grace` exceeding the publish duration
- **THEN** the in-flight blobs are retained by blob-grace, the freshly pointed version is retained by reachability, and no in-use data is reclaimed

### Requirement: Safe version reclamation under concurrent promotion
`drop_version` on the transaction-less S3 backend SHALL be safe against a concurrent `set_pointer` that targets the same version: it SHALL NOT leave a pointer referencing a reclaimed manifest. The closure SHALL be a TLA-verified protocol (a shared put-if-absent marker serializing drop against promote, or an equivalent the model proves sufficient). `set_pointer` SHALL refuse to advance to a version undergoing reclamation.

#### Scenario: Promote racing a drop never dangles
- **WHEN** `drop_version(v)` and `set_pointer(name, v, …)` run concurrently for an out-of-retention `v`
- **THEN** either the promotion wins and the version is retained (drop aborts), or the drop wins and the promotion raises `NotFound` — never a pointer left targeting a reclaimed manifest

### Requirement: Behavioral equivalence to the reference backend
`S3Registry` SHALL be differentially tested against the in-memory reference backend over the same operation sequences, agreeing on pointer resolution, version enumeration, and commit/move provenance — modulo the append-only history semantics (tombstoned versions excluded from enumeration; event objects retained). The differential harness MAY run against an in-process S3 emulator that models conditional writes.

#### Scenario: Differential sequences agree
- **WHEN** the same sequence of commits, pointer moves, and drops is applied to the reference backend and `S3Registry`
- **THEN** `head`, `resolve`, `list_pointers`, and `list_versions` agree at every step, and `list_log`/`list_pointer_history` agree modulo retained-but-tombstoned history

### Requirement: One-URL opener with a conditional-write probe
The system SHALL provide `open_s3(url, *, blob_url=None, storage_options=None, cache_dir=None) -> Repository` assembling an `S3Registry` and an S3 blob `Store` from a single bucket URL (blobs default to a sibling prefix; `blob_url` splits the planes), reusing one fsspec configuration. `open_s3` SHALL probe the endpoint for conditional-write support and SHALL raise a clear error if the endpoint does not enforce put-if-absent.

#### Scenario: Single-URL open round-trips
- **WHEN** `open_s3("s3://bucket/repo")` publishes an artifact and later resolves it
- **THEN** the manifest is served from `repo/` object state and the blobs from the `repo/blobs` plane

#### Scenario: Non-conforming endpoint is rejected
- **WHEN** `open_s3` targets an S3-compatible endpoint that does not enforce `If-None-Match:*`
- **THEN** it raises an error naming the missing conditional-write support rather than operating unsafely
