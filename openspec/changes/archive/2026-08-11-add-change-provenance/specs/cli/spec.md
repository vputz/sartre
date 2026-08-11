## MODIFIED Requirements

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

## ADDED Requirements

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
