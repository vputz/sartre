# Stage 5 — Initial Zoom-Out Review

Target: `review/fix-promote-base-from-staging-pointer/review-target.md` (proposal + design).
Reviewers: `egm-zoom-out` (zoom-out-standard) and `egm-inventive-catalyst` (Mode: REVIEW),
run in isolation with baseline = current `repository-facade` spec, `registry-port` spec, and
`src/sartre/repository.py`. Two rounds: an initial pass and one re-audit after revisions.

## Round 1 (initial) — decisions taken

Both reviewers confirmed the bug and endorsed the structural direction (base belongs on the
non-deduplicated pointer-move plane). Findings F1–F5 drove these decisions, now applied:
- **F1** — keep the explicit `base` field (not the seed-the-staging-pointer alternative).
  Decided for lower concurrency risk in a bug-fix release; only S3 is in active use, so the
  avoided SQL migration is one we would not run in prod anyway. Seed alternative recorded + why
  rejected (design D1).
- **F2** — cut the optional `expected=` override (overlaps existing `Repository.point(...,
  expected=)`).
- **F3** — remove `derived_from` from manifest metadata entirely (design D6).
- **F4/F5** — pre-change-straddle recovery (re-stage or `force`) named; no-op-before-base
  ordering stated.

(Full Round 1 detail preserved in git history of this file / the change's edit trail.)

## Round 2 (re-audit) — verdict and findings

Both reviewers independently cleared the revised decisions as sound and mutually consistent,
and confirmed the seed alternative was rejected on correct grounds. Also confirmed: encoding
the base in the staging pointer *name* is a dead end (a `Version` is an opaque `sha256:<hex>`
id whose `:` is forbidden in alias segments and which must not be parsed) — which is itself a
point in favor of the explicit-field choice. Four refinements:

### R1 [MAJOR — contradiction, now fixed] Proposal capabilities list still advertised `expected=`
Source: zoom-out. The "Modified Capabilities" bullet for `repository-facade` still said promote
"gains an optional `expected=` override" — a leftover contradicting the cut (Non-Goals, D4,
"No signature change"). As written it would instruct the implementer to add the rejected
surface. **Fix applied:** removed that clause from the proposal.

### R2 [MAJOR — performance, now fixed in design] Recover base from the staging pointer alone, not whole-coordinate history
Source: inventive-catalyst. D4 said promote recovers the base via `list_pointer_history`, which
on S3 (`s3.py` `_all_events`) lists *every* pointer under the coordinate and GETs *every* event
of *every* pointer — O(all moves in the coordinate). This lands on the exact read path hardened
in 0.2.2, and promote needs only one datum: the staging pointer's own advance move. **Fix
applied (design):** promote recovers the base from that single pointer's current advance event
via a focused `registry.pointer_base(coord, name)` lookup (one pointer read on every backend —
the same order of cost as resolving the alias it already does), never a whole-coordinate scan.
The staging pointer is content-named, ephemeral, and single-advance, so its newest non-deletion
event always carries this operation's base.

### R3 [MINOR — now folded into R2] State the base-recovery selection rule
Source: zoom-out. With multiple history rows for one staging-pointer name (re-runs, or
create→delete→re-derive), which row's `base` is authoritative was unstated. Safe in practice
(the name encodes the base, so every advance for that name carries the same base), but the rule
is now stated: the pointer's current (newest non-deletion) advance move.

### R4 [NIT — decided: keep staging-only + document] `base` is a move-kind-dependent nullable
Source: inventive-catalyst. `base` is populated only for staging advances (`None` elsewhere,
where `from_version` already is the CAS base), so its meaning forks on move kind. The reviewer
offered a uniform `cas_base` on every move (the version each move CAS'd against) to remove the
fork. **Decision:** keep the staging-only field and state in-spec that `base` is defined only
for staging advances and MUST NOT be consulted for other move kinds — this mirrors the existing
move-kind-dependent `to_version` (null = deletion), and a uniform `cas_base` would redundantly
duplicate `from_version` on every non-staging move forever. Alternative recorded in design D2.

### R5 [NIT — now noted in design] Field cost lands on unused backends
Source: zoom-out. The explicit-field choice spends a SQL migration + a replicated field on
backends (SQLite/Postgres) not in production use. **Fix applied:** design D3 now states this
cost is to keep the registry-port contract and the differential-test oracle uniform, and is the
accepted price of the explicit-field choice over the seed alternative.

## Soundness checks both reviewers cleared
- No-op-before-base (D4 step 2) does not weaken the lost-update guard (head-already-at-target is
  the same tree, not a different one); it is what neutralizes the dedup-collision false conflict.
- Removing the `derived_from` fallback entirely (D6) is correct, not over-correction — keeping it
  would reintroduce the bug for deduped/reverted trees.
- The new nullable field reuses the established `to_version` cross-backend precedent; no new
  mechanism invented. `ADD COLUMN` is genuinely lighter than the `to_version` NOT-NULL-drop
  rebuild.
- TLA+ coverage (D5) is proportionate for a concurrency bug.

## Status
All five Round-2 findings addressed (R1/R2/R5 by edits, R3 folded into R2, R4 decided). The
Stage 5 re-audit budget (one per stage) is now spent; further changes would be accepted
trade-offs rather than another reviewer pass.
