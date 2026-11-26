"""fix-promote-base-from-staging-pointer: promote recovers its CAS base from the staging
pointer's move (not content-addressed manifest metadata), no-ops when head already holds the
target (incl. after a CAS conflict), refuses a non-alias/no-base ref, and the `base` field +
`pointer_base` lookup work across backends with an additive migration.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path

import fsspec
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sartre import (
    Alias,
    CasStore,
    Conflict,
    Coordinate,
    Entry,
    FsspecBlobBackend,
    MemoryRegistry,
    Repository,
    SartreError,
    SqliteRegistry,
)
from sartre.model import Pin
from sartre.ports import Registry

COORD = Coordinate("models", "prod")


def _store() -> CasStore:
    fs = fsspec.filesystem("memory")
    return CasStore(FsspecBlobBackend(fs, f"b-{uuid.uuid4().hex}"))


def _commit(reg: Registry, tag: str) -> str:
    return reg.commit(COORD, (Entry(f"{tag}.bin", f"sha256:{tag * 8}", 3),), {})


def _sp(result: object) -> str:
    """The staged pointer name from a stage=True derive (narrowed from ``str | None``)."""
    name = result.staged_pointer  # type: ignore[attr-defined]
    assert name is not None
    return name


@pytest.fixture(params=["memory", "sqlite"])
def reg(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Registry]:
    if request.param == "memory":
        yield MemoryRegistry()
    else:
        r = SqliteRegistry(tmp_path / f"reg-{uuid.uuid4().hex}.sqlite")
        yield r
        r.close()


@pytest.fixture(params=["memory", "sqlite"])
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Repository]:
    store = _store()
    if request.param == "memory":
        yield Repository(MemoryRegistry(), store)
    else:
        r = SqliteRegistry(tmp_path / f"reg-{uuid.uuid4().hex}.sqlite")
        yield Repository(r, store)
        r.close()


# --- 6.1 regression: the staged tree already exists as a version -------------------------


def test_promote_revert_to_an_existing_tree(repo: Repository) -> None:
    """Revert: a derive that reproduces an earlier tree dedups to its version, whose manifest
    metadata belongs to the earlier commit — promote must still succeed via the pointer base."""
    base = repo.publish(COORD, {"a.txt": b"A"})
    r1 = repo.publish_over(COORD, changes={"a.txt": b"B"}, stage=True)
    repo.promote(COORD, Alias(_sp(r1)))
    assert repo.head(COORD) == r1.version
    r2 = repo.publish_over(COORD, changes={"a.txt": b"A"}, stage=True)  # back to A
    assert r2.version == base  # dedup: the reverted tree already exists
    repo.promote(COORD, Alias(_sp(r2)))  # pre-fix: false Conflict
    assert repo.head(COORD) == base


def test_promote_adopts_another_coords_existing_tree(repo: Repository) -> None:
    """Cross-coordinate adoption: coord2 stages a tree coord1 already committed (dedup), so the
    staged version carries coord1's metadata; promote must base-CAS on coord2's own base."""
    c1 = Coordinate("m", "dev")
    c2 = Coordinate("m", "stage")
    va = repo.publish(c1, {"a.txt": b"A"})
    repo.publish(c2, {"a.txt": b"B"})
    r = repo.publish_over(c2, changes={"a.txt": b"A"}, stage=True)
    assert r.version == va  # dedup: c2's staged tree is c1's existing version
    repo.promote(c2, Alias(_sp(r)))
    assert repo.head(c2) == va


# --- 6.2 concurrent / duplicate publish is a no-op success -------------------------------


def test_promote_is_noop_when_head_already_at_target(repo: Repository) -> None:
    repo.publish(COORD, {"a.txt": b"A"})
    r = repo.publish_over(COORD, changes={"a.txt": b"B"}, stage=True)
    repo.promote(COORD, Alias(_sp(r)))
    assert repo.head(COORD) == r.version
    assert repo.promote(COORD, Alias(_sp(r))) == r.version  # again: no-op
    assert repo.head(COORD) == r.version


def test_promote_rechecks_after_conflict_from_duplicate(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duplicate publish of the same tree landing between the no-op check and the CAS makes the
    CAS conflict; promote must re-read head, see the target, and return success — not Conflict."""
    base = repo.publish(COORD, {"a.txt": b"A"})
    r = repo.publish_over(COORD, changes={"a.txt": b"B"}, stage=True)
    real_set = repo.registry.set_pointer
    state = {"armed": True}

    def racing_set(
        coord: Coordinate, name: str, version: str, *, expected, base=None,  # noqa: ANN001
        actor: str = "unknown", reason: str | None = None,
    ) -> None:
        if state["armed"] and name == "head":  # the promote CAS: simulate the race, then lose it
            state["armed"] = False
            real_set(coord, name, version, expected=expected, base=base, actor="dup", reason="race")
            raise Conflict("simulated lost race to a duplicate publish")
        real_set(coord, name, version, expected=expected, base=base, actor=actor, reason=reason)

    monkeypatch.setattr(repo.registry, "set_pointer", racing_set)
    assert repo.promote(COORD, Alias(_sp(r))) == r.version
    assert repo.head(COORD) == r.version
    assert base != r.version


# --- 6.3 lost-update guard ---------------------------------------------------------------


def test_promote_conflicts_when_head_moved_to_a_different_tree(repo: Repository) -> None:
    repo.publish(COORD, {"a.txt": b"A"})
    r = repo.publish_over(COORD, changes={"a.txt": b"B"}, stage=True)
    moved = repo.publish(COORD, {"a.txt": b"C"})  # head → a different tree
    with pytest.raises(Conflict):
        repo.promote(COORD, Alias(_sp(r)))
    assert repo.head(COORD) == moved  # concurrent publish not clobbered


