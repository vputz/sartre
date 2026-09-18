## ADDED Requirements

### Requirement: Durable pointer deletion with nullable move target
The durable (SQLite/Postgres) registry SHALL delete a pointer as a single conditional `DELETE` guarded so the removal applies only when the current value equals `expected` (a row-count of one), within the same transaction as the appended pointer-move row — so deletion is atomic and compare-and-swap-safe under concurrent writers. The `pointer_moves` schema SHALL permit a null move target (`to_version` nullable) to record a deletion, and existing databases SHALL be migrated to the nullable column without loss of history. A deletion of an absent pointer with `expected=None` SHALL be a no-op.

#### Scenario: Conditional delete is atomic and CAS-safe
- **WHEN** two writers concurrently attempt to delete or move the same pointer
- **THEN** at most one delete commits (its guarded `DELETE` affects one row) and the other observes the mismatch as `Conflict`, with the pointer and its history left consistent

#### Scenario: Deletion recorded with a null target
- **WHEN** a pointer is deleted
- **THEN** a `pointer_moves` row is committed with `from_version` set to the prior value and `to_version` null, in the same transaction as the delete

#### Scenario: Existing database migrates to the nullable column
- **WHEN** a registry created before this change is opened
- **THEN** its `pointer_moves.to_version` column is relaxed to nullable and all prior rows remain readable and unchanged
