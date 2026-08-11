"""The Typer application: thin command handlers over :mod:`sartre.cli.ops`.

Each handler resolves a repository (:mod:`sartre.cli.config`), parses references
(:mod:`sartre.cli.refs`), calls one operation, and renders it (human by default, ``--json``
on demand). All domain/CLI errors are funnelled to a clean stderr message + non-zero exit.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import typer

from sartre.cli import config, ops, refs
from sartre.cli.duration import parse_duration
from sartre.cli.errors import CliError
from sartre.errors import Conflict, IntegrityError, NotFound, PathError

app = typer.Typer(
    name="sartre",
    help="Content-addressed, versioned binary artifact repository.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _global(
    ctx: typer.Context,
    repo: str | None = typer.Option(None, "--repo", help="Local repository path."),
    registry: str | None = typer.Option(None, "--registry", help="Registry DSN (cloud)."),
    blobs: str | None = typer.Option(None, "--blobs", help="Blob store URL (cloud)."),
    profile: str | None = typer.Option(None, "--profile", help="Config profile name."),
    env: str | None = typer.Option(None, "--env", help="Default env for bare names."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
) -> None:
    ctx.obj = {
        "repo": repo, "registry": registry, "blobs": blobs,
        "profile": profile, "env": env, "json": json_out,
    }


@contextmanager
def _handle() -> Iterator[None]:
    try:
        yield
    except (CliError, NotFound, Conflict, IntegrityError, PathError) as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


def _target(ctx: typer.Context) -> config.RepoTarget:
    o = ctx.obj
    return config.resolve_target(
        repo=o["repo"], registry=o["registry"], blobs=o["blobs"],
        profile=o["profile"], env=o["env"],
    )


def _author(target: config.RepoTarget, flag: str | None) -> str:
    """Resolve the required author for a mutating command (flag › env › profile › OS user)."""
    return config.resolve_author(flag=flag, target=target)


def _emit(ctx: typer.Context, human: str, data: Any) -> None:
    if ctx.obj["json"]:
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(human)


def _table(rows: list[list[str]], headers: list[str]) -> str:
    cols = [headers, *rows]
    widths = [max(len(r[i]) for r in cols) for i in range(len(headers))]
    line = lambda r: "  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip()  # noqa: E731
    return "\n".join([line(headers), *(line(r) for r in rows)])


# --- read commands ---


_REF = typer.Argument(..., help="name/env[:alias|@version]")


@app.command()
def show(ctx: typer.Context, ref: str = _REF) -> None:
    """Resolve a reference and print its version, metadata, and entries."""
    with _handle():
        target = _target(ctx)
        coord, r = refs.parse_ref(ref, default_env=target.default_env)
        data = ops.show(config.open_target(target), coord, r)
        meta = " ".join(f"{k}={v}" for k, v in data["metadata"].items()) or "(none)"
        rows = [[e["path"], str(e["size"]), e["content_hash"]] for e in data["entries"]]
        human = "\n".join([
            f"version:  {data['version']}",
            f"created:  {data['created_at']}",
            f"author:   {data['actor'] or '(unknown)'}",
            f"reason:   {data['reason'] or '(none)'}",
            f"metadata: {meta}",
            "",
            _table(rows, ["path", "size", "content_hash"]) if rows else "(no entries)",
        ])
        _emit(ctx, human, data)


@app.command()
def head(ctx: typer.Context, ref: str = _REF) -> None:
    """Print the bare version id a reference resolves to (porcelain)."""
    with _handle():
        target = _target(ctx)
        coord, r = refs.parse_ref(ref, default_env=target.default_env)
        version = ops.head(config.open_target(target), coord, r)
        _emit(ctx, version, {"version": version})


@app.command(name="ls")
def ls_cmd(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="name/env[:alias|@version]"),
    long: bool = typer.Option(False, "-l", "--long", help="Show size and content hash."),
) -> None:
    """List a version's entries (no blob fetch)."""
    with _handle():
        target = _target(ctx)
        coord, r = refs.parse_ref(ref, default_env=target.default_env)
        rows = ops.ls(config.open_target(target), coord, r)
        if long:
            human = _table(
                [[e["path"], str(e["size"]), e["content_hash"]] for e in rows],
                ["path", "size", "content_hash"],
            )
        else:
            human = "\n".join(e["path"] for e in rows)
        _emit(ctx, human, rows)


@app.command()
def cat(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="name/env[:alias|@version]"),
    path: str = typer.Argument(..., help="Logical path within the version."),
) -> None:
    """Stream one file's (verified) bytes to stdout."""
    with _handle():
        target = _target(ctx)
        coord, r = refs.parse_ref(ref, default_env=target.default_env)
        local = ops.materialize(config.open_target(target), coord, r, path)
        sys.stdout.buffer.write(local.read_bytes())


@app.command()
def log(ctx: typer.Context, coord: str = typer.Argument(..., help="name/env")) -> None:
    """Show a coordinate's commit history."""
    with _handle():
        target = _target(ctx)
        c = refs.parse_coord(coord, default_env=target.default_env)
        rows = ops.log(config.open_target(target), c)
        human = _table(
            [
                [
                    r["version"],
                    r["actor"],
                    r["reason"] or "-",
                    ",".join(r["pointers"]) or "-",
                ]
                for r in rows
            ],
            ["version", "author", "reason", "pointers"],
        )
        _emit(ctx, human, rows)


