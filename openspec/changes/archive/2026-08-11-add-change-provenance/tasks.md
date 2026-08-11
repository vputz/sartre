## 1. Ports & model (registry-port, version-log, change-provenance)

- [x] 1.1 `src/sartre/ports.py`: add `actor: str` and `reason: str | None` to `LogEntry`; add a `PointerMove` dataclass `(name, from_version: Version | None, to_version, actor, reason, at: datetime)`.
- [x] 1.2 `ports.py`: widen `set_pointer(coord, name, version, *, expected, actor="unknown", reason=None)` — the sole provenance write point (stamps the tip/log event and appends the move); a rejected CAS writes neither. `commit` is left unchanged: the log row is appended by `set_pointer`, and the manifest is shared/content-addressed so it carries no provenance. (Refinement from the proposal, recorded in design.md.)
- [x] 1.3 `ports.py`: add `list_pointer_history(coord) -> Sequence[PointerMove]` (append order, oldest first) to the `Registry` protocol.

## 2. In-memory reference backend (in-memory-backend)

- [x] 2.1 `src/sartre/memory.py`: record `actor`/`reason` on commit-log rows; expose them via `list_log`.
- [x] 2.2 `memory.py`: maintain an append-only in-process pointer-move list; append one record inside a successful `set_pointer` (none on rejected CAS); implement `list_pointer_history`.

## 3. Persistent backend (persistent-registry)

- [x] 3.1 `src/sartre/_sql.py`: add `actor`/`reason` columns to the commit-log table (default `'unknown'` / NULL); write them in the `set_pointer` transaction (the tip event); surface in `list_log`.
- [x] 3.2 `_sql.py`: create a `pointer_moves` table `(move_seq, coord_name, coord_env, pointer, from_version, to_version, actor, reason, at)`; INSERT one row inside the same transaction as the `set_pointer` CAS (only on success); implement `list_pointer_history` ordered by `move_seq`. (Used a TEXT `at` via `datetime.now(UTC).isoformat()` to match the existing log `created_at` convention rather than the `_NOW_SQL`/`_TS_TYPE` epoch hooks — those are for lease-clock arithmetic; audit timestamps mirror the commit log.)

## 4. Repository facade (repository-facade)

- [x] 4.1 `src/sartre/repository.py`: `publish(..., actor: str = "unknown", reason: str | None = None)` — thread to the `set_pointer` tip event (not `commit`); reason is NOT injected into `metadata`.
- [x] 4.2 `repository.py`: `point(..., actor: str = "unknown", reason: str | None = None)` — thread to `set_pointer`; keep the CAS/NotFound semantics.
- [x] 4.3 `repository.py`: add a `list_pointer_history(coord)` delegator; publish's internal pointer advance passes its own actor/reason.

## 5. CLI (cli)

- [x] 5.1 `src/sartre/cli/config.py`: `resolve_author(*, flag, target, environ) -> str` with ladder `--author/--as › $SARTRE_AUTHOR › profile.author › getpass.getuser()`; raise `CliError` when unresolvable (guards `getpass.getuser()` raising as unresolved). `author` loaded from the active profile onto `RepoTarget`.
- [x] 5.2 `src/sartre/cli/ops.py` (framework-free): thread `actor`/`reason` through `publish` and `move_pointer`; add a `pointer_history(repo, coord)` op returning plain dicts; `log`/`show` include actor/reason (show finds the creating tip event).
- [x] 5.3 `src/sartre/cli/app.py`: add `--author/--as` (required, via `resolve_author`) and map `-m/--message` to `reason` on `publish` and `point`; dropped the `metadata["message"]` write.
- [x] 5.4 `app.py`: add a `history <coord>` command (human table newest-first: pointer, from → to, author, reason, at; `--json` array oldest→newest); render actor/reason in `log` and `show` output.

## 6. Tests

- [x] 6.1 `tests/test_provenance.py`: the SAME content published into two coordinates with different `(actor, reason)` records DISTINCT per-event provenance though the version is shared (the core invariant).
- [x] 6.2 `tests/test_provenance.py`: Hypothesis `actor`/`reason` roundtrip through `publish` → `list_log`; pointer-move history is append-only and ordered under a sequence of moves.
- [x] 6.3 differential — memory vs SQL agree on `list_log` provenance and `list_pointer_history` for the same operation sequence (extended `DiffMachine` invariant + Postgres TRUNCATE).
- [x] 6.4 `tests/test_provenance.py` (parametrized memory + sqlite): a rejected `set_pointer` CAS appends no `pointer_moves` row and no log row; omitted `actor` is recorded as `"unknown"`.
- [x] 6.5 `tests/test_cli_provenance.py` — author ladder precedence (flag > env > profile > OS user) and unresolvable → clean error; `-m` lands in `reason` not `metadata`; `history` human + `--json` (oldest→newest); `log`/`show` show actor/reason.

## 7. Gates

- [x] 7.1 `ruff` clean, `pyright` clean (0 errors), full default suite green (164 passed, 1 skipped). No CLI test asserted `metadata["message"]`; the `_RaceOnceRepo` double was updated for the new `set_pointer` kwargs.
- [x] 7.2 `openspec validate add-change-provenance --strict` passes.
