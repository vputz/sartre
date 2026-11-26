# Stage 8 — Intermediate Zoom-Out (post-revision audit)

Target: revised `review-target.md`. Reviewer: `egm-zoom-out` (zoom-out-standard), given the
baseline context plus the Stage 5 (`zoom_out_report.md`) and Stage 7 (`critic_report.md`) reports,
tasked with auditing whether the comprehension/critic edits introduced any new high-level problem.

## Verdict
The revised document still coheres. The C1–C8 + style edits resolved cleanly; none reopened a
settled decision (field over seed, cut `expected=`, remove `derived_from`, single-pointer
recovery), none introduced a false compromise, and the whole still solves the root problem
directly. The reviewer re-verified against live code that the C2 narrowing is real and correctly
stated (`repository.py:561-600` recovers the base from the version's `derived_from` for any `Ref`
today). **No blockers.**

## Findings (confined to what the revisions changed)

### 1 [Observation — no action] `pointer_base` scope growth is justified
The R2/C1 edits grew the change from "add an optional field" to "add a field **plus** a new
`Registry` Protocol method across four backends." The reviewer sanity-checked this is proportionate,
not creep: it lands on the live S3 read path and replaces an O(all-moves-in-coordinate) scan with a
single-pointer read; no existing port method returns a pointer's advance-move base; and D2 now
weighs the method against the scoped-`list_pointer_history` alternative. Awareness point only: the
change now modifies the port Protocol, not just a dataclass field (already reflected in the
capabilities list).

### 2 [Minor — fixed] C5 tombstone rationale named the wrong trigger
My C5 edit justified `pointer_base`'s tombstone-skipping by "the stage→promote→delete cycle and
idempotent re-runs" — but neither routes through `pointer_base` with a tombstone above the advance
(promote runs while the pointer is live; an idempotent re-run re-stages a fresh advance and
short-circuits at the step-2 no-op). The genuine trigger is a **concurrent `delete_pointer` racing
between `promote`'s alias resolution (D4 step 1) and its `pointer_base` read (D4 step 3a)** — a
TOCTOU where the staged version still exists and its base is still correct, so promote can still
complete. **Fix applied:** D1/D2 now name the concurrent-delete race (and frame tombstone-skipping
as a standalone port robustness property), and D5 now requires the model to interleave a concurrent
delete in that window.

### 3 [Nit — fixed] Proposal still carried pre-C6 `derived_from` phrasing
The proposal's What-Changes bullet still said removal is "a change to the metadata a reader sees"
(true only for new versions), inconsistent with D6's forward-only framing. **Fix applied:** the
proposal bullet now states removal is forward-only (existing manifests keep stale `derived_from`;
the protection is that nothing reads it).

## Soundness re-checks that held after the revisions
- C3's step-5 retry is internally consistent with the step-2 pre-CAS no-op and does not weaken the
  lost-update guard; What-Changes and Risks describe the retryable-`Conflict` residual consistently.
- C2's narrowing is stated in What-Changes, D4 step 3b, and Risks, and matches the code it narrows.
- C1's `pointer_base` is surfaced in What-Changes, the port + three backend capabilities, and
  Impact — an implementer scoping from the proposal will not miss the new read operation.
- C7 resolved (no dangling "F3").

## Status
Stage 8 clean after two minor post-audit fixes. With no implementation-plan range in the document,
Stage 9 (implementation-readiness) has no target and is skipped — Stage 8 concludes the review.
