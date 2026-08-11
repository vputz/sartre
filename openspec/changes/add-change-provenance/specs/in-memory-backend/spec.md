## ADDED Requirements

### Requirement: In-memory backend mirrors change provenance
The in-memory reference backend SHALL record commit `actor`/`reason` on its per-coordinate commit log and SHALL maintain an in-memory append-only pointer-move history, so that `list_log` and `list_pointer_history` behave identically to the persistent backend. This keeps the reference backend a faithful oracle for differential testing of provenance.

#### Scenario: Reference backend reports commit and move provenance
- **WHEN** a version is committed and a pointer moved with an actor and reason against the in-memory backend
- **THEN** `list_log` exposes the commit's actor/reason and `list_pointer_history` exposes the move's from/to versions, actor, reason, and time — matching the persistent backend for the same sequence
