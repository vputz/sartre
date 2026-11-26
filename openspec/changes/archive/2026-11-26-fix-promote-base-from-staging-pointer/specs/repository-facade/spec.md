# Spec Delta

## MODIFIED Requirements

### Requirement: Derive a version from a base
The `Repository` SHALL provide `publish_over(coord, base=HEAD, *, changes={}, remove=(), rename={}, pointer="head", stage=False, metadata=None, actor="unknown", reason=None) -> PublishOverResult` that creates a new immutable version from an existing one. It SHALL resolve `base` to a snapshot and seed the manifest entry set from that snapshot's entries, then apply, in order: `remove` (drop those paths), `rename` (re-key entries), and `changes` (override/add). It SHALL reuse every untouched entry by its existing `content_hash` — such entries require no source and SHALL NOT be uploaded or re-hashed. Paths SHALL be normalized and the case-collision invariant checked on the final merged set.

- `changes` maps a logical path to a source (`bytes` or `Path`) that overrides an existing path or adds a new one; each such source is hashed to build its entry and uploaded (skipping the wire if the durable store already holds it).
- `remove` names one or more paths to drop; removing a path absent from the merged set SHALL raise a typed error.
- `rename` maps an existing `old` path to a `new` path: it SHALL move the base entry (its `content_hash`, `size`, `inline`) to `new` and drop `old`, requiring no source and uploading nothing. Renaming a path absent from the base SHALL raise a typed error.

`publish_over` SHALL follow the same lease/commit/advance discipline as `publish`: acquire the lease over the new version and *all* its blob hashes (changed and inherited) before uploading, so an inherited blob cannot be swept mid-derive; upload only the `changes` sources; re-verify the lease before `commit` and before advancing; then advance the pointer via compare-and-swap, recording `actor`/`reason` on the tip event. It SHALL always create a new version and SHALL NOT mutate the base. It SHALL return a `PublishOverResult` carrying the new `version`, the counts of entries `inherited`, `replaced`, `added`, `removed`, and `renamed`, and `staged_pointer` (the staging pointer name when `stage=True`, otherwise `None`).

When `stage=False` (the default) it advances `pointer`. When `stage=True` it SHALL instead advance a staging pointer it names itself (ignoring `pointer`) and report that pointer as `staged_pointer` — so the derived version can be verified via that pointer and later promoted to head with a base-CAS `promote`. It SHALL record the base's resolved version as the **base of the staging pointer's advance move** (a per-operation, append-only, non-deduplicated record), which is the authoritative base a later `promote` compare-and-swaps against. It SHALL NOT write `derived_from` into the new version's manifest metadata: that field is removed, because manifest metadata is content-addressed and first-writer-wins and therefore cannot carry a per-operation base. The staging pointer name SHALL be content-derived from the base version and the set of affected logical paths (those changed, removed, or renamed), so that re-running the same derive reuses the same staging pointer rather than creating a new one (idempotent repair), and SHALL be a valid alias segment.

The advance SHALL be a compare-and-swap against the version the derive inherited from: for a same-lineage advance (the advanced pointer is the base), the CAS `expected` SHALL be the base's resolved version, so if that pointer moved between the base read and the advance the operation SHALL raise `Conflict` rather than silently discard the intervening change. A derive SHALL NOT advance a pointer to a version whose base differs from the pointer's value at the moment of advance. (This no-silent-lost-update property is verified in `model/PublishOver.tla`.)

#### Scenario: Stale base loses the CAS
- **WHEN** `publish_over` derives from head `v5` and, before it advances, a concurrent publish moves head to `v6`
- **THEN** the derive's advance raises `Conflict` (its `expected` is `v5`) and head remains `v6` — the derived version does not overwrite `v6`

#### Scenario: Change one file, reuse the rest by hash
- **WHEN** `publish_over(coord, changes={"cfg.json": new_bytes})` derives from a base with `cfg.json` and other files
- **THEN** the new version contains the new `cfg.json` and every other file identical to the base, only `cfg.json`'s blob is uploaded (unchanged blobs are neither uploaded nor re-hashed), the base version is unchanged, and the result reports one entry replaced and the rest inherited

#### Scenario: No-op derive equals the base version
- **WHEN** `publish_over(coord, base=b)` is called with empty `changes`, `remove`, and `rename`
- **THEN** the resulting version id equals `b`'s version (identical entries yield an identical manifest hash)

#### Scenario: Overriding with identical bytes equals the base version
- **WHEN** a path is overridden via `changes` with bytes identical to the base's content for that path
- **THEN** the resulting version id equals the base's version, and no blob is uploaded

#### Scenario: Rename reuses the blob with no upload
- **WHEN** `publish_over(coord, rename={"old.bin": "new.bin"})` derives from a base containing `old.bin`
- **THEN** the new version contains `new.bin` with `old.bin`'s content hash, does not contain `old.bin`, and no blob is uploaded; renaming a path not present in the base raises a typed error

#### Scenario: Remove drops a path
- **WHEN** `publish_over(coord, remove=["old.txt"])` derives from a base containing `old.txt`
- **THEN** the new version does not contain `old.txt`; removing a path not present raises a typed error

