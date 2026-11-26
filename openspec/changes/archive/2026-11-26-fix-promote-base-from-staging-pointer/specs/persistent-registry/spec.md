# Spec Delta

## MODIFIED Requirements

### Requirement: Durable commit provenance and pointer-move history
The persistent registry SHALL store commit provenance and pointer-move history durably. The commit-log table SHALL include `actor` and `reason` columns written in the commit transaction. A `pointer_moves` table SHALL record one row per successful pointer move — `(coordinate, pointer, from_version, to_version, base_version, actor, reason, at)` — inserted within the same transaction as the pointer compare-and-swap, using the backend's timestamp/`NOW` dialect hooks for `at`. The `base_version` column SHALL be nullable and SHALL be written only for staging advances (null otherwise). Both tables SHALL survive process restart and SHALL be readable via `list_log` and `list_pointer_history`, and `pointer_base(coord, name)` SHALL be served as a single query for that pointer's newest advance row carrying a non-null `base_version`.

#### Scenario: Provenance survives a restart
- **WHEN** a version is committed and a pointer moved with an actor and reason, then the registry is reopened
- **THEN** `list_log` reports the commit's actor and reason and `list_pointer_history` reports the move's from/to versions, base, actor, reason, and time

#### Scenario: Move history is written under the CAS transaction
- **WHEN** a pointer move succeeds
- **THEN** exactly one `pointer_moves` row is committed atomically with the pointer advance; a rejected move commits no row

#### Scenario: A staged base survives a restart and is read without a history scan
- **WHEN** a staging advance recorded `base_version=b`, the registry is reopened, and `pointer_base(coord, name)` is called
- **THEN** it returns `b` from a single latest-advance-row query, not by enumerating the coordinate's history

## ADDED Requirements

### Requirement: Additive migration for the pointer-move base column
The durable (SQLite/Postgres) registry SHALL add the nullable `base_version` column to an existing `pointer_moves` table via an additive `ALTER TABLE ... ADD COLUMN` in the `_migrate` hook on open. The migration SHALL NOT rewrite or backfill existing rows, and SHALL be idempotent on a store that already has the column.

#### Scenario: Existing database gains the nullable column
- **WHEN** a registry created before this change is opened
- **THEN** its `pointer_moves` table gains a nullable `base_version` column, all prior rows remain readable with `base_version` null, and no row is rewritten

#### Scenario: Re-opening an already-migrated database is a no-op
- **WHEN** a registry that already has the `base_version` column is opened again
- **THEN** the migration makes no change and all data is intact
