## Why

`S3Registry` read paths resolve a pointer from an s3fs listing that is cached **forever**, so a long-lived reader keeps returning a stale head indefinitely. `fsspec.core.url_to_fs` hands every `open_s3` in a process the same `S3FileSystem`, whose `dircache` has `use_listings_cache=True` and `listings_expiry_time=None`; once `coords/<name>/<env>/pointers/head/` has been listed, newer `NNNN.json` events are invisible. `_resolve_ref` → `_pointer_scan(coord, name)` calls `_list_names(prefix, refresh=False)`, picks the newest key in the **stale** list, reads it live, and returns a real-but-old version — no error. The write/CAS path already passes `refresh=True` (the module docstring says a stale listing "can never cause a lost write"); reads never got the same treatment.

A customer hit this in production: a long-lived backtest server calls `resolve(coord, Head())` on each refresh and loaded versions from days earlier; each stale value was the head current when that process first resolved. Single-process/dev always matched. Content-addressed manifests and blobs are immutable so caching them is correct — pointer event streams and tombstones are the only mutable listings, and reads must see them live.

## What Changes

- **Pointer resolution reads bypass the listing cache.** `_resolve_ref` (Head/Alias), `list_pointers`, and the pointer-stream scans behind `list_versions`/`list_log`/`list_pointer_history` SHALL re-list the pointer prefix so a long-lived reader observes the current tail — the same freshness the write path already has. Cost: one S3 `LIST` per resolve.
- **Tombstone visibility on reads.** Reads that skip tombstoned versions (`_pointer_scan`, `list_versions`) SHALL see current tombstones, so a long-lived reader cannot resolve a pointer to a manifest that was reclaimed after its view froze (upholding the S3Drop invariant for long-lived processes, not just fresh ones).
- **Pin resolution** (`_resolve_ref` for `Pin`, via `list_versions`) refreshes too, so a just-published version resolves by id in a long-lived process instead of reading `NotFound`.
- **Regression test:** two `Repository` handles over one moto bucket in one process (same fsspec instance) — resolve head with A, publish with B, resolve head with A again; A must see the new version.

Immutable reads (manifest/blob fetches by content hash) are unchanged — they stay cached.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `s3-registry`: pointer/tombstone *reads* resolve against the live listing, not a frozen cache, so a long-lived reader always sees the current tail; immutable content reads stay cached.

## Impact

- **Code:** `src/sartre/s3.py` read paths (`_resolve_ref`, `_pointer_scan`, `list_pointers`, `list_versions`, `_all_events`, tombstone checks). No API or signature change.
- **Performance:** one extra `LIST` per pointer resolve on S3 (resolve already does a `cat_file`, so a facade `resolve` is still one round trip's worth of extra latency; the `head` poll primitive gains a `LIST`). An opt-out for hot polling loops is an open design question below.
- **Backends:** S3 only — memory/sqlite/postgres read the authoritative value directly and were never affected.
- **Severity:** silent wrong-version resolution in any multi-request, long-lived S3 reader; no data loss (writes were always safe), but reads served stale manifests.

## Open questions (for design/review)

- Does the `head` poll primitive need a cache-tolerant fast path, or is one `LIST` per call acceptable? (Lean: accept it; correctness first. A documented opt-out could come later.)
- Refresh `_tombstoned_versions` on every read, or only where a read resolves a pointer? (Lean: refresh on the resolve path; it is where a stale tombstone could violate S3Drop.)
- Prefer per-call `invalidate_cache(prefix)` (surgical) over disabling `use_listings_cache` for the fs (broad, also affects blob listings)? (Lean: per-call, matching the write path.)
