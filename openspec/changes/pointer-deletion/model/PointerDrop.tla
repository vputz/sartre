---------------------------- MODULE PointerDrop ----------------------------
(***************************************************************************)
(* The race pointer DELETION reopens on the transaction-less object store:   *)
(* delete_pointer (append a tombstone event) racing a concurrent set_pointer  *)
(* that advances / re-creates the SAME pointer. A pointer is an append-only    *)
(* stream of immutable events; each writer computes seq+1 from the tail it     *)
(* read and lands it with put-if-absent, so at most one writer wins a given    *)
(* slot and the loser must re-read and re-check (a CAS retry, i.e. Conflict    *)
(* when its expected value is now stale).                                      *)
(*                                                                             *)
(* This is NOT the S3Drop race (that one is version/manifest-keyed). Here the  *)
(* subject is the pointer's own event stream, and the two design choices under *)
(* test are:                                                                   *)
(*   SkipTombstonedTail  : a reader whose tail event is a tombstone resolves   *)
(*                         the pointer to "none" (deleted), NOT the value below *)
(*                         it. (FALSE => a deleted pointer still reads stale.)  *)
(*   TombstoneIsCasEvent : the tombstone is appended THROUGH the gap-free      *)
(*                         seq+1 put-if-absent CAS, so it is part of the single *)
(*                         append-only order. (FALSE => the tombstone is an     *)
(*                         out-of-band marker outside that order, so a delete   *)
(*                         and a concurrent advance have no coherent            *)
(*                         serialization.)                                      *)
(*                                                                             *)
(* The log is the single source of truth; the correct answer is: a tombstone   *)
(* tail means "none", otherwise the tail version. Safe == the implementation's *)
(* resolve equals that. Three configs:                                         *)
(*   ok         (T,T) => Safe holds, non-vacuously (delete, advance, and       *)
(*                       delete-then-recreate all reachable and coherent).      *)
(*   bad_noskip (F,T) => Safe BREAKS: a tombstoned-tail pointer reads the       *)
(*                       stale value below the tombstone.                       *)
(*   bad_order  (T,F) => Safe BREAKS: an out-of-band tombstone is not in the    *)
(*                       append-only order, so it collides incoherently with a  *)
(*                       concurrent advance (torn outcome).                     *)
(***************************************************************************)
EXTENDS Naturals, Sequences

CONSTANTS SkipTombstonedTail,   \* TRUE: resolve treats a tombstone tail as "deleted"
          TombstoneIsCasEvent   \* TRUE: the tombstone is a seq+1 CAS append (in the order)

MaxLen == 3   \* v0 + at most two further appends (advance / delete / recreate)

VARIABLES
    log,        \* the append-only event stream: Seq of {"v0","v1","tomb"}, head = seq 0
    tombFlag,   \* an out-of-band tombstone marker (used only when ~TombstoneIsCasEvent)
    setter,     \* advancing writer phase
    setLen,     \* log length the setter observed at its check (its seq basis)
    deleter,    \* deleting writer phase
    delLen      \* log length the deleter observed at its check

vars == <<log, tombFlag, setter, setLen, deleter, delLen>>

Phases == {"idle", "checked", "done", "failed"}

TailEv(s) == s[Len(s)]   \* last (newest) event; NB not Sequences.Tail

\* Largest index of a non-tombstone event (for a non-skipping read-through); "none" if all tomb.
LastNonTomb(s) ==
    LET idxs == {i \in 1..Len(s) : s[i] # "tomb"}
    IN  IF idxs = {} THEN "none"
        ELSE s[CHOOSE m \in idxs : \A j \in idxs : m >= j]

LT == TailEv(log)

\* What a reader actually returns, honoring the two toggles.
BaseResolve ==
    IF LT = "tomb"
      THEN (IF SkipTombstonedTail THEN "none" ELSE LastNonTomb(log))
      ELSE LT
Resolve ==
    IF tombFlag /\ SkipTombstonedTail THEN "none" ELSE BaseResolve

\* The single-source-of-truth answer: the append-only order alone decides.
CorrectResolve == IF LT = "tomb" THEN "none" ELSE LT

TypeOk ==
    /\ log \in Seq({"v0", "v1", "tomb"})
    /\ Len(log) \in 1..MaxLen
    /\ tombFlag \in BOOLEAN
    /\ setter \in Phases
    /\ deleter \in Phases
    /\ setLen \in 0..MaxLen
    /\ delLen \in 0..MaxLen

Init ==
    /\ log = <<"v0">>            \* pointer currently at v0
    /\ tombFlag = FALSE
    /\ setter = "idle"
    /\ setLen = 0
    /\ deleter = "idle"
    /\ delLen = 0

(***************************************************************************)
(* Setter: set_pointer advancing v0 -> v1. Check the tail equals its         *)
(* expected (v0), then land v1 at the next slot iff nobody else took it.      *)
(***************************************************************************)
SetCheck ==
    /\ setter = "idle"
    /\ LT = "v0"                 \* expected == v0
    /\ setter' = "checked"
    /\ setLen' = Len(log)
    /\ UNCHANGED <<log, tombFlag, deleter, delLen>>

SetWrite ==
    /\ setter = "checked"
    /\ IF Len(log) = setLen /\ Len(log) < MaxLen
         THEN /\ log' = Append(log, "v1")   \* won the seq slot
              /\ setter' = "done"
         ELSE /\ setter' = "failed"          \* slot taken -> stale expected -> Conflict
              /\ UNCHANGED log
    /\ UNCHANGED <<tombFlag, setLen, deleter, delLen>>

(***************************************************************************)
(* Deleter: delete_pointer (expected v0). Check the tail, then delete.        *)
(* With TombstoneIsCasEvent the delete is an in-order seq+1 append; without    *)
(* it, an out-of-band marker with no ordering guard.                          *)
(***************************************************************************)
DelCheck ==
    /\ deleter = "idle"
    /\ LT = "v0"                 \* expected == v0
    /\ deleter' = "checked"
    /\ delLen' = Len(log)
    /\ UNCHANGED <<log, tombFlag, setter, setLen>>

DelWrite ==
    /\ deleter = "checked"
    /\ IF TombstoneIsCasEvent
         THEN IF Len(log) = delLen /\ Len(log) < MaxLen
                THEN /\ log' = Append(log, "tomb")   \* won the seq slot
                     /\ deleter' = "done"
                     /\ UNCHANGED tombFlag
                ELSE /\ deleter' = "failed"           \* slot taken -> Conflict
                     /\ UNCHANGED <<log, tombFlag>>
         ELSE /\ tombFlag' = TRUE                     \* out-of-band, no ordering guard
              /\ deleter' = "done"
              /\ UNCHANGED log
    /\ UNCHANGED <<setter, setLen, delLen>>

(***************************************************************************)
(* Recreate: a set_pointer with expected=none re-creating a tombstoned        *)
(* pointer (the tombstone is a tail state, not a lock).                       *)
(***************************************************************************)
Recreate ==
    /\ LT = "tomb"
    /\ Len(log) < MaxLen
    /\ log' = Append(log, "v1")
    /\ UNCHANGED <<tombFlag, setter, setLen, deleter, delLen>>

Next ==
    \/ SetCheck \/ SetWrite
    \/ DelCheck \/ DelWrite
    \/ Recreate

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Invariant: the implementation's resolve always matches the single         *)
(* append-only order's coherent answer -- a deleted pointer never reads a      *)
(* stale value, and a delete racing an advance never yields a torn outcome.    *)
(***************************************************************************)
Safe == Resolve = CorrectResolve

=============================================================================
