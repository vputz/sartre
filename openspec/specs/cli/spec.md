# cli Specification

## Purpose
TBD - created by archiving change add-cli. Update Purpose after archive.

## Requirements

### Requirement: Command-line interface over the library
The system SHALL provide a `sartre` command-line interface exposing the repository
lifecycle without writing Python: read commands `show`, `head`, `ls`, `cat`, `log`,
`coords`, `checkout`; write commands `publish` and `point`; and maintenance command `gc`.
Each command SHALL resolve a `Repository` (see the addressing requirement), operate through
the `Repository`/registry API, and exit non-zero with a typed, human-readable message when
an operation raises (`NotFound`, `Conflict`, `IntegrityError`, `PathError`). Command
handlers SHALL be thin over a reusable operations layer so the same logic can back a future
interactive UI.

#### Scenario: Publish then read back
- **WHEN** a user runs `sartre publish <coord> <dir>` and then `sartre show <coord>`
- **THEN** the shown version equals the published version and its entries match the tree

#### Scenario: Failure exits non-zero with a message
- **WHEN** a command targets a coordinate or ref that does not resolve
- **THEN** the CLI prints a clear error and exits with a non-zero status

### Requirement: OCI-style reference grammar
The CLI SHALL parse a reference token as `name/env` optionally followed by `:alias` (a
mutable pointer) or `@version` (an immutable pin), where a bare `name/env` denotes head.
Parsing SHALL resolve to the domain refs: `@` → `Pin(version)` (the version is the full
`algo:digest` key and its internal `:` is not a delimiter), else `:` → `Alias(name)`, else
`Head()`. A default env (from `--env`, `SARTRE_ENV`, or the active profile) SHALL let a bare
`name` mean `name/<default-env>`. The CLI SHALL provide the inverse rendering, and
`parse(render(x))` SHALL equal `x` for every coordinate/ref.

#### Scenario: Bare coordinate resolves to head
- **WHEN** `resnet/prod` is parsed
- **THEN** it yields coordinate `(resnet, prod)` with ref `Head()`

#### Scenario: Alias and pin are distinguished by punctuation
- **WHEN** `resnet/prod:stable` and `resnet/prod@sha256:abcd…` are parsed
- **THEN** the first yields `Alias("stable")` and the second yields `Pin("sha256:abcd…")`

#### Scenario: Default env fills a bare name
- **WHEN** a default env `prod` is configured and `resnet` is parsed
- **THEN** it yields coordinate `(resnet, prod)`

#### Scenario: Round-trips
- **WHEN** any coordinate+ref is rendered to a token and parsed back
- **THEN** the parsed coordinate and ref equal the originals

### Requirement: Repository addressing and configuration
The CLI SHALL resolve which repository to act on by a fixed precedence, highest first:
explicit flags (`--repo <path>`, or `--registry <dsn>` + `--blobs <url>`); then environment
(`SARTRE_REPO`, or `SARTRE_REGISTRY_DSN` + `SARTRE_BLOB_URL`); then a named profile
(`--profile` / `SARTRE_PROFILE`) read from a config file under the XDG config directory
(`~/.config/sartre/config.toml`); then auto-detection of a local repository by walking up
from the current directory; then the `default` profile. The backend SHALL be inferred — a
path resolves via `open_local`, a registry DSN plus blob URL via `open_cloud` — with no
explicit backend selector. A profile MAY carry `cache_dir` and `storage_options`, passed
through to `open_cloud`.

#### Scenario: Flags win over environment and profile
- **WHEN** `--repo` is given alongside `SARTRE_REPO` and a `default` profile
- **THEN** the `--repo` path is used

#### Scenario: Backend inferred from the resolved target
- **WHEN** the resolved target is a filesystem path
- **THEN** the repository is opened via `open_local`; and when it is a registry DSN plus a
  blob URL, via `open_cloud`

#### Scenario: Local repository auto-detected from the working directory
- **WHEN** no flags, env, or profile are set and the current directory is inside a local
  repository
