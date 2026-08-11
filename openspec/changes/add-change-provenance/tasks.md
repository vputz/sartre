## 1. Ports & model (registry-port, version-log, change-provenance)

- [ ] 1.1 `src/sartre/ports.py`: add `actor: str` and `reason: str | None` to `LogEntry`; add a `PointerMove` dataclass `(name, from_version: Version | None, to_version, actor, reason, at: datetime)`.
- [ ] 1.2 `ports.py`: widen `Registry.commit(coord, entries, metadata, *, actor, reason) -> Version` and `set_pointer(coord, name, version, *, expected, actor, reason)`; document that `actor`/`reason` are event provenance recorded off the manifest, and that a rejected CAS appends no move.
- [ ] 1.3 `ports.py`: add `list_pointer_history(coord) -> Sequence[PointerMove]` (append order, oldest first) to the `Registry` protocol.

## 2. In-memory reference backend (in-memory-backend)

- [ ] 2.1 `src/sartre/memory.py`: record `actor`/`reason` on commit-log rows; expose them via `list_log`.
- [ ] 2.2 `memory.py`: maintain an append-only in-process pointer-move list; append one record inside a successful `set_pointer` (none on rejected CAS); implement `list_pointer_history`.

## 3. Persistent backend (persistent-registry)

- [ ] 3.1 `src/sartre/_sql.py`: add `actor`/`reason` columns to the commit-log table (default `'unknown'` / NULL); write them in the commit transaction; surface in `list_log`.
- [ ] 3.2 `_sql.py`: create a `pointer_moves` table `(seq, name, env, pointer, from_version, to_version, actor, reason, at)` using the `_NOW_SQL`/`_TS_TYPE` dialect hooks; INSERT one row inside the same transaction as the `set_pointer` CAS (only on success); implement `list_pointer_history` ordered by `seq`.

## 4. Repository facade (repository-facade)

- [ ] 4.1 `src/sartre/repository.py`: `publish(..., actor: str = "unknown", reason: str | None = None)` — thread to `commit`; ensure the reason is NOT injected into `metadata`.
- [ ] 4.2 `repository.py`: `point(..., actor: str = "unknown", reason: str | None = None)` — thread to `set_pointer`; keep the CAS/NotFound semantics.
- [ ] 4.3 `repository.py`: add a `list_pointer_history(coord)` delegator; verify publish's internal pointer advance passes an actor/reason (its own, not a second "unknown").

## 5. CLI (cli)

- [ ] 5.1 `src/sartre/cli/config.py`: `resolve_author(*, flag, environ, profile) -> str` with ladder `--author/--as › $SARTRE_AUTHOR › profile.author › getpass.getuser()`; raise `CliError` when unresolvable (guard `getpass.getuser()` raising as unresolved). Load `author` from the active profile.
- [ ] 5.2 `src/sartre/cli/ops.py` (framework-free): thread `actor`/`reason` through `publish` and `move_pointer`; add a `pointer_history(repo, coord)` op returning plain dicts; have `log`/`show` include actor/reason in their returned data.
- [ ] 5.3 `src/sartre/cli/app.py`: add `--author/--as` (required, via `resolve_author`) and map `-m/--message` to `reason` on `publish` and `point`; drop the `metadata["message"]` write.
- [ ] 5.4 `app.py`: add a `history <coord>` command (human table: pointer, from → to, actor, reason, time; `--json` array); render actor/reason in `log` and `show` output.

## 6. Tests

- [ ] 6.1 `tests/`: property (Hypothesis) — committing the SAME entries into multiple coordinates/pointers with different `(actor, reason)` records DISTINCT per-event provenance though the version is shared (the core invariant).
- [ ] 6.2 property — `actor`/`reason` roundtrip through `publish` → `list_log`; pointer-move history is append-only and ordered under a sequence of moves.
- [ ] 6.3 differential — memory vs SQL agree on `list_log` provenance and `list_pointer_history` for the same operation sequence (extend the existing differential/property harness).
- [ ] 6.4 backend — a rejected `set_pointer` CAS appends no `pointer_moves` row (both backends); omitted library `actor` is recorded as `"unknown"`.
- [ ] 6.5 CLI — author ladder precedence (flag > env > profile > OS user) and unresolvable → clean non-zero error; `-m` lands in `reason` not `metadata`; `history` human + `--json`; `log`/`show` show actor/reason.

## 7. Gates

- [ ] 7.1 `ruff` clean, `pyright` clean, full default suite green (update CLI tests that asserted `metadata["message"]`).
- [ ] 7.2 `openspec validate add-change-provenance --strict` passes.
