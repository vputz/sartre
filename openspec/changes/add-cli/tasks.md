## 1. Library additions (repository-facade)

- [ ] 1.1 `src/sartre/repository.py`: add `Repository.point(coord, name, version, *, expected)` — verify `version` is committed (else `NotFound`), then `registry.set_pointer(coord, name, version, expected=expected)`; propagate `Conflict`. (Its CAS reuses the verified `set_pointer`; no new port method.)
- [ ] 1.2 `Repository.list_coordinates()` / `list_log(coord)` / `list_pointers(coord)` — thin delegators to the registry.
- [ ] 1.3 Unit tests: `point` success / stale-expected `Conflict` / uncommitted-version `NotFound`; delegators return registry data. Keep existing repository tests green.

## 2. Reference grammar

- [ ] 2.1 `src/sartre/cli/refs.py` (framework-free): `parse_ref(token, *, default_env) -> tuple[Coordinate, Ref]` with the `@`→Pin / `:`→Alias / else Head rule (version after `@` keeps its internal `:`); `render_ref(coord, ref) -> str` inverse; a `parse_coord` for commands that take a bare coordinate. Restrict name/env/alias to a safe charset; raise a typed CLI error on malformed input.
- [ ] 2.2 Hypothesis property: `parse_ref(render_ref(c, r), default_env=…) == (c, r)` over generated coordinates/refs (incl. Head/Alias/Pin and default-env elision).

## 3. Addressing / config resolution

- [ ] 3.1 `src/sartre/cli/config.py` (framework-free): a `RepoTarget` (local path | registry dsn + blob url + cache_dir + storage_options) and `resolve_target(flags, env, cwd) -> RepoTarget` implementing the precedence ladder (flags › env › profile › cwd-detect › default profile); `open_target(target) -> Repository` inferring `open_local` vs `open_cloud`.
- [ ] 3.2 TOML profile loading from `$XDG_CONFIG_HOME/sartre/config.toml` (fallback `~/.config`); `default` profile; per-profile `env`, `cache_dir`, `storage_options`. Local repo cwd auto-detect (walk up for `registry.db`).
- [ ] 3.3 Unit tests: precedence (flags>env>profile>cwd>default) with a tmp XDG config + tmp cwd repo; backend inference (path→local, dsn+url→cloud).

## 4. Operations seam + Typer app

- [ ] 4.1 `src/sartre/cli/ops.py`: framework-free operations returning plain data (dicts/dataclasses) + a renderer split (human vs json), so a future TUI reuses them — one function per command backing the handlers.
- [ ] 4.2 `src/sartre/cli/app.py`: the Typer app and thin command handlers (resolve target → parse refs → call ops → render). Global options: `--repo`/`--registry`/`--blobs`/`--profile`/`--env`/`--json`.
- [ ] 4.3 Read commands: `show`, `head` (bare id), `ls` (`-l` long), `cat` (verified `get_to`→stdout), `log`, `coords`, `checkout`.
- [ ] 4.4 Write commands: `publish` (dir-walk / basename / `logical=source`; `-p/--pointer`, `--point ALIAS`, `-m/--message`, `--meta k=v` — **string values** in v1; full-replacement help text); `point` (`<coord[:ptr]> <ref>`; CAS-safe: read current → `Repository.point(expected=current)`; `--force` skips CAS; conflict → clear "re-run or --force" message).
- [ ] 4.5 `gc` with `--keep-last N`, `--keep-within <dur>`, `--grace <dur>`; a **strict internal** duration parser (`src/sartre/cli/duration.py`): `<int><unit>` s/m/h/d/w, concatenation `7d12h`, else a clear error (Hypothesis round-trip test). `--json` reports `GCResult`.
- [ ] 4.7 `cat` output: `get_to` to a temp file then copy → stdout (verified, bounded); no new store API in this change.
- [ ] 4.6 `src/sartre/cli/__init__.py` `main()` entry point; typed CLI errors → non-zero exit + message; map `NotFound`/`Conflict`/`IntegrityError`/`PathError` to clean messages.

## 5. Packaging

- [ ] 5.1 `pyproject.toml`: `cli` optional extra (`typer>=0.12`); `[project.scripts] sartre = "sartre.cli:main"`; add `typer` to the dev group so tests run.
- [ ] 5.2 Lazy-import Typer at the entry point (mirror the `postgres`/psycopg pattern): a clear `RuntimeError`/message naming `sartre[cli]` if absent; `import sartre` stays Typer-free.

## 6. Tests

- [ ] 6.1 End-to-end via Typer `CliRunner` against a temp local repo: `publish` a dir → `show`/`ls`/`cat`/`log`/`coords` → `point` (promote + rollback) → `checkout` → `gc`; assert human and `--json` output for each.
- [ ] 6.2 `point` CAS: promote succeeds; a stale pointer → conflict message + non-zero exit; `--force` overrides.
- [ ] 6.3 `publish` mappings: directory relative paths, explicit basename, `logical=source` remap, and `--meta`/`--message` land in the manifest.
- [ ] 6.4 Library-importable-without-typer smoke (import `sartre` in-process is already covered; assert the entry point errors cleanly when typer is stubbed absent, if feasible).

## 7. Gates

- [ ] 7.1 `pyright` clean, `ruff` clean, full default suite green.
- [ ] 7.2 `openspec validate add-cli --strict` passes.