- **THEN** the CLI operates on that repository

### Requirement: Publish input mapping
`sartre publish <coord> <src…>` SHALL map sources to logical paths: a single directory
argument maps every file under it to its path relative to that directory; explicit file
arguments map to their basename; and a `logical=source` token overrides the logical path
for a source. Read-once stdin is not accepted as a source. Publish SHALL be
full-replacement — the resulting version is exactly the given tree. Options SHALL include
the target pointer (`-p/--pointer`, default head), an optional alias to also advance
(`--point`), an attributing author (`--author/--as`, required — resolved per the author
resolution ladder), a change reason (`-m/--message`, mapped to the commit `reason`), and
domain metadata (`--meta key=value`). The reason SHALL NOT be written into `metadata`.

#### Scenario: Directory maps relative logical paths
- **WHEN** `sartre publish m/prod ./ckpt --as alice` is run and `./ckpt/w/model.bin` exists
- **THEN** the published version contains an entry at logical path `w/model.bin`

#### Scenario: Explicit remap
- **WHEN** `sartre publish m/prod w.bin=./out/model.st --as alice` is run
- **THEN** the published version contains an entry at logical path `w.bin` with the bytes of
  `./out/model.st`

#### Scenario: Message becomes the commit reason
- **WHEN** `sartre publish m/prod ./ckpt --as alice -m "retrain on Q3"` is run
- **THEN** the commit-log entry records `actor="alice"` and `reason="retrain on Q3"`, and the manifest `metadata` has no `message` key

### Requirement: Pointer move on the command line
`sartre point <coord[:pointer]> <ref>` SHALL move a mutable pointer (head when no `:pointer`
is given, otherwise the named alias) to the version that `<ref>` resolves to, changing only
the pointer plane. It SHALL accept an attributing author (`--author/--as`, required —
resolved per the author resolution ladder) and a change reason (`-m/--message`, mapped to
the move `reason`). It SHALL be compare-and-swap safe by default: it reads the current
pointer and advances only if unchanged, reporting a typed conflict (with guidance to re-run
or pass `--force`) when a concurrent move is detected; `--force` SHALL move unconditionally.
The target version MUST already be committed. A successful move SHALL be recorded in the
pointer-move history with the resolved author and reason.

#### Scenario: Promote an existing version to an alias
- **WHEN** `sartre point m/prod:stable @sha256:v2 --as bob -m "passed eval"` is run and `v2` is committed
- **THEN** the `stable` pointer of `m/prod` resolves to `v2` with no blob upload, and a history record is written with `actor="bob"` and `reason="passed eval"`

#### Scenario: Rollback moves head
- **WHEN** `sartre point m/prod @sha256:v1 --as bob` is run
- **THEN** head of `m/prod` resolves to `v1`

#### Scenario: Concurrent move is refused without force
- **WHEN** the pointer changed since it was read and `--force` is not given
- **THEN** the command reports a conflict and does not move the pointer

### Requirement: Output formats
Every read command SHALL render a human-readable form by default and a machine-readable
form under `--json`. `head` SHALL print only the bare version id and `ls` SHALL print only
logical paths by default (a `-l` long form adds hash and size), so both compose in shell
scripts. `log` SHALL render the commit history as version, time, and pointer.

#### Scenario: JSON output on demand
- **WHEN** a read command is given `--json`
- **THEN** it emits valid JSON conveying the same information as the human form

#### Scenario: Porcelain head
- **WHEN** `sartre head <ref>` succeeds
- **THEN** its entire stdout is the version id with no decoration

### Requirement: Packaging as an optional extra
The CLI SHALL be installable via a `cli` optional extra (`sartre[cli]`) and exposed as a
`sartre` console-script entry point. Its command framework SHALL be imported lazily so that
importing the `sartre` library without the extra does not require it; invoking the CLI
without the extra installed SHALL fail with a clear message naming the extra to install.

#### Scenario: Library imports without the CLI extra
- **WHEN** `sartre` is imported in an environment without the `cli` extra
- **THEN** the import succeeds

