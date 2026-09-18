## ADDED Requirements

### Requirement: Derive a version on the command line
The CLI SHALL provide `sartre publish-over <coord> [logical=source ...]` that derives a new version from a base, changing only the given paths. It SHALL accept `--from <ref>` (the base, default the coordinate's head), repeatable `--rm <path>` (drop), repeatable `--mv <old:new>` (rename, reusing the blob), `--stage` (land on an auto-generated unique staging pointer instead of advancing head), and the same options as `publish`: `-p/--pointer`, `--point <alias>`, `--as/--author` (required, via the author ladder), `-m/--message` (reason), and `--meta key=value`. All other paths carry over from the base unchanged. On success it SHALL print the new version id (or JSON under `--json`) together with a summary of counts (inherited / replaced / added / removed / renamed), and SHALL NOT modify the base.

#### Scenario: Change one file against head
- **WHEN** `sartre publish-over m/prod cfg.json=./cfg.json --as alice -m "tweak"` is run
- **THEN** a new version is published whose `cfg.json` is the given file and whose other files are inherited from the current head unchanged, head advances to it, and the output reports the inherited/replaced counts

#### Scenario: Rename and remove against an explicit base
- **WHEN** `sartre publish-over m/prod --from @sha256:… --mv old.bin:new.bin --rm stale.txt --as alice` is run
- **THEN** the new version is the named base with `old.bin` re-keyed to `new.bin` (same blob, nothing uploaded) and `stale.txt` removed, everything else inherited

### Requirement: Divergent base is guarded, staging lands off head
`sartre publish-over` SHALL refuse when the resolved `--from` base is not the coordinate's current head, unless `--force` is given; `--force` SHALL additionally require a `-m/--message` reason and SHALL print what is being discarded (versions and/or paths present in head but not in the derived version) before writing, so advancing off an older base is explicit and visible. `--stage` SHALL advance a freshly generated unique staging pointer (never head), print its name, and leave head unchanged — enabling verify-then-promote: derive to the staging ref, verify it, then advance head separately.

#### Scenario: Divergent base refused without force
- **WHEN** `sartre publish-over m/prod --from @<old> cfg.json=./cfg.json --as alice` is run and `<old>` is not the current head
- **THEN** the command exits non-zero explaining the base is not head and how to override, and writes nothing

#### Scenario: Force states what is discarded
- **WHEN** the same command is re-run with `--force` and `-m "rollback"`
- **THEN** it prints the versions/paths being discarded relative to head, then publishes the derived version; run with `--force` but no `-m`, it refuses

#### Scenario: Stage lands off head for verification
- **WHEN** `sartre publish-over m/prod cfg.json=./cfg.json --stage --as alice -m "…"` is run
- **THEN** the derived version is published to a new unique staging pointer whose name is printed, head is unchanged, and the version can be resolved via that staging ref for verification before head is advanced
