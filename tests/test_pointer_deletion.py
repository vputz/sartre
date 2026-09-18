"""pointer-deletion: delete_pointer (CAS) across backends + base-CAS promote."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import fsspec
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from typer.testing import CliRunner

from sartre import (
    Alias,
    CasStore,
    Conflict,
    Coordinate,
    Entry,
    FsspecBlobBackend,
    MemoryRegistry,
    NotFound,
    Repository,
    SartreError,
    SqliteRegistry,
)
from sartre.cli.app import app
from sartre.ports import Registry

COORD = Coordinate("models", "prod")
runner = CliRunner()


# --- registry-level (§6.1): memory + sqlite ---


@pytest.fixture(params=["memory", "sqlite"])
def reg(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Registry]:
    if request.param == "memory":
        yield MemoryRegistry()
    else:
        r = SqliteRegistry(tmp_path / f"reg-{uuid.uuid4().hex}.sqlite")
        yield r
        r.close()


def _commit(reg: Registry, tag: str) -> str:
    return reg.commit(COORD, (Entry(f"{tag}.bin", f"sha256:{tag * 8}", 3),), {})


def _seed(reg: Registry, name: str, tag: str) -> str:
    v = _commit(reg, tag)
    reg.set_pointer(COORD, name, v, expected=None)
    return v


def test_delete_then_resolve_is_notfound(reg: Registry) -> None:
    v = _seed(reg, "stable", "a")
    reg.delete_pointer(COORD, "stable", expected=v)
    with pytest.raises(NotFound):
        reg.head(COORD, Alias("stable"))
    assert "stable" not in reg.list_pointers(COORD)


def test_delete_cas_conflict_leaves_pointer(reg: Registry) -> None:
    v1 = _seed(reg, "stable", "a")
    v2 = _commit(reg, "b")
    reg.set_pointer(COORD, "stable", v2, expected=v1)  # now at v2
    with pytest.raises(Conflict):
        reg.delete_pointer(COORD, "stable", expected=v1)  # stale expected
    assert reg.list_pointers(COORD)["stable"] == v2


def test_delete_absent_is_idempotent_noop(reg: Registry) -> None:
    reg.delete_pointer(COORD, "never", expected=None)  # no such pointer → no error
    assert reg.list_pointer_history(COORD) == []  # records nothing


def test_delete_records_move_to_none(reg: Registry) -> None:
    v = _seed(reg, "stable", "a")
    reg.delete_pointer(COORD, "stable", expected=v, actor="alice", reason="cleanup")
    moves = reg.list_pointer_history(COORD)
    last = moves[-1]
    assert (last.name, last.from_version, last.to_version) == ("stable", v, None)
    assert (last.actor, last.reason) == ("alice", "cleanup")


def test_delete_then_recreate(reg: Registry) -> None:
    v1 = _seed(reg, "stable", "a")
    reg.delete_pointer(COORD, "stable", expected=v1)
    v2 = _commit(reg, "b")
    reg.set_pointer(COORD, "stable", v2, expected=None)  # re-create (was unset)
    assert reg.list_pointers(COORD)["stable"] == v2


# --- schema migration (§2.2): a pre-change sqlite DB gains a nullable to_version ---


def test_sqlite_migrates_pre_change_to_version_nullable(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(  # the OLD schema: pointer_moves.to_version is NOT NULL
        "CREATE TABLE pointer_moves (move_seq INTEGER PRIMARY KEY AUTOINCREMENT, "
        "coord_name TEXT NOT NULL, coord_env TEXT NOT NULL, pointer TEXT NOT NULL, "
        "from_version TEXT, to_version TEXT NOT NULL, "
        "actor TEXT NOT NULL, reason TEXT, at TEXT NOT NULL);"
        "INSERT INTO pointer_moves(coord_name, coord_env, pointer, from_version, to_version, "
        "actor, reason, at) VALUES "
        "('models','prod','head', NULL, 'sha256:aa', 'alice', 'init', '2026-01-01T00:00:00+00:00');"
    )
    conn.commit()
    conn.close()

    reg = SqliteRegistry(db)  # opening runs the migration
    try:
        moves = reg.list_pointer_history(COORD)
        assert any(m.name == "head" and m.to_version == "sha256:aa" for m in moves)  # row survived
        # a deletion (to_version NULL) is now recordable — impossible under the old NOT NULL column
        v = _commit(reg, "z")
        reg.set_pointer(COORD, "tmp", v, expected=None)
        reg.delete_pointer(COORD, "tmp", expected=v)
        deleted = [m for m in reg.list_pointer_history(COORD) if m.name == "tmp"]
        assert deleted and deleted[-1].to_version is None
    finally:
        reg.close()


# --- facade (§6.2) ---


def _repo() -> Repository:
    fs = fsspec.filesystem("memory")
    return Repository(MemoryRegistry(), CasStore(FsspecBlobBackend(fs, f"b-{uuid.uuid4().hex}")))


def test_facade_refuses_to_delete_head() -> None:
    repo = _repo()
    repo.publish(COORD, {"a.txt": b"A"})
    with pytest.raises(SartreError):
        repo.delete_pointer(COORD, "head")
    assert repo.head(COORD)  # head untouched


def test_facade_delete_absent_alias_is_noop() -> None:
    repo = _repo()
    repo.publish(COORD, {"a.txt": b"A"})
    repo.delete_pointer(COORD, "ghost")  # no error


def test_promote_clean_stage_advances_head() -> None:
    repo = _repo()
    base = repo.publish(COORD, {"a.txt": b"A"})
    staged = repo.publish_over(
        COORD, changes={"a.txt": b"B"}, pointer=f"staging-{uuid.uuid4().hex[:8]}",
        metadata={"derived_from": base},
    ).version
    # head still at base → base-CAS promote succeeds
    got = repo.promote(COORD, Alias(_only_staging(repo)))
    assert got == staged
    assert repo.head(COORD) == staged


def test_promote_conflicts_when_head_moved() -> None:
    repo = _repo()
    base = repo.publish(COORD, {"a.txt": b"A"})
    sp = f"staging-{uuid.uuid4().hex[:8]}"
    staged = repo.publish_over(
        COORD, changes={"a.txt": b"B"}, pointer=sp, metadata={"derived_from": base},
    ).version
    moved = repo.publish(COORD, {"a.txt": b"C"})  # head advances off base during "verification"
    with pytest.raises(Conflict):
        repo.promote(COORD, Alias(sp))
    assert repo.head(COORD) == moved  # concurrent publish not clobbered
    assert staged != moved


def test_promote_refuses_without_derived_from() -> None:
    repo = _repo()
    repo.publish(COORD, {"a.txt": b"A"})
    v2 = repo.publish_over(COORD, changes={"a.txt": b"B"}, pointer="handmade").version
    with pytest.raises(SartreError):
        repo.promote(COORD, Alias("handmade"))  # no derived_from
    # force falls back to last-writer against current head
    got = repo.promote(COORD, Alias("handmade"), force=True)
    assert got == v2 and repo.head(COORD) == v2


def _only_staging(repo: Repository) -> str:
    return next(n for n in repo.list_pointers(COORD) if n.startswith("staging-"))


# --- property (§6.4) ---


@settings(max_examples=40)
@given(ops=st.lists(st.sampled_from(["set", "del"]), min_size=1, max_size=8))
def test_property_resolve_tracks_last_op(ops: list[str]) -> None:
    reg = MemoryRegistry()
    v = reg.commit(COORD, (Entry("x.bin", "sha256:" + "ab" * 16, 2),), {})
    expected_set = False  # whether "p" currently exists
    for op in ops:
        if op == "set":
            cur = v if expected_set else None
            reg.set_pointer(COORD, "p", v, expected=cur)
            expected_set = True
        else:  # del
            cur = v if expected_set else None
            reg.delete_pointer(COORD, "p", expected=cur)
            expected_set = False
        if expected_set:
            assert reg.head(COORD, Alias("p")) == v
        else:
            with pytest.raises(NotFound):
                reg.head(COORD, Alias("p"))


# --- CLI e2e (§6.5) ---


def _run(repo: Path, *args: str):  # noqa: ANN202
    return runner.invoke(app, ["--repo", str(repo), *args])


def test_cli_delete_pointer_and_refuse_head(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    f = tmp_path / "a.txt"
    f.write_bytes(b"A")
    _run(repo, "publish", "m/prod", str(f), "--as", "a", "--point", "stable")
    assert _run(repo, "head", "m/prod:stable").exit_code == 0  # alias exists
    r = _run(repo, "delete-pointer", "m/prod:stable", "--as", "a")
    assert r.exit_code == 0, r.output
    assert _run(repo, "head", "m/prod:stable").exit_code != 0  # alias gone
    assert _run(repo, "head", "m/prod").exit_code == 0  # head remains
    r = _run(repo, "delete-pointer", "m/prod", "--as", "a")  # bare coord → head
    assert r.exit_code != 0 and "head" in r.output


def test_cli_stage_records_derived_from_and_promote(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    f = tmp_path / "a.txt"
    f.write_bytes(b"A")
    v1 = _run(repo, "publish", "m/prod", str(f), "--as", "a").stdout.strip()
    f2 = tmp_path / "a2.txt"
    f2.write_bytes(b"B")
    r = _run(repo, "--json", "publish-over", "m/prod", f"a.txt={f2}", "--stage", "--as", "b")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    staged, version = out["staged"], out["version"]
    # derived_from recorded on the staged version
    show = json.loads(_run(repo, "--json", "show", f"m/prod:{staged}").stdout)
    assert show["metadata"]["derived_from"] == v1
    # promote advances head and cleans up the staging alias
    r = _run(repo, "promote", "m/prod", staged, "--as", "b", "-m", "verified")
    assert r.exit_code == 0, r.output
    assert _run(repo, "head", "m/prod").stdout.strip() == version
    # the staging alias is cleaned up
    assert _run(repo, "head", f"m/prod:{staged}").exit_code != 0


def test_cli_promote_refuses_when_head_moved(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    f = tmp_path / "a.txt"
    f.write_bytes(b"A")
    _run(repo, "publish", "m/prod", str(f), "--as", "a")
    f2 = tmp_path / "a2.txt"
    f2.write_bytes(b"B")
    staged = json.loads(
        _run(repo, "--json", "publish-over", "m/prod", f"a.txt={f2}", "--stage", "--as", "b").stdout
    )["staged"]
    f3 = tmp_path / "a3.txt"
    f3.write_bytes(b"C")
    _run(repo, "publish-over", "m/prod", f"a.txt={f3}", "--as", "b")  # head moves off base
    r = _run(repo, "promote", "m/prod", staged, "--as", "b")
    assert r.exit_code != 0 and "moved" in r.output
