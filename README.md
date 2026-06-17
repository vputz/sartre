# sartre

**S**imple **ART**ifact **RE**pository — a generic, content-addressed
**versioned binary artifact repository**.

Two cleanly separated planes:

- **Manifest plane** — a transactional metadata store (Delta Lake over S3, or any
  ACID store) mapping `(name, env, version, path) → content_hash`, plus mutable
  **pointers** (`Head`/`Alias`/`Pin`) that resolve to immutable versions.
- **Blob plane** — a dumb content-addressed object store (S3) holding immutable
  bytes keyed by their own hash. Dedup is structural; GC is one anti-join.

The read core is `head` (cheap pointer read) → `resolve` (manifest, no bytes) →
`open` / `fetch_all` (lazy, content-cached blob fetch). See
[`binary-artifact-repo-design.md`](binary-artifact-repo-design.md) for the full
design memo.

## Development

This project uses:

- **uv** — Python environment & packaging (`uv sync`, `uv run pytest`)
- **jj** (colocated with git) — version control
- **nix** (`shell.nix` + `direnv`) — reproducible dev shell
- **OpenSpec** — spec-driven change workflow (`openspec/`, `/opsx:*` skills)
- **TLA+** — protocol model checking (`tla-*` skills, `tla-verifier` agent)

```bash
nix-shell           # or: direnv allow
uv sync             # create .venv and install dev tooling
uv run pytest       # run tests
uv run ruff check   # lint
uv run pyright      # type-check
```

## Layout

```
src/sartre/         library source
tests/              tests
openspec/           spec-driven change proposals & specs
binary-artifact-repo-design.md   design memo
```