#### Scenario: Clear error when the extra is missing
- **WHEN** the `sartre` console script is invoked without the `cli` extra installed
- **THEN** it exits with a message instructing the user to install `sartre[cli]`

### Requirement: Author resolution at the CLI edge
The CLI SHALL require an author for every mutating command and SHALL resolve it in precedence order: the `--author/--as` flag, then the `SARTRE_AUTHOR` environment variable, then the active config profile's `author`, then the operating-system user (`getpass.getuser()`). If none resolves, the command SHALL fail with a clear error naming how to supply an author, and SHALL NOT perform the change.

#### Scenario: Flag beats environment and profile
- **WHEN** `--as alice` is given while `SARTRE_AUTHOR=bob` is set
- **THEN** the change is attributed to `alice`

#### Scenario: Falls back to the OS user
- **WHEN** no flag, environment variable, or profile author is present
- **THEN** the author resolves to the operating-system user

#### Scenario: Unresolvable author fails cleanly
- **WHEN** author resolution yields nothing and the change is mutating
- **THEN** the command exits non-zero with a message on how to supply `--author`, and no change is made

### Requirement: Pointer-move history command
The CLI SHALL provide `sartre history <coord>` printing the coordinate's pointer-move history — pointer, from → to version, actor, reason, and time — newest or oldest first in a stable order, in a human table by default and as JSON under `--json`.

#### Scenario: Show a coordinate's move history
- **WHEN** `sartre history m/prod` is run after `stable` was moved from `v1` to `v2` by `bob`
- **THEN** the output includes a row for `stable` showing `v1 → v2`, `bob`, and the reason

#### Scenario: JSON history on demand
- **WHEN** `sartre history m/prod --json` is run
- **THEN** it emits a JSON array of move records, each with pointer, from, to, actor, reason, and time

### Requirement: Read commands surface provenance
The `show` and `log` commands SHALL display the commit `actor` and `reason` for versions, so provenance is visible without a separate query.

#### Scenario: Log shows who and why
- **WHEN** `sartre log m/prod` is run for a coordinate with attributed commits
- **THEN** each row includes the version's actor and reason

### Requirement: Checkout refuses a non-empty destination unless forced
`sartre checkout <ref> <dest>` SHALL refuse to write into a destination that already exists and is non-empty, exiting non-zero with a clear message naming the directory and the `--force` override, rather than overlaying silently. A nonexistent or empty destination materializes the version as normal. `--force` SHALL write into a non-empty destination with overlay semantics (manifest files written/overwritten, extraneous files left in place — no pruning).

#### Scenario: Non-empty destination is refused
- **WHEN** `sartre checkout m/prod ./out` is run and `./out` already contains files
- **THEN** the command exits non-zero with a message naming `./out` and `--force`, and writes nothing

#### Scenario: Force overlays into a non-empty destination
- **WHEN** `sartre checkout m/prod ./out --force` is run and `./out` is non-empty
- **THEN** the version's files are written (overwriting collisions), pre-existing extraneous files remain, and the command succeeds

