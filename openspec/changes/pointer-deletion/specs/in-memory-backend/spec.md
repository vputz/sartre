## ADDED Requirements

### Requirement: In-memory backend supports pointer deletion
The in-memory reference backend SHALL implement `delete_pointer` under its lock: with the lock held it SHALL compare the pointer's current value to `expected`, raise `Conflict` on a mismatch, otherwise remove the pointer and append the corresponding deletion to its pointer-move history. Deleting an absent pointer with `expected=None` SHALL be a no-op. This preserves the backend's role as the executable oracle the persistent and S3 backends are checked against.

#### Scenario: Delete under the lock is compare-and-swap
- **WHEN** `delete_pointer(coord, name, expected=v)` is called on the in-memory backend and the pointer currently holds `v`
- **THEN** the pointer is removed atomically and a deletion move (`to_version=None`) is recorded; if it held a different value, `Conflict` is raised and nothing changes
