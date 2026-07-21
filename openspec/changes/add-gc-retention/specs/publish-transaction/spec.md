## ADDED Requirements

### Requirement: Blob lease for the publish duration
A publish SHALL hold a lease over its version and the set of blob hashes it writes,
acquired before the first blob upload and released after the transaction completes.
Because the publish orders `blobs → manifest → pointer`, a blob exists before any
manifest references it and a manifest is committed before its pointer advances; the
lease closes both windows — a concurrent garbage collector would otherwise see the
blob as unreferenced and collect it, or drop the just-committed manifest as unpointed
history. The lease SHALL be honored as a GC root (leased hashes protected, leased
version retained), so no reachable state lets GC delete a blob a committed manifest
references (`BlobSafe`).

#### Scenario: Uploaded-but-uncommitted blob is protected
- **WHEN** a publish has uploaded a blob but has not yet committed the manifest, and a
  garbage collector runs concurrently
- **THEN** the blob is under the publish's lease and is not collected, so the ensuing
  commit and pointer advance reference present bytes

#### Scenario: Lease released after completion
- **WHEN** a publish has advanced the pointer (or definitively failed) and released its lease
- **THEN** its blobs are thereafter protected only by ordinary reachability from retained manifests, not by the lease
