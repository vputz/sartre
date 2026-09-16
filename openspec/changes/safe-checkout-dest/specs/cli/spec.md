## ADDED Requirements

### Requirement: Checkout refuses a non-empty destination unless forced
`sartre checkout <ref> <dest>` SHALL refuse to write into a destination that already exists and is non-empty, exiting non-zero with a clear message naming the directory and the `--force` override, rather than overlaying silently. A nonexistent or empty destination materializes the version as normal. `--force` SHALL write into a non-empty destination with overlay semantics (manifest files written/overwritten, extraneous files left in place — no pruning).

#### Scenario: Non-empty destination is refused
- **WHEN** `sartre checkout m/prod ./out` is run and `./out` already contains files
- **THEN** the command exits non-zero with a message naming `./out` and `--force`, and writes nothing

#### Scenario: Force overlays into a non-empty destination
- **WHEN** `sartre checkout m/prod ./out --force` is run and `./out` is non-empty
- **THEN** the version's files are written (overwriting collisions), pre-existing extraneous files remain, and the command succeeds
