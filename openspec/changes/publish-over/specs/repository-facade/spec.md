## ADDED Requirements

### Requirement: Derive a version from a base
The `Repository` SHALL provide `publish_over(coord, base=HEAD, *, set={}, remove=(), pointer="head", metadata=None, actor="unknown", reason=None) -> Version` that creates a new immutable version from an existing one. It SHALL resolve `base` to a snapshot, seed the manifest entry set from that snapshot's entries, drop the paths in `remove`, then override or add the paths in `set` (hashing each supplied source to build its entry). It SHALL reuse every unchanged entry by its existing `content_hash` — such entries require no source and SHALL NOT be uploaded or re-hashed. Paths SHALL be normalized and the case-collision invariant checked on the merged set. Removing a path absent from `base` SHALL raise a typed error.

`publish_over` SHALL follow the same lease/commit/advance discipline as `publish`: acquire the lease over the new version and *all* its blob hashes (changed and reused) before uploading, so a reused blob cannot be swept mid-derive; upload only the `set` sources (skipping any the durable store already holds); re-verify the lease before `commit` and before advancing; then advance the target pointer via compare-and-swap, recording `actor`/`reason` on the tip event. It SHALL always create a new version and SHALL NOT mutate the base.

#### Scenario: Change one file, reuse the rest by hash
- **WHEN** `publish_over(coord, set={"cfg.json": new_bytes})` derives from a base with `cfg.json` and other files
- **THEN** the new version contains the new `cfg.json` and every other file identical to the base, only `cfg.json`'s blob is uploaded (unchanged blobs are neither uploaded nor re-hashed), and the base version is unchanged

#### Scenario: No-op derive equals the base version
- **WHEN** `publish_over(coord, base=b)` is called with empty `set` and `remove`
- **THEN** the resulting version id equals `b`'s version (identical entries yield an identical manifest hash)

#### Scenario: Overriding with identical bytes equals the base version
- **WHEN** a path is overridden via `set` with bytes identical to the base's content for that path
- **THEN** the resulting version id equals the base's version, and no blob is uploaded

#### Scenario: Remove drops a path
- **WHEN** `publish_over(coord, remove=["old.txt"])` derives from a base containing `old.txt`
- **THEN** the new version does not contain `old.txt`; removing a path not present in the base raises a typed error

#### Scenario: Reused blobs are protected and resolvable
- **WHEN** a derived version is committed and later resolved
- **THEN** its unchanged files are served from the pre-existing blobs, which were held as GC roots under the derive's lease through commit and advance
