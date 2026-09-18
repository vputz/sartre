## Context

`publish(coord, files)` requires a source for every file (full replacement). Changing one file in a multi-file artifact means re-supplying the whole tree even though the unchanged blobs are already stored. A base version's `Snapshot.entries` already carry `(path, content_hash, size)` for every file, so a derived publish can reuse unchanged entries by hash and only take sources for changed paths. See the proposal.

## Goals / Non-Goals

**Goals:** derive a new version from a base changing only some paths; never need the bytes of unchanged files; identical lease/commit/CAS safety and provenance as `publish`.

**Non-Goals:** no in-place mutation (always a new immutable version); no delta/partial-blob encoding; no change to `publish`'s semantics; no new concurrency protocol.

## Decisions

- **A separate method `publish_over`, not a `base=` flag on `publish`.** Overloading `publish` would blur its full-replacement contract (the thing we deliberately keep crisp). `publish_over` is explicit: it *derives*.
- **`base: Ref = HEAD`.** Default to the current head — the common "amend the latest" case — while allowing an alias or pin. Resolved internally to a snapshot; the base is only read, never modified.
- **Reuse entries by content hash; hash only `set` sources.** Seed `{path: entry}` from the base snapshot, delete `remove` paths, then for each `set` source hash it (pass 1) into a fresh `Entry` and override/add. `manifest_version` over the merged entries gives the new version — so a no-op derive, or an override with identical bytes, yields the base's own version id (content-addressed identity). This is a clean Hypothesis property.
- **Lease over ALL hashes, upload only the changed ones.** The lease covers every hash in the new manifest (changed + reused), so a reused blob is a GC root from before the upload through commit and advance — exactly the protection `publish` gives its own blobs. Only `set` sources are streamed to the store (`known_hash` dedup skips even those if already present); reused entries are never uploaded or re-hashed. If a reused blob were somehow already gone, the base would not have resolved — and the lease-first ordering closes the window from then on, so no new TLA is needed (it is publish's verified ordering over a different entry set).
- **Factor the shared tail.** Extract the `publish` body after entry-building — `acquire lease → heartbeat → upload the given sources → self-check → commit → self-check → CAS advance → release` — into a helper taking `(coord, entries, uploads: Mapping[path→source], pointer, expected, metadata, actor, reason)`. `publish` calls it with every path as an upload; `publish_over` calls it with only the changed paths. No duplicated lease/commit/CAS logic.
- **`remove` of an absent path raises.** Explicit beats silent — it catches typos and stale assumptions about the base. (A lenient ignore was considered; rejected as a footgun.)
- **CLI `publish-over`** reuses the framework-free ops seam and mirrors `publish`'s flags, adding `--from` and repeatable `--rm`. `ops.publish_over` stays Typer-free.

## Risks / Trade-offs

- **Reused-blob-missing edge case** (base out of retention, concurrently GC'd) → mitigated by lease-first over all hashes and by the fact that `base` had to resolve; deriving from a non-retained base is inherently racy the same way publishing is. Acceptable; documented.
- **`set` paths vs base paths both go through `normalize_path`** → the merge and the case-collision check run on the final set, so an override matches the base entry only after normalization (consistent with `publish`).

## Migration Plan

Purely additive — a new method and CLI command. No data, schema, or existing-API change. Rollback is dropping the method/command.

## Open Questions

- **CLI shape** — resolved to a dedicated `publish-over` command (not a `--from` flag on `publish`), to keep `publish` unambiguous. Revisit only if users want the one-command form.