#### Scenario: Reused blobs are protected and resolvable
- **WHEN** a derived version is committed and later resolved
- **THEN** its unchanged files are served from the pre-existing blobs, which were held as GC roots under the derive's lease through commit and advance

#### Scenario: Stage lands off head and records its base
- **WHEN** `publish_over(coord, changes={"cfg.json": new_bytes}, stage=True)` derives from head `b`
- **THEN** head is unchanged, the derived version is advanced onto a content-derived staging pointer reported as `staged_pointer`, and the staging pointer's advance move records `base = b` — so a later `promote` can base-CAS head to it regardless of whether the derived tree already exists as a version

#### Scenario: Staged base is recorded even when the derived tree already exists
- **WHEN** `publish_over(..., stage=True)` derives a tree byte-identical to a version already committed (so `commit` returns the existing version and does not rewrite its metadata)
- **THEN** the staging pointer's advance move still records this derive's own `base`, independent of any `derived_from` the pre-existing manifest's metadata carries

#### Scenario: Re-running the same staged derive reuses its pointer
- **WHEN** the same `publish_over(..., stage=True)` derive (same base and same affected paths) is run twice
- **THEN** both runs use the same staging pointer name, so a re-run after a failed verify reuses that pointer rather than leaving an orphaned one

### Requirement: Base-CAS promotion of a staged version
`Repository` SHALL expose `promote(coord, staged, *, pointer="head", force=False, actor="unknown", reason=None)` that advances `pointer` to the version `staged` resolves to, compare-and-swapping against the base that version was staged from so a concurrent publish that advanced the pointer since staging SHALL cause `Conflict`, never a silent lost update. If `pointer` already resolves to the target version, `promote` SHALL be a success no-op. Otherwise, when `staged` is an alias, the base SHALL be the one recorded on that pointer's newest advance move, read from that single pointer's own record (not by scanning the whole coordinate's pointer-move history) and read even when a later deletion tombstone sits above that advance; this recorded base is defined only for staging advances. When no base is available — `staged` is a `Pin` or `Head` (no pointer to read), or the alias records none (created before this behavior, or hand-made) — `promote` SHALL refuse with a clear message naming promotion via the staging alias, re-staging, or `force` as the recovery, unless `force` requests a plain last-writer move against the pointer's current value. If the base compare-and-swap raises `Conflict`, `promote` SHALL re-read `pointer` and, if it now equals the target version, return it as a success no-op; otherwise it SHALL raise `Conflict`. `promote` SHALL NOT consult manifest metadata for the base. `AsyncRepository` SHALL expose an awaitable wrapper for both `delete_pointer` and `promote`.

#### Scenario: Promote a cleanly-staged version
- **WHEN** a version was staged from base `b` (head still at `b`) and `repo.promote(coord, staged)` is called
- **THEN** head advances to the staged version (CAS against `b`, recovered from the staging pointer's move, succeeds)

#### Scenario: Promote a staged tree that already exists as a version
- **WHEN** a tree identical to an existing version is staged from base `b` with head at `b`, and `repo.promote(coord, staged)` is called (the existing manifest's shared metadata reflects whoever first committed that tree, not this operation)
- **THEN** head advances to that version, because the CAS base is recovered from this staging pointer's move (`b`) — no false conflict from stale shared metadata

#### Scenario: Promote conflicts when head moved during verification
- **WHEN** a version was staged from base `b`, another publish then advanced head to a different version `c`, and `repo.promote(coord, staged)` is called
- **THEN** it raises `Conflict` and head still resolves to `c` (the concurrent publish is not clobbered)

#### Scenario: Concurrent duplicate publish of the same tree is a no-op success
- **WHEN** head already resolves to the version `staged` resolves to (a concurrent or duplicate publish of the same content-addressed tree) and `repo.promote(coord, staged)` is called
- **THEN** it returns that version without error and head is unchanged

#### Scenario: Duplicate publish during the base compare-and-swap resolves to a no-op
- **WHEN** `promote` observes head not yet at the target, then a concurrent duplicate publish advances head to the target version before `promote`'s base compare-and-swap runs
- **THEN** the compare-and-swap raises `Conflict`, `promote` re-reads head, finds it at the target version, and returns success — no lost update and no spurious conflict surfaced to the caller

#### Scenario: Promote by version id or head ref has no recoverable base
- **WHEN** `repo.promote(coord, Pin(v))` (or a `Head` ref) is called — a non-alias ref with no staging pointer to read a base from — and head does not already hold `v`
- **THEN** without `force` it refuses with a message naming the staging alias or `force`; with `force` it performs a last-writer move against head's current value

#### Scenario: Promote refuses a version with no recorded base
- **WHEN** `repo.promote(coord, staged)` is called for an alias whose move records no base (staged before this behavior, or hand-made), and head does not already hold the target
- **THEN** it refuses with a message explaining the base is unknown and naming re-staging or `force` as the recovery, unless `force` requests a last-writer override
