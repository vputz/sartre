# Stage 7 — Critic Review (design / structural / style)

Target: `review/fix-promote-base-from-staging-pointer/review-target.md`, with baseline context
(current `repository-facade` + `registry-port` specs, `repository.py`; reviewers also read
`ports.py` and `s3.py`). Reviewers: `egm-design` + `egm-structural` (feature-spec-standard) and
`egm-style`. No blockers to the architecture; the core fix is confirmed correct against the S3
implementation. Three substantive Majors to resolve before implementation planning, plus minors
and a batch of style tightenings.

## Majors (fix before Stage 8 / before writing remaining specs)

### C1 — The new `pointer_base` port method is not surfaced in the proposal (design#1 + structural M1)
`pointer_base(coord, name) -> Version | None` was introduced in design D2 (the R2 performance
fix) as a brand-new `Registry` Protocol method with per-backend implementations, and D4 makes it
the sole recovery path. But the proposal's "What Changes", the `registry-port` capability bullet,
the three backend capability bullets, and Impact→Code all mention only the `base` *field* /
`set_pointer`. An engineer scoping from the proposal would add a field and miss a whole new
cross-backend read operation + its spec requirement + differential tests. **Fix:** add
`pointer_base` to What Changes, the `registry-port` modified capability (with a scenario), each
backend entry (they must implement the read path), and the Impact `ports.py` line. Design-critic
nit: also weigh the *method* shape against a scoped `list_pointer_history(coord, name=...)`
variant in D2 (the Alternatives Rule wants the new method's shape justified, not just the field).

### C2 — `promote` with a non-Alias `staged` ref (Pin/Head) is an unstated behavior narrowing (design#2)
Today `promote` recovers the base from the resolved *version's* `derived_from`, so it works for
any `Ref` — `Pin(version)`, `Head`, or `Alias`. The new design keys the base to a *pointer* and
D4 gates recovery on "when `staged` is an `Alias`". For `Pin`/`Head` there is no staging pointer,
so recovery falls through to refuse-unless-`force`. This is a real narrowing (a `Pin`-by-version
promote that worked before now refuses) and the design never states it. **Fix (recommended):**
state that safe base-CAS promotion requires passing the staging **alias**; `Pin`/`Head` refs have
no recoverable base and therefore require `force` (last-writer) or are refused. Rewrite the spec
scenario "Promote refuses a version with no recorded base" from the `derived_from` condition to
the pointer-based condition (including "staged is not an alias"). Low real impact — the documented
workflow (and the QS1 client) always promotes via the staging alias — but it must be specified.

### C3 — No-op-if-equal is not atomic with the base-CAS; the "duplicate publish = no-op success" guarantee is overstated (design#3)
D4 step 2 (read head; if already == target, succeed) and step 4 (CAS `expected=base`) are
separate. A concurrent duplicate promote that lands *between* them advances head to the target
version; our step-4 CAS (`expected=base`, `base != version`) then fails with `Conflict` even
though head is now exactly where we wanted it. Safe (no lost update) and retryable, but the stated
"not a conflict" guarantee only holds when the duplicate finishes before step 2. **Fix:** on a
step-4 `Conflict`, re-read head; if it now equals the target version, convert to a no-op success;
otherwise propagate `Conflict`. Update D4, the promote spec requirement, and D5 (C4).

## Minors

- **C4 (design#4):** D5 must model the no-op head-read and the CAS as two distinct atomic steps
  with an interleaving publish between them, or it verifies an idealized atomic promote the
  implementation does not provide and misses the C3 window.
- **C5 (design#5):** `pointer_base` must read the base off the newest *advance* event even when a
  later deletion tombstone exists (alias *resolution* stops at a tombstone and never looks below).
  So "the same tail read resolution performs" is inaccurate once the staging pointer is deleted;
  soften that phrasing, specify the tombstone-skipping semantics, and add a "recover base after
  the staging pointer was deleted" scenario (D1 already leans on recovery surviving deletion).
- **C6 (design#6):** Removing `derived_from` is forward-only — content-addressed manifests are
  never rewritten, so every already-committed manifest keeps its stale `derived_from` forever.
  The real mitigation is that `promote` no longer reads it at all; state that (and that external
  readers must stop trusting it entirely, not expect it absent), rather than framing removal as
  "a change to the metadata a reader sees" (true only for new versions).
- **C7 (structural m1):** D4 has a dangling internal reference "F3" (no F-list exists in the doc;
  it pointed at review findings). Drop the `F3/` token.
- **C8 (structural m2):** The `version-log` modified capability has weak traceability — tie it to
  a decision (D2/D3) rather than only alluding to it in a Risks bullet.
- **n (structural n2):** The baseline `promote` spec omits `force` (pre-existing spec/code drift);
  our spec delta includes `force`, so archiving this change reconciles it — no extra action.

## Style (batch — mostly a side effect of iterative editing)
- **S1/S2 [Major]:** Two facts are each repeated ~5–6×: "only S3 is in active use" (Impact, D2,
  D3, D3-note, two Risks) and "no signature change / caller passes nothing" (What Changes ×2,
  Impact, Goals, D4). State each once (D3 and D4 respectively) and cross-reference.
- **S3 [Major]:** The Risks section re-states decided material (with "see D2/D4", "see D6"
  pointers that signal the redundancy). Collapse to residual exposure + cross-refs.
- **S4 [Major, reconcile with C3]:** The "No-op could mask a re-promote" risk is self-negating as
  written — but C3 shows the no-op is *not* trivially safe (the step2→step4 window yields a
  retryable conflict). Replace this entry with the real residual: a concurrent duplicate can
  surface a retryable `Conflict` that a retry resolves to a no-op.
- **S5 [Nit]:** "near-impossible in practice" is uncalibrated; replace with the concrete reason
  (staging pointers are created and deleted within one publish).
- **S6 [Major]:** D5 closes with "Verified via the tla-verifier agent" while describing the model
  as to-be-extended — contradictory. The model is not written yet; phrase it as the intended gate
  ("to be verified via …"), not as done.
- **S7–S14 [Nits]:** "self-documenting"/"clean"/"trap"/"rots"/"smuggle" → neutral wording; expand
  "CAS" at first use and avoid "CAS-es" as a verb; use "field" at the abstract level (not
  "datum"); de-duplicate the version-number question (keep in Open Questions); trim the design
  Context's near-full restatement of proposal Why; drop the "an earlier draft" self-reference.

## Disposition
C1–C3 are the substance and should be resolved now. C4–C8 and S1–S6 are clear, low-risk edits.
S7–S14 are register polish. All are compatible with the settled decisions (field over seed, cut
`expected=`, remove `derived_from`, single-pointer recovery) — none reopen them.
