# Design Memo: A Generic Content-Addressed Binary Artifact Repository (Delta + S3)

> **Status:** exploratory design notes, not implemented. Written to be *portable* —
> a reader with no access to the originating codebase ("signalq") should be able to
> continue this discussion and decide whether to build it as a standalone library.
>
> **Origin:** distilled from a planning session that examined how one production system
> (an ML signal-training/serving platform, "signalq") stores and retrieves versioned
> binary artifacts today, and what a unified replacement would look like.

---

## 0. What this is

A proposal for a small, general-purpose **versioned binary artifact repository** with two
cleanly separated planes:

- **Manifest plane** — a transactional metadata store (Delta Lake over S3, or any ACID
  store) that maps `(artifact name, environment, version, logical path) → content hash`,
  plus mutable **pointers** (`latest`, `production`, per-env "HEAD") that resolve to versions.
- **Blob plane** — a dumb **content-addressed** object store (S3) holding immutable bytes
  keyed by their own hash (`sha256`/`blake3`). No logical names, no versions — just
  `hash → bytes`.

It is essentially **content-addressable storage (CAS)** — the same shape as git's object
store + refs, OCI image layers + manifests, Nix store, and restic/borg — repackaged as a
reusable library over Delta + S3.

The goal is to **replace two ad-hoc artifact stores with one abstraction**:
1. a **git-based registry** (a git repo + Git-LFS used purely as a versioned file store), and
2. a **WandB-artifact-based store** (used as a blob/seed store, not for its experiment-tracking).

Neither of those uses the *real* power of its backend (no git merges/history/tags; no WandB
sweeps/metrics/lineage on the relevant paths). Both are, in effect, "fetch a named, versioned
artifact from a remote registry to bootstrap or feed a service." That overlap is the
opportunity.

---

## 1. The two usage patterns we observed (abstracted)

These are the two real-world call sites the abstraction must serve. They are described
generically so they make sense without the original code.

### Pattern A — "Model registry" (live, polled, lazy, many-file)
A long-running **service** continuously tracks a registry and serves from it:
- **Addressed by** an environment selector (`dev`/`stage`/`release`) that resolves to a
  *mutable tip*, OR an **immutable pin** (a commit-SHA-like id) for reproducible reruns.
- The artifact is a **tree of many files**: small metadata/config files (~KB, JSON/YAML)
  plus large model weights (~100 MB–1 GB binary "checkpoints").
- **Lazy large blobs:** the service reads the small metadata eagerly to build a catalog,
  but pulls a heavy checkpoint blob only when that specific model is actually loaded.
  (Implemented today via Git-LFS pointer files + `git lfs pull` on demand.)
- **Freshness:** the service **polls** the tip on an interval (~60 s), cheaply detects
  whether it moved, and **hot-reloads** the changed entries at runtime.
- **Failure mode:** degraded-but-up — keeps serving the previously fetched version if the
  remote is briefly unreachable; network calls are timeout-guarded.
- **Mutability:** treated as a read replica; refreshed in place as the tip advances.

### Pattern B — "Seed snapshot" (one-shot, whole-blob, bootstrap)
A **bootstrap/init step** downloads a single pre-built binary file to seed a database:
- **Addressed by** a registry/namespace per environment + a fixed artifact name + an
  **alias** (e.g. `latest`, or a version string like `v0.1.0`). In practice it pins by
  *alias only* — there is no immutable-version pin in use, which is a reproducibility gap.
- The artifact is **one big binary blob** (a whole embedded-DB snapshot file).
- Fetched **once**, eagerly and in full, in an offline/init phase *before* the service runs;
  copied into the real (read-write) database; then **discarded**. The seed is strictly
  one-way and read-only — runtime writes go to the real DB, never back to the seed.
- **No polling, no hot-reload.**
- **Failure mode:** hard fail — if the artifact can't be fetched, bootstrap aborts (no
  empty fallback).

