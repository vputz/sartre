## Context

`add-core-ports` shipped the interface skeleton with `Version` as an opaque
identifier and `Registry.commit` idempotency marked TBD. The format of a version
id is the last Tier-1 decision: it ossifies `commit`, `head` change-detection,
dedup, cross-env promotion, and GC reachability. This change settles it and pins
the manifest plane's ordering model. The read interface still excludes date-based
*addressing* (memo §3); ordering/time-travel are ops queries over a log, not new
addressing axes.

## Goals / Non-Goals

**Goals:**
- A version identity that makes the publish path idempotent and cross-env
  promotion free.
- An ordering/history model that gives `list_versions` order and as-of lookups
  without coupling identity to a backend sequence or to wall-clock time.
- A concrete, tested manifest-hash function.

**Non-Goals:**
- The publish transaction that atomically appends a log row and CAS-moves a
  pointer (next change; first TLA+ target).
- Garbage collection / retention (a later change that *reads* this log).
- A backend implementation of the log (Delta/local); only the contract is fixed.
- Date-based core addressing — `resolve` stays `Head`/`Alias`/`Pin`.

## Decisions

### D1 — Version = content hash of the canonical manifest entries
Hash the sorted `(path, content_hash)` pairs of the entries, domain-separated so
a version can never equal a raw blob key. Self-describing (`"sha256:…"`) and
computed with the same pluggable `Hasher` as blobs.
- *Why*: idempotent `commit`, exact/honest `head` change-detection (the id moves
  iff the content moves), manifest-level dedup, integrity for free, and the
  whole system collapses to a git shape (refs → manifests → blobs).
- *Alternatives*: random/ULID (not idempotent, no integrity); monotonic integer
  (needs coordination, backend-coupled, not idempotent); git-style commit hash
  with parent+time (regains ordering but **loses** idempotency and promotion).

### D2 — Hash entries only; exclude size, inline, metadata, coordinate
Identity is `(path, content_hash)` per entry. `size` and `inline` are derived
from / carry the same bytes as `content_hash`; `metadata` is decoration; the
coordinate must stay out or `models@dev` and `models@release` with identical
files would hash differently.
- *Why*: this exact boundary is what preserves dedup and free promotion. Folding
  any mutable/contextual field into the hash would break "same files → same
  version."
- *Risk*: "same bytes = same version" means an intentional re-release of
  identical files is the *same* version with the pointer moved — accepted as
  correct for a content-addressed store.

### D3 — Identity is content; order is position in a log
A new append-only **commit log**, partitioned per coordinate, records tip events:
`(name, env, seq, version, created_at, pointer, metadata)`. `seq` is the
authoritative monotonic order; `created_at` serves time queries only (never
trusted for ordering). The **pointer table** holds only the current tip
(`(name, env, pointer_name) → version`) so `head` stays a single-row read.
- *Why*: content hashes are unordered by construction. Ordering must live
  outside the id. This recovers the entire appeal of monotonic integers (a
  friendly ordinal) as a *log column*, without coupling it to identity.
- `list_versions(coord)` reads the log ordered by `seq`. **As-of**: find the
  latest log row for a pointer with `created_at ≤ T`, return its `version`; the
  caller then `Pin`s it. The same log is GC's future reachability source.

### D4 — env-in-coordinate, promotion = repoint
Confirmed. Since the version id is coordinate-independent, promoting `dev`'s
version X to `release` appends a `release` log row referencing X and CAS-moves
`release`'s pointer to X — the manifest and blobs are shared, never copied.

### D5 — Schema note: name/env as two columns (§7.6)
Pointer and log tables key on `name` and `env` as **separate typed columns**, not
a concatenated string — for env-spanning queries (promotion, ops, per-env
retention), columnar pushdown/partitioning, and no delimiter/escaping hazard. The
domain `Coordinate` stays a single value in code; the storage adapter splits it.
This is a physical-schema lean, recorded here, finalized with the Delta schema.

## Risks / Trade-offs

- **Unordered, non-human-friendly ids** → mitigated by `seq`/`created_at` in the
  log and by `Alias` for friendly handles; you reference versions via pointers
  and the log, never by reading a hash (as with git SHAs).
- **"Same bytes = same version"** → an intentional duplicate publish is the same
  version (a new log event, same id). Accepted; it is the dedup being paid for.
- **Orphan versions** (committed but never pointed at) won't appear in a
  tip-event log → they are exactly GC-eligible garbage; if visibility is wanted,
  the log can also record bare commits. Flagged for the GC change.
- **Hash-input canonicalization must be stable** → fixed encoding (sorted,
  NUL-separated, domain-tagged) pinned in `manifest_version`; changing it later
  would change every id, so it is versioned by the domain tag.

## Open Questions

- Should `manifest_version` fold in a manifest-format version beyond the domain
  tag (forward-compat for future entry fields)?
- Does the as-of query belong on the `Registry` Protocol or a separate ops
  interface (keeping the core read surface minimal)?
