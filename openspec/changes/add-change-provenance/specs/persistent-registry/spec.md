## ADDED Requirements

### Requirement: Durable commit provenance and pointer-move history
The persistent registry SHALL store commit provenance and pointer-move history durably. The commit-log table SHALL include `actor` and `reason` columns written in the commit transaction. A `pointer_moves` table SHALL record one row per successful pointer move — `(coordinate, pointer, from_version, to_version, actor, reason, at)` — inserted within the same transaction as the pointer compare-and-swap, using the backend's timestamp/`NOW` dialect hooks for `at`. Both SHALL survive process restart and SHALL be readable via `list_log` and `list_pointer_history`.

#### Scenario: Provenance survives a restart
- **WHEN** a version is committed and a pointer moved with an actor and reason, then the registry is reopened
- **THEN** `list_log` reports the commit's actor and reason and `list_pointer_history` reports the move's from/to versions, actor, reason, and time

#### Scenario: Move history is written under the CAS transaction
- **WHEN** a pointer move succeeds
- **THEN** exactly one `pointer_moves` row is committed atomically with the pointer advance; a rejected move commits no row
