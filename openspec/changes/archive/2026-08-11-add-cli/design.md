## Context

The library API is complete and clean: `open_local(path)` / `open_cloud(dsn, blob_url,
*, cache_dir, storage_options)` build a `Repository`, which exposes
`head`/`resolve`/`open`/`checkout`/`snapshot_fs`/`publish`/`gc`; the registry underneath
carries `list_coordinates`/`list_log`/`list_pointers`/`set_pointer`. The domain nouns are
`Coordinate(name, env)`, `Ref = Head | Alias(name) | Pin(version)`, and a `Version`
content id. A CLI is a thin adapter over this — the design work is choosing surface
syntax, addressing, and output, not new mechanism. All choices below were settled in an
explore session.

## Goals / Non-Goals

**Goals:**
- A `sartre` CLI that covers the full lifecycle (publish, inspect, fetch, promote, gc)
  with no Python.
- A reference syntax that is learnable (OCI/git muscle memory) and teaches the
  mutable/immutable distinction.
- Zero-config local use; profile-based cloud use; scriptable output.
- Keep command handlers thin over a reusable operations seam so a future Textual TUI
  drives the same logic.

**Non-Goals:**
- The TUI itself; FUSE mounting; stdin publish; incremental (`add`/`rm`) publish;
  immutable/protected aliases; pointer deletion; a config-management subcommand;
  shell-completion; secrets management beyond env + `storage_options`.

## Decisions

### D1. Reference grammar: OCI-lineage `name/env[:alias | @version]`
A reference token parses by:
```
   has '@'  → Pin      (everything after '@' is the Version; its internal ':' is fine)
   has ':'  → Alias    (name after ':')
   neither  → Head
```
so `resnet/prod` = head, `resnet/prod:stable` = the `stable` alias, `resnet/prod@sha256:…`
= an exact pin. A **default env** (`--env` / `SARTRE_ENV` / profile) lets a bare `resnet`
mean `resnet/<default>`. Committing to `:` = mutable and `@` = immutable makes
reproducibility visible in the syntax. A `format(coord, ref)` inverse exists for output,
and `parse(format(x)) == x` is a property test.
*Alternative rejected:* Docker's `env/name:tag` order — sartre's primary axis is the name,
env is the qualifier, so `name/env` reads better and the default-env elision is natural.

### D2. Addressing ladder (flags › env › profile › cwd › default)
```
   --repo <path> | --registry <dsn> --blobs <url>          (explicit, one-off)
     ↓ else   SARTRE_REPO | SARTRE_REGISTRY_DSN + SARTRE_BLOB_URL   (12-factor / CI)
     ↓ else   --profile NAME / SARTRE_PROFILE → ~/.config/sartre/config.toml (XDG)
     ↓ else   cwd auto-detect: walk up for a local repo marker (registry.db)   (git-like)
     ↓ else   the "default" profile
```
The resolver produces a small `RepoTarget` (either a local path, or a registry DSN + blob
URL + cache_dir + storage_options), and the **backend is inferred**: a path → `open_local`;
a registry + blobs → `open_cloud`. No explicit `backend=` field. Cloud credentials ride
s3fs's own `AWS_*`/`~/.aws`; a profile MAY carry `storage_options` and `cache_dir`, passed
straight through to `open_cloud`. This resolver is the one piece of real logic in the CLI
and is unit-tested independently of Typer.
*Alternative rejected:* a single `sartre+scheme://` URL — the cloud case has two endpoints
(registry + blobs); cramming both into one URL is worse than two fields.

### D3. Commands are thin handlers over an operations seam
Each command is a small Typer function that (1) resolves a `Repository` via D2, (2) parses
references via D1, (3) calls one or two `Repository` methods, (4) renders. The
render/`--json` and the Repository calls live in a plain module (an "operations" seam) the
Typer layer is a shell around, so a future Textual TUI reuses step 3–4 without the CLI
plumbing. Surface:
- `show <ref>` → `resolve` → metadata + entry table; `head <ref>` → bare version id
  (porcelain, composes in `$(…)`); `ls <ref> [-l]` → entries from the snapshot with **zero
  blob fetch**; `cat <ref> <path>` → **verified** whole-blob stream to stdout (`get_to`,
  not the unverified `open`); `log <coord>` → `version │ when │ pointer`; `coords` →
  enumerate; `checkout <ref> <dir>`.