# --- 6.4 non-alias / no recoverable base ------------------------------------------------


def test_promote_pin_ref_refuses_without_force(repo: Repository) -> None:
    repo.publish(COORD, {"a.txt": b"A"})
    r = repo.publish_over(COORD, changes={"a.txt": b"B"}, stage=True)
    with pytest.raises(SartreError):
        repo.promote(COORD, Pin(r.version))  # no pointer to read a base from
    got = repo.promote(COORD, Pin(r.version), force=True)  # last-writer
    assert got == r.version and repo.head(COORD) == r.version


# --- 6.5 pointer_base: recovery + tombstone-skipping (registry level) --------------------


def test_pointer_base_recovers_and_survives_deletion(reg: Registry) -> None:
    v1 = _commit(reg, "a")
    v2 = _commit(reg, "b")
    reg.set_pointer(COORD, "staging-x", v2, expected=None, base=v1)
    assert reg.pointer_base(COORD, "staging-x") == v1
    reg.delete_pointer(COORD, "staging-x", expected=v2)  # tombstone above the advance
    assert reg.pointer_base(COORD, "staging-x") == v1  # read past the tombstone


def test_pointer_base_is_none_without_a_recorded_base(reg: Registry) -> None:
    v1 = _commit(reg, "a")
    reg.set_pointer(COORD, "head", v1, expected=None)  # a non-staging advance records no base
    assert reg.pointer_base(COORD, "head") is None
    assert reg.pointer_base(COORD, "ghost") is None  # never existed


# --- 6.5a property: pointer_base == base of the newest advance ---------------------------


@given(ops=st.lists(st.sampled_from(["DEL", "ADV_NONE", "B1", "B2"]), max_size=10))
@settings(max_examples=150)
def test_property_pointer_base_is_newest_advance_base(ops: list[str]) -> None:
    reg = MemoryRegistry()
    name = "staging-p"
    current: str | None = None
    expected_base: str | None = None
    i = 0
    for op in ops:
        if op == "DEL":
            if current is not None:
                reg.delete_pointer(COORD, name, expected=current)
                current = None
        else:
            v = _commit(reg, f"v{i}")
            i += 1
            base = None if op == "ADV_NONE" else op
            reg.set_pointer(COORD, name, v, expected=current, base=base)
            current = v
            expected_base = base  # newest advance's base, regardless of later deletes
    assert reg.pointer_base(COORD, name) == expected_base


# --- 6.6 facade property: head never silently clobbers -----------------------------------


@given(seq=st.lists(st.sampled_from(["pub", "stage_promote", "promote_again"]), max_size=12))
@settings(max_examples=60, deadline=None)
def test_property_head_never_clobbers(seq: list[str]) -> None:
    repo = Repository(MemoryRegistry(), _store())
    repo.publish(COORD, {"a.txt": b"seed"})
    last = None
    n = 0
    for op in seq:
        if op == "pub":
            repo.publish(COORD, {"a.txt": f"p{n}".encode()})
            n += 1
        elif op == "stage_promote":
            before = repo.head(COORD)
            r = repo.publish_over(COORD, changes={"a.txt": f"s{n}".encode()}, stage=True)
            n += 1
            assert repo.head(COORD) == before  # staging never touches head
            repo.promote(COORD, Alias(_sp(r)))
            assert repo.head(COORD) == r.version  # advanced to the staged version
            last = r
        elif op == "promote_again" and last is not None:
            h = repo.head(COORD)
            try:
                repo.promote(COORD, Alias(_sp(last)))
            except Conflict:
                assert repo.head(COORD) == h  # a conflict never moves head
            else:
                assert repo.head(COORD) in {h, last.version}  # no-op or re-advance, never clobber


# --- 6.7 additive migration for the base_version column ----------------------------------


def _old_db(path: Path, *, to_version_notnull: bool) -> None:
    """Create a pre-change pointer_moves table (no base_version column) with one row."""
    notnull = " NOT NULL" if to_version_notnull else ""
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE pointer_moves (move_seq INTEGER PRIMARY KEY AUTOINCREMENT, "
        "coord_name TEXT NOT NULL, coord_env TEXT NOT NULL, pointer TEXT NOT NULL, "
        f"from_version TEXT, to_version TEXT{notnull}, "
        "actor TEXT NOT NULL, reason TEXT, at TEXT NOT NULL);"
        "INSERT INTO pointer_moves(coord_name, coord_env, pointer, from_version, to_version, "
        "actor, reason, at) VALUES "
        "('models','prod','head', NULL, 'sha256:aa', 'alice', 'init', '2026-01-01T00:00:00+00:00');"
    )
    conn.commit()
    conn.close()


@pytest.mark.parametrize("to_version_notnull", [False, True])
def test_sqlite_migrates_to_add_base_version(tmp_path: Path, to_version_notnull: bool) -> None:
    """Both migration paths end with a usable base_version: the ADD COLUMN branch (to_version
    already nullable) and the table-rebuild branch (to_version still NOT NULL)."""
    db = tmp_path / "old.sqlite"
    _old_db(db, to_version_notnull=to_version_notnull)
    reg = SqliteRegistry(db)  # opening runs the migration
    try:
        moves = reg.list_pointer_history(COORD)
        assert any(m.to_version == "sha256:aa" and m.base is None for m in moves)  # row survived
        v1 = _commit(reg, "a")
        v2 = _commit(reg, "b")
        reg.set_pointer(COORD, "staging-x", v2, expected=None, base=v1)  # base_version column works
        assert reg.pointer_base(COORD, "staging-x") == v1
    finally:
        reg.close()
