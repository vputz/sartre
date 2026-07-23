"""Repository.point compare-and-swap pointer move + enumeration delegators."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from sartre import Conflict, Coordinate, NotFound, Repository
from sartre.model import Alias

COORD = Coordinate("models", "prod")


def test_point_promote_move_and_cas(repo: Repository) -> None:
    v1 = repo.publish(COORD, {"a.bin": b"1"})
    v2 = repo.publish(COORD, {"a.bin": b"2"})  # head is now v2

    repo.point(COORD, "stable", v1, expected=None)  # create the alias
    assert repo.head(COORD, Alias("stable")) == v1

    repo.point(COORD, "stable", v2, expected=v1)  # move with correct expected
    assert repo.head(COORD, Alias("stable")) == v2

    with pytest.raises(Conflict):  # stale expected — current is v2
        repo.point(COORD, "stable", v1, expected=v1)
    assert repo.head(COORD, Alias("stable")) == v2  # unchanged


def test_point_refuses_uncommitted_version(repo: Repository) -> None:
    repo.publish(COORD, {"a.bin": b"1"})
    with pytest.raises(NotFound):
        repo.point(COORD, "stable", "sha256:" + "0" * 64, expected=None)


def test_enumeration_delegators(repo: Repository) -> None:
    v = repo.publish(COORD, {"a.bin": b"1"})
    assert (COORD.name, COORD.env) in {(c.name, c.env) for c in repo.list_coordinates()}
    assert repo.list_pointers(COORD)["head"] == v
    assert [e.version for e in repo.list_log(COORD)] == [v]


def test_point_rollback_moves_head(make_repo: Callable[[], Repository]) -> None:
    repo = make_repo()
    v1 = repo.publish(COORD, {"a.bin": b"1"})
    v2 = repo.publish(COORD, {"a.bin": b"2"})
    assert repo.head(COORD) == v2
    repo.point(COORD, "head", v1, expected=v2)  # rollback
    assert repo.head(COORD) == v1
