# Stage 6 — Comprehension Test

Target: `review/fix-promote-base-from-staging-pointer/review-target.md`, read **alone** (no
baseline context, no document standard) by `egm-comprehension`. The paraphrases below are the
reviewer's, verbatim, and come first per the stage protocol.

## Reviewer paraphrase 1 — "What this proposes"

The change fixes a bug in `Repository.promote`. Today, when `publish_over(stage=True)` stages a
tree for publication, it records the "derive base" (the head version the new tree was derived
from) as a `derived_from` field inside the content-addressed manifest metadata, and `promote`
later reads that field to perform a compare-and-swap (CAS) of head. Because manifests are
content-addressed and first-writer-wins, that metadata is never rewritten when an identical tree
is re-committed, so a staged tree that already exists as a version carries some earlier writer's
`derived_from`, and `promote` CAS-es against the wrong base and raises a false `Conflict`. The
proposal relocates the derive base from the (deduplicated) manifest to the staging pointer's
append-only, per-operation move record; stops writing `derived_from` entirely; makes `promote`
treat "head is already at the target version" as a success no-op; and threads a new optional
`base` field through the registry port and all four backends (memory, S3, SQLite, Postgres) with
an additive, non-backfilling storage change. The caller-facing signature of `promote` does not
change.

## Reviewer paraphrase 2 — "How the existing system works, per this document"

A `publish_over(stage=True)` call creates a fresh, ephemeral "staging pointer" whose name is
content-derived from `(base_version, affected_paths)`, stages the new tree, and writes
`derived_from` into the new version's manifest metadata. `promote` then resolves the staged tree
to a version and compare-and-swaps head against the base it read from that manifest metadata,
raising `Conflict` if head has moved. Separately, every pointer move (publish, repoint, delete,
staging advance) is appended to a per-pointer, non-content-deduplicated history via `set_pointer`,
readable through `list_pointer_history`, surviving pointer deletion and persisted by every
backend; `PointerMove` already carries a nullable `to_version` as precedent. The lost-update
guard distinguishing "head moved to a different tree" from a safe no-op came from a prior change
(`publish-over-staging`, 0.2.1).

**That both paraphrases are accurate is the main signal**: the causal chain (dedup → stale
`derived_from` → wrong CAS base → false conflict → move the fact to the non-deduplicated plane)
reads correctly from the document alone, and the rejected alternatives each have a concrete
reason. No section was impossible to paraphrase; no blocker.

## Findings and orchestrator disposition

### 1 [Major → mostly expected] `coordinate`/`coord` never defined
Reviewer: `coord` is the first arg of nearly every operation and the scope the cost arguments
rest on ("O(all-moves-in-the-coordinate)"), but the doc never says what a coordinate is.
Disposition: this is project vocabulary defined in `openspec/specs/` (`artifact-model`,
`registry-port`), which an OpenSpec change doc assumes. Not redefining the data model in a change
doc. **Cheap mitigation worth taking:** one orientation line in the design Context defining the
three load-bearing nouns (coordinate, pointer/alias, snapshot) so the cost argument is legible.

### 2 [Major → mostly expected] `Alias` type / its relation to pointers undefined
Reviewer: base recovery only happens "when `staged` is an `Alias`", but alias vs. pointer vs.
staging-pointer is never defined. Disposition: same as #1 — defined in the specs; covered by the
same one-line orientation note.

### 3 [Nit] `Snapshot`/`base_snap` used without definition
Reviewer: `base_snap.version` implies a snapshot object; term not introduced. Disposition:
covered by the orientation note; also note `base_snap.version` is design-level implementation
shorthand, acceptable in a design doc.

### 4 [Major → act] D5 (TLA+) is written for insiders
Reviewer: references `PublishOver.tla`, a `BaseSource`/`dedup` toggle, the "ok/bad-config pattern
used by `S3Drop` and `PointerDrop`", and a `tla-verifier` pipeline, none explained. **Act:** add
one sentence explaining the ok/bad-config pattern (a model with toggles that must hold in the
"ok" config and be violated in the "bad" config, proving the model actually catches the bug it
targets). This is the one finding that improves the doc for a project-internal reader too.

### 5 [Nit] External evidence (QS1 CI run, PR, "the QS1 client") uninspectable
Reviewer accepts as provenance; a reader takes the bug and the "single consumer" claim on faith.
Disposition: acceptable as provenance; no change. (The "single consumer" claim is the user's
own knowledge, recorded as a risk input.)

### 6 [Nit → non-issue] "existing" refuse-no-base wording in D4 step 2
Reviewer: calling "refuse a version with no recorded base" *existing* is confusing when "recorded
base" is new. Disposition: the current `promote` already refuses a staged version with no base
(today sourced from `derived_from`) — see the existing spec scenario "Promote refuses a version
with no recorded base". So "existing behavior, new source" is accurate. Optional one-word tweak;
not required.

### 7 [Nit → non-issue] "differential oracle/harness" assumes test design
Reviewer downgraded this himself (the doc does state the memory backend is the reference oracle).
No change.

## Recommendation
Two cheap clarity edits are worth taking regardless of audience: (a) a one-line orientation note
defining coordinate / pointer-alias / snapshot (closes #1–#3), and (b) one sentence explaining
the TLA ok/bad-config pattern (closes #4). The rest are artifacts of a context-stripped read of a
doc that legitimately assumes the project's spec glossary; no action. No blocker to proceeding.
