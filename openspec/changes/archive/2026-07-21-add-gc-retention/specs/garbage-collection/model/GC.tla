------------------------------- MODULE GC -------------------------------
(***************************************************************************)
(* Abstracts sartre's garbage collection racing concurrent publishes.      *)
(*                                                                          *)
(* Publish (per publisher p, blobs -> manifest -> pointer), bracketed by a  *)
(* lease it holds for its whole duration:                                   *)
(*     acquire_lease(version p, blobs of p)      \* before any upload       *)
(*     store.put(blob p)                         \* (1) blob                 *)
(*     commit(version p)                         \* (2) manifest             *)
(*     set_pointer(expected=start)               \* (3) CAS                  *)
(*     release_lease                             \* after completion         *)
(*                                                                          *)
(* GC is mark-and-sweep, SPLIT into two steps so the mark/sweep window is    *)
(* observable (a real GC scans, then deletes):                              *)
(*     mark:  snapshot { live blobs, droppable manifests, sweepable blobs }  *)
(*            roots = pointer target UNION leased versions;                  *)
(*            live blobs = blobs of retained manifests UNION leased hashes.   *)
(*     sweep: delete the snapshotted sweepable blobs and droppable manifests.*)
(* Retention here keeps only the pointer target (keep_last_n = 0), the       *)
(* configuration that most stresses the race.                               *)
(*                                                                          *)
(* LeaseDisabled is the bug toggle: TRUE removes all lease protection,       *)
(* modelling GC with no lease discipline. It must break BlobSafe/TipSafe.    *)
(***************************************************************************)
EXTENDS Naturals, Sequences

CONSTANTS Publishers,     \* concurrent publishers, e.g. {p1, p2}
          NoTip,          \* sentinel: pointer not yet set
          NoMark,         \* sentinel: no GC scan in progress
          LeaseDisabled   \* BOOLEAN bug toggle (TRUE = no lease protection)

Versions == Publishers          \* version published by p is p (opaque id)
Blobs    == Publishers          \* version p needs blob p
BlobsOf(v) == {v}

VARIABLES
    storedBlobs,   \* subset of Blobs physically present (grows via put, shrinks via sweep)
    committed,     \* subset of Versions whose manifest record exists
    tip,           \* current pointer value, in Versions \cup {NoTip}
    log,           \* append-only history of pointer tips
    pub,           \* pub[p] = [phase |-> ..., start |-> tip-observed-at-begin]
    gcMark         \* NoMark, or a snapshot [blobs, dropV, sweep] taken at mark time

vars == <<storedBlobs, committed, tip, log, pub, gcMark>>

Phases == {"idle", "started", "blobbed", "committed", "done", "failed"}

\* A publisher holds its lease from acquire (Begin) until release (done/failed).
Holding(p) == pub[p].phase \in {"started", "blobbed", "committed"}
LeasedVersions == IF LeaseDisabled THEN {} ELSE {p \in Publishers : Holding(p)}
LeasedHashes   == IF LeaseDisabled THEN {}
                                   ELSE UNION {BlobsOf(p) : p \in {q \in Publishers : Holding(q)}}

Marks == [blobs: SUBSET Blobs, dropV: SUBSET Versions, sweep: SUBSET Blobs]

TypeOk ==
    /\ storedBlobs \subseteq Blobs
    /\ committed \subseteq Versions
    /\ tip \in Versions \cup {NoTip}
    /\ log \in Seq(Versions)
    /\ pub \in [Publishers -> [phase: Phases, start: Versions \cup {NoTip}]]
    /\ gcMark \in {NoMark} \cup Marks

Init ==
    /\ storedBlobs = {}
    /\ committed = {}
    /\ tip = NoTip
    /\ log = << >>
    /\ pub = [p \in Publishers |-> [phase |-> "idle", start |-> NoTip]]
    /\ gcMark = NoMark

(***************************************************************************)
(* Publish steps (lease acquired at Begin, released at Advance)            *)
(***************************************************************************)

\* acquire_lease(version p, blobs of p); start = head(pointer)
Begin(p) ==
    /\ pub[p].phase = "idle"
    /\ pub' = [pub EXCEPT ![p] = [phase |-> "started", start |-> tip]]
    /\ UNCHANGED <<storedBlobs, committed, tip, log, gcMark>>

\* store.put(blob p) -- idempotent set union
PutBlobs(p) ==
    /\ pub[p].phase = "started"
    /\ storedBlobs' = storedBlobs \cup BlobsOf(p)
    /\ pub' = [pub EXCEPT ![p].phase = "blobbed"]
    /\ UNCHANGED <<committed, tip, log, gcMark>>

\* commit(version p) -- records the manifest, does not move the pointer
Commit(p) ==
    /\ pub[p].phase = "blobbed"
    /\ committed' = committed \cup {p}
    /\ pub' = [pub EXCEPT ![p].phase = "committed"]
    /\ UNCHANGED <<storedBlobs, tip, log, gcMark>>

\* set_pointer(version p, expected=start) -- CAS; releases the lease either way
Advance(p) ==
    /\ pub[p].phase = "committed"
    /\ \/ /\ tip = pub[p].start                 \* CAS succeeds
          /\ tip' = p
          /\ log' = Append(log, p)
          /\ pub' = [pub EXCEPT ![p].phase = "done"]
          /\ UNCHANGED <<storedBlobs, committed, gcMark>>
       \/ /\ tip # pub[p].start                 \* CAS conflict -> fail-fast
          /\ pub' = [pub EXCEPT ![p].phase = "failed"]
          /\ UNCHANGED <<storedBlobs, committed, tip, log, gcMark>>

\* Crash mid-publish: durable state persists, the publisher restarts from idle.
\* Modelled as releasing the lease (the adversarial case: it gives GC MORE freedom
\* to collect, so BlobSafe here implies safety under lease-survives-crash too).
Crash(p) ==
    /\ pub[p].phase \notin {"idle", "done", "failed"}
    /\ pub' = [pub EXCEPT ![p] = [phase |-> "idle", start |-> NoTip]]
    /\ UNCHANGED <<storedBlobs, committed, tip, log, gcMark>>

(***************************************************************************)
(* GC steps (mark snapshots; sweep deletes only what mark identified)      *)
(***************************************************************************)

GCMark ==
    /\ gcMark = NoMark
    /\ LET retainedV == (({tip} \ {NoTip}) \cup LeasedVersions)
           liveB     == (UNION {BlobsOf(v) : v \in retainedV}) \cup LeasedHashes
       IN gcMark' = [ blobs |-> liveB,
                      dropV |-> committed \ retainedV,
                      sweep |-> storedBlobs \ liveB ]
    /\ UNCHANGED <<storedBlobs, committed, tip, log, pub>>

