## Why

Sartre is a capable library — content-addressed, versioned, multi-writer-safe, cloud-
and streaming-ready — but every operation still requires writing Python. A command-line
interface is the single biggest usability lever: it lets a human or a CI job publish,
resolve, check out, promote, and garbage-collect artifacts without importing the package.
It is the last thing standing between "impressive library" and "something people use."

## What Changes

- **New `sartre` CLI** (Typer) exposing the library surface as commands:
  - read: `show`, `head`, `ls`, `cat`, `log`, `coords`, `checkout`
  - write: `publish`, `point`
  - maintenance: `gc`
- **OCI-style reference grammar** — a reference is `name/env` optionally with `:alias`
  (mutable pointer) or `@version` (immutable pin); bare is head. A default env lets a bare
  `name` resolve to `name/<default>`. `:` = mutable, `@` = immutable, teaching
  reproducibility through syntax.
- **Layered addressing** — the CLI finds its repo through a precedence ladder: explicit
  flags › `SARTRE_*` env › `--profile` (a `~/.config/sartre/config.toml`) › cwd
  auto-detect (git-like) › the `default` profile. Backend (local vs cloud) is inferred
  from which params are set.
- **`point` — a compare-and-swap pointer move** — promote an existing version to a
  channel, re-point an alias, or roll head back, touching only the pointer plane (no
  blobs). CAS-safe by default (refuses on a concurrent move), `--force` to override.
- **`--json` on every read command**; `head`/`ls` stay bare for scripting.
- **Packaging** — Typer lives behind a `cli` optional extra (`sartre[cli]`), lazy-imported
  with a clear error if absent (mirroring the `postgres`/psycopg pattern); a `sartre`
  console-script entry point. The library stays importable without Typer.
- **Two small `repository-facade` additions** the CLI needs: `Repository.point(...)`
  (centralizing the CAS pointer move) and thin enumeration delegators
  (`list_coordinates`/`list_log`/`list_pointers`).

## Capabilities

### New Capabilities
- `cli`: the `sartre` command-line interface — reference grammar, repo addressing/config
  resolution, the command set and their output (human + `--json`), and packaging as an
  optional extra with a console-script entry point.

### Modified Capabilities
- `repository-facade`: the `Repository` facade gains a `point(coord, name, version, *,
  expected)` compare-and-swap pointer move (promotion/rollback without re-publishing) and
  thin enumeration delegators (`list_coordinates`, `list_log`, `list_pointers`) so callers
  (the CLI, and a future TUI) get a clean facade instead of reaching into `repo.registry`.

## Impact

- **Code**: new `src/sartre/cli/` (Typer app, reference parsing, config/addressing
  resolution, command handlers kept thin over an operations seam a future TUI can reuse);
  `src/sartre/repository.py` (`point` + enumeration delegators); `pyproject.toml` (`cli`
  extra with `typer`, `[project.scripts] sartre = ...`).
- **Tests**: Hypothesis round-trip for the reference grammar (`parse(format(x)) == x`);
  addressing-precedence unit tests over a temp XDG config; end-to-end CLI runs via Typer's
  `CliRunner` against a temp local repo (publish→show→ls→cat→point→checkout→gc→log,
  human + `--json`); `point` CAS conflict path (stale expected → error; `--force`
  overrides); `Repository.point` + delegator unit tests.
- **No new TLA**: `point` is not a new concurrency protocol — its CAS reuses the already-
  verified `set_pointer`.
- **Non-goals**: the Textual TUI (later — handlers kept thin so it can reuse the ops);
  FUSE/`sartre://` mount; stdin publish; incremental `add`/`rm` (publish is full-
  replacement); immutable/protected aliases and pointer deletion (registry-model changes);
  a `sartre config` profile-management subcommand (v1 documents editing the TOML);
  shell-completion polish; auth/secrets beyond env + `storage_options`.
