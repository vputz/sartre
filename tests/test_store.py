"""Behavioral tests for the blob plane: CasStore, CachingStore, FsspecBlobBackend."""

from __future__ import annotations

import io
import threading
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import BinaryIO, cast

import fsspec
import pytest
from hypothesis import given
from hypothesis import strategies as st

from sartre import (
    DEFAULT_HASHER,
    CachingStore,
    CasStore,
    FsspecBlobBackend,
    IntegrityError,
    NotFound,
)
from sartre.model import Hash


def _fresh_backend() -> FsspecBlobBackend:
    fs = fsspec.filesystem("memory")
    return FsspecBlobBackend(fs, root=f"blobs-{uuid.uuid4().hex}")


class _ExplodingReader(io.BytesIO):
    """Dribbles one byte, then raises — simulates a put failing mid-write."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self._calls = 0

    def read(self, size: int | None = -1) -> bytes:
        self._calls += 1
        if self._calls >= 2:
            raise RuntimeError("boom")
        return super().read(1)


def test_put_has_get_roundtrip(make_store: Callable[[], CasStore], tmp_path: Path) -> None:
    store = make_store()
    key = store.put(io.BytesIO(b"hello"))
    assert key.startswith("sha256:")
    assert store.has(key)
    assert store.open(key).read() == b"hello"
    dest = store.get_to(key, tmp_path / "out.bin")
    assert dest.read_bytes() == b"hello"


def test_put_is_idempotent(make_store: Callable[[], CasStore]) -> None:
    store = make_store()
    k1 = store.put(io.BytesIO(b"same"))
    k2 = store.put(io.BytesIO(b"same"))
    assert k1 == k2


def test_missing_blob_raises_not_found(make_store: Callable[[], CasStore]) -> None:
    store = make_store()
    with pytest.raises(NotFound):
        store.open("sha256:" + "0" * 64)


def test_corrupted_blob_fails_verification(make_store: Callable[[], CasStore]) -> None:
    store = make_store()
    key = store.put(io.BytesIO(b"trustworthy"))
    # Corrupt the stored bytes directly via the filesystem (backend.put is now
    # idempotent and would no-op on an existing key).
    backend = cast(FsspecBlobBackend, store.backend)
    with cast(BinaryIO, backend.fs.open(backend._path(key), "wb")) as handle:
        handle.write(b"tampered!!")
    with pytest.raises(IntegrityError):
        store.open(key)


def test_caching_store_reuses_local_without_remote(make_store: Callable[[], CasStore]) -> None:
    remote, local = make_store(), make_store()
    key = remote.put(io.BytesIO(b"payload"))  # only remote has it
    cache = CachingStore(local=local, remote=remote)
    assert cache.open(key).read() == b"payload"  # back-fills local
    assert local.has(key)
    remote.delete(key)  # now only the cache has it
    assert cache.open(key).read() == b"payload"  # served from local, no remote


class _CountingStore:
    """Wraps a Store and counts open() calls (a fetch probe)."""

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

    def list(self) -> Iterable[Hash]:
        return self.inner.list()

    def mtime(self, content_hash: Hash) -> float | None:
        return self.inner.mtime(content_hash)


def test_concurrent_fetch_downloads_once(make_store: Callable[[], CasStore]) -> None:
    remote = _CountingStore(make_store())
    key = remote.put(io.BytesIO(b"x" * 256))
    cache = CachingStore(local=make_store(), remote=remote)

    barrier = threading.Barrier(8)

    def fetch() -> bytes:
        barrier.wait()
        return cache.open(key).read()

    threads = [threading.Thread(target=fetch) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert remote.opens == 1  # per-hash lock: downloaded exactly once


# --- atomic blob writes ---


def test_failed_put_leaves_no_partial_blob() -> None:
    backend = _fresh_backend()
    key = "sha256:" + "a" * 64  # opaque key at the backend level
    with pytest.raises(RuntimeError):
        backend.put(key, _ExplodingReader(b"partial-payload-bytes"))
    assert not backend.exists(key)  # no partial blob at the hash
    assert key not in set(backend.list())
    assert backend.sweep_temp() == 0  # finally cleaned the staging object

    backend.put(key, io.BytesIO(b"whole"))  # a subsequent good put works
    assert backend.get(key).read() == b"whole"


def test_list_excludes_temp_and_sweep_reclaims_orphans() -> None:
    backend = _fresh_backend()
    key = "sha256:" + "b" * 64
    backend.put(key, io.BytesIO(b"real"))
    # Plant an orphan staging object (a put that crashed before its rename).
    backend.fs.makedirs(backend._temp_root, exist_ok=True)
    orphan = f"{backend._temp_root}/{uuid.uuid4().hex}"
    with cast(BinaryIO, backend.fs.open(orphan, "wb")) as handle:
        handle.write(b"orphan")

    assert set(backend.list()) == {key}  # staging namespace excluded
    assert backend.sweep_temp() == 1  # orphan reclaimed
    assert not backend.fs.exists(orphan)
    assert backend.sweep_temp() == 0  # no-op when clean


def test_concurrent_atomic_puts_all_verify(make_store: Callable[[], CasStore]) -> None:
    store = make_store()
    payloads = [f"blob-{i}".encode() for i in range(20)]
    payloads = payloads + payloads  # duplicates race on the same hash

    def worker(p: bytes) -> None:
        store.put(io.BytesIO(p))

    threads = [threading.Thread(target=worker, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    listed = set(store.backend.list())
    assert len(listed) == 20  # 20 distinct blobs, dedup under concurrency
    for key in listed:
        assert store.open(key).read()  # every listed blob verifies (no partial)


@given(ops=st.lists(st.tuples(st.binary(min_size=1, max_size=32), st.booleans()), max_size=15))
def test_no_partial_blob_invariant(ops: list[tuple[bytes, bool]]) -> None:
    store = CasStore(_fresh_backend())
    backend = cast(FsspecBlobBackend, store.backend)
    succeeded: set[str] = set()
    for payload, should_fail in ops:
        key = DEFAULT_HASHER.hash(io.BytesIO(payload))
        if should_fail and key not in succeeded:
            with pytest.raises(RuntimeError):  # absent key: the failing put raises
                backend.put(key, _ExplodingReader(payload))
        elif should_fail:
            backend.put(key, _ExplodingReader(payload))  # present key: idempotent no-op
        else:
            backend.put(key, io.BytesIO(payload))
            succeeded.add(key)

    # list() is exactly the successfully-put blobs — never a partial — and each verifies.
    assert set(backend.list()) == succeeded
    for key in succeeded:
        assert DEFAULT_HASHER.hash(store.open(key)) == key  # verifies + hashes back to key
