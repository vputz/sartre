------------------------------ MODULE S3Drop ------------------------------
(***************************************************************************)
(* The one race S3's lack of transactions reopens: dropping an out-of-       *)
(* retention version `v` while a promote points at `v`. The SQL backend is    *)
(* serialized by row visibility; a transaction-less object store is not, so    *)
(* GC (drop) and set_pointer (promote) interleave freely and neither can        *)
(* retract: an appended pointer event is IMMUTABLE (append-only), so a          *)
(* promoter cannot "un-point" once its event lands.                             *)
(*                                                                             *)
(* Because the dangling event cannot be prevented, the closure makes it         *)
(* HARMLESS: the dropper writes the tombstone BEFORE deleting the manifest,     *)
(* and resolve/head SKIP a tombstoned version (a tail event pointing at a        *)
(* tombstoned version reverts to the prior value / not-found). Then a           *)
(* reader never observes a pointer resolving to a reclaimed manifest.           *)
(*                                                                             *)
(* Two toggles pin down that BOTH halves are load-bearing:                      *)
(*   SkipTombstoned : resolve ignores tombstoned versions.                      *)
(*   TombstoneFirst : the manifest is deleted only after the tombstone exists.  *)
(* The three configs:                                                           *)
(*   ok        (T,T) => Safe holds, non-vacuously (a promote can succeed AND    *)
(*                      GC can reclaim).                                         *)
(*   bad_noskip (F,T) => Safe BREAKS: resolve returns a dangling version.       *)
(*   bad_order  (T,F) => Safe BREAKS: manifest vanishes with no tombstone, so   *)
(*                      even a skip-aware reader cannot know to skip it.         *)
(***************************************************************************)
EXTENDS Naturals

CONSTANTS SkipTombstoned,   \* TRUE: resolve skips a tombstoned version
          TombstoneFirst    \* TRUE: delete-manifest requires the tombstone to exist first

VARIABLES
    manifestExists,   \* v's manifest object is present
    tombstoned,       \* v's tombstone marker is present
    ptr,              \* the pointer's tail event target: "none" or "v"
    prom,             \* promoter phase
    drop              \* dropper (GC) phase

vars == <<manifestExists, tombstoned, ptr, prom, drop>>

PromPhases == {"idle", "checked", "pointed"}
DropPhases == {"idle", "checked", "tombstoned", "deleted"}

(* What a reader actually resolves the pointer to. With SkipTombstoned, a tail  *)
(* event targeting a tombstoned version is ignored (reverts to "none").         *)
EffectiveTarget ==
    IF ptr = "v" /\ (~SkipTombstoned \/ ~tombstoned) THEN "v" ELSE "none"

TypeOk ==
    /\ manifestExists \in BOOLEAN
    /\ tombstoned \in BOOLEAN
    /\ ptr \in {"none", "v"}
    /\ prom \in PromPhases
    /\ drop \in DropPhases

Init ==
    /\ manifestExists = TRUE     \* v is committed (out of retention, droppable)
    /\ tombstoned = FALSE
    /\ ptr = "none"              \* v is not currently a pointer target
    /\ prom = "idle"
    /\ drop = "idle"

(***************************************************************************)
(* Promoter: set_pointer(name, v). Checks then appends — two steps, so the    *)
(* check and the (immutable) append straddle any concurrent drop.             *)
(***************************************************************************)

\* Refuses a version whose manifest is gone or already tombstoned.
PromCheck ==
    /\ prom = "idle"
    /\ manifestExists
    /\ ~tombstoned
    /\ prom' = "checked"
    /\ UNCHANGED <<manifestExists, tombstoned, ptr, drop>>

\* Appends the pointer event. Append-only: this cannot be retracted afterward.
PromPoint ==
    /\ prom = "checked"
    /\ ptr' = "v"
    /\ prom' = "pointed"
    /\ UNCHANGED <<manifestExists, tombstoned, drop>>

(***************************************************************************)
(* Dropper (GC): reclaim v. Checks no current target, tombstones, deletes.    *)
(***************************************************************************)

\* GC candidate: v is not a current (resolvable) pointer target.
DropCheck ==
    /\ drop = "idle"
    /\ EffectiveTarget # "v"
    /\ drop' = "checked"
    /\ UNCHANGED <<manifestExists, tombstoned, ptr, prom>>

\* Write the tombstone (put-if-absent) BEFORE touching the manifest.
DropTombstone ==
    /\ drop = "checked"
    /\ tombstoned' = TRUE
    /\ drop' = "tombstoned"
    /\ UNCHANGED <<manifestExists, ptr, prom>>

\* Delete the manifest. With TombstoneFirst, only once the tombstone exists.
DropDelete ==
    /\ drop \in {"checked", "tombstoned"}
    /\ (TombstoneFirst => tombstoned)
    /\ manifestExists' = FALSE
    /\ drop' = "deleted"
    /\ UNCHANGED <<tombstoned, ptr, prom>>

Next ==
    \/ PromCheck \/ PromPoint
    \/ DropCheck \/ DropTombstone \/ DropDelete

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Invariant: a reader never resolves the pointer to a reclaimed manifest.   *)
(***************************************************************************)
Safe == (EffectiveTarget = "v") => manifestExists

=============================================================================
