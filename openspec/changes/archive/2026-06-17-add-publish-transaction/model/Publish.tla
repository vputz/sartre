---------------------------- MODULE Publish ----------------------------
(***************************************************************************)
(* Abstracts sartre's publish transaction (full-replacement, fail-fast).   *)
(*                                                                          *)
(*   publish(coord, files, pointer):                                        *)
(*     start = head(coord, pointer)            \* read current tip          *)
(*     for f in files: store.put(f.bytes)      \* (1) blobs  - idempotent   *)
(*     version = commit(coord, entries)        \* (2) manifest - idempotent *)
(*     set_pointer(..., version, expected=start)  \* (3) CAS, raises Conflict*)
(*                                                                          *)
(* One coordinate / one pointer. Each publisher p publishes a fixed version *)
(* identified with p; that version needs one blob, also identified with p   *)
(* (the blob/manifest payloads are opaque). The datastore is an atomic KV.  *)
(*                                                                          *)
(* PointerFirst is the bug toggle: FALSE models the correct order           *)
(* (blobs -> manifest -> pointer); TRUE advances the pointer first, opening *)
(* a window where the tip references an uncommitted manifest with no blobs. *)
(***************************************************************************)
EXTENDS Naturals, Sequences

CONSTANTS Publishers,    \* set of concurrent publishers, e.g. {p1, p2}
          NoTip,         \* sentinel: pointer not yet set
          PointerFirst   \* BOOLEAN bug toggle (TRUE = wrong order)

Versions == Publishers         \* version published by p is p (opaque id)
Blobs    == Publishers         \* version p needs blob p
BlobsOf(v) == {v}

VARIABLES
    storedBlobs,   \* subset of Blobs physically present (atomic KV, grows only)
    committed,     \* subset of Versions whose manifest is committed
    tip,           \* current pointer value, in Versions \cup {NoTip}
    log,           \* append-only history of pointer tips (the commit log)
    pub            \* pub[p] = [phase |-> ..., start |-> tip-observed-at-begin]

vars == <<storedBlobs, committed, tip, log, pub>>

Phases == {"idle", "started", "advanced", "blobbed", "committed", "done", "failed"}

TypeOk ==
    /\ storedBlobs \subseteq Blobs
    /\ committed \subseteq Versions
    /\ tip \in Versions \cup {NoTip}
    /\ log \in Seq(Versions)
    /\ pub \in [Publishers -> [phase: Phases, start: Versions \cup {NoTip}]]

Init ==
    /\ storedBlobs = {}
    /\ committed = {}
    /\ tip = NoTip
    /\ log = << >>
    /\ pub = [p \in Publishers |-> [phase |-> "idle", start |-> NoTip]]

\* Abstracts: start = head(coord, pointer)
Begin(p) ==
    /\ pub[p].phase = "idle"
    /\ pub' = [pub EXCEPT ![p] = [phase |-> "started", start |-> tip]]
    /\ UNCHANGED <<storedBlobs, committed, tip, log>>

\* Abstracts: store.put(bytes) for each file (idempotent set union)
PutBlobs(p) ==
    /\ pub[p].phase = (IF PointerFirst THEN "advanced" ELSE "started")
    /\ storedBlobs' = storedBlobs \cup BlobsOf(p)
    /\ pub' = [pub EXCEPT ![p].phase = "blobbed"]
    /\ UNCHANGED <<committed, tip, log>>

\* Abstracts: commit(coord, entries) -> version (content-idempotent)
Commit(p) ==
    /\ pub[p].phase = "blobbed"
    /\ committed' = committed \cup {p}
    /\ pub' = [pub EXCEPT ![p].phase = (IF PointerFirst THEN "done" ELSE "committed")]
    /\ UNCHANGED <<storedBlobs, tip, log>>

\* Abstracts: set_pointer(..., version, expected=start) -- atomic compare-and-swap.
\* On match: advance tip and append the log row atomically. On mismatch: fail-fast.
Advance(p) ==
    /\ pub[p].phase = (IF PointerFirst THEN "started" ELSE "committed")
    /\ \/ /\ tip = pub[p].start                  \* CAS succeeds
          /\ tip' = p
          /\ log' = Append(log, p)
          /\ pub' = [pub EXCEPT ![p].phase = (IF PointerFirst THEN "advanced" ELSE "done")]
          /\ UNCHANGED <<storedBlobs, committed>>
       \/ /\ tip # pub[p].start                  \* CAS conflict -> raise, no retry
          /\ pub' = [pub EXCEPT ![p].phase = "failed"]
          /\ UNCHANGED <<storedBlobs, committed, tip, log>>

\* A crash mid-publish: durable state (blobs/manifests/tip/log) persists; the
\* publisher restarts and re-runs from scratch (safe because every step is
\* idempotent). Terminal phases cannot crash.
Crash(p) ==
    /\ pub[p].phase \notin {"idle", "done", "failed"}
    /\ pub' = [pub EXCEPT ![p] = [phase |-> "idle", start |-> NoTip]]
    /\ UNCHANGED <<storedBlobs, committed, tip, log>>

Next == \E p \in Publishers:
            Begin(p) \/ PutBlobs(p) \/ Commit(p) \/ Advance(p) \/ Crash(p)

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Invariants                                                              *)
(***************************************************************************)

\* No reader ever follows a dangling reference: the tip is always a fully
\* committed manifest whose blobs are all stored. This is what the ordering
\* protects -- it is violated in the PointerFirst (wrong-order) model.
PointerSafe ==
    \/ tip = NoTip
    \/ /\ tip \in committed
       /\ BlobsOf(tip) \subseteq storedBlobs

\* The pointer and the append-only log stay consistent: every logged tip was a
\* committed version, and the current tip is the log's last entry.
LogConsistent ==
    /\ \A i \in 1..Len(log): log[i] \in committed
    /\ \/ (tip = NoTip /\ log = << >>)
       \/ (tip # NoTip /\ Len(log) > 0 /\ log[Len(log)] = tip)

=============================================================================