### Requirement: Derive a version on the command line
The CLI SHALL provide `sartre publish-over <coord> [logical=source ...]` that derives a new version from a base, changing only the given paths. It SHALL accept `--from <ref>` (the base, default the coordinate's head), repeatable `--rm <path>` (drop), repeatable `--mv <old:new>` (rename, reusing the blob), `--stage` (land on an auto-generated unique staging pointer instead of advancing head), and the same options as `publish`: `-p/--pointer`, `--point <alias>`, `--as/--author` (required, via the author ladder), `-m/--message` (reason), and `--meta key=value`. All other paths carry over from the base unchanged. On success it SHALL print the new version id (or JSON under `--json`) together with a summary of counts (inherited / replaced / added / removed / renamed), and SHALL NOT modify the base.

#### Scenario: Change one file against head
- **WHEN** `sartre publish-over m/prod cfg.json=./cfg.json --as alice -m "tweak"` is run
- **THEN** a new version is published whose `cfg.json` is the given file and whose other files are inherited from the current head unchanged, head advances to it, and the output reports the inherited/replaced counts

#### Scenario: Rename and remove against an explicit base
- **WHEN** `sartre publish-over m/prod --from @sha256:… --mv old.bin:new.bin --rm stale.txt --as alice` is run
- **THEN** the new version is the named base with `old.bin` re-keyed to `new.bin` (same blob, nothing uploaded) and `stale.txt` removed, everything else inherited

### Requirement: Divergent base is guarded, staging lands off head
`sartre publish-over` SHALL refuse when the resolved `--from` base is not the coordinate's current head, unless `--force` is given; `--force` SHALL additionally require a `-m/--message` reason and SHALL print what is being discarded (versions and/or paths present in head but not in the derived version) before writing, so advancing off an older base is explicit and visible. `--stage` SHALL advance a freshly generated unique staging pointer (never head), print its name, record the base version it derived from in the staged version's metadata (`derived_from`), and leave head unchanged — enabling verify-then-promote: derive to the staging ref, verify it, then `sartre promote` head to it (a base-CAS advance that refuses if head moved).

#### Scenario: Divergent base refused without force
- **WHEN** `sartre publish-over m/prod --from @<old> cfg.json=./cfg.json --as alice` is run and `<old>` is not the current head
- **THEN** the command exits non-zero explaining the base is not head and how to override, and writes nothing

#### Scenario: Force states what is discarded
- **WHEN** the same command is re-run with `--force` and `-m "rollback"`
- **THEN** it prints the versions/paths being discarded relative to head, then publishes the derived version; run with `--force` but no `-m`, it refuses

#### Scenario: Stage lands off head for verification
- **WHEN** `sartre publish-over m/prod cfg.json=./cfg.json --stage --as alice -m "…"` is run
- **THEN** the derived version is published to a new unique staging pointer whose name is printed, head is unchanged, the staged version records the base it derived from, and it can be resolved via that staging ref and later promoted to head with a base-CAS `sartre promote`

### Requirement: Delete a pointer on the command line
The CLI SHALL provide `sartre delete-pointer <coord:alias>` that removes a mutable alias via the facade's compare-and-swap deletion. It SHALL refuse to delete `head` (a coordinate's identity) with a clear non-zero error, and SHALL surface a concurrent move as a conflict advising a retry. Deleting an absent alias SHALL succeed as a no-op. It SHALL accept the standard mutating flags `--as/--author` and `-m/--message`.

#### Scenario: Delete a staging alias
- **WHEN** `sartre delete-pointer m/prod:staging-abc --as alice` is run for an existing staging alias
- **THEN** the alias is removed, the command reports the deletion (or JSON under `--json`), and the version it referenced is untouched

#### Scenario: Refuse to delete head
- **WHEN** `sartre delete-pointer m/prod` (or `m/prod:head`) is run
- **THEN** the command exits non-zero explaining head cannot be deleted, and nothing is changed

### Requirement: Promote a staged version on the command line
The CLI SHALL provide `sartre promote <coord[:pointer]> <staging-alias|@version>` that advances the target pointer (default `head`) to the staged version using the facade's base-CAS promotion — so a publish that moved the pointer since staging surfaces as a conflict rather than a silent overwrite. On success it MAY delete the spent staging alias. When the staged version records no base, it SHALL refuse with a clear message unless a last-writer override flag is given. It SHALL accept `--as/--author` and `-m/--message`.

#### Scenario: Promote a verified staging pointer to head
- **WHEN** `sartre promote m/prod m/prod:staging-abc --as alice -m "verified"` is run and head has not moved since staging
- **THEN** head advances to the staged version and the staging alias is cleaned up

#### Scenario: Promote refuses when head moved
- **WHEN** the same promote is run but head advanced during verification
- **THEN** the command exits non-zero explaining head moved and the staged version can be re-derived, and head is left unchanged