- `publish <coord> <src…>` (D4a); `point <coord[:ptr]> <ref>` (D4b).
- `gc [--keep-last N --keep-within 30d --grace 1h]`.
`--json` on every read command; `head`/`ls` stay bare for scripting.

### D4a. `publish` input mapping
Directory-walk is primary: `publish resnet/prod ./ckpt/` maps logical paths to paths
relative to the dir (the inverse of `checkout`). Explicit files map to their basename; a
`logical=source` token overrides (`w.bin=./out/model.st`). **No stdin** in v1 — streaming
publish requires re-readable sources (`bytes`/`Path`), and stdin is read-once; the CLI does
not silently spill it. Options: `-p/--pointer` (default head), `--point ALIAS` (also
advance an alias), `-m/--message` + `--meta k=v` (metadata). Publish is
**full-replacement** — the given tree becomes the new version; the help text says so
plainly to head off `git add` expectations.

### D4b. `point` — compare-and-swap pointer move
`point` edits the pointer plane only (no blobs, no manifest): move any mutable pointer
(head or an alias) to an existing committed version. It generalizes to promotion
(`point m/prod:stable @v2`), alias-to-alias, and **rollback** (`point m/prod @v1` moves
head). **CAS-safe by default:** read the current pointer, then `set_pointer(expected=
current)` (`expected=None` when the alias is absent → create); a concurrent move surfaces
the registry `Conflict` as "pointer moved — re-run or `--force`". `--force` skips the CAS
(last-writer-wins). This is the CLI payoff for the whole multi-writer CAS/lease effort. It
requires the new `Repository.point` (D5).
*Name:* `point` — honest about "these are mutable pointers." May alias to `tag`/`promote`
later if users ask; the semantics, not the spelling, are what matter.

### D5. Library additions on `Repository`
- `point(coord, name, version, *, expected)` — centralizes the read-current-then-CAS so
  the CLI/TUI never reach into `repo.registry`; raises `NotFound` if the version is not
  committed, `Conflict` on a stale `expected`.
- `list_coordinates()` / `list_log(coord)` / `list_pointers(coord)` — thin delegators to
  the registry for a clean facade (`coords`/`log` need them).
No new port methods — these compose existing registry calls.

### D6. Packaging: Typer behind a `cli` extra
`typer` is an optional dependency (`sartre[cli]`), imported lazily at the entry point with
a clear `RuntimeError` if missing — the exact pattern `PostgresRegistry` uses for psycopg.
`[project.scripts] sartre = "sartre.cli:main"` gives the console command. `import sartre`
stays Typer-free.

## Risks / Trade-offs

- **Reference ambiguity if names contain `/`, `:`, `@`.** → Restrict coordinate `name`/`env`
  and alias names to a safe charset (alnum + `-`/`_`/`.`); versions only appear after `@`.
  The parser is total and property-tested.
- **cwd auto-detect surprising a user in the wrong directory.** → It is the lowest rung
  above the default profile and only fires for a *local* marker; explicit `--repo`/env/
  profile always win, and `--json`/errors name the resolved target.
- **`point --force` throws away CAS safety.** → Off by default; the safe path is the
  default and `--force` is a deliberate, documented opt-out.
- **Full-replacement publish surprises `git`-minded users.** → Explicit in `--help` and the
  spec; incremental publish is a named non-goal for a later change.
- **Typer/Click version drift.** → Pin a floor in the `cli` extra; the handler seam keeps
  Typer at the edges so a framework change is contained.

### D7. Small resolved choices
- **Durations** (`--keep-within`, `--grace`): a tiny **internal, strict** parser —
  `<int><unit>` with `s/m/h/d/w`, concatenation allowed (`7d12h`), everything else a clear
  error. No dependency; property-tested by round-trip.
- **`cat` output**: v1 materializes via `get_to` to a temp file, then copies temp →
  stdout — correct, verified, bounded memory. A truly-streaming verified read
  (`Store.open_verified`) is a possible later `blob-store` addition; the CLI does not grow
  the store API. `cat` on a very large blob using scratch space is an accepted v1 edge.
- **`--meta key=value`**: values are stored as **strings** in v1 (predictable; metadata is
  descriptive). Typed values (JSON coercion) are a possible later `--meta-json`; not now.

## Open Questions

- None blocking. (Durations, `cat` output, and metadata typing resolved in D7.)
