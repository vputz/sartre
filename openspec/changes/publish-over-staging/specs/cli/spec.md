## MODIFIED Requirements

### Requirement: Divergent base is guarded, staging lands off head
`sartre publish-over` SHALL refuse when the resolved `--from` base is not the coordinate's current head, unless `--force` is given; `--force` SHALL additionally require a `-m/--message` reason and SHALL print what is being discarded (versions and/or paths present in head but not in the derived version) before writing, so advancing off an older base is explicit and visible. `--stage` SHALL derive onto a staging pointer (never head) via the library's staging operation, print its name, and leave head unchanged — enabling verify-then-promote: derive to the staging ref, verify it, then `sartre promote` head to it (a base-CAS advance that refuses if head moved). The staging pointer name SHALL be content-derived (from the base and the affected paths), so re-running the same repair after a failed verify reuses the same staging pointer rather than leaving an orphaned one; the base it derived from SHALL be recorded in the staged version's metadata (`derived_from`) by that same operation.

#### Scenario: Divergent base refused without force
- **WHEN** `sartre publish-over m/prod --from @<old> cfg.json=./cfg.json --as alice` is run and `<old>` is not the current head
- **THEN** the command exits non-zero explaining the base is not head and how to override, and writes nothing

#### Scenario: Force states what is discarded
- **WHEN** the same command is re-run with `--force` and `-m "rollback"`
- **THEN** it prints the versions/paths being discarded relative to head, then publishes the derived version; run with `--force` but no `-m`, it refuses

#### Scenario: Stage lands off head for verification
- **WHEN** `sartre publish-over m/prod cfg.json=./cfg.json --stage --as alice -m "…"` is run
- **THEN** the derived version is published to a content-derived staging pointer whose name is printed, head is unchanged, the staged version records the base it derived from, and it can be resolved via that staging ref and later promoted to head with a base-CAS `sartre promote`

#### Scenario: Re-running a staged repair reuses its pointer
- **WHEN** the same `sartre publish-over … --stage` repair is run twice (same base and affected paths)
- **THEN** both runs report the same staging pointer name, so a re-run after a failed verify reuses that pointer instead of creating another
