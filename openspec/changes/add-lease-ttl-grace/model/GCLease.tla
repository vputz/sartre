----------------------------- MODULE GCLease -----------------------------
(***************************************************************************)
(* TTL leases: does a lease that can EXPIRE mid-publish reopen the race    *)
(* the (permanent) lease discipline was verified to close?                  *)
(*                                                                          *)
(* Motivation: in a shared/multi-writer registry a lease must live in the   *)
(* registry (so every writer's GC sees it) AND must expire (so a crashed    *)
(* publisher's lease can't pin blobs forever). Expiry is the new adversary. *)
(* This model keeps the GC + publish structure of GC.tla but DECOUPLES      *)
(* lease validity from publish progress: `leased[p]` is the lease's         *)
(* registry-side validity, which the environment may revoke (Expire) while  *)
(* publisher p is still mid-flight.                                          *)
(*                                                                          *)
(* Only VALID (unexpired) leases count as GC roots — a GC host reads the    *)
(* registry and honours exactly the leases whose expires_at is in the       *)
(* future. So expiry strips protection from p's in-flight blobs.            *)
(*                                                                          *)
(* SelfCheck is the fix toggle:                                             *)
(*   TRUE  — publish re-verifies its lease is still valid immediately before *)
(*           Commit and before Advance, and aborts (FailExpired) if it       *)
(*           lapsed. Realistic: heartbeat renewal is UPDATE .. WHERE          *)
(*           expires_at > now, so once lapsed the publisher provably knows.   *)
(*   FALSE — publish barrels ahead regardless (TTL added, no self-check).     *)
(*                                                                          *)
(* Renewal is NOT a separate action: in this boolean abstraction "renewed   *)
(* in time" is indistinguishable from "did not expire yet", which the        *)
(* nondeterministic Expire already covers. Renewal is a liveness concern     *)
(* (keep long publishes from expiring), not a safety one — the safety proof  *)
(* must hold even in the worst case where Expire fires as early as possible. *)
(***************************************************************************)
EXTENDS Naturals, Sequences

CONSTANTS Publishers,   \* concurrent publishers, e.g. {p1, p2}
          NoTip,        \* sentinel: pointer not yet set
          NoMark,       \* sentinel: no GC scan in progress
          SelfCheck     \* BOOLEAN fix toggle (TRUE = publish verifies lease before commit/advance)

Versions == Publishers          \* version published by p is p (opaque id)
Blobs    == Publishers          \* version p needs blob p
BlobsOf(v) == {v}

VARIABLES
    storedBlobs,   \* subset of Blobs physically present (grows via put, shrinks via sweep)
    committed,     \* subset of Versions whose manifest record exists
    tip,           \* current pointer value, in Versions \cup {NoTip}
    log,           \* append-only history of pointer tips
    pub,           \* pub[p] = [phase |-> ..., start |-> tip-observed-at-begin]
    gcMark,        \* NoMark, or a snapshot [blobs, dropV, sweep] taken at mark time
    leased         \* leased[p] = p's lease is currently VALID (registry-side, unexpired)

vars == <<storedBlobs, committed, tip, log, pub, gcMark, leased>>

Phases == {"idle", "started", "blobbed", "committed", "done", "failed"}

Holding(p) == pub[p].phase \in {"started", "blobbed", "committed"}

\* GC roots come from leases the registry still considers valid — expired
\* leases contribute nothing (their expires_at is in the past).
LeasedVersions == {p \in Publishers : leased[p]}
LeasedHashes   == UNION {BlobsOf(p) : p \in LeasedVersions}

Marks == [blobs: SUBSET Blobs, dropV: SUBSET Versions, sweep: SUBSET Blobs]

TypeOk ==
    /\ storedBlobs \subseteq Blobs
    /\ committed \subseteq Versions
    /\ tip \in Versions \cup {NoTip}
    /\ log \in Seq(Versions)
    /\ pub \in [Publishers -> [phase: Phases, start: Versions \cup {NoTip}]]
    /\ gcMark \in {NoMark} \cup Marks
    /\ leased \in [Publishers -> BOOLEAN]

Init ==
    /\ storedBlobs = {}
    /\ committed = {}
    /\ tip = NoTip
    /\ log = << >>
    /\ pub = [p \in Publishers |-> [phase |-> "idle", start |-> NoTip]]
    /\ gcMark = NoMark
    /\ leased = [p \in Publishers |-> FALSE]

(***************************************************************************)
(* Publish steps                                                           *)
(***************************************************************************)

\* acquire_lease(version p, blobs of p): inserts a fresh, valid lease row.
Begin(p) ==
    /\ pub[p].phase = "idle"
    /\ pub' = [pub EXCEPT ![p] = [phase |-> "started", start |-> tip]]
    /\ leased' = [leased EXCEPT ![p] = TRUE]
    /\ UNCHANGED <<storedBlobs, committed, tip, log, gcMark>>

\* store.put(blob p) -- idempotent set union
PutBlobs(p) ==
    /\ pub[p].phase = "started"
    /\ storedBlobs' = storedBlobs \cup BlobsOf(p)
    /\ pub' = [pub EXCEPT ![p].phase = "blobbed"]
    /\ UNCHANGED <<committed, tip, log, gcMark, leased>>

\* commit(version p). Under SelfCheck the publisher first re-verifies its lease
\* is still valid; a lapsed lease means its blob may already be swept, so it must
\* NOT commit (it aborts via FailExpired instead).
Commit(p) ==
    /\ pub[p].phase = "blobbed"
    /\ (SelfCheck => leased[p])
    /\ committed' = committed \cup {p}
    /\ pub' = [pub EXCEPT ![p].phase = "committed"]
    /\ UNCHANGED <<storedBlobs, tip, log, gcMark, leased>>

\* set_pointer(expected=start): CAS. Same self-check guard — never point at a
\* manifest whose blobs a lapsed lease may have let GC reclaim. Releases the lease.
Advance(p) ==
    /\ pub[p].phase = "committed"
    /\ (SelfCheck => leased[p])
    /\ \/ /\ tip = pub[p].start                 \* CAS succeeds
          /\ tip' = p
          /\ log' = Append(log, p)
          /\ pub' = [pub EXCEPT ![p].phase = "done"]
          /\ leased' = [leased EXCEPT ![p] = FALSE]
          /\ UNCHANGED <<storedBlobs, committed, gcMark>>
       \/ /\ tip # pub[p].start                 \* CAS conflict -> fail-fast
          /\ pub' = [pub EXCEPT ![p].phase = "failed"]
          /\ leased' = [leased EXCEPT ![p] = FALSE]
          /\ UNCHANGED <<storedBlobs, committed, tip, log, gcMark>>

\* TTL lapses mid-publish: the registry-side lease becomes invalid while p is
\* still holding. The adversary; may fire as early as right after acquire.
Expire(p) ==
    /\ leased[p]
    /\ Holding(p)
    /\ leased' = [leased EXCEPT ![p] = FALSE]
    /\ UNCHANGED <<storedBlobs, committed, tip, log, pub, gcMark>>

\* Self-check tripped: the publisher observes its lease lapsed and aborts rather
\* than committing/pointing over possibly-reclaimed blobs. Only exists when the
\* publisher actually performs the check.
FailExpired(p) ==
    /\ SelfCheck
    /\ pub[p].phase \in {"blobbed", "committed"}
    /\ ~leased[p]
    /\ pub' = [pub EXCEPT ![p].phase = "failed"]
    /\ UNCHANGED <<storedBlobs, committed, tip, log, gcMark, leased>>

\* Crash mid-publish: durable state persists; the lease is released (adversarial:
\* gives GC more freedom). Publisher restarts from idle.
Crash(p) ==
    /\ pub[p].phase \notin {"idle", "done", "failed"}
    /\ pub' = [pub EXCEPT ![p] = [phase |-> "idle", start |-> NoTip]]
    /\ leased' = [leased EXCEPT ![p] = FALSE]
    /\ UNCHANGED <<storedBlobs, committed, tip, log, gcMark>>

(***************************************************************************)
(* GC steps (mark snapshots; sweep deletes only what mark identified, and   *)
(* RE-VALIDATES against current state — carried over from GC.tla)           *)
(***************************************************************************)

GCMark ==
    /\ gcMark = NoMark
    /\ LET retainedV == (({tip} \ {NoTip}) \cup LeasedVersions)
           liveB     == (UNION {BlobsOf(v) : v \in retainedV}) \cup LeasedHashes
       IN gcMark' = [ blobs |-> liveB,
                      dropV |-> committed \ retainedV,
                      sweep |-> storedBlobs \ liveB ]
    /\ UNCHANGED <<storedBlobs, committed, tip, log, pub, leased>>

GCSweep ==
    /\ gcMark # NoMark
    /\ LET retainedNow == (({tip} \ {NoTip}) \cup LeasedVersions)
           droppable   == gcMark.dropV \cap (committed \ retainedNow)
           survivors   == committed \ droppable
           liveNow     == (UNION {BlobsOf(v) : v \in survivors}) \cup LeasedHashes
           deleteSet   == gcMark.sweep \cap (storedBlobs \ liveNow)
       IN /\ committed' = survivors
          /\ storedBlobs' = storedBlobs \ deleteSet
    /\ gcMark' = NoMark
    /\ UNCHANGED <<tip, log, pub, leased>>

GCAbort ==
    /\ gcMark # NoMark
    /\ gcMark' = NoMark
    /\ UNCHANGED <<storedBlobs, committed, tip, log, pub, leased>>

Next ==
    \/ \E p \in Publishers:
          Begin(p) \/ PutBlobs(p) \/ Commit(p) \/ Advance(p)
          \/ Expire(p) \/ FailExpired(p) \/ Crash(p)
    \/ GCMark \/ GCSweep \/ GCAbort

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Invariants                                                              *)
(***************************************************************************)

\* Every committed manifest has all its blobs present.
BlobSafe == \A v \in committed : BlobsOf(v) \subseteq storedBlobs

\* The pointer never dangles.
TipSafe ==
    \/ tip = NoTip
    \/ /\ tip \in committed
       /\ BlobsOf(tip) \subseteq storedBlobs

=============================================================================
