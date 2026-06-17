---
name: tla-model
disable-model-invocation: true
description: Turn one TLA+-shaped design candidate (from /tla-audit) plus its source artifacts — OpenSpec requirements, Pydantic models, an existing transition table — into a small TLA+ spec (.tla + .cfg) ready for /tla-verify. Enforces house conventions: a TypeOk, small bounded constants, the environment modelled as a nondeterministic actor, async channels as an unordered in-flight set, and a source-comment trail mapping each action back to the code it abstracts.
argument-hint: [candidate name] [source: spec path / module / table.py]
---

Produce a **small, checkable** TLA+ model of one design candidate. This is the modelling step
(`/tla-audit` → **`/tla-model`** → `/tla-verify`). Input: one candidate the audit marked
**MODEL**, plus the source artifacts (the OpenSpec requirement text, relevant Pydantic models /
handlers, any existing transition table such as `tests/subscription_model/table.py`).

The canonical worked example to imitate lives at
`openspec/specs/subscription-state-machine/model/` (`SubscriptionTierSwap.tla` +
`_fixed.cfg`/`_buggy.cfg` + `README.md`). Read it before writing a new model.

## From-source workflow

1. **Analyze** — list the design's states, the events/actions that move between them, and the
   guards. If a transition table or Pydantic model exists, lift the states/fields from it
   verbatim so the model and code share vocabulary.
2. **Abstract** — pick the **<6 variables** that carry the invariants. Collapse everything else:
   the external system (Stripe, a queue) becomes a **nondeterministic actor**, a datastore
   becomes an atomic KV, payloads become opaque tiers/ids. If you need ≥6 vars, the scope is too
   big — split the candidate.
3. **Specify** — write `Init`, one action per event in `Next`, and the invariants as one-line
   predicates. Bound every constant **small** in the `.cfg` (≈3 events, 2 retries, 2 actors).
4. **Hand off** — run it through `/tla-verify` (or the `tla-verifier` subagent) and iterate on
   counterexamples.

## House conventions (enforce all)

- **Always a `TypeOk`** invariant, listed first in the `.cfg`. Cheap; catches modelling slips
  that would otherwise make the real invariants vacuous.
- **Constants bounded small** in the `.cfg`. Bugs live in small instances; a state space that
  takes minutes usually means loose bounds, not a subtle design.
- **Model the environment as an unconstrained nondeterministic actor.** Stripe transitions its
  own status and *queues* a webhook; it does not cooperate with your handlers.
- **Async delivery = an unordered in-flight set.** A `Deliver` action picks **any** pending
  message (`\E m \in inflight: …`). This is the whole reason to use TLC over a flat table — it
  exhaustively explores reordering. If the design has no async channel, prefer plain
  property-based tests; reconsider whether this is really a TLA+ problem.
- **Comment each action with the source it abstracts** — `\* Abstracts
  _handle_subscription_updated(active)` — that comment trail is the refinement mapping and the
  drift-detection anchor when the code changes.
- **Make a fix/bug toggle a `CONSTANT`** when modelling a known or suspected defect, so verify
  can show both the counterexample (buggy) and the clean run (fixed) from one module — see the
  worked example's `UsePerTierGuard`.

## Where the artifacts live

Co-locate the model with its living spec under `openspec/specs/<capability>/model/` so it
becomes a synced sibling of the human reference doc and any code-side transition table. For a
model produced inside an in-flight change, keep it in the change dir
(`openspec/changes/<change>/model/`) and promote it to the living spec on archive.

## Boundary

Never model anything whose implementation lives under `src/quantumsignals/` — that directory is
off-limits to the OpenSpec-driven design flow. Refuse and say so.
