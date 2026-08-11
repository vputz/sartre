## Why

In a multi-user repository, when an artifact changes people need to know **who** did it and **why**. Today neither is recorded: a commit's only free-text lives in `metadata["message"]` (ad hoc, and — because it rides on the content-addressed manifest — shared across promotions and first-writer-wins), and a pointer move (head advance, alias promote, rollback) leaves **no history at all**. We want honest, per-change attribution without taking on identity *proof*.

## What Changes

- Model every mutating change as an **event** carrying two typed provenance fields — `actor` (attribution string; no identity proof) and `reason` (free text). Provenance attaches to events, never to the immutable manifest, because version identity excludes metadata and the same manifest is shared across acts.
- **Commit attribution**: the per-coordinate commit-log row gains `actor` + `reason`. Promotions of identical content each keep their own event provenance.
- **Pointer-move history**: a new append-only log records every pointer move as `(coordinate, pointer, from_version|none, to_version, actor, reason, at)`, with a read surface to enumerate it. This is what a consumer usually means by "prod changed."
- **`-m/--message` now means `reason`.** The ad-hoc `metadata["message"]` convention is removed; `metadata` stays free-form for *domain* payload only (date ranges, stage, …). **BREAKING** (pre-1.0, no released consumers): `-m` no longer lands in `metadata`.
- **Actor sourcing**: required at the CLI, resolved `--author/--as` › `$SARTRE_AUTHOR` › profile `author` › `getpass.getuser()`, with a clean error if unresolvable. Library callers pass `actor` explicitly; when omitted it is recorded as the sentinel `"unknown"` (optional, never rejected).
- **No concurrency protocol added**: attribution is an additive side-record written in the *same* transaction as the commit / pointer compare-and-swap. The existing `set_pointer` CAS already governs the race, so no TLA model is warranted — a deliberate, stated scope call.

## Capabilities

### New Capabilities
- `change-provenance`: the cross-cutting contract that every commit and every pointer move records an `actor` and a `reason`, that provenance is a property of events (not of the content-addressed manifest), and that pointer moves are retained as an append-only, ordered history.

### Modified Capabilities
- `registry-port`: `commit` and `set_pointer` gain `actor`/`reason`; add a `list_pointer_history(coord)` read and a `PointerMove` record; `LogEntry` gains `actor`/`reason`.
- `version-log`: commit-log entries carry `actor`/`reason` alongside `version`/`seq`/`created_at`.
- `persistent-registry`: commit-log table gains `actor`/`reason` columns; a new `pointer_moves` table records the move history (via the existing timestamp/NOW dialect hooks).
- `in-memory-backend`: the in-memory registry mirrors commit provenance and the pointer-move history for parity.
- `repository-facade`: `publish` and `point` gain `actor`/`reason` (actor defaulting to `"unknown"` when omitted); add a `list_pointer_history` delegator.
- `cli`: `publish`/`point` gain `--author/--as` (required, with the resolution ladder); `-m/--message` maps to `reason`; a new `history` command shows the pointer-move log (human + `--json`); `show`/`log` surface `actor`/`reason`.

## Impact

- **Code**: `src/sartre/ports.py`, `repository.py`, `_sql.py`, `memory.py`, `cli/{app,ops,config}.py`.
- **APIs**: `Registry.commit` / `set_pointer` signatures grow `actor`/`reason`; `Repository.publish` / `point` grow `actor`/`reason`; new `list_pointer_history` on both. Backward-lenient at the library edge (defaults), required at the CLI edge.
- **Storage**: additive schema — two columns on the log table plus a new `pointer_moves` table. Greenfield, so no migration of live data.
- **Behavior change**: `-m/--message` no longer populates `metadata`; downstream readers of `metadata["message"]` (only the current CLI) move to `reason`.