\* Delete, but RE-VALIDATE against current state at delete time — the mark only
\* bounds the candidate sets. This is load-bearing: because puts are content-
\* addressed and idempotent, a blob marked sweepable can be reused/adopted by a
\* new leased publish in the mark->sweep window, so trusting the stale mark would
\* delete a now-live blob (found by TLC). A candidate manifest is dropped only if
\* still unretained now; a candidate blob is deleted only if referenced by no
\* surviving committed manifest and named by no live lease now.
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
    /\ UNCHANGED <<tip, log, pub>>

\* GC interrupted between scan and delete: the mark is simply discarded (re-runnable).
GCAbort ==
    /\ gcMark # NoMark
    /\ gcMark' = NoMark
    /\ UNCHANGED <<storedBlobs, committed, tip, log, pub>>

Next ==
    \/ \E p \in Publishers: Begin(p) \/ PutBlobs(p) \/ Commit(p) \/ Advance(p) \/ Crash(p)
    \/ GCMark \/ GCSweep \/ GCAbort

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Invariants                                                              *)
(***************************************************************************)

\* Every existing committed manifest has all its blobs present. The blob lease
\* protects the put->commit window that would otherwise break this.
BlobSafe == \A v \in committed : BlobsOf(v) \subseteq storedBlobs

\* The pointer never dangles: its target manifest exists and its blobs are present.
\* The version lease protects the commit->advance window (GC must not drop a
\* just-committed, not-yet-pointed manifest an in-flight publish will point to).
TipSafe ==
    \/ tip = NoTip
    \/ /\ tip \in committed
       /\ BlobsOf(tip) \subseteq storedBlobs

=============================================================================
