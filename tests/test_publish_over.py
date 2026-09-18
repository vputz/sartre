"""publish_over: derive a new version from a base, reusing unchanged entries by hash."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

import fsspec
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from typer.testing import CliRunner

from sartre import (
    HEAD,
    CasStore,
    Conflict,
    Coordinate,
    FsspecBlobBackend,
    MemoryRegistry,
    PathError,
    Repository,
)
from sartre.cli.app import app
from sartre.model import Pin, Ref

COORD = Coordinate("models", "prod")
runner = CliRunner()


class _CountingBackend:
    """A `BlobBackend` counting `stage` calls (the upload op)."""

    def __init__(self, inner: FsspecBlobBackend) -> None:
        self.inner = inner
        self.stages = 0

    def get(self, key: str) -> BinaryIO:
        return self.inner.get(key)

    def stage(self, data: BinaryIO) -> str:
        self.stages += 1
        return self.inner.stage(data)

    def promote(self, staging_key: str, final_key: str) -> None:
        self.inner.promote(staging_key, final_key)

    def exists(self, key: str) -> bool:
        return self.inner.exists(key)

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    def list(self) -> Iterable[str]:
        return self.inner.list()

    def mtime(self, key: str) -> float | None:
        return self.inner.mtime(key)


def _counting_repo() -> tuple[Repository, _CountingBackend]:
    fs = fsspec.filesystem("memory")
    backend = _CountingBackend(FsspecBlobBackend(fs, root=f"b-{uuid.uuid4().hex}"))
    return Repository(MemoryRegistry(), CasStore(backend)), backend


# --- change one file, reuse the rest ---


def test_change_one_file_reuses_the_rest_by_hash() -> None:
    repo, backend = _counting_repo()
    v1 = repo.publish(COORD, {"a.txt": b"AAA", "b.txt": b"BBB"})
    assert backend.stages == 2

    r = repo.publish_over(COORD, changes={"a.txt": b"AAA-2"})  # base defaults to head
    v2 = r.version
    assert backend.stages == 3  # only a.txt uploaded; b.txt neither uploaded nor re-hashed
    assert v2 != v1
    assert (r.inherited, r.replaced, r.added, r.removed, r.renamed) == (1, 1, 0, 0, 0)

    snap = repo.resolve(COORD)
    assert {e.path for e in snap.entries} == {"a.txt", "b.txt"}
    assert repo.open(snap, "a.txt").read_bytes() == b"AAA-2"
    assert repo.open(snap, "b.txt").read_bytes() == b"BBB"  # reused blob still resolvable


# --- identity invariants ---


def test_noop_derive_equals_base() -> None:
    repo, _ = _counting_repo()
    v1 = repo.publish(COORD, {"a.txt": b"A", "b.txt": b"B"})
    v2 = repo.publish_over(COORD, changes={}, remove=()).version
    assert v2 == v1


def test_identical_override_equals_base_and_uploads_nothing() -> None:
    repo, backend = _counting_repo()
    v1 = repo.publish(COORD, {"a.txt": b"A"})
    assert backend.stages == 1
    v2 = repo.publish_over(COORD, changes={"a.txt": b"A"}).version  # byte-identical override
    assert v2 == v1
    assert backend.stages == 1  # known_hash skip: no re-upload


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(files=st.dictionaries(st.text("abc", min_size=1, max_size=4), st.binary(max_size=16),
                             min_size=1, max_size=4))
def test_property_noop_derive_is_identity(make_repo, files: dict[str, bytes]) -> None:  # noqa: ANN001
    repo = make_repo()
    v1 = repo.publish(COORD, files)
    assert repo.publish_over(COORD, changes={}).version == v1


# --- remove ---


def test_remove_drops_a_path() -> None:
    repo, _ = _counting_repo()
    repo.publish(COORD, {"a.txt": b"A", "old.txt": b"O"})
    repo.publish_over(COORD, remove=["old.txt"])
    assert {e.path for e in repo.resolve(COORD).entries} == {"a.txt"}


def test_remove_absent_path_raises() -> None:
    repo, _ = _counting_repo()
    repo.publish(COORD, {"a.txt": b"A"})
    with pytest.raises(PathError):
        repo.publish_over(COORD, remove=["nope.txt"])


# --- base selection, provenance, pointer ---


def test_derive_from_pin_and_advance_head() -> None:
    repo, _ = _counting_repo()
    v1 = repo.publish(COORD, {"a.txt": b"1"})
    v2 = repo.publish(COORD, {"a.txt": b"2"})  # head is v2
    v3 = repo.publish_over(COORD, base=Pin(v1), changes={"b.txt": b"B"}, actor="x").version
    assert repo.head(COORD) == v3  # head advanced to the derived version
    snap = repo.resolve(COORD)
    assert {e.path for e in snap.entries} == {"a.txt", "b.txt"}
    assert repo.open(snap, "a.txt").read_bytes() == b"1"  # derived from v1, not v2
    assert v3 not in (v1, v2)


def test_derive_records_provenance() -> None:
    repo, _ = _counting_repo()
    repo.publish(COORD, {"a.txt": b"A"}, actor="alice", reason="init")
    repo.publish_over(COORD, changes={"a.txt": b"A2"}, actor="bob", reason="tweak")
    tip = repo.list_log(COORD)[-1]
    assert (tip.actor, tip.reason) == ("bob", "tweak")


# --- CLI e2e ---


def _run(repo: Path, *args: str):  # noqa: ANN202
    return runner.invoke(app, ["--repo", str(repo), *args])


def test_cli_publish_over(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    src = tmp_path / "src"
    (src / "w").mkdir(parents=True)
    (src / "w" / "model.bin").write_bytes(b"WEIGHTS")
    (src / "cfg.json").write_bytes(b"{}")
    v1 = _run(repo, "publish", "m/prod", str(src), "--as", "alice").stdout.strip()

    newcfg = tmp_path / "cfg2.json"
    newcfg.write_bytes(b'{"lr":1}')
    r = _run(repo, "publish-over", "m/prod", f"cfg.json={newcfg}", "--as", "bob", "-m", "tune")
    assert r.exit_code == 0
    v2 = r.stdout.splitlines()[0].strip()  # version on line 1, counts on line 2
    assert v2 != v1
    assert "replaced 1" in r.stdout and "inherited 1" in r.stdout

    data = json.loads(_run(repo, "--json", "show", "m/prod").stdout)
    paths = {e["path"] for e in data["entries"]}
    assert paths == {"cfg.json", "w/model.bin"}  # weights inherited, cfg changed
    assert _run(repo, "cat", "m/prod", "cfg.json").stdout == '{"lr":1}'
    assert _run(repo, "cat", "m/prod", "w/model.bin").stdout == "WEIGHTS"  # reused

    # derive from an explicit (now non-head) pin, removing a path — a divergent base
    r = _run(repo, "publish-over", "m/prod", "--from", f"@{v1}", "--rm", "cfg.json", "--as", "bob")
    assert r.exit_code != 0  # refused: base is not head, no --force
    r = _run(
        repo, "publish-over", "m/prod", "--from", f"@{v1}", "--rm", "cfg.json",
        "--as", "bob", "--force", "-m", "roll cfg back off v1",
    )
    assert r.exit_code == 0
    assert {e["path"] for e in json.loads(_run(repo, "--json", "ls", "m/prod").stdout)} == {
        "w/model.bin"
    }


# --- rename (§6.2) ---


def test_rename_reuses_blob_and_counts() -> None:
    repo, backend = _counting_repo()
    repo.publish(COORD, {"a.txt": b"A", "b.txt": b"B"})
    assert backend.stages == 2
    r = repo.publish_over(COORD, rename={"a.txt": "c.txt"})
    assert backend.stages == 2  # a rename touches no blobs
    assert (r.renamed, r.inherited, r.replaced, r.added, r.removed) == (1, 1, 0, 0, 0)
    snap = repo.resolve(COORD)
    assert {e.path for e in snap.entries} == {"b.txt", "c.txt"}
    assert repo.open(snap, "c.txt").read_bytes() == b"A"  # same blob, new path


def test_rename_absent_path_raises() -> None:
    repo, _ = _counting_repo()
    repo.publish(COORD, {"a.txt": b"A"})
    with pytest.raises(PathError):
        repo.publish_over(COORD, rename={"nope.txt": "x.txt"})


def test_counts_cover_every_dimension() -> None:
    repo, _ = _counting_repo()
    repo.publish(COORD, {"keep.txt": b"K", "old.txt": b"O", "mv.txt": b"M", "drop.txt": b"D"})
    r = repo.publish_over(
        COORD,
        changes={"old.txt": b"O2", "new.txt": b"N"},  # 1 replaced + 1 added
        remove=["drop.txt"],                            # 1 removed
        rename={"mv.txt": "moved.txt"},                 # 1 renamed
    )
    assert (r.inherited, r.replaced, r.added, r.removed, r.renamed) == (1, 1, 1, 1, 1)


# --- concurrency: stale base must conflict, not clobber (§6.3) ---


def test_stale_base_conflicts_and_does_not_clobber() -> None:
    repo, _ = _counting_repo()
    repo.publish(COORD, {"a.txt": b"1"})  # head = v1
    orig_resolve = repo.resolve

    def racing_resolve(coord: Coordinate, ref: Ref = HEAD):  # noqa: ANN202
        snap = orig_resolve(coord, ref)
        if not getattr(racing_resolve, "fired", False):
            racing_resolve.fired = True  # type: ignore[attr-defined]
            repo.publish(COORD, {"a.txt": b"2"})  # a concurrent writer advances head to v2
        return snap

    repo.resolve = racing_resolve  # type: ignore[method-assign]
    with pytest.raises(Conflict):
        # base is head-at-read (v1); by advance time head is v2 → CAS on v1 must fail
        repo.publish_over(COORD, changes={"a.txt": b"3"})
    repo.resolve = orig_resolve  # type: ignore[method-assign]
    assert repo.open(repo.resolve(COORD), "a.txt").read_bytes() == b"2"  # v2 not clobbered


# --- CLI: --mv, --stage, --force (§6.4) ---


def test_cli_mv(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    f = tmp_path / "a.txt"
    f.write_bytes(b"A")
    _run(repo, "publish", "m/prod", str(f), "--as", "a")
    r = _run(repo, "--json", "publish-over", "m/prod", "--mv", "a.txt:b.txt", "--as", "b")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["renamed"] == 1
    assert {e["path"] for e in json.loads(_run(repo, "--json", "ls", "m/prod").stdout)} == {"b.txt"}


def test_cli_stage_lands_off_head(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    f = tmp_path / "a.txt"
    f.write_bytes(b"A")
    _run(repo, "publish", "m/prod", str(f), "--as", "a")
    head_before = _run(repo, "head", "m/prod").stdout.strip()

    f2 = tmp_path / "a2.txt"
    f2.write_bytes(b"A2")
    r = _run(repo, "--json", "publish-over", "m/prod", f"a.txt={f2}", "--stage", "--as", "b")
    assert r.exit_code == 0, r.output
    staged = json.loads(r.stdout)["staged"]
    assert staged.startswith("staging-")
    assert _run(repo, "head", "m/prod").stdout.strip() == head_before  # head untouched
    assert _run(repo, "cat", f"m/prod:{staged}", "a.txt").stdout == "A2"  # resolves via staging ref


def test_cli_force_requires_message(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    f = tmp_path / "a.txt"
    f.write_bytes(b"A")
    v1 = _run(repo, "publish", "m/prod", str(f), "--as", "a").stdout.strip()
    f2 = tmp_path / "a2.txt"
    f2.write_bytes(b"A2")
    _run(repo, "publish-over", "m/prod", f"a.txt={f2}", "--as", "b")  # head advances off v1

    # divergent base under --force but no -m → refused
    r = _run(
        repo, "publish-over", "m/prod", "--from", f"@{v1}", "--rm", "a.txt", "--force", "--as", "b"
    )
    assert r.exit_code != 0
    assert "requires -m" in r.output
