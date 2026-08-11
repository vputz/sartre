"""Change provenance: actor/reason on the tip event + append-only pointer-move history.

Covers the core invariant (identical content keeps distinct per-event provenance), the
roundtrip through publish/log, append-only ordering, and the rejected-CAS-writes-nothing
rule — the last two across both the memory and SQLite backends.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sartre import Conflict, Coordinate, Entry, MemoryRegistry, Repository, SqliteRegistry

COORD = Coordinate("models", "prod")


def _entries(tag: str) -> tuple[Entry, ...]:
    return (Entry(f"{tag}.bin", f"sha256:{tag * 4}", 3),)


# --- registry-level, both backends ---


@pytest.fixture(params=["memory", "sqlite"])
def registry(request: pytest.FixtureRequest):  # noqa: ANN201 - a Registry of either backend
    if request.param == "memory":
        yield MemoryRegistry()
    else:
        reg = SqliteRegistry(":memory:")
        yield reg
        reg.close()


def test_omitted_actor_recorded_as_unknown(registry) -> None:  # noqa: ANN001
    v = registry.commit(COORD, _entries("a"), {})
    registry.set_pointer(COORD, "head", v, expected=None)  # no actor/reason supplied
    entry = registry.list_log(COORD)[-1]
    assert entry.actor == "unknown" and entry.reason is None
    move = registry.list_pointer_history(COORD)[-1]
    assert move.actor == "unknown" and move.reason is None


def test_rejected_cas_writes_no_move_and_no_log(registry) -> None:  # noqa: ANN001
    v1 = registry.commit(COORD, _entries("a"), {})
    v2 = registry.commit(COORD, _entries("b"), {})
    registry.set_pointer(COORD, "head", v1, expected=None, actor="alice", reason="first")
    registry.set_pointer(COORD, "head", v2, expected=v1, actor="alice", reason="second")
    log_before = [(e.version, e.actor, e.reason) for e in registry.list_log(COORD)]
    moves_before = len(registry.list_pointer_history(COORD))

    with pytest.raises(Conflict):  # stale expected — current is v2
        registry.set_pointer(COORD, "head", v1, expected=v1, actor="mallory", reason="race")

    assert [(e.version, e.actor, e.reason) for e in registry.list_log(COORD)] == log_before
    assert len(registry.list_pointer_history(COORD)) == moves_before


def test_pointer_history_is_append_only_and_ordered(registry) -> None:  # noqa: ANN001
    v1 = registry.commit(COORD, _entries("a"), {})
    v2 = registry.commit(COORD, _entries("b"), {})
    registry.set_pointer(COORD, "head", v1, expected=None, actor="a", reason="create")
    registry.set_pointer(COORD, "head", v2, expected=v1, actor="b", reason="advance")
    registry.set_pointer(COORD, "stable", v1, expected=None, actor="c", reason="promote")

    moves = registry.list_pointer_history(COORD)
    assert [(m.name, m.from_version, m.to_version, m.actor) for m in moves] == [
        ("head", None, v1, "a"),
        ("head", v1, v2, "b"),
        ("stable", None, v1, "c"),
    ]


# --- Repository-level: the core invariant + roundtrip ---


def test_same_content_keeps_distinct_provenance_per_coordinate(
    make_repo: Callable[[], Repository],
) -> None:
    """The load-bearing invariant: a shared version records distinct provenance per event."""
    repo = make_repo()
    dev, prod = Coordinate("m", "dev"), Coordinate("m", "prod")
    files = {"w.bin": b"identical-bytes"}

    v_dev = repo.publish(dev, files, actor="alice", reason="train")
    v_prod = repo.publish(prod, files, actor="bob", reason="promote")

    assert v_dev == v_prod  # same content → one shared, content-addressed version
    dev_tip, prod_tip = repo.list_log(dev)[-1], repo.list_log(prod)[-1]
    assert (dev_tip.actor, dev_tip.reason) == ("alice", "train")
    assert (prod_tip.actor, prod_tip.reason) == ("bob", "promote")  # not shared with dev


def test_publish_reason_stays_out_of_metadata(make_repo: Callable[[], Repository]) -> None:
    repo = make_repo()
    repo.publish(COORD, {"a.bin": b"1"}, metadata={"stage": "rc"}, actor="alice", reason="why")
    snap = repo.resolve(COORD)
    assert dict(snap.metadata) == {"stage": "rc"}  # reason is NOT injected into metadata
    assert repo.list_log(COORD)[-1].reason == "why"


# The repo is reused across examples on purpose — each example checks the latest tip event.
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(actor=st.text(min_size=1, max_size=20), reason=st.text(max_size=40))
def test_provenance_roundtrips_through_publish(
    make_repo: Callable[[], Repository], actor: str, reason: str
) -> None:
    repo = make_repo()
    coord = Coordinate("m", "prod")
    repo.publish(coord, {"a.bin": b"x"}, actor=actor, reason=reason)
    tip = repo.list_log(coord)[-1]
    assert tip.actor == actor and tip.reason == reason


def test_point_records_promotion_provenance(make_repo: Callable[[], Repository]) -> None:
    repo = make_repo()
    v1 = repo.publish(COORD, {"a.bin": b"1"}, actor="alice")
    v2 = repo.publish(COORD, {"a.bin": b"2"}, actor="alice")
    repo.point(COORD, "stable", v2, expected=None, actor="bob", reason="passed eval")

    move = repo.list_pointer_history(COORD)[-1]
    assert (move.name, move.from_version, move.to_version) == ("stable", None, v2)
    assert (move.actor, move.reason) == ("bob", "passed eval")
    assert v1 != v2
