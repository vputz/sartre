## Context

`SnapshotFS` (`src/sartre/fs.py`) is an interface-only stub: it subclasses fsspec's
`AbstractFileSystem`, declares the read-only contract in docstrings, and raises on
every method. The canonical `filesystem-view` spec already specifies the full
desired behavior (manifest-served listings, lazy random-access open, whole-tree
checkout, and — as future work — `sartre://` URL addressing and FUSE). A resolved
`Snapshot` already carries `entries: tuple[Entry, ...]` where each `Entry` has
`(path, content_hash, size)`, and the `Store` already returns seekable,
verify-on-read handles. This change wires those two together.

## Goals / Non-Goals

**Goals:**
- Make `SnapshotFS` fully usable as an in-process, read-only `AbstractFileSystem`:
  `ls`/`info`/`exists`/`find` from the manifest with zero blob fetch, and `_open`
  by logical path with random access.
- Add `Repository.snapshot_fs(snap)` and `Repository.checkout(snap, dest)`.
- Verify the interop thesis: hand the `fs` to a real fsspec consumer and read an
  artifact by logical name.

**Non-Goals:**
- `sartre://` URL protocol registration and the URL→`Repository` resolver.
- FUSE mounting and the fsspec key/value mapper.
- Any write path — the view is strictly read-only.
- On-disk cache tuning; the CachingStore already owns caching.

## Decisions

### D1: Build a path index once in `__init__`, serve all metadata from it
Walk `snapshot.entries` into `self._files: dict[str, Entry]` keyed by canonical
path, and derive `self._dirs: set[str]` as the set of all proper path prefixes of
every file (`a/b/c.txt` → `{"a", "a/b"}`). `ls`, `info`, `exists`, and `find` then
read only these structures — no blob is touched, satisfying the spec's zero-fetch
requirement. Directories are **synthetic**: a manifest records only files, so
intermediate directories exist only as prefixes. Paths are already `normalize_path`'d
at publish (posix, no leading slash, no `.`/`..`), so the index needs no
re-normalization beyond `_strip_protocol`/root-stripping that fsspec applies.

*Alternative considered:* lazily compute directory membership per `ls` call. Rejected
— the index is tiny (one entry per file), built once, and makes `info`/`exists` O(1).

### D2: `_open` returns the Store handle directly, not an fsspec-wrapped buffer
`_open(path, "rb")` looks up `self._files[path].content_hash` and returns
`self.store.open(content_hash)`. The CAS store already returns a **seekable,
integrity-verified** handle (a `BytesIO` in the reference backend; a local cache
file on a real backend). Returning it directly gives random access — the
parquet-footer seek in the spec — without layering fsspec's own block-cache on top,
which would double-cache: our CAS *is* the cache. Any non-`rb` mode raises
`PermissionError` (read-only).

*Alternative considered:* wrap in `AbstractBufferedFile` for fsspec read-ahead.
Rejected — redundant with the CAS and it would fetch bytes the consumer never reads.

### D3: `info`/`ls` return fsspec-shaped dicts
`info(path)` → `{"name": path, "size": entry.size, "type": "file"}` for a file, or
`{"name": path, "size": 0, "type": "directory"}` for a synthetic directory; missing
paths raise `FileNotFoundError`. `ls(path, detail=True)` returns the `info` dicts of
immediate children only; `detail=False` returns their names. The root is addressed
as `""` (post-strip), and `find`/`glob` come for free from `AbstractFileSystem`'s
default walk over `ls`.

### D4: `checkout` lives on `Repository`, reusing the `fetch_all` fan-out
`fetch_all(snap)` already lays the tree out to a private temp dir via a
`ThreadPoolExecutor`. Factor the layout into a helper that takes an explicit
destination, and have `checkout(snap, dest)` call it with the caller's dir.
Containment: because publish normalizes paths (no `..`, no absolute), an entry
cannot escape; as defense in depth, `checkout` resolves each target and asserts it
is under `dest.resolve()` before writing, raising `PathError` otherwise. `fetch_all`
becomes `checkout` into a `TemporaryDirectory`.

### D5: Interop test is optional-dependency-guarded
The pass-`fs`-to-consumer test uses `pyarrow` (`pytest.importorskip("pyarrow")`) so
the default test run stays dependency-light; the core listing/open/checkout tests
use only in-tree code and fsspec's memory backend.

## Risks / Trade-offs

- **Returning a raw `BytesIO` instead of an `AbstractBufferedFile`** → some exotic
  fsspec consumers expect the buffered type. Mitigation: the handle satisfies the
  binary read/seek protocol those consumers actually use; if a real consumer needs
  the wrapper, we can opt into it behind a flag later. Object-form interop with
  pyarrow/pandas is verified by test.
- **Synthetic directories have no size/mtime** → reported as `size: 0`. Mitigation:
  matches how object stores present prefixes; consumers treat directories as
  containers, not byte sources.
- **`checkout` on a huge version writes every file** → by design (whole-tree). The
  concurrency limit is the facade's configured worker count; nothing new here.

## Open Questions

- None blocking. URL addressing's resolver design (how a bare `sartre://` string
  finds its `Repository`) is deferred to the follow-up change and tracked there.
