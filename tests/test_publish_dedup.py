"""Wire-level dedup: `Store.put(known_hash=…)` skips uploads the durable store already holds,
and publish uses it — without falling into the CachingStore local-cache trap.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterable
from typing import BinaryIO

import fsspec

from sartre import (
    CachingStore,
    CasStore,
    Coordinate,
    FsspecBlobBackend,
    MemoryRegistry,
    Repository,
)

COORD = Coordinate("models", "prod")
_ABSENT = "sha256:" + "0" * 64


class _CountingBackend:
    """A `BlobBackend` that counts `stage` calls — the actual upload/wire operation."""

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


def _counting_cas() -> tuple[CasStore, _CountingBackend]:
    fs = fsspec.filesystem("memory")
    backend = _CountingBackend(FsspecBlobBackend(fs, root=f"b-{uuid.uuid4().hex}"))
    return CasStore(backend), backend


def _plain_cas() -> CasStore:
    fs = fsspec.filesystem("memory")
    return CasStore(FsspecBlobBackend(fs, root=f"b-{uuid.uuid4().hex}"))


# --- Store.put(known_hash) contract ---


def test_known_present_hash_skips_stage() -> None:
    store, backend = _counting_cas()
    h = store.put(io.BytesIO(b"hello"))
    assert backend.stages == 1

    again = store.put(io.BytesIO(b"hello"), known_hash=h)  # durable already holds it
    assert again == h
    assert backend.stages == 1  # no second upload


def test_known_absent_hash_still_uploads_named_by_true_bytes() -> None:
    store, backend = _counting_cas()
    # a bogus known_hash for bytes the backend does not have: must upload and name truthfully
    got = store.put(io.BytesIO(b"world"), known_hash=_ABSENT)
    assert backend.stages == 1
    assert got != _ABSENT
    assert got == _plain_cas().put(io.BytesIO(b"world"))  # named by real content


# --- publish uses it ---


def test_publish_second_time_uploads_only_new_blobs() -> None:
    store, backend = _counting_cas()
    repo = Repository(MemoryRegistry(), store)

    repo.publish(COORD, {"a.txt": b"1", "b.txt": b"2"})
    assert backend.stages == 2

    repo.publish(COORD, {"a.txt": b"1", "b.txt": b"2", "c.txt": b"3"})  # only c is new
    assert backend.stages == 3  # a, b skipped; one new upload


# --- the CachingStore trap: skip must be durable (remote), never local-cache ---


def test_publish_uploads_to_remote_even_when_only_local_cache_has_it() -> None:
    remote, remote_backend = _counting_cas()
    local = _plain_cas()
    store = CachingStore(local=local, remote=remote)

    h = local.put(io.BytesIO(b"cached-only"))  # seed LOCAL only
    assert not remote.has(h)  # remote (durable) lacks it

    repo = Repository(MemoryRegistry(), store)
    repo.publish(COORD, {"x": b"cached-only"})

    assert remote.has(h)          # durable store received it despite the local hit
    assert remote_backend.stages == 1

    # and it resolves through a COLD cache (fresh local) over the same remote
    cold = Repository(repo.registry, CachingStore(local=_plain_cas(), remote=remote))
    snap = cold.resolve(COORD)
    assert cold.open(snap, "x").read_bytes() == b"cached-only"


def test_republish_identical_content_is_still_idempotent() -> None:
    store, backend = _counting_cas()
    repo = Repository(MemoryRegistry(), store)
    v1 = repo.publish(COORD, {"a.txt": b"same"})
    v2 = repo.publish(COORD, {"a.txt": b"same"})
    assert v1 == v2               # same version
    assert backend.stages == 1    # second publish uploaded nothing
