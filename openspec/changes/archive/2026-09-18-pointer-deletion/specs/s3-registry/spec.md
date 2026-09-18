## ADDED Requirements

### Requirement: Pointer deletion as an append-only tombstone event
On the transaction-less object store a pointer is an append-only event stream with no mutable row to remove, so the S3 registry SHALL delete a pointer by appending a **pointer-tombstone sentinel event** at the next sequence number via the same put-if-absent compare-and-swap used for advances. A reader SHALL treat a pointer whose tail event is a tombstone as unset: `head`/`resolve` SHALL raise `NotFound` and `list_pointers` SHALL omit it. The compare-and-swap SHALL be gap-free against the authoritative tail sequence, so a delete racing a concurrent advance of the same pointer resolves to exactly one coherent outcome (the sequence winner) — never a torn or half-applied state. A pointer tombstoned by such an event MAY be re-created by a later advance appended after it (the tombstone is a tail state, not a permanent lock). The delete event SHALL be retained in `list_log`/`list_pointer_history` forever, like every other event.

#### Scenario: A deleted pointer no longer resolves
- **WHEN** a pointer-tombstone event is appended as the tail of a pointer's stream
- **THEN** `resolve`/`head` for that pointer raise `NotFound`, `list_pointers` omits it, and its full event history (including the delete) is still readable

#### Scenario: Delete racing a concurrent advance yields one winner
- **WHEN** a delete and an advance both attempt to append at the same next sequence for one pointer
- **THEN** exactly one put-if-absent succeeds and the other observes the collision and re-reads the tail; the pointer resolves to the winner's outcome (deleted or advanced), never a mix

#### Scenario: A tombstoned pointer can be re-created
- **WHEN** an advance is appended after a pointer's tombstone tail
- **THEN** the pointer resolves again to the newly advanced version