### Key realization
**Pattern A is a strict superset of Pattern B.** A is many files, polled, lazy, pinnable;
B is one file, once, eager, alias-addressed. If the abstraction satisfies A, then B is just
"resolve a pinned/aliased version, then fetch the whole thing." Design to A; B falls out.

The differences that the unified model must *absorb* (not erase):

| Axis | Pattern A (model registry) | Pattern B (seed) |
|---|---|---|
| Unit fetched | manifest with **many** entries | manifest with **one** entry |
| Env separation | per-env *tip* (branch-like) | per-env *namespace* (separate registry) |
| Mutable pointer | env HEAD | alias (`latest`) |
| Immutable pin | yes (SHA-like) — used | possible but **unused** (gap to close) |
| Blob fetch | lazy, per-file | eager, whole |
| Freshness | poll + hot-reload | one-shot |
| After load | live read replica | one-way seed, discarded |
| Failure | degraded-but-up | hard fail |

---

## 2. The unifying abstraction

> **An artifact is a named, versioned *manifest of entries*.**
> A **version** is immutable. A **pointer** is a mutable name that resolves to a version.
> An **entry** is one logical file: `(path, content_hash, size)`, whose bytes are fetched
> lazily by content hash.

A single-file seed is just a manifest with one entry. A model catalog is a manifest with many
entries. "Tree vs blob" stops being a distinction — it's "manifest with N entries." That
collapse is what makes one interface viable.

---

## 3. The read interface

Types (Python-flavored pseudocode; the language is incidental):

```python
@dataclass(frozen=True)
class Coordinate:
    name: str      # "models", "backtest-db"
    env: str       # "dev" | "stage" | "release"

class Ref: ...                        # sealed union — WHICH version of a coordinate
@dataclass(frozen=True)
class Head(Ref):  pass                # the mutable tip (env HEAD / alias "latest")
@dataclass(frozen=True)
class Alias(Ref): name: str           # a named mutable pointer ("production", "best")
@dataclass(frozen=True)
class Pin(Ref):   version: str        # immutable (a SHA-like id / explicit version)

@dataclass(frozen=True)
class Entry:
    path: str            # logical path inside the artifact
    content_hash: str    # the blob key — bytes are NOT here
    size: int

@dataclass(frozen=True)
class Snapshot:
    coord: Coordinate
    version: str                  # immutable id this ref resolved to
    created_at: datetime
    metadata: Mapping[str, Any]   # free-form (date ranges, provenance, ...)
    entries: tuple[Entry, ...]    # the manifest — cheap, no heavy bytes
```

Interface (3-method core + 2 optional):

```python
class BinaryRepo(Protocol):

    # 1. Cheap pointer read — "what version is this ref right now?"  (the polling primitive)
    def head(self, coord: Coordinate, ref: Ref = Head()) -> str: ...          # -> version id

    # 2. Resolve a ref to an immutable manifest (no heavy bytes yet).
    def resolve(self, coord: Coordinate, ref: Ref = Head()) -> Snapshot: ...

    # 3. Lazily materialize ONE entry's bytes to a local path. Cached by content_hash.
    def open(self, snap: Snapshot, path: str) -> Path: ...

    # 4. (convenience) materialize the WHOLE artifact eagerly — the seed's one move.
    def fetch_all(self, snap: Snapshot) -> Path: ...

    # 5. (optional) enumerate pointers / versions — reproducibility, ops tooling, GC.
    def list_pointers(self, coord: Coordinate) -> Mapping[str, str]: ...       # alias -> version
    def list_versions(self, coord: Coordinate) -> Sequence[str]: ...
```

The two call sites against it:

```python
# Pattern B — seed: one blob, pinned, once, fail-hard
snap  = repo.resolve(Coordinate("backtest-db", "release"), Pin("v0.1.0"))  # or Head()
local = repo.fetch_all(snap)        # the whole snapshot file
# resolve() raising == bootstrap fails, preserving today's behavior

# Pattern A — model registry: many files, polled, lazy, hot-reload
coord, last = Coordinate("models", "release"), None
while True:
    cur = repo.head(coord, Head())              # cheap; replaces "ls-remote + catalog hash"
    if cur != last:
        snap = repo.resolve(coord, Head())      # manifest of small metadata entries
        reload_catalog(snap)                    # parse the small entries eagerly
        last = cur
    sleep(60)

ckpt = repo.open(snap, f"models/{id}/checkpoint.ckpt")  # heavy blob, lazy, content-cached
# reproducible rerun:
snap = repo.resolve(coord, Pin("a1b2c3..."))
```

