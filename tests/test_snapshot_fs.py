"""Behavioral + property tests for the read-only SnapshotFS view and checkout."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import fsspec
import pytest
from hypothesis import given
from hypothesis import strategies as st

from sartre import (
    CachingStore,
    CasStore,
    Coordinate,
    Entry,
    FsspecBlobBackend,
    MemoryRegistry,
    PathError,
    Repository,
    Snapshot,
    SnapshotFS,
)
from sartre.model import Hash

COORD = Coordinate("models", "release")


def _cas() -> CasStore:
    fs = fsspec.filesystem("memory")
    return CasStore(FsspecBlobBackend(fs, root=f"b-{uuid.uuid4().hex}"))


def _repo(remote: CasStore | None = None) -> Repository:
    return Repository(MemoryRegistry(), CachingStore(local=_cas(), remote=remote or _cas()))


class _RaisingStore:
    """A Store whose byte-plane methods raise — proves listings never fetch."""

    def has(self, content_hash: Hash) -> bool:
        return True

    def open(self, content_hash: Hash) -> BinaryIO:
        raise AssertionError("listing must not fetch blobs")

    def get_to(self, content_hash: Hash, dest: Path) -> Path:
        raise AssertionError("listing must not fetch blobs")

    def put(self, data: BinaryIO) -> Hash:
        raise AssertionError("read-only view")

    def delete(self, content_hash: Hash) -> None:
        raise AssertionError("read-only view")


class _CountingRemote:
    """Wraps a Store and counts open() calls (a download probe)."""

    def __init__(self, inner: CasStore) -> None:
        self.inner = inner
        self.opens = 0

    def has(self, content_hash: Hash) -> bool:
        return self.inner.has(content_hash)

    def open(self, content_hash: Hash) -> BinaryIO:
        self.opens += 1
        return self.inner.open(content_hash)

    def get_to(self, content_hash: Hash, dest: Path) -> Path:
        return self.inner.get_to(content_hash, dest)

    def put(self, data: BinaryIO) -> Hash:
        return self.inner.put(data)

    def delete(self, content_hash: Hash) -> None:
        self.inner.delete(content_hash)


def test_listing_served_from_manifest_without_fetch() -> None:
    repo = _repo()
    repo.publish(COORD, {"a/b/c.txt": b"deep", "a/x.txt": b"x", "top.txt": b"t"})
    snap = repo.resolve(COORD)

    fs = SnapshotFS(snap, _RaisingStore())  # any blob fetch would raise
    assert set(fs.ls("", detail=False)) == {"a", "top.txt"}
    assert set(fs.ls("a", detail=False)) == {"a/b", "a/x.txt"}
    assert fs.info("a")["type"] == "directory"
    assert fs.info("top.txt") == {"name": "top.txt", "size": 1, "type": "file"}
    assert fs.exists("a/b/c.txt")
    assert not fs.exists("nope")
    assert set(fs.find("")) == {"a/b/c.txt", "a/x.txt", "top.txt"}


def test_open_roundtrip_and_random_access() -> None:
    repo = _repo()
    repo.publish(COORD, {"w.bin": b"0123456789abcdef"})
    fs = repo.snapshot_fs(repo.resolve(COORD))

    with fs.open("w.bin", "rb") as f:
        assert f.read() == b"0123456789abcdef"
    with fs.open("w.bin", "rb") as f:
        f.seek(10)
        assert f.read(3) == b"abc"  # random access on the materialized blob
        f.seek(-2, 2)
        assert f.read() == b"ef"


def test_nested_listing_returns_immediate_children_only() -> None:
    repo = _repo()
    repo.publish(COORD, {"a/b/c.txt": b"1", "a/b/d.txt": b"2", "a/e.txt": b"3"})
    fs = repo.snapshot_fs(repo.resolve(COORD))
    assert set(fs.ls("a", detail=False)) == {"a/b", "a/e.txt"}
    assert set(fs.ls("a/b", detail=False)) == {"a/b/c.txt", "a/b/d.txt"}


def test_writes_are_rejected() -> None:
    repo = _repo()
    repo.publish(COORD, {"a.txt": b"x"})
    fs = repo.snapshot_fs(repo.resolve(COORD))
    with pytest.raises(PermissionError):
        fs.open("a.txt", "wb")
    with pytest.raises(PermissionError):
        fs.mkdir("newdir")
    with pytest.raises(PermissionError):
        fs.rm("a.txt")


def test_checkout_lays_out_tree_and_reuses_cache(tmp_path: Path) -> None:
    # Producer publishes straight to the remote (no caching); a separate consumer
    # with a cold cache observes downloads, then re-uses them on a second checkout.
    registry = MemoryRegistry()
    remote = _CountingRemote(_cas())
    Repository(registry, remote).publish(COORD, {"a/b/c.txt": b"deep", "top.txt": b"top"})
    assert remote.opens == 0  # publishing only puts

    consumer = Repository(registry, CachingStore(local=_cas(), remote=remote))
    snap = consumer.resolve(COORD)

    dest = tmp_path / "co"
    consumer.checkout(snap, dest)
    assert (dest / "a" / "b" / "c.txt").read_bytes() == b"deep"
    assert (dest / "top.txt").read_bytes() == b"top"
    assert remote.opens == 2  # two unique blobs downloaded once each

    consumer.checkout(snap, tmp_path / "co2")  # cache now warm
    assert remote.opens == 2  # no re-download


def test_checkout_rejects_escaping_entry(tmp_path: Path) -> None:
    # Hand-build a snapshot with a malicious path (publish would have normalized it)
    # to prove the containment guard fires before any bytes are written.
    evil = Snapshot(
        coord=COORD,
        version="sha256:" + "0" * 64,
        created_at=datetime.now(UTC),
        metadata={},
        entries=(Entry("../evil.txt", "sha256:" + "1" * 64, 4),),
    )
    repo = _repo()
    with pytest.raises(PathError):
        repo.checkout(evil, tmp_path / "co")
    assert not (tmp_path / "evil.txt").exists()


_names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=6)
_filesets = st.dictionaries(_names, st.binary(max_size=64), min_size=1, max_size=5)


@given(files=_filesets)
def test_find_equals_published_and_opens_to_bytes(files: dict[str, bytes]) -> None:
    repo = _repo()
    repo.publish(COORD, files)
    fs = repo.snapshot_fs(repo.resolve(COORD))
    assert set(fs.find("")) == set(files)
    for path, data in files.items():
        assert fs.open(path, "rb").read() == data


def test_pyarrow_reads_by_logical_path() -> None:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")

    table = pa.table({"n": [1, 2, 3], "s": ["a", "b", "c"]})
    buf = io.BytesIO()
    pq.write_table(table, buf)

    repo = _repo()
    repo.publish(COORD, {"data/train.parquet": buf.getvalue()})
    fs = repo.snapshot_fs(repo.resolve(COORD))

    got = pq.read_table("data/train.parquet", filesystem=fs)  # by name, bytes by hash
    assert got.equals(table)
