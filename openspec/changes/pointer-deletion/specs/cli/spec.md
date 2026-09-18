## ADDED Requirements

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

## MODIFIED Requirements

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