What each concern maps to:
- **Lazy large blobs:** `resolve` returns only the manifest (paths + hashes + sizes); bytes
  move only on `open`/`fetch_all`, cached by `content_hash`.
- **Change detection:** `head` returns an immutable version id; the poller diffs it against
  last-seen. Cheaper and more honest than recomputing a catalog hash. Diff `snap.entries`
  by `content_hash` to find *which* entries changed for surgical hot-reload.
- **Pin vs latest:** `Head`/`Alias` = mutable; `Pin` = immutable. Unifying gives the seed a
  reproducible pin "for free," closing its current gap.

**Deliberately NOT in the read interface** (matching what real usage exercises): no
commit/push/publish (that's the write side), no history/log/diff/blame/merge, no date-based
addressing (dates are *data* in `metadata`, never an index).

---

## 4. The two-plane architecture (the core idea)

Separate **identity/structure** from **bytes**:

```
        MANIFEST PLANE (Delta over S3)              BLOB PLANE (S3)
  ┌──────────────────────────────────────┐   ┌──────────────────────────┐
  │ pointers:   (name,env,ptr) → version  │   │  s3://blobs/<hash>        │
  │ manifests:  (name,env,version, path)  │   │     = raw bytes, immutable│
  │                 → content_hash, size  │   │     deduped by identity   │
  └──────────────────────────────────────┘   └──────────────────────────┘

  head(coord, ref)    = read 1 pointer row              → version            (cheap)
  resolve(coord, ref) = SELECT path, content_hash       → Snapshot/manifest  (cheap, no bytes)
                        FROM manifests WHERE version=…
  open(snap, path)    = S3 GET blobs/<content_hash>      → bytes (lazy)
                        + local cache ALSO keyed by hash
```

### 4.1 The key must be a content hash, not a random UUID
This is the crux that makes dedup *structural* rather than *bookkept*:
- A new version that changed one file: every unchanged file hashes to the **same** key it
  had before, so the new manifest just lists those same keys. There is nothing to "alias
  back to" — identical bytes *are* the same key.
- A random UUID would force you to hash anyway (to detect a dup), then maintain a
  `content_hash → uuid` side-index to reuse the old id. Same hashing, extra indirection,
  less benefit.
- Content-hash keys also give **integrity for free** (verify on read) and **idempotent
  writes** (re-uploading identical bytes is a no-op).
- The only reason to prefer opaque UUIDs is if identity must survive content changes
  (re-encryption, re-compression) without rewriting manifests — not a need here.

### 4.2 Dedup helps reads too, not just storage
Because the **local cache is also keyed by content hash**, a consumer that already pulled
version v1 keeps those blobs; resolving v2 re-downloads only the blob(s) that actually
changed. For the model-registry hot-reload path this is a large win over per-version full
pulls — and it's automatic.

> Note: Git-LFS already content-addresses by sha256 with pointer files in the tree. So the
> git-based registry is *already half* this architecture; unifying means generalizing LFS's
> model to everything and swapping the backends (git + WandB) for Delta + S3.

### 4.3 What Delta specifically earns (vs hand-rolled S3 manifest files)
- **Atomic `resolve`** — the transaction log makes version commits all-or-nothing; you never
  resolve a half-published manifest. (Raw-S3 alternative would need a careful
  manifest-pointer-swap discipline.)
- **Version history = time-travel for free** — `list_versions` and "manifest as of version N"
  are native.
- **Queryable manifests** — which makes GC (below) tractable.

---

## 5. The new responsibility: garbage collection

CAS + dedup means you **cannot delete a blob until no live manifest references its hash** —
mark-and-sweep / reference counting. The backends did this implicitly before (LFS prune;
artifact retention); now the library owns it. With manifests in Delta it's one anti-join:

```sql
SELECT content_hash FROM blobs
WHERE content_hash NOT IN (
  SELECT content_hash FROM manifests WHERE version IN (<retained versions>)
)
```

Upside: this **centralizes and retires** two prior ad-hoc pruning mechanisms (LFS prune +
publish-side orphan deletion) into one policy-driven job.

---

## 6. Refinements to file away
- **Inline tiny files.** Manifests have many ~1 KB entries (metadata/config/small YAML).
  Store blobs below a threshold (e.g. a few KB) **inline in the Delta row** (a `bytes`
  column); only large blobs (checkpoints, DB snapshots) go to S3. Keeps the common catalog
  read to a single Delta query with zero S3 round-trips. (Git does the analogous thing with
  packfiles.)
- **Do not sub-file chunk.** Restic/borg chunk *within* files; unnecessary here. The dedup
  win is across *versions* where most files are byte-identical, not within a single large
  file that changes wholesale. Whole-file CAS captures essentially all the benefit.
- **Hash choice.** `blake3` (fast) or `sha256` (ubiquitous, and matches LFS for an easy
  migration). Decide early; it's baked into every key.

---

## 7. Open design questions (for the next session)
1. **`env` in the Coordinate vs in the Ref.** This memo puts env *in the coordinate*
   (`models@release`), unifying "branch" and "separate registry" into one dimension so
   `Head/Alias/Pin` mean the same thing in every env. Confirm or revisit.
2. **`head()` cheapness is a contract.** The poll loop depends on `head` being a single
   cheap round-trip (one pointer-row read), *not* a manifest fetch. Make it an explicit
   interface guarantee so a naive impl can't accidentally list/scan.
3. **Sync vs async.** Original call sites are mixed (sync registry fetch; async services;
   offline bootstrap). Likely a sync core with async wrappers — decide before the interface
   ossifies.
4. **Manifest atomicity** is the one place the backend choice leaks through the interface:
   Delta gives it transactionally; hand-rolled S3 would need a pointer-swap protocol.
5. **Write/publish transaction (not yet designed).** Crash-safe ordering is
   *blobs-first → manifest → pointer*: (a) PUT any blobs whose hash isn't already present
   (idempotent), (b) commit the new manifest version, (c) atomically advance the pointer.
   A crash before (c) leaves unreferenced blobs (collected later by GC) but never a
   dangling/half pointer. This is the next thing to flesh out.
6. **Schema shape:** one wide `manifests` table + a small mutable `pointers` table is the
   minimum; whether `blobs` needs its own table (size/refcount/created_at) or can be derived
   from S3 listing + manifest scan is open. Decide alongside the GC policy.

---

## 8. Suggested next steps for a standalone library
- Define the **write interface** (§7.5) and the **publish transaction** ordering.
- Pin the **Delta schema** (§7.6): tables, keys, the inline-vs-S3 threshold.
- Build a **reference implementation** of the read core (`head`/`resolve`/`open`) over
  Delta + S3 (or a local-filesystem stub first), plus the content-hash cache.
- Validate against the two patterns: a **polled, lazy, many-file** consumer and a
  **one-shot whole-blob** bootstrap.
- Decide the **GC/retention policy** and write it as a single job over the manifest plane.
- Only then evaluate migrating the original two stores behind it (model registry first —
  it's the superset; the seed is the trivial special case).

### One-line summary
> **Delta = the source of truth for structure and identity** (pointers, versions, manifests,
> path→hash). **S3 = a dumb content-addressed bag of immutable blobs keyed by hash.** Reads
> are `resolve` (Delta query) + `open` (S3 GET by hash); dedup is structural on both storage
> and client cache; GC is one anti-join. The model-registry pattern is the superset to design
> for; the seed-snapshot pattern is `resolve(pin) → fetch_all`.
