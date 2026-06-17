---
name: tla-verify
disable-model-invocation: true
description: Run a TLA+ spec through the pinned verification protocol (SANY parse → smoke sim → exhaustive model check → coverage gate) and interpret the result. Use to vet a design model before implementation; delegates the noisy TLC run to the tla-verifier subagent and returns a short digest (invariant status, any counterexample narrative, coverage verdict).
argument-hint: [path/to/Module.tla] [path/to/Module.cfg]
---

Drive TLC over a TLA+ design model and return a **short, decision-ready digest** — not raw
state dumps. This is the verification half of the design-vetting flow
(`/tla-audit` → `/tla-model` → **`/tla-verify`**).

Tooling: `tlc` / `tlasany` from `nixpkgs#tlaplus` (bundles its own JRE — no separate `java`).
If `tlc` is not on PATH: `nix profile install nixpkgs#tlaplus`.

## What to run

The mechanism is the pinned protocol script:

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/run_tlc.sh <Module.tla> [<Module.cfg>]
```

(From other contexts the script lives at `.claude/skills/tla-verify/scripts/run_tlc.sh`.)

**Prefer to run it via the `tla-verifier` subagent** (Agent tool,
`subagent_type: tla-verifier`) when that agent is available: TLC output is voluminous, and the
subagent runs it in an isolated context and returns only the digest, keeping the design session
clean. The `tla-verifier` agent is optional local tooling — if it isn't registered, run the
script directly here and summarize the result yourself using the same digest shape below.

The script runs the **pinned protocol** in order and stops at the first hard failure:

1. **SANY parse** (`tlasany`) — fail fast on syntax/semantic errors. Exit 2 = `PARSE_FAILED`.
2. **Smoke** (`tlc -simulate num=200 -depth 25`) — a few seconds of random walk. Catches gross
   modelling errors (always-false `Init`, type errors) cheaply before the full check.
3. **Exhaustive** (`tlc -coverage 1`) — breadth-first check of the whole bounded state space.
   This is the real verification: TLC tries **every** interleaving the `.cfg` admits.
4. **Coverage gate** — parses the coverage report; **any action that never fired FAILS the
   run** even if all invariants held. This is the anti-vacuity guard: invariants that "pass"
   only because a transition never happens are the #1 silent failure of unguided TLA+.

### Flags

- `--smoke-only` — stop after phase 2 (fast inner loop while drafting a spec).
- `--deadlock-check` — ENABLE deadlock checking. **Default is OFF**, because design models
  routinely have legitimate terminal states (e.g. `Canceled`) that TLC would otherwise report
  as deadlock false-positives. Turn it on only when "the system can get stuck" is itself a
  property you want to check.

## Conventions the model must already follow

These belong in `/tla-model`, but verify will surface them as failures if missing:

- **Constants bounded small** in the `.cfg` — bugs live in small instances (≈3 events, 2
  retries, 2 actors). A blown-up state space that runs for minutes usually means the bounds are
  too loose, not that the design is subtle.
- **A `TypeOk` invariant** listed in the `.cfg` `INVARIANT`s — cheap, and catches modelling
  slips that would otherwise make other invariants vacuous.
- **An unordered in-flight set** for any async channel (webhooks, queues) so the exhaustive
  check actually exercises reordering — that is the whole reason to reach for TLC over a flat
  transition table.

## Reading the result — the digest to return

Keep it to **≤10 lines**. Report:

- **Verdict**: `PASS` / `CHECK_FAILED` / `VACUOUS` / `PARSE_FAILED` (from the script's `RESULT:`).
- On a violation: **which invariant**, the **trace length**, and a **plain-English narrative**
  of the counterexample — the sequence of actions that reached the bad state (e.g. *"`deleted`
  delivered, then a late `updated(active)` re-grants `paid` on a `Canceled` sub → `NoZombie`
  violated"*). Use `-difftrace` output (already on) to keep this tight.
- A one-line **hypothesis: is the bug in the design or in the model?** A counterexample that
  describes a real interleaving the implementation could hit ⇒ design bug (the valuable
  outcome). One that relies on a transition the real system can't make ⇒ model bug (tighten the
  spec and re-run).
- On `VACUOUS`: name the action(s) that never fired and what guard/cfg-bound is starving them.

Do **not** paste raw TLC state dumps back into the parent session — that is exactly what the
subagent exists to absorb.

## Boundary

These skills consume design specs that feed implementation under OpenSpec, which **must never
touch `src/quantumsignals/`**. If the model or its source artifacts live under
`src/quantumsignals/`, refuse and say so — that directory is off-limits to this flow.
