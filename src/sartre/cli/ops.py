"""Framework-free operations backing the CLI (and reusable by a future TUI).

Each function takes an open :class:`~sartre.repository.Repository` plus already-parsed
arguments and returns plain data (dicts / ids / paths). No Typer, no printing — rendering
and ``--json`` live in the Typer layer. This is the seam a Textual UI would call directly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sartre.cli.errors import CliError
from sartre.model import Alias, Coordinate, Head, Pin, Ref, Version
from sartre.repository import Repository, RetentionPolicy

# --- source gathering for publish (filesystem logic, testable without Typer) ---


def gather_sources(args: Sequence[str]) -> dict[str, Path]:
    """Build a ``{logical_path: file}`` map from CLI publish arguments.

    A directory maps every file under it to its relative path; an explicit file maps to its
    basename; a ``logical=source`` token overrides the logical path for a source.
    """
    out: dict[str, Path] = {}
    for arg in args:
        logical_override, source = _split_remap(arg)
        if source.is_dir():
            if logical_override is not None:
                raise CliError(f"cannot remap a directory: {arg!r}")
            for f in sorted(source.rglob("*")):
                if f.is_file():
                    _place(out, f.relative_to(source).as_posix(), f)
        elif source.is_file():
            _place(out, logical_override or source.name, source)
        else:
            raise CliError(f"no such file or directory: {source}")
    return out


def _split_remap(arg: str) -> tuple[str | None, Path]:
    """Interpret a publish arg as either ``logical=source`` (remap) or a bare path."""
    if "=" in arg:
        logical, _, src = arg.partition("=")
        if Path(src).exists():
            return logical, Path(src)
    return None, Path(arg)


def _place(out: dict[str, Path], logical: str, source: Path) -> None:
    if logical in out:
        raise CliError(f"duplicate logical path {logical!r}")
    out[logical] = source


# --- read operations ---


def _entry_rows(repo: Repository, coord: Coordinate, ref: Ref) -> list[dict[str, Any]]:
    snap = repo.resolve(coord, ref)
    return [
        {"path": e.path, "content_hash": e.content_hash, "size": e.size} for e in snap.entries
    ]


def _commit_provenance(repo: Repository, coord: Coordinate, version: Version) -> dict[str, Any]:
    """The actor/reason of the tip event that first made ``version`` a tip (its creator)."""
    for e in repo.list_log(coord):  # oldest first → first match is the creating event
        if e.version == version:
            return {"actor": e.actor, "reason": e.reason}
    return {"actor": None, "reason": None}


def show(repo: Repository, coord: Coordinate, ref: Ref) -> dict[str, Any]:
    snap = repo.resolve(coord, ref)
    prov = _commit_provenance(repo, coord, snap.version)
    return {
        "coordinate": {"name": coord.name, "env": coord.env},
        "version": snap.version,
        "created_at": snap.created_at.isoformat(),
        "actor": prov["actor"],
        "reason": prov["reason"],
        "metadata": dict(snap.metadata),
        "entries": [
            {"path": e.path, "content_hash": e.content_hash, "size": e.size} for e in snap.entries
        ],
    }


def head(repo: Repository, coord: Coordinate, ref: Ref) -> Version:
    return repo.head(coord, ref)


def ls(repo: Repository, coord: Coordinate, ref: Ref) -> list[dict[str, Any]]:
    return _entry_rows(repo, coord, ref)


def materialize(repo: Repository, coord: Coordinate, ref: Ref, path: str) -> Path:
    """Materialize one file to a temp path (verified via the store) for `cat`."""
    snap = repo.resolve(coord, ref)
    return repo.open(snap, path)


def log(repo: Repository, coord: Coordinate) -> list[dict[str, Any]]:
    pointers = repo.list_pointers(coord)  # annotate which versions current pointers target
    at: dict[Version, list[str]] = {}
    for name, version in pointers.items():
        at.setdefault(version, []).append(name)
    return [
        {
            "version": e.version,
            "seq": e.seq,
            "created_at": e.created_at.isoformat(),
            "actor": e.actor,
            "reason": e.reason,
            "pointers": sorted(at.get(e.version, [])),
        }
        for e in repo.list_log(coord)
    ]


def pointer_history(repo: Repository, coord: Coordinate) -> list[dict[str, Any]]:
    """The coordinate's pointer-move audit trail as plain dicts (oldest first)."""
    return [
        {
            "pointer": m.name,
            "from_version": m.from_version,
            "to_version": m.to_version,
            "actor": m.actor,
            "reason": m.reason,
            "at": m.at.isoformat(),
        }
        for m in repo.list_pointer_history(coord)
    ]


def coords(repo: Repository) -> list[dict[str, str]]:
    return [{"name": c.name, "env": c.env} for c in repo.list_coordinates()]


def checkout(repo: Repository, coord: Coordinate, ref: Ref, dest: Path) -> Path:
    snap = repo.resolve(coord, ref)
    return repo.checkout(snap, dest)


# --- write operations ---


def publish(
    repo: Repository,
    coord: Coordinate,
    sources: Mapping[str, Path],
    *,
    pointer: str = "head",
    also_alias: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    actor: str = "unknown",
    reason: str | None = None,
) -> Version:
    """Full-replacement publish of the given ``{logical: file}`` map; returns the version."""
    if not sources:
        raise CliError("nothing to publish: give a directory or one or more files")
    files: dict[str, Path] = dict(sources)
    version = repo.publish(
        coord, files, pointer=pointer, metadata=dict(metadata or {}), actor=actor, reason=reason
    )
    if also_alias is not None and also_alias != pointer:
        move_pointer(repo, coord, also_alias, Pin(version), force=False, actor=actor, reason=reason)
    return version


def move_pointer(
    repo: Repository,
    coord: Coordinate,
    name: str,
    source: Ref,
    *,
    force: bool,
    actor: str = "unknown",
    reason: str | None = None,
    max_retries: int = 5,
) -> Version:
    """Move pointer ``name`` to the version ``source`` resolves to. CAS-safe unless ``force``.

    Raises :class:`~sartre.errors.Conflict` on a concurrent move when not forcing; with
    ``force`` it re-reads and retries (last-writer-wins). ``actor``/``reason`` attribute
    each attempted move in the pointer-move history.
    """
    from sartre.errors import Conflict, NotFound

    version = source.version if isinstance(source, Pin) else repo.head(coord, source)
    target = Head() if name == "head" else Alias(name)
    for _ in range(max_retries + 1):
        try:
            current: Version | None = repo.head(coord, target)
        except NotFound:
            current = None
        try:
            repo.point(coord, name, version, expected=current, actor=actor, reason=reason)
            return version
        except Conflict:
            if not force:
                raise
    raise CliError(f"pointer {name!r} kept moving under --force; giving up")


def gc(
    repo: Repository,
    *,
    keep_last: int = 0,
    keep_within: Any = None,
    grace: Any = None,
) -> dict[str, Any]:
    policy = RetentionPolicy(keep_last_n=keep_last, keep_within=keep_within, grace=grace)
    result = repo.gc(policy)
    return {
        "dropped_versions": list(result.dropped_versions),
        "deleted_blobs": list(result.deleted_blobs),
        "dropped_count": len(result.dropped_versions),
        "deleted_count": len(result.deleted_blobs),
    }
