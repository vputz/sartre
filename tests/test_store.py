"""Behavioral tests for the blob plane: CasStore, CachingStore, FsspecBlobBackend."""

from __future__ import annotations

import io
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import BinaryIO

import pytest

from sartre import CachingStore, CasStore, IntegrityError, NotFound
from sartre.model import Hash


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
    # Corrupt the bytes behind the key directly in the backend.
    store.backend.put(key, io.BytesIO(b"tampered!!"))
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
