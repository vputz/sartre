# Spec Delta

## MODIFIED Requirements

### Requirement: In-memory backend mirrors change provenance
The in-memory reference backend SHALL record commit `actor`/`reason` on its per-coordinate commit log and SHALL maintain an in-memory append-only pointer-move history, recording the optional `base` on each move (set only for staging advances), so that `list_log` and `list_pointer_history` behave identically to the persistent backend. It SHALL implement `pointer_base(coord, name)` by returning the base on the pointer's newest advance move (reading past a later deletion tombstone), matching the other backends. This keeps the reference backend a faithful oracle for differential testing of provenance, including the base field and `pointer_base`.

#### Scenario: Reference backend reports commit and move provenance
- **WHEN** a version is committed and a pointer moved with an actor and reason against the in-memory backend
- **THEN** `list_log` exposes the commit's actor/reason and `list_pointer_history` exposes the move's from/to versions, base, actor, reason, and time — matching the persistent backend for the same sequence

#### Scenario: Reference backend recovers a staged base
- **WHEN** a staging advance recorded `base=b` against the in-memory backend and `pointer_base(coord, name)` is called (including after the staging pointer is deleted)
- **THEN** it returns `b`, matching the persistent and S3 backends for the same sequence
