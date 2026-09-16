"""checkout destination safety: refuse a non-empty dir by default; overlay only with overwrite."""

from __future__ import annotations

from pathlib import Path

import pytest

from sartre import Coordinate, PathError, Repository

COORD = Coordinate("models", "prod")


def test_checkout_into_fresh_or_empty_dir(repo: Repository, tmp_path: Path) -> None:
    repo.publish(COORD, {"a.txt": b"A", "d/b.txt": b"B"})
    snap = repo.resolve(COORD)

    out = repo.checkout(snap, tmp_path / "new")  # nonexistent → created
    assert out.joinpath("a.txt").read_bytes() == b"A"
    assert out.joinpath("d", "b.txt").read_bytes() == b"B"

    empty = tmp_path / "empty"
    empty.mkdir()
    repo.checkout(snap, empty)  # existing but empty → allowed
    assert (empty / "a.txt").read_bytes() == b"A"


def test_checkout_refuses_non_empty_dir(repo: Repository, tmp_path: Path) -> None:
    repo.publish(COORD, {"a.txt": b"A"})
    snap = repo.resolve(COORD)
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "stale.txt").write_bytes(b"OLD")

    with pytest.raises(PathError):
        repo.checkout(snap, dest)

    assert not (dest / "a.txt").exists()  # wrote nothing
    assert (dest / "stale.txt").read_bytes() == b"OLD"  # left untouched


def test_checkout_overwrite_overlays_without_pruning(repo: Repository, tmp_path: Path) -> None:
    repo.publish(COORD, {"a.txt": b"A"})
    snap = repo.resolve(COORD)
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "a.txt").write_bytes(b"PREV")    # collision with the version
    (dest / "stale.txt").write_bytes(b"OLD")  # extraneous, not in the version

    out = repo.checkout(snap, dest, overwrite=True)

    assert out.joinpath("a.txt").read_bytes() == b"A"       # collision overwritten
    assert out.joinpath("stale.txt").read_bytes() == b"OLD"  # overlay, not sync: extraneous kept


def test_checkout_onto_a_non_directory_is_refused(repo: Repository, tmp_path: Path) -> None:
    repo.publish(COORD, {"a.txt": b"A"})
    snap = repo.resolve(COORD)
    afile = tmp_path / "afile"
    afile.write_bytes(b"x")

    with pytest.raises(PathError):  # default
        repo.checkout(snap, afile)
    with pytest.raises(PathError):  # --force can't overlay onto a file either
        repo.checkout(snap, afile, overwrite=True)
    assert afile.read_bytes() == b"x"  # untouched


def test_overlay_file_shadowing_a_dir_raises_patherror(repo: Repository, tmp_path: Path) -> None:
    repo.publish(COORD, {"d/x.txt": b"X"})
    snap = repo.resolve(COORD)
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "d").write_bytes(b"blocker")  # a FILE where the version needs a directory

    with pytest.raises(PathError):  # a clean PathError, not a raw NotADirectoryError traceback
        repo.checkout(snap, dest, overwrite=True)
