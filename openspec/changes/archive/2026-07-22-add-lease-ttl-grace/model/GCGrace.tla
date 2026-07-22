----------------------------- MODULE GCGrace -----------------------------
(***************************************************************************)
(* The grace-period backstop, on a real clock. Discharges the timed lemma   *)
(* deferred in the first GC change: grace >= max_publish_duration => BlobSafe *)
(* for a writer that takes NO lease (the degenerate/no-registration case).   *)
(*                                                                          *)
(* Scope — grace is a BLOB backstop only. It protects the put->commit window *)
(* (a stored-but-not-yet-referenced blob) by refusing to sweep any blob      *)
(* younger than Grace. It does NOT protect the commit->advance manifest       *)
(* window — keeping a committed-but-unpointed manifest alive is the version    *)
(* lease's job (see GCLease.tla / GC.tla, TipSafe). So this model has no       *)
(* pointer and checks BlobSafe only; that is exactly, and only, what grace     *)
(* buys an unleased writer.                                                    *)
(*                                                                          *)
(* Time is discrete: `clock` ticks up to MaxTime. Each stored blob remembers  *)
(* `bornAt` (its store mtime; refreshed on idempotent re-put). A publish's     *)
(* put->commit gap is bounded by MaxGap — the operational "max publish         *)
(* duration" d. GC treats a blob as live if it is referenced by a committed    *)
(* manifest OR younger than Grace (age = clock - bornAt < Grace).              *)
(*                                                                          *)
(* The theorem the two configs pin down:                                     *)
(*   Grace >  MaxGap  (grace covers the publish window) => BlobSafe holds,     *)
(*                     AND GC still reclaims abandoned uploads (non-vacuous).  *)
(*   Grace <= MaxGap  (a publish can outlast grace)     => BlobSafe BREAKS.    *)
(* No self-check here: pure grace's safety rests entirely on the scalar        *)
(* operational assumption g >= d. That is the whole point — and its limit.     *)
(***************************************************************************)
EXTENDS Naturals

CONSTANTS Writers,    \* unleased/direct writers, e.g. {w1, w2}
          NoMark,     \* sentinel: no GC scan in progress
          MaxTime,    \* clock horizon (bounds the state space)
          Grace,      \* GC won't sweep a blob younger than this many ticks
          MaxGap      \* bound on a publish's put->commit gap (= max publish duration d)

Blobs   == Writers            \* writer w's blob is w
Versions == Writers           \* writer w's manifest version is w
BlobsOf(v) == {v}

VARIABLES
    storedBlobs,   \* subset of Blobs physically present
    committed,     \* subset of Versions whose manifest record exists
    pub,           \* pub[w].phase
    gcMark,        \* NoMark, or the snapshotted sweep candidate set (SUBSET Blobs)
    clock,         \* discrete time, 0..MaxTime
    bornAt         \* bornAt[b] = clock at which blob b was last put (its mtime)

vars == <<storedBlobs, committed, pub, gcMark, clock, bornAt>>

Phases == {"idle", "started", "blobbed", "committed"}
Age(b) == clock - bornAt[b]

TypeOk ==
    /\ storedBlobs \subseteq Blobs
    /\ committed \subseteq Versions
    /\ pub \in [Writers -> [phase: Phases]]
    /\ gcMark \in {NoMark} \cup (SUBSET Blobs)
    /\ clock \in 0..MaxTime
    /\ bornAt \in [Blobs -> 0..MaxTime]

Init ==
    /\ storedBlobs = {}
    /\ committed = {}
    /\ pub = [w \in Writers |-> [phase |-> "idle"]]
    /\ gcMark = NoMark
    /\ clock = 0
    /\ bornAt = [b \in Blobs |-> 0]

(***************************************************************************)
(* Publish steps (NO lease — protection is grace only)                     *)
(***************************************************************************)

Begin(w) ==
    /\ pub[w].phase = "idle"
    /\ pub' = [pub EXCEPT ![w].phase = "started"]
    /\ UNCHANGED <<storedBlobs, committed, gcMark, clock, bornAt>>

\* store.put(blob w): idempotent; (re-)stamps the blob's mtime to now.
PutBlobs(w) ==
    /\ pub[w].phase = "started"
    /\ storedBlobs' = storedBlobs \cup BlobsOf(w)
    /\ bornAt' = [bornAt EXCEPT ![w] = clock]
    /\ pub' = [pub EXCEPT ![w].phase = "blobbed"]
    /\ UNCHANGED <<committed, gcMark, clock>>

\* commit(version w): records the manifest. Pure grace — no self-check. Safety
\* must come from the blob still being young (Grace > MaxGap), not from checking.
Commit(w) ==
    /\ pub[w].phase = "blobbed"
    /\ committed' = committed \cup {w}
    /\ pub' = [pub EXCEPT ![w].phase = "committed"]
    /\ UNCHANGED <<storedBlobs, gcMark, clock, bornAt>>

\* Abandon mid-publish (crash/abort). The blob stays stored and keeps aging;
\* once it passes Grace with no committed manifest referencing it, GC reclaims it.
Crash(w) ==
    /\ pub[w].phase \in {"started", "blobbed"}
    /\ pub' = [pub EXCEPT ![w].phase = "idle"]
    /\ UNCHANGED <<storedBlobs, committed, gcMark, clock, bornAt>>

\* Time advances, but never past the put->commit deadline of an in-flight publish:
\* a blob may sit un-committed for at most MaxGap ticks. This IS the operational
\* bound d on publish duration; the grace theorem relates Grace to it.
Tick ==
    /\ clock < MaxTime
    /\ \A w \in Writers : pub[w].phase = "blobbed" => clock + 1 <= bornAt[w] + MaxGap
    /\ clock' = clock + 1
    /\ UNCHANGED <<storedBlobs, committed, pub, gcMark, bornAt>>

(***************************************************************************)
(* GC steps — mark snapshots candidates; sweep RE-VALIDATES against now      *)
(* (carried over from GC.tla: idempotent re-put can re-adopt a marked blob).  *)
(***************************************************************************)

LiveBlobs ==
    (UNION {BlobsOf(v) : v \in committed})                 \* referenced by a manifest
      \cup {b \in storedBlobs : Age(b) < Grace}            \* OR still within grace

GCMark ==
    /\ gcMark = NoMark
    /\ gcMark' = storedBlobs \ LiveBlobs
    /\ UNCHANGED <<storedBlobs, committed, pub, clock, bornAt>>

GCSweep ==
    /\ gcMark # NoMark
    /\ storedBlobs' = storedBlobs \ (gcMark \cap (storedBlobs \ LiveBlobs))
    /\ gcMark' = NoMark
    /\ UNCHANGED <<committed, pub, clock, bornAt>>

GCAbort ==
    /\ gcMark # NoMark
    /\ gcMark' = NoMark
    /\ UNCHANGED <<storedBlobs, committed, pub, clock, bornAt>>

Next ==
    \/ \E w \in Writers: Begin(w) \/ PutBlobs(w) \/ Commit(w) \/ Crash(w)
    \/ Tick \/ GCMark \/ GCSweep \/ GCAbort

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Invariant                                                               *)
(***************************************************************************)

\* Every committed manifest has all its blobs present. Grace's whole job here.
BlobSafe == \A v \in committed : BlobsOf(v) \subseteq storedBlobs

=============================================================================
