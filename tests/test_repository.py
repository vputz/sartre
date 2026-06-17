"""Behavioral tests for Repository: publish, read core, concurrent single-winner."""

from __future__ import annotations

import io

import pytest

from sartre import Conflict, Coordinate, Entry, Pin, Repository

COORD = Coordinate("models", "release")


def test_publish_resolve_open_roundtrip(repo: Repository) -> None:
    files = {"a/cfg.json": b'{"k": 1}', "w.ckpt": b"X" * 500}
    version = repo.publish(COORD, files)
    snap = repo.resolve(COORD)
    assert snap.version == version
    assert {e.path for e in snap.entries} == {"a/cfg.json", "w.ckpt"}
    assert repo.open(snap, "a/cfg.json").read_bytes() == b'{"k": 1}'


def test_publish_is_idempotent(repo: Repository) -> None:
    files = {"a.txt": b"same"}
    assert repo.publish(COORD, files) == repo.publish(COORD, files)


def test_pin_resolves_old_version_after_advance(repo: Repository) -> None:
    v1 = repo.publish(COORD, {"a.txt": b"one"})
    v2 = repo.publish(COORD, {"a.txt": b"two"})
    assert v1 != v2
    assert repo.resolve(COORD, Pin(v1)).version == v1
    assert repo.open(repo.resolve(COORD, Pin(v1)), "a.txt").read_bytes() == b"one"


def test_fetch_all_lays_out_tree(repo: Repository) -> None:
    repo.publish(COORD, {"a/b/c.txt": b"deep", "top.txt": b"top"})
    root = repo.fetch_all(repo.resolve(COORD))
    assert (root / "a" / "b" / "c.txt").read_bytes() == b"deep"
    assert (root / "top.txt").read_bytes() == b"top"


def test_normalizes_paths_on_publish(repo: Repository) -> None:
    repo.publish(COORD, {"a//b.txt": b"x", "dir/": b"y"})  # // and trailing / coerced
    paths = {e.path for e in repo.resolve(COORD).entries}
    assert paths == {"a/b.txt", "dir"}


def test_stale_publish_conflicts_and_does_not_clobber(repo: Repository) -> None:
    # A publisher that started from an empty pointer (start=None) but commits
    # after another publish already advanced the tip must fail-fast on the CAS,
    # leaving the winner's version in place.
    stale_start = None
    v1 = repo.publish(COORD, {"a.txt": b"winner"})  # head: None -> v1

    h = repo.store.put(io.BytesIO(b"loser"))
    v2 = repo.registry.commit(COORD, (Entry("a.txt", h, 5),), {})
    with pytest.raises(Conflict):
        repo.registry.set_pointer(COORD, "head", v2, expected=stale_start)
    assert repo.head(COORD) == v1  # loser did not clobber the winner
