## Context

`FsspecBlobBackend.put(key, data)` opens the final path and `shutil.copyfileobj`s into
it. If the process dies or the source raises mid-copy, a truncated object is left at the
content hash. `has(hash)`/`exists` then report it present, and `CasStore` only catches it
later via verify-on-read (`IntegrityError`) — a corrupt-cache failure mode that a clean
write would avoid entirely. The blob-store spec already calls for "a temporary file
followed by an atomic rename," implemented so far only as a per-hash lock in
`CachingStore`. This change makes the rename real, at the backend.

## Goals / Non-Goals

**Goals:**
- Atomic `FsspecBlobBackend.put`: stage then rename, so no partial blob is ever visible
  at its hash.
- Inherit that atomicity for `CasStore.put` and the `CachingStore` cache back-fill (both
  route through the backend `put`).
- Keep `list()` clean (temp namespace excluded) and give operators a `sweep_temp()`.
- A Hypothesis property proving the no-partial-blob invariant under interrupted puts.

**Non-Goals:**
- The optional `get_many` batch-fetch hook (throughput; deferred to the S3 backend).
- Cross-process temp GC policy/TTL — `sweep_temp()` is explicit, like `Repository.gc`.
- Changing the CAS/verify semantics or the `put` signature.

## Decisions

### D1: Stage under `{root}/.tmp/{uuid}`, then rename onto `{root}/{key}`
`put` writes to a unique staging path and `fs.mv(staging, final)`. Uniqueness (uuid)
means concurrent puts of the same hash never collide on the staging object; the final
rename is last-writer-wins onto an identical-bytes target (content-addressed, so any
winner is correct). If the final key already exists, `put` returns without writing
(idempotent, unchanged). On failure the staging object is removed in a `finally` where
possible; if the process dies first, it is simply orphaned.

*Why uuid staging rather than staging keyed by hash?* Two threads writing the same hash
would race on one staging path and could see a half-written stage; a per-put uuid keeps
each write private until its atomic rename.

### D2: Atomicity guarantees per filesystem
- **local, memory**: `mv` is `os.rename`/dict swap — truly atomic.
- **object stores (S3/GCS)**: no rename primitive; fsspec implements `mv` as
  server-side copy + delete. The copy publishes the destination object atomically (an
  object is never readable half-written), so a reader still sees all-or-nothing at the
  key. The staging object is the copy source and is deleted after. This is the standard
  content-addressed-store pattern and is sufficient: verify-on-read remains the backstop.

### D3: `.tmp` is a reserved namespace excluded from `list()`
Content hashes are `algo:hexdigest` and never begin with `.`. `list()` filters names
beginning with `.` (and skips the `.tmp` directory), so staging objects are never
surfaced as blobs — GC's sweep domain stays exactly the real blobs. `sweep_temp()`
enumerates and deletes staging objects for operators who want to reclaim orphans
promptly; leftover temps are otherwise inert (never read, never listed).

### D4: Failure injection is testable without real crashes
The Hypothesis/behavioral tests wrap the source stream (or the backend) so a `put` can
be made to raise partway through, then assert: the final key is absent, `has` is False,
`list()` omits it, and a subsequent successful `put` of the same bytes yields a
complete, verifying blob. A concurrency test drives many threads putting the same and
different hashes and asserts every listed key verifies.

## Risks / Trade-offs

- **Object-store `mv` is copy+delete, not a true rename** → a brief double-store and
  non-atomic *delete* of the staging object. Mitigation: the destination publish is
  atomic (all-or-nothing at the key), which is what correctness needs; a failed delete
  just orphans a temp that `sweep_temp()` reclaims.
- **Orphaned temps accumulate** if `put` dies before cleanup and `sweep_temp()` is never
  run. Mitigation: they are inert and excluded from `list()`; `sweep_temp()` is the
  explicit reclaim, consistent with GC being explicit.
- **Extra write + rename cost** vs a direct stream. Mitigation: negligible on local;
  on object stores the copy is server-side. Correctness outweighs it.

## Open Questions

- None. `get_many` and any cross-process temp TTL are explicit Non-Goals / future work.