@app.command()
def coords(ctx: typer.Context) -> None:
    """Enumerate every coordinate in the repository."""
    with _handle():
        rows = ops.coords(config.open_target(_target(ctx)))
        human = "\n".join(f"{c['name']}/{c['env']}" for c in rows)
        _emit(ctx, human, rows)


@app.command()
def history(ctx: typer.Context, coord: str = typer.Argument(..., help="name/env")) -> None:
    """Show a coordinate's pointer-move history (who moved what, from → to, and why)."""
    with _handle():
        target = _target(ctx)
        c = refs.parse_coord(coord, default_env=target.default_env)
        rows = ops.pointer_history(config.open_target(target), c)  # oldest → newest (JSON order)
        human = _table(
            [
                [
                    m["pointer"],
                    f"{m['from_version'] or '-'} -> {m['to_version']}",
                    m["actor"],
                    m["reason"] or "-",
                    m["at"],
                ]
                for m in reversed(rows)  # human table reads newest-first
            ],
            ["pointer", "move", "author", "reason", "at"],
        )
        _emit(ctx, human, rows)


@app.command()
def checkout(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="name/env[:alias|@version]"),
    dest: Path = typer.Argument(..., help="Destination directory."),
) -> None:
    """Materialize a whole version under a directory."""
    with _handle():
        target = _target(ctx)
        coord, r = refs.parse_ref(ref, default_env=target.default_env)
        out = ops.checkout(config.open_target(target), coord, r, dest)
        _emit(ctx, f"checked out to {out}", {"dest": str(out)})


# --- write commands ---


@app.command()
def publish(
    ctx: typer.Context,
    coord: str = typer.Argument(..., help="name/env"),
    sources: list[str] = typer.Argument(..., help="A directory, files, or logical=source."),
    pointer: str = typer.Option("head", "-p", "--pointer", help="Pointer to advance."),
    also: str | None = typer.Option(None, "--point", help="Also advance this alias."),
    author: str | None = typer.Option(None, "--author", "--as", help="Who is publishing."),
    message: str | None = typer.Option(None, "-m", "--message", help="Why (the change reason)."),
    meta: list[str] = typer.Option([], "--meta", help="Domain metadata key=value (repeatable)."),
) -> None:
    """Publish files as a new version (full replacement of the coordinate's tree)."""
    with _handle():
        target = _target(ctx)
        who = _author(target, author)
        c = refs.parse_coord(coord, default_env=target.default_env)
        metadata: dict[str, Any] = {}
        for item in meta:
            if "=" not in item:
                raise CliError(f"--meta expects key=value, got {item!r}")
            k, _, v = item.partition("=")
            metadata[k] = v
        version = ops.publish(
            config.open_target(target), c, ops.gather_sources(sources),
            pointer=pointer, also_alias=also, metadata=metadata, actor=who, reason=message,
        )
        _emit(ctx, version, {"version": version})


@app.command()
def point(
    ctx: typer.Context,
    target_ref: str = typer.Argument(..., help="name/env[:pointer] (default head)."),
    source: str = typer.Argument(..., help="A version id, 'head', or an alias name."),
    author: str | None = typer.Option(None, "--author", "--as", help="Who is moving it."),
    message: str | None = typer.Option(None, "-m", "--message", help="Why (the move reason)."),
    force: bool = typer.Option(False, "--force", help="Move even if it changed (last wins)."),
) -> None:
    """Move a mutable pointer to an existing version (promote / re-alias / rollback)."""
    with _handle():
        target = _target(ctx)
        who = _author(target, author)
        coord, r = refs.parse_ref(target_ref, default_env=target.default_env)
        name = refs.pointer_name(r)
        src = refs.parse_source(coord, source)
        try:
            version = ops.move_pointer(
                config.open_target(target), coord, name, src,
                force=force, actor=who, reason=message,
            )
        except Conflict as exc:
            raise CliError(
                f"{name!r} moved since it was read — re-run, or pass --force"
            ) from exc
        moved = f"{refs.render_coord(coord)}:{name} -> {version}"
        _emit(ctx, moved, {"pointer": name, "version": version})


@app.command()
def gc(
    ctx: typer.Context,
    keep_last: int = typer.Option(0, "--keep-last", help="Keep the newest N versions/coord."),
    keep_within: str | None = typer.Option(None, "--keep-within", help="Keep within e.g. 30d."),
    grace: str | None = typer.Option(None, "--grace", help="Retain blobs younger than e.g. 1h."),
) -> None:
    """Reclaim unreferenced blobs and out-of-retention manifests."""
    with _handle():
        result = ops.gc(
            config.open_target(_target(ctx)),
            keep_last=keep_last,
            keep_within=parse_duration(keep_within) if keep_within else None,
            grace=parse_duration(grace) if grace else None,
        )
        human = (
            f"dropped {result['dropped_count']} versions, "
            f"deleted {result['deleted_count']} blobs"
        )
        _emit(ctx, human, result)
