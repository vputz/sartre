## ADDED Requirements

### Requirement: Provenance is a property of events, not of the manifest
Every mutating change SHALL record two provenance fields — `actor` (an attribution string; the system SHALL NOT prove or verify identity) and `reason` (free text explaining why the change was made). Provenance SHALL attach to the **event** that performed the change (a commit or a pointer move), never to the immutable, content-addressed manifest. Because a `Version` is the content hash of its entries and is shared across coordinates and promotions, provenance MUST NOT be stored on the manifest or on version-excluded `metadata`, where it would be first-writer-wins and shared across distinct acts.

#### Scenario: The same content committed by two people keeps two provenances
- **WHEN** identical entries are committed into two coordinates by different actors with different reasons
- **THEN** the shared version is stored once, but each coordinate's commit event records its own `actor` and `reason`

#### Scenario: Metadata does not carry provenance
- **WHEN** a change supplies `actor` and `reason`
- **THEN** neither is written into the manifest `metadata`, which remains reserved for domain payload

### Requirement: Commits are attributed
Committing a new version into a coordinate SHALL record the committing `actor` and the `reason` on that coordinate's commit-log event, alongside the version, sequence, and time. The reason SHALL be the single free-text field for a commit; there SHALL be no separate `message` metadata key.

#### Scenario: A commit records who and why
- **WHEN** a version is published with `actor="alice"` and `reason="retrain on Q3 data"`
- **THEN** the coordinate's commit log exposes that version's `actor` and `reason`

### Requirement: Pointer moves are retained as an ordered history
Moving a mutable pointer (advancing head, promoting or re-pointing an alias, or rolling back) SHALL append an immutable record to a per-coordinate, ordered **pointer-move history** capturing the pointer name, the version it moved from (or none, for creation), the version it moved to, the `actor`, the `reason`, and the time. The history SHALL be append-only and readable so operators can answer "who moved this pointer, from what, to what, and why."

#### Scenario: A promotion is recorded with from and to
- **WHEN** the `stable` alias is moved from `v1` to `v2` with `actor="bob"` and `reason="passed eval"`
- **THEN** a history record is appended for `stable` with `from=v1`, `to=v2`, `actor="bob"`, `reason="passed eval"`, and a timestamp

#### Scenario: Creating a pointer records no prior version
- **WHEN** an alias is first created pointing at a version
- **THEN** its history record has an empty `from` and the created `to` version

#### Scenario: History is append-only and ordered
- **WHEN** a pointer is moved several times
- **THEN** the history returns every move in the order it occurred, and earlier records are never mutated

### Requirement: Actor attribution without identity proof
The system SHALL treat `actor` as caller-supplied attribution and SHALL NOT authenticate it. At the library edge `actor` MAY be omitted, in which case the change SHALL be recorded with the sentinel `"unknown"` rather than rejected. Callers that require attribution (such as the CLI) SHALL enforce presence at their own edge.

#### Scenario: Library records unknown when actor is omitted
- **WHEN** a library caller publishes or moves a pointer without supplying `actor`
- **THEN** the event is recorded with `actor="unknown"` and the operation succeeds

### Requirement: Attribution adds no concurrency protocol
Recording provenance SHALL be an additive side-record written within the same transaction as the commit or the pointer compare-and-swap it describes. It SHALL NOT introduce any new lock, retry, or ordering guarantee beyond the existing `set_pointer` compare-and-swap.

#### Scenario: A rejected CAS records no move
- **WHEN** a pointer move fails its compare-and-swap
- **THEN** no pointer-move history record is appended for that failed attempt
