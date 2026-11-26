----------------------------- MODULE Promote -----------------------------
(***************************************************************************)
(* `promote` advances head to a staged version V, compare-and-swapping      *)
(* against the base V was derived from. The bug being fixed is WHERE that    *)
(* base comes from, plus two concurrency refinements the review added.       *)
(*                                                                          *)
(* head starts at TrueBase — the version the stage derived from — so a       *)
(* correct promote SHOULD advance head to V (Staged). V's base is recorded   *)
(* in two places that can disagree when the staged tree already exists:      *)
(*                                                                          *)
(*   BaseSource = "pointer"  : the staging pointer's own move records the    *)
(*                             operation's TRUE base (PointerBase=TrueBase). *)
(*                             Correct — the pointer is per-operation.       *)
(*   BaseSource = "manifest" : the content-addressed manifest's metadata,    *)
(*                             which under dedup is the FIRST writer's base   *)
(*                             (ManifestBase=Other # TrueBase). Wrong.       *)
(*                                                                          *)
(* Concurrency toggles under test:                                          *)
(*   TombstoneSkip        : pointer_base reads the base past a later         *)
(*                          deletion tombstone. FALSE => a staging pointer   *)
(*                          deleted (racing) between resolve and the base    *)
(*                          read yields "no base".                           *)
(*   RecheckAfterConflict : on a base-CAS Conflict, re-read head and treat   *)
(*                          head-already-at-target as success. FALSE => a    *)
(*                          duplicate publish landing between the no-op check *)
(*                          and the CAS surfaces a spurious Conflict.        *)
(*                                                                          *)
(* Two bad outcomes are tracked. lostUpdate: head advanced to V when head    *)
(* was not V's true base (silent overwrite — the hard safety guard).         *)
(* spurious: promote refused when a correct promote would have advanced      *)
(* (head at the true base) or no-op'd (head already at V) — the reported     *)
(* production symptom (a false Conflict). A refusal is legitimate only when  *)
(* head moved to a DIFFERENT tree (Diff).                                    *)
(*                                                                          *)
(* Configs: ok (pointer, skip, recheck) holds non-vacuously even under       *)
(* Dedup; bad_manifest, bad_notombskip, bad_norecheck each break Safe.       *)
(***************************************************************************)
EXTENDS Integers

CONSTANTS BaseSource,            \* "pointer" (correct) or "manifest" (buggy under dedup)
          TombstoneSkip,         \* TRUE: pointer_base reads past a deletion tombstone
          RecheckAfterConflict,  \* TRUE: re-read head after a CAS conflict (no-op if at target)
          Dedup                  \* TRUE: the staged tree already exists; manifest base differs

TrueBase == 1    \* the version head was at when the stage derived V (V's true base)
Staged   == 2    \* V, the staged version promote advances head to
Other    == 3    \* the first writer's base recorded in V's shared manifest (under dedup)
Diff     == 4    \* a different tree a concurrent publish can move head to

Versions == {TrueBase, Staged, Other, Diff}

ManifestBase == IF Dedup THEN Other ELSE TrueBase
PointerBase  == TrueBase

VARIABLES
    head,            \* current head version
    stagingDeleted,  \* has the staging pointer been (concurrently) deleted?
    pub,             \* one concurrent publish: "none" | "diff" | "dup"
    pphase,          \* promote phase: "idle" -> "checked" -> "based" -> "done"
    pexp,            \* base promote read to CAS against (-1 = not yet read)
    lostUpdate,      \* safety: advanced over a wrong base
    spurious         \* correctness: refused a promote that should have succeeded

vars == <<head, stagingDeleted, pub, pphase, pexp, lostUpdate, spurious>>

TypeOk ==
    /\ head \in Versions
    /\ stagingDeleted \in BOOLEAN
    /\ pub \in {"none", "diff", "dup"}
    /\ pphase \in {"idle", "checked", "based", "done"}
    /\ pexp \in ({-1} \cup Versions)
    /\ lostUpdate \in BOOLEAN
    /\ spurious \in BOOLEAN

Init ==
    /\ head = TrueBase
    /\ stagingDeleted = FALSE
    /\ pub = "none"
    /\ pphase = "idle"
    /\ pexp = -1
    /\ lostUpdate = FALSE
    /\ spurious = FALSE

(* Step 2: no-op if head already at the target version; else proceed. *)
PNoop ==
    /\ pphase = "idle"
    /\ pphase' = IF head = Staged THEN "done" ELSE "checked"
    /\ UNCHANGED <<head, stagingDeleted, pub, pexp, lostUpdate, spurious>>

(* Step 3: recover the base. The pointer source may see "no base" only if the  *)
(* staging pointer was deleted and tombstone-skipping is off.                   *)
PReadBase ==
    /\ pphase = "checked"
    /\ IF (BaseSource = "pointer" /\ stagingDeleted /\ ~TombstoneSkip)
         THEN /\ pphase' = "done"                 \* no base -> refuse
              /\ spurious' = (head # Diff)
              /\ UNCHANGED pexp
         ELSE /\ pexp' = (IF BaseSource = "pointer" THEN PointerBase ELSE ManifestBase)
              /\ pphase' = "based"
              /\ UNCHANGED spurious
    /\ UNCHANGED <<head, stagingDeleted, pub, lostUpdate>>

(* Steps 4 and 5: CAS on the recovered base; on conflict, optionally re-read. *)
PCas ==
    /\ pphase = "based"
    /\ IF head = pexp
         THEN /\ head' = Staged
              /\ lostUpdate' = (lostUpdate \/ (head # TrueBase))
              /\ pphase' = "done"
              /\ UNCHANGED <<stagingDeleted, pub, pexp, spurious>>
         ELSE /\ pphase' = "done"
              /\ spurious' = IF (RecheckAfterConflict /\ head = Staged)
                               THEN spurious                      \* step 5: no-op success
                               ELSE (spurious \/ (head # Diff))   \* refuse (spurious unless head moved away)
              /\ UNCHANGED <<head, stagingDeleted, pub, pexp, lostUpdate>>

(* A concurrent publish moves head to a different tree. *)
PubDiff ==
    /\ pub = "none"
    /\ pphase # "done"
    /\ head' = Diff
    /\ pub' = "diff"
    /\ UNCHANGED <<stagingDeleted, pphase, pexp, lostUpdate, spurious>>

(* A concurrent DUPLICATE publish of the same tree advances head to V. *)
PubDup ==
    /\ pub = "none"
    /\ pphase # "done"
    /\ head' = Staged
    /\ pub' = "dup"
    /\ UNCHANGED <<stagingDeleted, pphase, pexp, lostUpdate, spurious>>

(* A concurrent delete of the staging pointer (TOCTOU with the base read). *)
Del ==
    /\ ~stagingDeleted
    /\ pphase # "done"
    /\ stagingDeleted' = TRUE
    /\ UNCHANGED <<head, pub, pphase, pexp, lostUpdate, spurious>>

Next == PNoop \/ PReadBase \/ PCas \/ PubDiff \/ PubDup \/ Del
Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Safe: promote never advances over a wrong base (no silent lost update),   *)
(* and never refuses a promote that should have advanced or no-op'd.          *)
(***************************************************************************)
Safe == (~lostUpdate) /\ (~spurious)

=============================================================================
