# sartre

**S**imple **ART**ifact **RE**pository — a generic, content-addressed
**versioned binary artifact repository** for Python.

> "Hell is other people's artifact repositories" — Jean-Paul Sartre

Publish a tree of files as an immutable, content-addressed **version**; move
mutable **pointers** (`head`, or named aliases like `stable`/`prod`) at it; fetch
any version back — lazily, verified, and deduplicated by content hash. Every
change records **who** made it and **why**.

Two cleanly separated planes:

- **Manifest plane** (`Registry`) — a transactional metadata store mapping
  `(name, env, version, path) → content_hash`, plus mutable pointers
  (`Head`/`Alias`/`Pin`) that resolve to immutable versions. Backed by SQLite
  (local) or Postgres (shared).
- **Blob plane** (`Store`) — a dumb content-addressed object store holding
  immutable bytes keyed by their own hash, over any [fsspec](https://filesystem-spec.readthedocs.io/)
  filesystem (local disk, S3, GCS, …). Dedup is structural; GC is mark-and-sweep.

A `Version` is the hash of its manifest entries, so identical content is stored
once and promotes across environments for free. Reads never materialize a whole
blob into memory, so multi-hundred-megabyte artifacts stream.

## Install

```bash
pip install sartre            # library core (fsspec only)
pip install "sartre[cli]"     # + the `sartre` command line
```

| Extra      | Pulls in                     | For                                   |
| ---------- | ---------------------------- | ------------------------------------- |
| `cli`      | `typer`                      | the `sartre` command-line tool        |
| `postgres` | `psycopg`                    | a shared Postgres manifest plane      |
| `s3`       | `boto3`, `s3fs`              | S3 blob storage                       |
| `gcs`      | `gcsfs`                      | Google Cloud Storage blob storage     |

Requires Python 3.12+. The package ships type information (PEP 561 `py.typed`).

## Quickstart — command line

A local repository is just a directory (`registry.db` + `blobs/`):

```bash
# publish a directory as a new version, attributing who and why
sartre --repo ./repo publish models/prod ./checkpoint --as alice -m "initial train"
# → sha256:554b7a…

# ...change the files, publish again — a new version, head advances
sartre --repo ./repo publish models/prod ./checkpoint --as alice -m "retrain on Q3"

# promote a specific version to a named pointer (compare-and-swap safe)
sartre --repo ./repo point models/prod:stable sha256:554b7a… --as bob -m "passed eval"

sartre --repo ./repo show   models/prod           # version, author, reason, entries
sartre --repo ./repo log    models/prod           # commit history with author + reason
sartre --repo ./repo history models/prod          # who moved which pointer, from → to, why
sartre --repo ./repo ls     models/prod:stable    # list a version's files (no download)
sartre --repo ./repo cat    models/prod cfg.json  # stream one file (verified) to stdout
sartre --repo ./repo checkout models/prod ./out   # materialize the whole tree
```

Reference grammar: `name/env` (head) · `name/env:alias` · `name/env@sha256:…` (pin).
Add `--json` to any command for machine-readable output. `head`/`ls` are bare for
scripting. Point a command at a shared repo with `--registry <dsn> --blobs <url>`,
`$SARTRE_REPO`, or a profile in `~/.config/sartre/config.toml`.

## Quickstart — library

```python
from pathlib import Path
from sartre import open_local, Coordinate, Alias

repo = open_local("./repo")                     # SQLite + local blobs; reopen to recover
coord = Coordinate("models", "prod")

# publish a mapping of logical path -> bytes (or Path); returns the version id
version = repo.publish(
    coord,
    {"w/model.bin": b"...weights...", "cfg.json": b"{}"},
    actor="alice",
    reason="initial train",
)

# resolve a pointer to an immutable snapshot (manifest only — no blob bytes yet)
snap = repo.resolve(coord)                       # head by default
print(snap.version, [e.path for e in snap.entries])

# materialize bytes lazily, verified and content-cached
local_path = repo.open(snap, "cfg.json")         # one file
repo.checkout(snap, Path("./out"))               # the whole tree

# move a mutable pointer to an existing version (promote / roll back)
repo.point(coord, "stable", version, expected=None, actor="bob", reason="passed eval")
assert repo.head(coord, Alias("stable")) == version

# provenance is on the event, not the shared manifest
for entry in repo.list_log(coord):               # who published/promoted each tip, and why
    print(entry.version, entry.actor, entry.reason)
for move in repo.list_pointer_history(coord):    # the pointer-move audit trail
    print(move.name, move.from_version, "->", move.to_version, move.actor)
```

### A shared, cloud-backed repository

```python
from sartre import open_cloud

repo = open_cloud(
    "postgresql://user:pw@host/db",   # Postgres manifest plane (needs the `postgres` extra)
    "s3://my-bucket/artifacts",       # any fsspec blob URL (needs `s3`/`gcs` as appropriate)
    cache_dir="/var/cache/sartre",    # optional local read-through cache
)
```

The API is identical; only the backends differ. `AsyncRepository` wraps a
`Repository` with awaitable equivalents.

## What you get

- **Content-addressed & deduplicated** — a version is its manifest hash; identical
  files are stored once and promote across environments with no copy.
- **Change provenance** — every commit and every pointer move records `actor` +
  `reason`; the pointer-move history answers "who changed prod, from what, and why."
- **Streaming** — publish and fetch never buffer a whole blob in memory.
- **Safe concurrency** — pointer moves are compare-and-swap; publishes hold a
  TTL lease so a concurrent garbage collector never reclaims in-flight blobs
  (the GC/lease protocol is verified in TLA+).
- **Garbage collection** — mark-and-sweep with retention policies
  (`keep_last_n`, `keep_within`, a blob grace period).

See [`binary-artifact-repo-design.md`](binary-artifact-repo-design.md) for the
full design memo.

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
src/sartre/         library source (registry backends, stores, repository, CLI)
tests/              tests (unit, property-based, differential, e2e)
openspec/           spec-driven change proposals & living specs
binary-artifact-repo-design.md   design memo
```

## License

Licensed under the [Apache License 2.0](LICENSE) — permissive, commercial-use
friendly, with an explicit patent grant. See [`NOTICE`](NOTICE).
