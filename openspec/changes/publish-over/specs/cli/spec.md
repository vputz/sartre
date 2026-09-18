## ADDED Requirements

### Requirement: Derive a version on the command line
The CLI SHALL provide `sartre publish-over <coord> [logical=source ...]` that derives a new version from a base, changing only the given paths. It SHALL accept `--from <ref>` (the base, default the coordinate's head), repeatable `--rm <path>` (paths to drop), and the same options as `publish`: `-p/--pointer`, `--point <alias>`, `--as/--author` (required, resolved by the author ladder), `-m/--message` (the reason), and `--meta key=value`. Each `logical=source` (or bare file/directory) supplies a changed/added path; all other paths carry over from the base unchanged. It SHALL print the new version id (or JSON under `--json`) like `publish`, and SHALL NOT modify the base version.

#### Scenario: Change one file against head
- **WHEN** `sartre publish-over m/prod cfg.json=./cfg.json --as alice -m "tweak"` is run
- **THEN** a new version is published whose `cfg.json` is the given file and whose other files are inherited from the current head unchanged, and head advances to it

#### Scenario: Derive from an explicit base and remove a path
- **WHEN** `sartre publish-over m/prod --from @sha256:… --rm old.txt w.bin=./w.bin --as alice` is run
- **THEN** the new version is the named base with `old.txt` removed and `w.bin` replaced, everything else inherited
