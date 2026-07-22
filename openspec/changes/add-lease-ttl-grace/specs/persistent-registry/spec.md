## REMOVED Requirements

### Requirement: Lease lifetime is process-scoped
**Reason**: Process-scoped, in-memory leases are invisible to other writers, so a GC
process on a shared registry cannot see another writer's in-flight lease and may collect
its blobs. Leases are now durable and expiry-bounded (see the added requirement below and
the modified `registry-port` lease surface).
**Migration**: None required for callers. Leases now persist in a `leases` table and are
reclaimed by TTL expiry rather than by process exit; a crashed publisher's lease is
cleared when its ttl elapses instead of on reopen. `acquire_lease` gains a `ttl`
parameter with a sensible default, so existing call sites are unaffected.

## ADDED Requirements

### Requirement: Durable, registry-clock TTL leases
Leases SHALL be stored durably in the registry (a `leases` table in the SQL backends; an
equivalent expiry-aware structure in the reference backend) so that every process sharing
the registry observes the same set of live leases. Each lease SHALL carry an `expires_at`
computed from the **registry's own clock** at `acquire`/`renew` time; the active-root
queries SHALL filter on `expires_at > now()` evaluated by the registry, never by a client
clock. A lease SHALL be reclaimed by expiry: once its ttl elapses it ceases to protect
its version and hashes, so a crashed publisher cannot pin storage indefinitely. Durable
leases SHALL NOT change observational equivalence for unexpired leases — a sequence of
operations that never lets a lease expire SHALL behave identically to the reference
backend.

#### Scenario: A live lease is visible across registry instances
- **WHEN** one registry instance acquires a lease over a shared database, and a second
  instance over the same database queries active leases before the ttl elapses
- **THEN** the second instance reports the lease's hashes and version as protected

#### Scenario: A lapsed lease stops protecting across instances
- **WHEN** the lease's ttl elapses against the registry clock with no renewal
- **THEN** every instance's `active_leased_hashes`/`active_leased_versions` omits it, so
  GC on any instance may reclaim the blobs

#### Scenario: Expiry uses the registry clock, not the caller's
- **WHEN** leases are acquired and queried
- **THEN** `expires_at` is computed and compared using the registry's clock, so writers
  and GC hosts with skewed local clocks agree on which leases are live
