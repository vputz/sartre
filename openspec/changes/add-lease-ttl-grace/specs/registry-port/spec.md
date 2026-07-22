## MODIFIED Requirements

### Requirement: Blob lease surface
The `Registry` SHALL expose a lease surface coordinating in-flight publishes with GC:
`acquire_lease(version, hashes, ttl) -> LeaseId`, `renew_lease(lease_id, ttl) -> bool`,
`release_lease(lease_id)`, `active_leased_hashes() -> Set[Hash]` (the union of hashes
under all live leases), and `active_leased_versions() -> Set[Version]` (the versions
under all live leases). A lease SHALL carry an expiry computed as `now + ttl` against the
**registry's own clock**; a lease whose expiry is in the past is **expired** and SHALL
NOT be reported by `active_leased_hashes`/`active_leased_versions`. `renew_lease` SHALL
extend a still-valid lease's expiry to `now + ttl` and return `True`; if the lease is
already expired or unknown it SHALL make no change and return `False`, so the caller can
detect a lapsed lease. GC SHALL treat live (unexpired) leased hashes as protected blobs
and live leased versions as retained manifests, so that both an in-flight publish's
uploaded blobs (before its manifest is committed) and its committed manifest (before its
pointer advances) are safe from collection while its lease is live. A lease not released
(e.g. a crashed publisher) SHALL keep its version and hashes protected only until it
expires.

#### Scenario: Leased hashes and version are reported as roots
- **WHEN** a lease is acquired over a version and its hashes with a ttl
- **THEN** `active_leased_hashes` includes those hashes and `active_leased_versions`
  includes that version, until the lease is released or expires

#### Scenario: Release removes protection
- **WHEN** a lease is released
- **THEN** its hashes and version no longer appear unless held by another live lease

#### Scenario: Expiry removes protection
- **WHEN** a lease's ttl elapses against the registry clock without a renewal
- **THEN** `active_leased_hashes` and `active_leased_versions` no longer report its
  hashes or version

#### Scenario: Renew extends a still-valid lease
- **WHEN** `renew_lease` is called on a lease that has not yet expired
- **THEN** it returns `True` and the lease's expiry is extended to `now + ttl`

#### Scenario: Renewing a lapsed lease reports failure
- **WHEN** `renew_lease` is called on a lease whose ttl has already elapsed
- **THEN** it returns `False` and no lease is revived
