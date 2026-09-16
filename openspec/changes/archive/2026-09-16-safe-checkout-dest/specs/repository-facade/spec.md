## ADDED Requirements

### Requirement: Checkout requires an empty destination by default
`Repository.checkout(snap, dest, *, overwrite=False)` SHALL materialize the version only into a destination that does not exist or is empty. If `dest` exists and is non-empty and `overwrite` is false, it SHALL raise a typed error (`PathError`) naming the directory and the override, and SHALL write nothing. With `overwrite=True` it SHALL write the manifest's files into `dest`, overwriting any colliding paths and leaving pre-existing extraneous files in place — overlay semantics, not sync: it SHALL NOT delete files absent from the version. `checkout` SHALL NOT provide a prune/mirror mode, because without an index it cannot distinguish its own stale files from the caller's unrelated files. `AsyncRepository.checkout` SHALL expose the same `overwrite` parameter and behavior. `fetch_all`, which uses a fresh temporary directory, is unaffected.

#### Scenario: Fresh or empty destination materializes the exact tree
- **WHEN** `checkout` targets a nonexistent or empty directory
- **THEN** every entry is written at its logical path under `dest` and the directory contents equal the version's tree

#### Scenario: Non-empty destination is refused by default
- **WHEN** `checkout` targets an existing directory that already contains files and `overwrite` is false
- **THEN** it raises `PathError` naming the directory and writes nothing

#### Scenario: Overwrite overlays without pruning
- **WHEN** `checkout(..., overwrite=True)` targets a non-empty directory that holds a file not in the version
- **THEN** the version's files are written (overwriting any collisions) and the extraneous pre-existing file remains — the destination is not reduced to exactly the version
