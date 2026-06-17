---
name: tla-audit
disable-model-invocation: true
description: Audit an OpenSpec change (or a living spec) for TLA+-shaped design risks before implementation, and emit a verdict table. Scores each candidate against discrimination criteria (interleaving, out-of-order messages, retries/idempotency, multi-state guards, conservation properties) so only designs that actually benefit get modelled. Output is meant to be pasted into the change's design.md.
argument-hint: [openspec/changes/<change> | openspec/specs/<capability> | path/to/spec.md]
---

Answer one question for a design: **is there a TLA+-shaped problem here worth modelling before
implementation?** This is the discrimination step of the design-vetting flow
(**`/tla-audit`** → `/tla-model` → `/tla-verify`). The failure mode this skill exists to prevent
is **modelling everything** — most specs do not need TLC, and saying so is a valid, common result.

## Input

The argument points at what to audit:

| Form | Meaning |
|---|---|
| `openspec/changes/<change>` | Audit the change's spec deltas under `…/specs/**`. The primary use: vet a design during the propose/design phase, before implementation. |
| `openspec/specs/<capability>` | Audit a living spec (e.g. `stripe-webhooks`, `subscription-state-machine`). |
| `path/to/spec.md` | Audit a single spec/requirement file. |
| _(none)_ | List `openspec/changes/` (non-archive) and prompt the user to pick one. |

Read the requirement text (`## Requirements`, scenarios, "MUST/SHALL", "WHEN/THEN"). Where the
spec references concrete code (handlers, Pydantic models, an existing transition table), skim it
to ground the abstract-state estimate — but the audit is about the **design**, not the code.

## Discrimination criteria

**Strong candidates (model it).** Score high when the design has any of:

- **Multiple writers / sources of truth that interleave** — webhook handlers vs user actions vs
  cron/retry jobs all mutating shared state.
- **Out-of-order / at-least-once message arrival** — anything consuming Stripe webhooks, Kafka,
  a queue; "the event may arrive late / twice / out of order".
- **Retry / idempotency / "exactly once"** claims.
- **State machines with >3 states and guards** — lifecycle, dunning/grace, entitlement.
- **Conservation properties** — "credits issued = consumed + balance", "proration never creates
  money", "at most one live subscription per signal".
- **Temporal language** — "eventually", "never while", "until", "always converges".

**Poor candidates (route elsewhere, score zero).** Say so explicitly and name the better tool:

- Pure functions / deterministic calculation given fixed inputs → **plain unit tests** or
  property-based tests (Hypothesis).
- Single-writer CRUD, validation, serialization → **integration/unit tests**.
- UI, formatting, logging.

A useful tie-breaker: *if you cannot write the invariant as a one-line predicate over abstract
state, it is probably not a TLA+ problem yet.*

## Output — a verdict table for `design.md`

Produce a table the user pastes into the change's `design.md`, so the modelling decision is
reviewable and versioned with the change:

| Candidate | Invariants at stake | Abstract state (<6 vars) | Abstracted away | State-space class | Verdict |
|---|---|---|---|---|---|
| <name> | <2–5 plain-English invariants> | <vars + small bounds> | <what becomes a nondeterministic actor / atomic KV> | small / medium / large | **MODEL** / skip → \<tool> |

For each **MODEL** verdict, add 2–4 lines: the environment actor to model nondeterministically
(e.g. Stripe), the async channel that needs an **unordered in-flight set**, and the single
sharpest invariant to check first. Hand those straight to `/tla-model`.

Keep the whole output scannable — a table plus a few bullet lines, not an essay.

## Boundary

This flow vets designs that become implementations under OpenSpec, which **must never touch
`src/quantumsignals/`**. If asked to audit anything under `src/quantumsignals/` (or a spec whose
implementation lives there), **refuse** and say that directory is off-limits to this flow.
