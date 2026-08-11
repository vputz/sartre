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
(`--point`), and metadata (`-m/--message`, `--meta key=value`).

#### Scenario: Directory maps relative logical paths
- **WHEN** `sartre publish m/prod ./ckpt` is run and `./ckpt/w/model.bin` exists
- **THEN** the published version contains an entry at logical path `w/model.bin`

#### Scenario: Explicit remap
- **WHEN** `sartre publish m/prod w.bin=./out/model.st` is run
- **THEN** the published version contains an entry at logical path `w.bin` with the bytes of
  `./out/model.st`

### Requirement: Pointer move on the command line
`sartre point <coord[:pointer]> <ref>` SHALL move a mutable pointer (head when no `:pointer`
is given, otherwise the named alias) to the version that `<ref>` resolves to, changing only
the pointer plane. It SHALL be compare-and-swap safe by default: it reads the current
pointer and advances only if unchanged, reporting a typed conflict (with guidance to re-run
or pass `--force`) when a concurrent move is detected; `--force` SHALL move unconditionally.
The target version MUST already be committed.

#### Scenario: Promote an existing version to an alias
- **WHEN** `sartre point m/prod:stable @sha256:v2` is run and `v2` is committed
- **THEN** the `stable` pointer of `m/prod` resolves to `v2`, with no blob upload

#### Scenario: Rollback moves head
- **WHEN** `sartre point m/prod @sha256:v1` is run
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

