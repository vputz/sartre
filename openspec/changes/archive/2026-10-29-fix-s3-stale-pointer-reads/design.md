## Context

`S3Registry` realizes a pointer as an append-only stream of `NNNN.json` events; the current value is the newest key under `coords/<name>/<env>/pointers/<p>/`. Finding that key is an s3fs `ls` (a listing). `_list_names(prefix, refresh=False)` serves it from `dircache` when present. The write/CAS paths (`set_pointer`, `delete_pointer`) call `_pointer_scan(..., refresh=True)`, which invalidates the prefix before listing, so a writer always CASes against the true tail. The read paths — `_resolve_ref` (Head/Alias) at s3.py:167, `list_pointers` at :194, and `_all_events` (behind `list_versions`/`list_log`/`list_pointer_history`) at :376–378 — all use the default `refresh=False`.

`fsspec.core.url_to_fs` memoizes filesystem instances by their constructor args, so every `open_s3` in one process gets the same `S3FileSystem`, and that instance's dircache has `listings_expiry_time=None` (never expires). A long-lived process therefore freezes the pointer listing at first read and never sees later events. Writes were never affected (they refresh); only reads.

## Goals / Non-Goals

**Goals:** a long-lived S3 reader always resolves the current pointer tail, the current set of published versions, and the current tombstones, regardless of what it cached earlier — matching the freshness the write path already guarantees, and the freshness every other backend has for free.

**Non-Goals:** no change to the write/CAS protocol or its ordering; no change to memory/sqlite/postgres; no re-caching of immutable content (manifests, blobs stay cached); no new concurrency protocol, so no new TLA.

**TLA note:** `model/S3Drop.tla` and `model/PointerDrop.tla` both reason over the *authoritative* tail (the live object listing) — they have no notion of a per-client listing cache, so they implicitly assume every read sees the current tail. This bug was a layer below the models: the implementation served reads from a stale client cache that the models never described. The fix makes reads observe the live tail, which is precisely the assumption the models already encode, so no model changes are needed (confirmed by re-reading both: neither mentions caching, and their `Safe` invariants are stated against the current listing).

## Decisions

- **Refresh the pointer/event/tombstone listings on read paths, surgically.** Make `_resolve_ref`, `list_pointers`, `list_versions`, and `_all_events` pass `refresh=True` down to `_list_names` for the mutable prefixes (pointer streams, the `pointers/` directory, the `tombstones/` namespace). This mirrors the write path (`invalidate_cache(prefix)` then `ls`) rather than disabling `use_listings_cache` for the whole filesystem — the latter is broader (it would also drop blob-listing caching used by GC) and reaches outside the registry's concern. One extra `LIST` per resolve.
- **Tombstone freshness on the resolve path.** `_pointer_scan` skips tombstoned tail targets via `_is_tombstoned` (a per-key `exists`), and `list_versions` filters via `_tombstoned_versions` (a `tombstones/` listing). Both SHALL see current state on a read: invalidate the tombstone path/namespace so a long-lived reader cannot resolve a pointer to a manifest reclaimed after its view froze. This keeps the S3Drop invariant (a reader never resolves to a reclaimed manifest) holding for long-lived processes, not only freshly-opened ones.
- **Keep immutable reads cached.** Manifest and blob objects are content-addressed and never change, so `cat_file(manifest_path)` and blob fetches keep whatever caching s3fs gives them — the fix is scoped to the mutable listings.
- **A single internal switch, defaulting to fresh.** Thread the refresh through the existing `refresh` parameter already on `_pointer_scan`/`_list_names` rather than adding new surface. The public API (`head`/`resolve`/`list_*`) is unchanged.
- **`head` pays one `LIST` per call.** Its contract is a "cheap pointer read" (bounded LIST + GET, no manifest scan); a poll loop now issues a LIST each tick. Correctness wins over shaving that LIST. **Open for review:** whether to offer a documented, opt-in cache-tolerant fast path for hot polling later — not in this change unless Vic wants it now.

## Risks / Trade-offs

- **One `LIST` per resolve/head — measured.** The op profile goes from `0 LIST + 1 GET` (warm cache hit) to `1 LIST + 1 GET`; the GET was always there. Cost of that one added `ListObjectsV2`: **~$0.005 / 1,000 requests**; **~3.3 ms** mechanical overhead (moto/localhost, no WAN); **~40 ms median** measured from an out-of-region client (dev laptop → S3 over the internet) against the real `qs-registries/backtest_seeds` bucket, where it takes a resolve from one WAN round trip to two (the ~130 ms warm baseline is the pre-existing GET + tombstone check, unchanged). In-region (EC2 → S3, the production reader) a small LIST is typically ~5–20 ms. Acceptable: a facade `resolve` already does a `cat_file`, this is one more same-kind round trip paid once per resolve, and the customer impact (silent stale versions) is far worse. The only regime where it bites is a tight `head` poll loop (~$13/mo + tens of ms/poll at 1 Hz) — the deferred, opt-in cache-tolerant fast path is for that, and is out of scope here.
- **Tombstone refresh cost.** `list_versions` gains a fresh `tombstones/` listing; this runs in GC and Pin resolution, both already listing-heavy, so the marginal cost is small.
- **No behavior change for existing single-process/short-lived callers** — they were re-reading fresh anyway (a freshly opened fs has an empty dircache); the fix only changes the long-lived, cache-warmed case.

## Migration Plan

Pure bug fix, internal only: no API, schema, or data change; no migration. A patch release. Rollback is reverting the read-path `refresh=True`.
