## 1. Factor the publish tail

- [x] 1.1 `_write_version(coord, entries, uploads, *, pointer, metadata, actor, reason)` extracted from `publish`; `publish` calls it with every path as an upload (behavior unchanged, existing tests green).

## 2. publish_over core (base + changes + remove)

- [x] 2.1 `Repository.publish_over(coord, base=HEAD, *, changes={}, remove=(), …)` — resolve base, seed entries, apply remove (raise if absent) + changes (hash sources), case-collision check, call `_write_version`. (Revised below: now returns `PublishOverResult`, uses base-version `expected` for same-lineage advances.)

## 3. TLA: derive vs concurrent advance (do first)

- [x] 3.1 `openspec/changes/publish-over/model/PublishOver.tla`: model a `publish_over` (reads base at one instant, advances a pointer at a later instant) racing a plain `publish` that moves the pointer in between. Invariant: **a pointer is never advanced to a version whose base ≠ the pointer's value at the advance** (no silent lost update). Toggle the CAS `expected` between fresh-head (should break) and base-version (should hold); include the `--stage`→verify→promote path (staged pointer new; separate promote of the staged version) to see whether the promote needs its own base-check.
- [x] 3.2 Run through the `tla-verifier` agent (SANY → smoke → exhaustive → coverage). Capture the verdict; record in `design.md` the confirmed `expected` rule and the resolution for the stage→promote path (base-checked promote / document re-derive / defer to the sibling change).

## 4. Advance semantics fix

- [x] 4.1 `_write_version` takes an explicit `expected: Version | None` (no longer reads head internally). `publish` passes head-at-write (unchanged behavior). `publish_over` computes `expected` per the §3-verified rule: base version for a same-lineage advance; `None` for a fresh `--stage` pointer; the advanced pointer's current value for a cross-lineage `--point`; current head only under `--force`. A moved pointer → `Conflict` (retryable).
- [x] 4.2 Apply whatever the model requires for the `--stage`→promote path (per 3.2).

## 5. Refinements

- [x] 5.1 `rename` param on `publish_over` — apply after `remove`, before `changes`: move the base entry (content_hash/size/inline) `old`→`new`, drop `old`, no source/upload; raise `PathError` if `old` absent.
- [x] 5.2 `PublishOverResult` frozen dataclass `(version, inherited, replaced, added, removed, renamed)`; compute counts against the base; return it. Export from `sartre`. Adjust existing publish_over tests to `.version`.
- [x] 5.3 CLI `ops.publish_over` + `app.py`: `--from`, `--rm`, `--mv <old:new>`, `--stage`, publish's shared flags; the head-divergence guard; `--force` requires `-m` and prints the discard summary; print `inherited/replaced/added/removed/renamed` (human + `--json`).

## 6. Tests

- [x] 6.1 Identity properties (no-op derive == base; identical override == base + no upload) — done; adjust to `.version`.
- [x] 6.2 Rename: reuses blob, zero uploads, absent raises. Result counts: replaced/inherited/added/removed/renamed correct.
- [x] 6.3 **Concurrency**: a `publish_over` whose base is stale (pointer advanced since base was read) raises `Conflict` and does not clobber the newer version — the code-level counterpart of the §3 invariant.
- [x] 6.4 CLI: `--mv`; `--stage` lands on the printed staging pointer, head unchanged, staged version resolves via that ref; reporting + `--json` counts; divergent `--from` refuses; `--force` without `-m` refuses; `--force -m …` prints discards and succeeds.

## 7. Gates

- [x] 7.1 `ruff` clean, `pyright` clean, full default suite green.
- [x] 7.2 `openspec validate publish-over --strict` passes.
