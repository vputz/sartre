## Context

Sartre records **what** changed (content-addressed versions, a per-coordinate commit log) but not **who** or **why**. Two shortcomings motivate this change:

1. A commit's only free text is `metadata["message"]`, an ad-hoc convention wired in the CLI. Because `metadata` rides on the content-addressed manifest and is **excluded from version identity** (`ports.py`: commit is content-idempotent regardless of metadata/coordinate/order), it is first-writer-wins and shared across every coordinate that shares the version. It cannot honestly attribute a *specific act*.
2. Pointer moves — the thing a consumer usually means by "prod changed" — leave **no trace**. `set_pointer` mutates a single row; `list_pointers` shows only the current value.

The load-bearing insight: a `Version` is shared across promotions, so provenance belongs to the **event** (a commit into a coordinate, or a pointer move), never to the manifest.

## Goals / Non-Goals

**Goals:**
- Attribute every commit and every pointer move with an `actor` (who) and `reason` (why).
- Retain pointer moves as an append-only, ordered, per-coordinate history that is readable.
- Keep provenance off the content-addressed manifest so identical content committed by different people into different coordinates keeps distinct provenance.
- Required attribution at the CLI edge; lenient (defaulted) at the library edge.

**Non-Goals:**
- Proving or authenticating identity. `actor` is a caller-supplied label; we trust the edge.
- Signing, audit tamper-evidence, or cryptographic provenance.
- A new concurrency protocol. No TLA model — see Decisions.
- Backfilling provenance for existing rows (greenfield; no released data).

## Decisions

### Provenance lives on events, not on the manifest
Commit provenance goes on the **commit-log row** (`LogEntry` gains `actor`/`reason`); pointer-move provenance goes in a new **append-only `pointer_moves` history**. Both are per-coordinate and ordered. The manifest and its `metadata` are untouched by provenance.

*Alternative rejected — store on `metadata`:* it is version-excluded and shared, so it is first-writer-wins across promotions; it physically cannot record the who/why of a promotion (same manifest, different act).

### `reason` unifies with `-m/--message`; `metadata` is domain-only
There is exactly one free-text-why per event: `reason`. The CLI's `-m/--message` now maps to `reason`; the `metadata["message"]` convention is removed. `--meta k=v` remains for domain payload (date ranges, stage). This avoids two overlapping "message" fields.

*Alternative rejected — keep both a typed `reason` and a `message` metadata key:* the user flagged the overlap; two fields inviting the same content is a footgun.

### Two events, one shape
Both events carry the same `(actor, reason)` pair. Commit attribution answers "who created this version here." Pointer-move history answers "who moved this pointer, from what, to what." Landing both together is the whole ask; either alone answers only half of "the artifact changed."

### Actor sourcing: required at the CLI, lenient at the library
- **Library** (`Repository.publish`/`point`, `Registry.commit`/`set_pointer`): `actor` is optional and defaults to the sentinel `"unknown"` — never rejected. This keeps the programmatic surface ergonomic and the port total.
- **CLI**: `--author/--as` is required, resolved `flag › $SARTRE_AUTHOR › profile.author › getpass.getuser()`; unresolvable → clean non-zero error before any change. The resolution ladder mirrors the existing addressing ladder in `cli/config.py`.

### No TLA / no new concurrency protocol — deliberate
Provenance is an **additive side-record** written in the *same transaction* as the operation it describes: the commit-log INSERT already exists; `pointer_moves` gets one INSERT inside the same transaction as the `set_pointer` compare-and-swap. A rejected CAS commits nothing, so no orphan history row. The existing CAS already serializes concurrent moves; recording who won adds no new interleaving. We have used TLA where a *protocol* had a race (leases, GC); here there is none, so a model would verify nothing new. Stated explicitly so the absence is a decision, not an oversight.

### Storage shape (`_sql.py` + `memory.py`)
- Commit-log table: add `actor TEXT NOT NULL DEFAULT 'unknown'`, `reason TEXT NULL`.
- New `pointer_moves` table: `(id/seq, name, env, pointer, from_version NULL, to_version, actor, reason NULL, at)` with `at` filled via the existing `_NOW_SQL`/`_TS_TYPE` dialect hooks (same pattern as leases). Append-only; ordered by an autoincrement/seq so reads are stable.
- Memory backend mirrors both as in-process lists so it stays a faithful differential oracle.

### API surface
- `ports.py`: `LogEntry(+actor,+reason)`; new `PointerMove` dataclass; `set_pointer(..., *, actor="unknown", reason=None)`, `list_pointer_history(coord)`. **`commit` is unchanged** — discovered during apply that in both backends the commit-log row is appended by `set_pointer`, not `commit`; `commit` only records the shared, content-addressed manifest, so it carries no provenance. This *strengthens* the "provenance on events, not the manifest" thesis: `set_pointer` (the tip event) is the sole provenance write point, stamping both the log row and the pointer-move history. A manifest committed but never made a tip has no attribution — acceptable, since the public `publish` always pairs commit+set_pointer.
- `repository.py`: `publish(..., actor="unknown", reason=None)`, `point(..., actor="unknown", reason=None)`, `list_pointer_history` delegator.
- `cli`: `--author/--as` + `-m→reason` on `publish`/`point`; new `history` command; `show`/`log` print actor/reason; author resolution in `cli/config.py`. `ops.py` stays Typer-free.

## Risks / Trade-offs

- **`set_pointer` signature grows required-ish params** → give `actor`/`reason` keyword-only with `actor="unknown"`/`reason=None` defaults so existing internal callers (e.g. publish's pointer advance) and tests keep working; publish threads its own actor/reason through.
- **Two "message"-like fields historically** → removing `metadata["message"]` is BREAKING, but the only consumer is our own CLI (pre-1.0, no released clients). Update CLI tests that assert `stage=` metadata to stop expecting `message`.
- **Differential drift between memory and SQL** → cover provenance in the existing differential/property tests so the two backends are checked to agree on `list_log`/`list_pointer_history`.
- **`getpass.getuser()` can raise** in exotic environments (no passwd entry) → treat a raised lookup as "unresolved" and fall through to the clean CLI error, don't crash.
- **History unbounded growth** → pointer moves are tiny rows and far rarer than blob writes; GC of history is out of scope for now (note it as a future retention knob).

## Migration Plan

Greenfield, no live data. Additive schema — new columns default `'unknown'`/NULL, new table created on registry init. Rollback is dropping the table/columns; no data reshaping. The one behavioral break (`-m` no longer in `metadata`) is contained to the CLI and its tests within this change.

## Resolved Questions

- **History ordering in `sartre history`** — RESOLVED: the human table renders **newest-first** ("what just happened to prod?"); `--json` emits the natural **oldest→newest** sequence so machines get stable chronological order.
- **Profile key name** — RESOLVED: a flat `author = "…"` under `[profiles.<name>]` in `config.toml`. It is honest attribution (mirrors git's unverified `user.name`), and a future real-identity block (e.g. `[profiles.<name>.identity]`) coexists without a key collision.
