--------------------------- MODULE PublishOver ---------------------------
(***************************************************************************)
(* The lost-update race in `publish_over`. A derive reads its base at one    *)
(* instant and advances the pointer at a later instant; a plain publish can   *)
(* move the pointer in between. Whether that is safe hinges entirely on what   *)
(* the advancing compare-and-swap uses as `expected`.                          *)
(*                                                                            *)
(*   UseBaseExpected = TRUE  : CAS expected := the version the derive READ as  *)
(*                             its base  → a moved pointer makes the CAS fail   *)
(*                             (Conflict), so no stale write. SAFE.            *)
(*   UseBaseExpected = FALSE : CAS expected := the pointer's CURRENT value at  *)
(*                             advance time (a freshly-read head) → the CAS     *)
(*                             always passes, overwriting the concurrent        *)
(*                             publish with content derived from a stale base.  *)
(*                             LOST UPDATE.                                    *)
(*                                                                            *)
(* This is exactly the interleaving NOT covered by the "two writers, same      *)
(* expected, one wins" set_pointer result: here the derive's `expected` is     *)
(* read fresher than its base. `publish` is unaffected (full replacement — it  *)
(* legitimately advances from whatever is current); only a *derive* must pin   *)
(* its advance to the base it inherited from.                                  *)
(***************************************************************************)
EXTENDS Integers

CONSTANTS UseBaseExpected,   \* TRUE: CAS on the base version; FALSE: on fresh head
          MaxVer             \* version-id horizon (bounds the state space)

VARIABLES
    head,       \* the pointer's current version id
    nextVer,    \* next version id to hand out
    dphase,     \* deriver phase: "idle" -> "based" -> "done"
    dbase,      \* the version the deriver read as its base (-1 = none yet)
    lost        \* set TRUE if the deriver ever advanced while its base was stale

vars == <<head, nextVer, dphase, dbase, lost>>

TypeOk ==
    /\ head \in 0..MaxVer
    /\ nextVer \in 1..(MaxVer + 1)
    /\ dphase \in {"idle", "based", "done"}
    /\ dbase \in (0..MaxVer) \cup {-1}
    /\ lost \in BOOLEAN

Init ==
    /\ head = 0
    /\ nextVer = 1
    /\ dphase = "idle"
    /\ dbase = -1
    /\ lost = FALSE

(* A plain publish: a fresh full version becomes head, from whatever is current. *)
Publish ==
    /\ nextVer <= MaxVer
    /\ head' = nextVer
    /\ nextVer' = nextVer + 1
    /\ UNCHANGED <<dphase, dbase, lost>>

(* Deriver reads its base (resolve(base)). *)
Base ==
    /\ dphase = "idle"
    /\ dbase' = head
    /\ dphase' = "based"
    /\ UNCHANGED <<head, nextVer, lost>>

(* Deriver advances the pointer to its derived version via CAS on `expected`. *)
Advance ==
    /\ dphase = "based"
    /\ nextVer <= MaxVer
    /\ LET expected == IF UseBaseExpected THEN dbase ELSE head IN
        IF head = expected
          THEN /\ head' = nextVer               \* the derived version wins the CAS
               /\ nextVer' = nextVer + 1
               /\ lost' = (lost \/ (dbase # head))  \* did we overwrite a newer head?
               /\ dphase' = "done"
               /\ UNCHANGED dbase
          ELSE /\ dphase' = "done"              \* Conflict: pointer moved, no write
               /\ UNCHANGED <<head, nextVer, dbase, lost>>

Next == Publish \/ Base \/ Advance
Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Invariant: the deriver never advances the pointer over a version its base *)
(* did not include — i.e. no silent lost update.                             *)
(***************************************************************************)
Safe == lost = FALSE

=============================================================================
